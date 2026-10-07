"""Indexer: crawl -> name vectors -> content vectors (images, docs, video, audio).

Run:  uv run python -m lupa.indexer            (full incremental run)
      uv run python -m lupa.indexer --crawl-only
"""
import argparse
import json
import os
import signal
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from .common import (CFG, DATA, KIND_LABELS, KIND_PRIORITY, V_AUDIO, V_NAME, V_TEXT, V_VISUAL,
                     connect, kind_of, meta_get, meta_set, nfc, root_of, root_online, thumb_path)

PID_FILE = DATA / "indexer.pid"
CONTENT_KINDS = {"image", "raw", "design", "video", "audio", "pdf", "doc", "sheet", "slides",
                 "text", "web", "code", "font", "model3d"}
STOP = False
DENIED: list[str] = []
PROTECTED: list[str] = []


def _other_users_private(d: str) -> bool:
    """Another account's home folders (e.g. /Users/someone/Desktop) are unreadable by design — not a problem."""
    try:
        st = os.stat(d)
    except OSError:
        return False
    return st.st_uid != os.getuid() and not (st.st_mode & 0o004)


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def status(con, **kw):
    s = json.loads(meta_get(con, "status", "{}") or "{}")
    s.update(kw, updated=time.time())
    meta_set(con, "status", json.dumps(s, ensure_ascii=False))
    con.commit()


# ---------------------------------------------------------------- crawl
def _walk(top, scan_id, rows, con, counter):
    ex_dirs, ex_paths = CFG["exclude_dirs"], set(CFG.get("exclude_paths", []))
    pk, skip = CFG["package_exts"], CFG["skip_exts"]
    root = root_of(top)
    stack = [top]
    errors = 0
    while stack and not STOP:
        d = stack.pop()
        try:
            it = list(os.scandir(d))
            errors = 0
        except PermissionError:
            PROTECTED.append(d)  # never purge what we couldn't read
            if not _other_users_private(d):
                DENIED.append(d)  # macOS privacy (TCC): worth a warning in the UI
            continue
        except OSError:
            errors += 1
            if errors > 50 and not root_online(root):
                raise RuntimeError(f"wolumen {root} zniknął w trakcie skanowania")
            continue
        for e in it:
            n = e.name
            if n.startswith(".") or n.startswith("._"):
                continue
            try:
                if e.is_symlink():
                    continue
                if e.is_dir():
                    if e.path in ex_paths or n in ex_dirs:
                        continue
                    if n == "Library" and (os.path.dirname(d) == "/Users" or d == os.path.expanduser("~")):
                        continue  # ~/Library (apps' internals); iCloud Drive comes via include_paths
                    if n.lower().endswith(pk):
                        st = e.stat()
                        k = kind_of(n)
                        rows.append((e.path, root, nfc(n), os.path.splitext(n)[1].lower(),
                                     k if k != "other" else "package", 0, st.st_mtime, st.st_birthtime, scan_id, 0))
                    else:
                        stack.append(e.path)
                    continue
                ext = os.path.splitext(n)[1].lower()
                if ext in skip or n.lower() in skip:
                    continue
                st = e.stat()
                dl = 1 if getattr(st, "st_flags", 0) & 0x40000000 else 0
                rows.append((e.path, root, nfc(n), ext, kind_of(n), st.st_size, st.st_mtime,
                             getattr(st, "st_birthtime", st.st_ctime), scan_id, dl))
            except OSError:
                continue
            if len(rows) >= 2000:
                _flush(con, rows)
                counter[0] += 2000
                status(con, phase="Skanowanie folderów", done=counter[0], total=0, current=d)


def _flush(con, rows):
    con.executemany("""
        INSERT INTO files(path,root,name,ext,kind,size,mtime,ctime,scan,dataless)
        VALUES(?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(path) DO UPDATE SET
          scan=excluded.scan, missing=0, dataless=excluded.dataless, root=excluded.root,
          stage=CASE WHEN files.size IS NOT excluded.size OR files.mtime IS NOT excluded.mtime
                     OR files.dataless != excluded.dataless THEN 0 ELSE files.stage END,
          size=excluded.size, mtime=excluded.mtime, ctime=excluded.ctime, name=excluded.name""", rows)
    con.commit()
    rows.clear()


def purge(con, ids):
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        q = ",".join("?" * len(part))
        con.execute(f"DELETE FROM vectors WHERE file_id IN ({q})", part)
        con.execute(f"DELETE FROM fts WHERE rowid IN ({q})", part)
        con.execute(f"DELETE FROM file_tags WHERE file_id IN ({q})", part)
        con.execute(f"DELETE FROM files WHERE id IN ({q})", part)
        for fid in part:
            try:
                thumb_path(fid).unlink()
            except FileNotFoundError:
                pass
    meta_set(con, "vec_gen", int(time.time() * 1000))
    con.commit()


def crawl(con, roots=None, cleanup=True):
    scan_id = int(time.time())
    counter = [0]
    for root in roots if roots is not None else CFG["roots"]:
        if not root_online(root):
            log(f"⚠️  {root} offline — pomijam (indeks zostaje)")
            continue
        log(f"Skanuję {root}")
        rows = []
        tops = [root] + [p for p in CFG.get("include_paths", []) if root_of(p) == root and os.path.isdir(p)]
        try:
            for t in tops:
                _walk(t, scan_id, rows, con, counter)
            _flush(con, rows)
        except RuntimeError as e:
            log("⚠️ ", e)
            _flush(con, rows)
            continue
        if STOP or not root_online(root):
            continue
        denied = tuple(d.rstrip("/") + "/" for d in PROTECTED)
        gone = [r[0] for r in con.execute(
            "SELECT id, path FROM files WHERE root=? AND scan<?", (root, scan_id))
                if not (denied and r[1].startswith(denied))]
        if gone:
            log(f"Usuwam z indeksu {len(gone)} plików, których już nie ma")
            purge(con, gone)
    if not cleanup:
        return
    # files whose root was removed from config
    roots = CFG["roots"]
    q = ",".join("?" * len(roots))
    stale = [r[0] for r in con.execute(f"SELECT id FROM files WHERE root NOT IN ({q})", roots)]
    if stale:
        purge(con, stale)
    status(con, denied=sorted(set(DENIED))[:50])
    if DENIED:
        log(f"⚠️  Brak dostępu do {len(DENIED)} folderów (np. {DENIED[0]}) — nadaj Lupie Pełny dostęp do dysku")
    log(f"Skan zakończony: {con.execute('SELECT count(*) FROM files').fetchone()[0]} plików w indeksie")


# ---------------------------------------------------------------- vectors
def _put_vecs(con, rows):
    con.executemany("INSERT INTO vectors(file_id,vtype,part,vec) VALUES(?,?,?,?)",
                    [(f, t, p, np.asarray(v, np.float16).tobytes()) for f, t, p, v in rows])


def folder_label(path, root):
    rel = os.path.dirname(path)[len(root):].strip("/")
    parts = rel.split("/") if rel else []
    return nfc(" / ".join(parts[-5:]))


def migrate_nfc(con, emb):
    """One-off: names indexed before NFC normalisation get fresh name vectors (content is kept)."""
    if meta_get(con, "nfc_v1"):
        return
    rows = [r for r in con.execute("SELECT id,path,root,name,kind FROM files WHERE stage!=0")
            if nfc(r["path"]) != r["path"]]
    log(f"Migracja NFC: {len(rows)} nazw z polskimi znakami")
    for i in range(0, len(rows), 256):
        part = rows[i:i + 256]
        ids = [r["id"] for r in part]
        q = ",".join("?" * len(ids))
        con.execute(f"DELETE FROM vectors WHERE vtype={V_NAME} AND file_id IN ({q})", ids)
        names = [nfc(os.path.basename(r["path"])) for r in part]
        folders = [folder_label(r["path"], r["root"]) for r in part]
        texts = [f"{KIND_LABELS.get(r['kind'], '')} w folderze: {f}" for r, f in zip(part, folders)]
        vecs = emb.documents([os.path.splitext(n)[0] for n in names], texts, batch_size=128)
        _put_vecs(con, [(r["id"], V_NAME, 0, v) for r, v in zip(part, vecs)])
        con.executemany("UPDATE files SET name=? WHERE id=?", list(zip(names, ids)))
        con.executemany("UPDATE fts SET name=?, folder=? WHERE rowid=?", list(zip(names, folders, ids)))
        status(con, phase="Poprawa polskich znaków w nazwach", done=i + len(part), total=len(rows), current="")
    meta_set(con, "nfc_v1", 1)
    meta_set(con, "vec_gen", int(time.time() * 1000))
    con.commit()


def embed_names(con, emb):
    total = con.execute("SELECT count(*) FROM files WHERE stage=0").fetchone()[0]
    done = 0
    while not STOP:
        rows = con.execute("""SELECT id,path,root,name,kind,dataless FROM files WHERE stage=0
                              ORDER BY root LIKE '/Volumes/%', mtime DESC LIMIT 512""").fetchall()
        if not rows:
            break
        ids = [r["id"] for r in rows]
        q = ",".join("?" * len(ids))
        con.execute(f"DELETE FROM vectors WHERE file_id IN ({q})", ids)
        con.execute(f"DELETE FROM fts WHERE rowid IN ({q})", ids)
        con.execute(f"UPDATE files SET thumb=0, snippet=NULL, error=NULL WHERE id IN ({q})", ids)
        folders = [folder_label(r["path"], r["root"]) for r in rows]
        texts = [f"{KIND_LABELS.get(r['kind'], '')} w folderze: {f}" for r, f in zip(rows, folders)]
        vecs = emb.documents([os.path.splitext(r["name"])[0] for r in rows], texts, batch_size=64)
        _put_vecs(con, [(r["id"], V_NAME, 0, v) for r, v in zip(rows, vecs)])
        con.executemany("INSERT INTO fts(rowid,name,folder,body) VALUES(?,?,?,'')",
                        [(r["id"], r["name"], f) for r, f in zip(rows, folders)])
        con.executemany("UPDATE files SET stage=? WHERE id=?",
                        [(1 if r["kind"] in CONTENT_KINDS and not r["dataless"] else 3, r["id"]) for r in rows])
        meta_set(con, "vec_gen_add", time.time())
        done += len(rows)
        status(con, phase="Indeksowanie nazw", done=done, total=total, current=rows[-1]["path"])
    log(f"Nazwy: {done} plików")


def _thumb_one(r):
    from .extract import quick_thumb, save_thumb
    if thumb_path(r["id"]).exists():  # already made on demand by the server
        return r["id"], 1
    try:
        im = quick_thumb(r["path"], r["kind"])
        if im is None:
            return r["id"], -1
        save_thumb(im, thumb_path(r["id"]))
        return r["id"], 1
    except Exception:
        return r["id"], -1


def make_thumbs(con):
    """Fast CPU-only pass so the grid shows real previews long before the (slow) GPU analysis finishes."""
    offline = [r for r in CFG["roots"] if not root_online(r)]
    excl = ",".join("?" * len(offline)) or "''"
    rows = con.execute(f"""SELECT id,path,kind,root FROM files WHERE thumb=0 AND dataless=0
                           AND kind IN ('image','video','pdf') AND root NOT IN ({excl})
                           ORDER BY mtime DESC""", offline).fetchall()
    total, done = len(rows), 0
    with ThreadPoolExecutor(6) as pool:
        for i in range(0, total, 96):
            if STOP:
                break
            part = rows[i:i + 96]
            res = list(pool.map(_thumb_one, part))
            con.executemany("UPDATE files SET thumb=? WHERE id=?", [(v, fid) for fid, v in res])
            done += len(part)
            status(con, phase="Miniatury", done=done, total=total, current=part[-1]["path"])
    log(f"Miniatury: {done}")


def _extract_one(r):
    from .extract import extract
    try:
        return r, extract(r["path"], r["kind"], r["size"] or 0), None
    except Exception as e:  # noqa: BLE001
        return r, None, f"{type(e).__name__}: {e}"[:300]


def embed_content(con, emb, limit=None):
    from .extract import chunks, save_thumb, snippet
    prio = " ".join(f"WHEN '{k}' THEN {v}" for k, v in KIND_PRIORITY.items())
    total = con.execute("SELECT count(*) FROM files WHERE stage=1").fetchone()[0]
    done, t0 = 0, time.time()
    offline = {r for r in CFG["roots"] if not root_online(r)}
    pool = ThreadPoolExecutor(6)
    skip_ids = []

    def next_rows(busy):
        excl = ",".join("?" * len(offline)) or "''"
        sk = ",".join(str(i) for i in (skip_ids[-5000:] + busy)) or "0"
        return con.execute(f"""
            SELECT id,path,root,name,kind,size FROM files
            WHERE stage=1 AND root NOT IN ({excl}) AND id NOT IN ({sk})
            ORDER BY CASE kind {prio} ELSE 9 END, mtime DESC LIMIT 48""", list(offline)).fetchall()

    rows = next_rows([])
    futs = [pool.submit(_extract_one, r) for r in rows]
    while rows and not STOP:
        results = [f.result() for f in futs]
        # prefetch the next batch from disk/NAS while the GPU works on this one
        nxt = next_rows([r["id"] for r in rows]) if not (limit and done + len(rows) >= limit) else []
        futs_next = [pool.submit(_extract_one, r) for r in nxt]
        img_items, txt_items, aud_items, updates = [], [], [], []
        for r, ex, err in results:
            fid = r["id"]
            if err or ex is None:
                if not root_online(r["root"]):
                    offline.add(r["root"])
                    skip_ids.append(fid)
                    continue
                updates.append(("err", fid, err))
                continue
            for i, im in enumerate(ex.images):
                img_items.append((fid, i, im))
            for i, c in enumerate(chunks(ex.text)):
                txt_items.append((fid, i, os.path.splitext(r["name"])[0], c))
            if ex.audio is not None:
                aud_items.append((fid, ex.audio))
            if ex.thumb is not None and not thumb_path(fid).exists():
                try:
                    save_thumb(ex.thumb, thumb_path(fid))
                    ex.meta["thumb"] = 1
                except Exception:
                    pass
            updates.append(("ok", fid, ex))
        vec_rows = []
        try:
            if img_items:
                vs = emb.images([x[2] for x in img_items])
                vec_rows += [(f, V_VISUAL, p, v) for (f, p, _), v in zip(img_items, vs)]
            if txt_items:
                vs = emb.documents([x[2] for x in txt_items], [x[3] for x in txt_items])
                vec_rows += [(f, V_TEXT, p, v) for (f, p, _, _), v in zip(txt_items, vs)]
            if aud_items:
                vs = emb.audio([x[1] for x in aud_items])
                vec_rows += [(f, V_AUDIO, 0, v) for (f, _), v in zip(aud_items, vs)]
        except Exception as e:  # noqa: BLE001 — fall back to one-by-one so one bad file can't block a batch
            log("batch encode failed, retrying singly:", e)
            vec_rows = _encode_singly(emb, img_items, txt_items, aud_items, updates)
        _put_vecs(con, vec_rows)
        for u in updates:
            if u[0] == "err":
                con.execute("UPDATE files SET stage=-1, error=? WHERE id=?", (u[2], u[1]))
                continue
            fid, ex = u[1], u[2]
            m = ex.meta
            con.execute("""UPDATE files SET stage=2, snippet=?, width=?, height=?, duration=?, pages=?,
                           thumb=MAX(thumb, ?) WHERE id=?""",
                        (snippet(ex.text) or None, m.get("width"), m.get("height"), m.get("duration"),
                         m.get("pages"), m.get("thumb", 0), fid))
            if ex.text.strip():
                con.execute("UPDATE fts SET body=? WHERE rowid=?", (ex.text, fid))
        done += len(rows)
        rate = done / max(time.time() - t0, 1)
        status(con, phase="Analiza treści (obrazy, dokumenty, wideo, audio)", done=done, total=total,
               current=rows[-1]["path"], rate=round(rate, 2))
        del results, img_items, txt_items, aud_items, updates, vec_rows
        emb.free()
        rows, futs = nxt, futs_next
    pool.shutdown(wait=False, cancel_futures=True)
    log(f"Treść: {done} plików ({done / max(time.time() - t0, 1):.1f}/s)")


def _encode_singly(emb, img_items, txt_items, aud_items, updates):
    out, bad = [], set()
    for f, p, im in img_items:
        try:
            out.append((f, V_VISUAL, p, emb.images([im])[0]))
        except Exception:
            bad.add(f)
    for f, p, title, c in txt_items:
        try:
            out.append((f, V_TEXT, p, emb.documents([title], [c])[0]))
        except Exception:
            bad.add(f)
    for f, a in aud_items:
        try:
            out.append((f, V_AUDIO, 0, emb.audio([a])[0]))
        except Exception:
            bad.add(f)
    for i, u in enumerate(updates):
        if u[0] == "ok" and u[1] in bad:
            updates[i] = ("err", u[1], "encode failed")
    return out


# ---------------------------------------------------------------- main
def main():
    global STOP
    ap = argparse.ArgumentParser()
    ap.add_argument("--crawl-only", action="store_true")
    ap.add_argument("--no-crawl", action="store_true")
    ap.add_argument("--limit", type=int, default=None, help="max files for content pass (testing)")
    a = ap.parse_args()

    def _stop(*_):
        global STOP
        STOP = True
        log("Zatrzymuję po bieżącej paczce…")
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    PID_FILE.write_text(str(os.getpid()))
    con = connect()
    try:
        status(con, running=True, started=time.time(), phase="Start", done=0, total=0, current="")
        local = [r for r in CFG["roots"] if not r.startswith("/Volumes/")]
        network = [r for r in CFG["roots"] if r.startswith("/Volumes/")]
        if not a.no_crawl:
            crawl(con, local, cleanup=False)  # seconds
        if a.crawl_only and not network:
            return
        from .embedder import Embedder
        status(con, phase="Ładowanie modelu")
        emb = Embedder()
        migrate_nfc(con, emb)
        embed_names(con, emb)  # local files become searchable before the slow NAS scan
        if not a.no_crawl and network:
            # a NAS scan can take an hour of pure I/O — don't sit on GBs of model memory meanwhile
            emb.free()
            del emb
            emb = None
            import gc
            gc.collect()
            crawl(con, network)  # also drops files of roots removed from config
            status(con, phase="Ładowanie modelu")
            emb = Embedder()
        elif not a.no_crawl:
            crawl(con, [])  # cleanup of roots removed from config
        if a.crawl_only:
            return
        embed_names(con, emb)
        make_thumbs(con)
        embed_content(con, emb, limit=a.limit)
        meta_set(con, "last_index", time.time())
    except Exception as e:
        status(con, running=False, phase=f"Błąd: {type(e).__name__} — szczegóły w data/indexer.log", current="")
        PID_FILE.unlink(missing_ok=True)
        raise
    else:
        status(con, running=False, phase="Gotowe" if not STOP else "Zatrzymano", current="")
    finally:
        PID_FILE.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
