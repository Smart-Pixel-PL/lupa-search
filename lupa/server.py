"""Lupa web server: http://localhost:7766"""
import io
import os
import signal
import subprocess
import sys
import threading
import time
import json
import re

import numpy as np
from fastapi import Body, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .common import (CFG, DATA, DEFAULT_FORMATS, KIND_LABELS, PROJECT, V_VISUAL, connect, meta_get, meta_set, root_online,
                     thumb_path)
from .search import REASONS, VectorIndex, keyword_hits

PORT = CFG.get("port", 7766)
STATIC = PROJECT / "static"
app = FastAPI(title="Lupa")
con = connect()
vindex = VectorIndex()
from .colors import ColorIndex  # noqa: E402
cindex = ColorIndex()
_emb = None      # text-only on CPU: ~60 ms per query even while the indexer owns the GPU
_emb_vis = None  # vision model on the GPU, loaded on first image search
_emb_lock = threading.Lock()
_indexer: subprocess.Popen | None = None
_online = {}
FRAME_POS = (0.08, 0.35, 0.62, 0.9)


def emb():
    global _emb
    with _emb_lock:
        if _emb is None:
            from .embedder import Embedder
            _emb = Embedder(audio=False, vision=False, device="cpu")
    return _emb


def emb_vis():
    global _emb_vis
    with _emb_lock:
        if _emb_vis is None:
            from .embedder import Embedder
            _emb_vis = Embedder(audio=False)
    return _emb_vis


def db():
    return connect(readonly=True)


# ------------------------------------------------------------- security
@app.middleware("http")
async def guard(request: Request, call_next):
    host = (request.headers.get("host") or "").split(":")[0]
    if host not in ("localhost", "127.0.0.1"):
        return JSONResponse({"error": "forbidden host"}, 403)
    if request.method != "GET":
        origin = request.headers.get("origin")
        if request.headers.get("x-lupa") != "1" or (origin and not origin.startswith(
                (f"http://localhost:{PORT}", f"http://127.0.0.1:{PORT}"))):
            return JSONResponse({"error": "forbidden"}, 403)
    resp = await call_next(request)
    if not request.url.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


# ------------------------------------------------------------- background
def _bg_loop():
    global _online
    last_try = 0.0
    while True:
        try:
            c = db()
            vindex.refresh(c)
            prev, _online = _online, {r: root_online(r) for r in CFG["roots"]}
            came_back = any(v and prev.get(r) is False for r, v in _online.items())
            every = CFG.get("reindex_every_hours", 3)
            last = float(meta_get(c, "last_index", "0") or 0)
            _indexer_speed()
            _colors_job(c)
            _ocr_job(c)
            _asr_job(c)
            if not _cleanup_busy.locked():  # weekly Porządki scan / low-space alert, off the main loop
                threading.Thread(target=_cleanup_periodic, daemon=True).start()
            if came_back and not indexer_running():  # e.g. NAS mounted again
                start_indexer()
            elif not indexer_running() and time.time() - last_try > 600 and (
                    _pending_work(c) or (every and time.time() - last > every * 3600)):
                # resume unfinished work right away; otherwise a cheap refresh every `every` hours
                # (the indexer itself decides whether the slow NAS scan is due — at most daily)
                last_try = time.time()
                start_indexer()
            c.close()
        except Exception as e:  # noqa: BLE001
            print("bg:", e, file=sys.stderr)
        time.sleep(20)


@app.on_event("startup")
def _startup():
    vindex.refresh(db(), force=True)
    threading.Thread(target=_bg_loop, daemon=True).start()
    threading.Thread(target=emb, daemon=True).start()  # warm the model


def indexer_running():
    if _indexer is not None and _indexer.poll() is None:
        return True
    pidf = DATA / "indexer.pid"
    if pidf.exists():
        try:
            os.kill(int(pidf.read_text()), 0)
            return True
        except (OSError, ValueError):
            pidf.unlink(missing_ok=True)
    return False


def start_indexer(force_crawl=False):
    global _indexer
    if indexer_running() or (DATA / "pause").exists():  # data/pause = hold all indexing (e.g. while re-granting access)
        return False
    logf = open(DATA / "indexer.log", "a")
    # taskpolicy -b = macOS "background" QoS: efficiency cores + throttled disk I/O, so the indexer only
    # uses what the user's apps leave free (the Mac stays responsive while it works).
    # macOS background QoS is fixed per thread at launch and can't be lifted later, so the mode is chosen here:
    # mains power → nice 10 (user apps still win, full speed otherwise); battery → background QoS.
    ac = _on_ac()
    prefix = ["nice", "-n", "10"] if ac or not CFG.get("fast_on_ac", True) else ["taskpolicy", "-b"]
    cmd = prefix + [sys.executable, "-m", "lupa.indexer"] + (["--force-crawl"] if force_crawl else [])
    (DATA / "indexer.mode").write_text("ac" if ac else "battery")
    _indexer = subprocess.Popen(cmd, cwd=PROJECT, stdout=logf, stderr=subprocess.STDOUT,
                                start_new_session=True)  # survives server restarts
    return True


def _on_ac() -> bool:
    try:
        return "AC Power" in subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return True


_last_mode_switch = 0.0


def _indexer_speed():
    """Relaunch the indexer when the power source changes (it resumes where it stopped, no rescans)."""
    global _last_mode_switch
    if not CFG.get("fast_on_ac", True) or not indexer_running() or time.time() - _last_mode_switch < 600:
        return
    try:
        mode = (DATA / "indexer.mode").read_text().strip()
    except OSError:
        return
    if mode != ("ac" if _on_ac() else "battery"):
        _last_mode_switch = time.time()
        try:
            os.kill(int((DATA / "indexer.pid").read_text()), signal.SIGTERM)  # bg loop restarts it in the new mode
        except (OSError, ValueError):
            pass


_colors_last = 0.0


def _colors_job(c):
    """Compute colour palettes for new thumbnails in a background CPU process (every few minutes)."""
    global _colors_last
    pidf = DATA / "colors.pid"
    if pidf.exists():
        try:
            os.kill(int(pidf.read_text()), 0)
            return
        except (OSError, ValueError):
            pidf.unlink(missing_ok=True)
    if time.time() - _colors_last < 300:
        return
    _colors_last = time.time()
    try:
        cindex.refresh(c)
    except Exception:
        pass
    if c.execute("SELECT 1 FROM files WHERE colors IS NULL AND thumb=1 AND kind IN "
                 "('image','design','raw','video','pdf') LIMIT 1").fetchone():
        prefix = ["nice", "-n", "10"] if _on_ac() else ["taskpolicy", "-b"]
        subprocess.Popen(prefix + [sys.executable, "-m", "lupa.colors"], cwd=PROJECT,
                         stdout=open(DATA / "colors.log", "a"), stderr=subprocess.STDOUT, start_new_session=True)


def _job_alive(name):
    pidf = DATA / f"{name}.pid"
    if not pidf.exists():
        return False
    try:
        os.kill(int(pidf.read_text()), 0)
        return True
    except (OSError, ValueError):
        pidf.unlink(missing_ok=True)
        return False


def _start_job(module, log):
    prefix = ["nice", "-n", "10"] if _on_ac() else ["taskpolicy", "-b"]
    subprocess.Popen(prefix + [sys.executable, "-m", module], cwd=PROJECT, stdout=open(DATA / log, "a"),
                     stderr=subprocess.STDOUT, start_new_session=True)


_ocr_last = 0.0


def _ocr_job(c):
    """OCR of scans/screenshots: at most every 12 h (new scans are picked up then)."""
    global _ocr_last
    if not CFG.get("ocr", True) or _job_alive("ocr") or time.time() - _ocr_last < 1800:
        return
    _ocr_last = time.time()
    last = float(meta_get(c, "ocr_last_run", "0") or 0)
    if time.time() - last < 12 * 3600:
        return
    connect().execute("INSERT INTO meta(k,v) VALUES('ocr_last_run',?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                      (time.time(),)).connection.commit()
    _start_job("lupa.ocr", "ocr.log")


_asr_last = 0.0


def _asr_job(c):
    """Transcription needs the GPU like the indexer, so it only runs while the indexer is idle."""
    global _asr_last
    if (not CFG.get("transcribe", True) or _job_alive("transcribe") or indexer_running()
            or (DATA / "pause").exists() or time.time() - _asr_last < 1800):
        return
    _asr_last = time.time()
    try:
        pending = c.execute("SELECT 1 FROM files WHERE kind IN ('audio','video') AND asr IS NULL AND size > 50000 LIMIT 1").fetchone()
    except Exception:
        pending = True  # column not created yet — the job creates it
    if pending:
        _start_job("lupa.transcribe", "transcribe.log")


def _color_ids(p, c):
    """File ids that contain a meaningful share of the requested colour, best first."""
    cindex.refresh(c)
    ids, sc = cindex.score(p["color"])
    keep = sc >= float(p.get("color_min", 0.12))
    order = np.argsort(-sc[keep])
    return ids[keep][order], sc[keep][order]


def _pending_work(c) -> bool:
    return c.execute("SELECT 1 FROM files WHERE stage IN (0,1) LIMIT 1").fetchone() is not None


# ------------------------------------------------------------- helpers
def _filters_sql(p):
    where, args = ["1=1"], []
    if p.get("kinds"):
        ks = [k for k in p["kinds"].split(",") if k]
        where.append(f"f.kind IN ({','.join('?' * len(ks))})")
        args += ks
    if p.get("roots"):
        rs = [r for r in p["roots"].split("|") if r]
        where.append(f"f.root IN ({','.join('?' * len(rs))})")
        args += rs
    if p.get("since"):
        where.append("f.mtime >= ?")
        args.append(time.time() - float(p["since"]) * 86400)
    if p.get("exts"):
        es = [e if e.startswith(".") else "." + e for e in p["exts"].lower().split(",") if e]
        where.append(f"f.ext IN ({','.join('?' * len(es))})")
        args += es
    if p.get("minsize"):
        where.append("f.size >= ?")
        args.append(int(p["minsize"]))
    if p.get("folder"):  # one or more folders, "|"-separated — each includes its subfolders
        fs = [f.rstrip("/") for f in p["folder"].split("|") if f]
        where.append("(" + " OR ".join("(f.path >= ? AND f.path < ?)" for _ in fs) + ")")
        for f in fs:
            args += [f + "/", f + "0"]  # '0' sorts right after '/': a fast range scan on the path index
        if p.get("direct") == "1" and len(fs) == 1:  # browsing: only files directly in the folder
            where.append("instr(substr(f.path, ?), '/') = 0")
            args.append(len(fs[0]) + 2)
    if p.get("tags"):
        for t in [int(x) for x in p["tags"].split(",") if x]:
            where.append("f.id IN (SELECT file_id FROM file_tags WHERE tag_id=?)")
            args.append(t)
    return " AND ".join(where), args


def _has_filters(p):
    return any(p.get(k) for k in ("kinds", "exts", "roots", "since", "minsize", "folder", "tags", "color"))


def _rows_to_items(c, rows, extra=None):
    ids = [r["id"] for r in rows]
    tags = {}
    if ids:
        q = ",".join("?" * len(ids))
        for r in c.execute(f"SELECT file_id, tag_id FROM file_tags WHERE file_id IN ({q})", ids):
            tags.setdefault(r[0], []).append(r[1])
    out = []
    for r in rows:
        d = {k: r[k] for k in ("id", "path", "name", "ext", "kind", "size", "mtime", "ctime", "width",
                               "height", "duration", "pages", "thumb", "stage", "root")}
        d["folder"] = os.path.dirname(r["path"])
        d["offline"] = not _online.get(r["root"], True)
        d["tags"] = tags.get(r["id"], [])
        if extra and r["id"] in extra:
            d.update(extra[r["id"]])
        out.append(d)
    return out


SORTS = {"mtime": "f.mtime", "name": "f.name COLLATE NOCASE", "size": "f.size", "ctime": "f.ctime", "kind": "f.kind"}


def _rank(c, qvec, p, keyword_q=None, exclude=None, vtypes=None, name_weight=0.8):
    allowed = None
    if _has_filters(p):
        w, a = _filters_sql(p)
        allowed = np.fromiter((r[0] for r in c.execute(f"SELECT f.id FROM files f WHERE {w}", a)), np.int64)
        if p.get("color"):
            allowed = np.intersect1d(allowed, _color_ids(p, c)[0])
    fids, z, rows = vindex.score(qvec, allowed, exclude, vtypes=vtypes, name_weight=name_weight)
    if not len(fids):
        return [], {}
    z = z.copy()
    reason = {}
    if keyword_q:
        hits = keyword_hits(c, keyword_q)
        if hits:
            pos = {int(f): i for i, f in enumerate(fids)}
            for f, b in hits.items():
                i = pos.get(f)
                if i is not None and z[i] > -8:  # never resurrect rows excluded by filters
                    z[i] += b
                    reason[f] = "słowo kluczowe"
    top = np.argsort(-z)[:600]
    top = top[z[top] > -8]  # drop rows excluded by filters / vector-type restriction
    top = top[z[top] > 1.2] if (z[top] > 1.2).sum() >= 5 else top[:50]
    extra = {}
    for i in top:
        f, r = int(fids[i]), int(rows[i])
        vt, part = int(vindex.vtype[r]), int(vindex.part[r])
        e = {"score": round(float(z[i]), 2), "why": reason.get(f) or REASONS.get(vt, "")}
        if vt == 2:
            e["part"] = part
        extra[f] = e
    return [int(fids[i]) for i in top], extra


def _fetch(c, ids, extra, p):
    if not ids:
        return [], 0
    q = ",".join("?" * len(ids))
    rows = {r["id"]: r for r in c.execute(f"SELECT * FROM files f WHERE id IN ({q})", ids)}
    rows = [rows[i] for i in ids if i in rows]
    sort = p.get("sort") or "relevance"
    if sort in SORTS:
        key = {"mtime": "mtime", "name": "name", "size": "size", "ctime": "ctime", "kind": "kind"}[sort]
        rows.sort(key=lambda r: (r[key] is None, (r[key] or "").lower() if key == "name" else (r[key] or 0)),
                  reverse=p.get("order", "desc") == "desc")
    items = _rows_to_items(c, rows, extra)
    for it in items:
        if it.get("part") is not None and it["kind"] == "video" and it.get("duration"):
            it["t"] = round(it["duration"] * FRAME_POS[min(it["part"], 3)], 1)
    if p.get("group") == "1":
        items = group_versions(items)
    off, lim = int(p.get("offset", 0)), int(p.get("limit", 120))
    return items[off:off + lim], len(items)


# Version markers that never change *what* the file is: "(1)", "copy", "kopia", "final", "v2", "wersja 3"…
_VER_WORD = re.compile(r"(?:\s*\(\d+\)|[\s_.\-]+(?:copy|kopia|final|finał|ostateczn\w*|popraw\w*|wersja\s*\d+|ver\d+|v\d+))$", re.I)
# A trailing number only counts as a version when another number precedes it: tigra_66_01 → tigra_66,
# but tigra_66 stays (66 is the design), and IMG_4482 stays (different photos).
_VER_NUM = re.compile(r"(\d+[\s_.\-]+)\d{1,3}$")


def _stem(name: str) -> str:
    stem = name.rsplit(".", 1)[0].lower()
    for _ in range(4):
        new = _VER_WORD.sub("", stem)
        new = _VER_NUM.sub(lambda m: m.group(1).rstrip(" _.-"), new)
        if new == stem:
            break
        stem = new
    return stem.strip(" _-.") or name.lower()


VISUAL_KINDS = {"image", "design", "raw"}


def group_versions(items):
    """Collapse versions of one project into a single card: same folder + same name stem
    (tigra_66_01.psd, tigra_66_02.png, tigra_66_final.jpg…) or visually near-identical images."""
    n = len(items)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)  # keep the better-ranked item as the group head

    seen = {}
    for i, it in enumerate(items):
        key = (it["folder"], _stem(it["name"]))
        if key in seen:
            union(seen[key], i)
        else:
            seen[key] = i
    vis = [i for i, it in enumerate(items[:400]) if it["kind"] in VISUAL_KINDS]
    vecs = vindex.visual_vectors([items[i]["id"] for i in vis])
    vis = [i for i in vis if items[i]["id"] in vecs]
    if len(vis) > 1:
        # Only practically identical images (same picture resized / re-exported). Measured on 1 894 sublimation
        # designs: different designs on one template still score ~0.88-0.97, so 0.99 keeps them apart.
        # Star grouping (compare with the group head only) — no A≈B≈C chains swallowing whole collections.
        m = np.stack([vecs[items[i]["id"]] for i in vis])
        m /= np.linalg.norm(m, axis=1, keepdims=True) + 1e-9
        sim = m @ m.T
        taken = set()
        for x in range(len(vis)):
            if x in taken:
                continue
            for y in np.flatnonzero(sim[x] >= 0.99):
                if y > x and y not in taken:
                    union(vis[x], vis[y])
                    taken.add(int(y))
    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    out = []
    for head in sorted(groups):
        members = groups[head]
        it = dict(items[head])
        if len(members) > 1:
            it["versions"] = [{k: items[j].get(k) for k in ("id", "name", "path", "folder", "kind", "ext", "size",
                                                              "mtime", "width", "height", "thumb", "offline", "tags")}
                              for j in members]
        out.append(it)
    return out


# ------------------------------------------------------------- API
@app.get("/api/search")
def search(request: Request):
    p = dict(request.query_params)
    c = db()
    q = (p.get("q") or "").strip()
    t0 = time.time()
    if p.get("collection"):
        r = c.execute("SELECT * FROM collections WHERE id=?", (int(p["collection"]),)).fetchone()
        if r:
            q = r["query"]
            if r["kinds"] and not p.get("kinds"):
                p["kinds"] = r["kinds"]
    if p.get("like_tag"):
        t = _tag_centroids(c).get(int(p["like_tag"]))
        if not t:
            raise HTTPException(404, "tag ma za mało plików, żeby się z niego uczyć (min. 2)")
        tagged = [r[0] for r in c.execute("SELECT file_id FROM file_tags WHERE tag_id=?", (int(p["like_tag"]),))]
        ids, extra = _rank(c, t["vec"], p)
        ids = [i for i in ids if i not in set(tagged)]
        items, total = _fetch(c, ids, extra, p)
        return {"items": items, "total": total, "ms": int((time.time() - t0) * 1000), "query": f"🏷 pasuje do: {t['name']}"}
    if p.get("similar"):
        fv = vindex.file_vector(int(p["similar"]))
        if fv is None:
            raise HTTPException(404, "brak wektora dla pliku")
        ids, extra = _rank(c, fv[0], p, exclude=int(p["similar"]), vtypes=fv[1])
        items, total = _fetch(c, ids, extra, p)
    elif q:
        vt = {"visual": {2}, "text": {1}, "content": {1, 2, 3}, "name": {0}}.get(p.get("vt"))
        ids, extra = _rank(c, emb().query(q), p, keyword_q=None if vt else q, vtypes=vt)
        items, total = _fetch(c, ids, extra, p)
    elif p.get("color") and (p.get("sort") in (None, "", "relevance")):
        # colour only: best colour match first
        w, a = _filters_sql(p)
        cids, csc = _color_ids(p, c)
        ok = set(r[0] for r in c.execute(f"SELECT f.id FROM files f WHERE {w}", a)) if (w != "1=1") else None
        order = [int(i) for i in cids if ok is None or int(i) in ok][:600]
        share = dict(zip(map(int, cids[:5000]), map(float, csc[:5000])))
        extra = {i: {"score": round(1 + 4 * share.get(i, 0), 2), "why": f"kolor {round(100 * share.get(i, 0))}%"}
                 for i in order}
        items, total = _fetch(c, order, extra, p)
    else:
        w, a = _filters_sql(p)
        sort = SORTS.get(p.get("sort"), "f.mtime")
        order = "ASC" if p.get("order") == "asc" else "DESC"
        off, lim = int(p.get("offset", 0)), int(p.get("limit", 120))
        rows = c.execute(f"SELECT * FROM files f WHERE {w} ORDER BY {sort} {order} LIMIT ? OFFSET ?",
                         a + [lim, off]).fetchall()
        total = c.execute(f"SELECT count(*) FROM files f WHERE {w}", a).fetchone()[0]
        items = _rows_to_items(c, rows)
        if p.get("group") == "1":
            items = group_versions(items)  # per page when browsing
    return {"items": items, "total": total, "ms": int((time.time() - t0) * 1000), "query": q}


@app.post("/api/search/image")
async def search_image(request: Request, file: UploadFile = File(...)):
    from PIL import Image
    from .extract import _fit, _rgb
    im = _fit(_rgb(Image.open(io.BytesIO(await file.read()))), 896)
    p = dict(request.query_params)
    c = db()
    ids, extra = _rank(c, emb_vis().images([im])[0], p, vtypes={V_VISUAL})
    items, total = _fetch(c, ids, extra, p)
    return {"items": items, "total": total, "ms": 0, "query": f"🖼 {file.filename}"}


@app.get("/api/file/{fid}")
def file_info(fid: int):
    c = db()
    r = c.execute("SELECT * FROM files f WHERE id=?", (fid,)).fetchone()
    if not r:
        raise HTTPException(404)
    it = _rows_to_items(c, [r])[0]
    it["snippet"] = r["snippet"]
    it["error"] = r["error"]
    body = c.execute("SELECT body FROM fts WHERE rowid=?", (fid,)).fetchone()
    it["text"] = (body[0] or "")[:4000] if body else ""
    return it


def _path(fid):
    r = db().execute("SELECT path FROM files WHERE id=?", (fid,)).fetchone()
    if not r:
        raise HTTPException(404)
    return r[0]


_thumb_sem = threading.Semaphore(6)


@app.get("/api/thumb/{fid}")
def thumb(fid: int):
    tp = thumb_path(fid)
    if tp.exists():
        return FileResponse(tp, media_type="image/webp", headers={"Cache-Control": "max-age=86400"})
    miss = tp.with_suffix(".none")  # failure marker as a file: the server never writes to the DB here
    if miss.exists():
        raise HTTPException(404)
    r = db().execute("SELECT path, kind, root, thumb FROM files WHERE id=?", (fid,)).fetchone()
    if not r or r["thumb"] == -1 or not _online.get(r["root"], True) or not os.path.exists(r["path"]):
        raise HTTPException(404)
    if r["kind"] in ("archive", "package", "other", "audio"):
        raise HTTPException(404)
    from .extract import quick_thumb, quicklook, save_thumb
    with _thumb_sem:
        im = None
        try:
            im = quick_thumb(r["path"], r["kind"])
            if im is None:
                im = quicklook(r["path"], 480, timeout=12)
        except Exception:
            im = None
    if im is None:
        miss.touch()
        raise HTTPException(404)
    save_thumb(im, tp)
    return FileResponse(tp, media_type="image/webp", headers={"Cache-Control": "max-age=86400"})


@app.get("/api/raw/{fid}")
def raw(fid: int):
    p = _path(fid)
    if not os.path.exists(p):
        raise HTTPException(404, "plik niedostępny")
    ext = os.path.splitext(p)[1].lower()
    if ext in (".heic", ".heif", ".tif", ".tiff", ".psd", ".cr2", ".nef", ".arw", ".dng"):
        from .extract import load_image, quicklook
        try:
            im = load_image(p)
        except Exception:
            im = quicklook(p, 1600)
        if im is None:
            raise HTTPException(415)
        im.thumbnail((2000, 2000))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=88)
        return Response(buf.getvalue(), media_type="image/jpeg")
    return FileResponse(p, content_disposition_type="inline")


@app.post("/api/action/{fid}/{what}")
def action(fid: int, what: str):
    p = _path(fid)
    cmd = {"open": ["open", p], "reveal": ["open", "-R", p]}.get(what)
    if not cmd:
        raise HTTPException(400)
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"ok": True}


# tag suggestions (learned from how you already tag) -----------------------------
_tag_cent: dict = {"t": 0, "data": {}}


def _tag_centroids(c):
    """Per tag and per vector kind (visual / text): mean vector of its files and how close its own files
    are to it. Images are compared with images and documents with documents — the spaces differ."""
    if time.time() - _tag_cent["t"] < 60:
        return _tag_cent["data"]
    out = {}
    for t in c.execute("SELECT t.id, t.name, t.color FROM tags t"):
        fids = [r[0] for r in c.execute("SELECT file_id FROM file_tags WHERE tag_id=?", (t["id"],))]
        groups = {}
        for f in fids[:500]:
            fv = vindex.file_vector(f)
            if fv is not None:
                groups.setdefault("visual" if 2 in fv[1] else "text", []).append(fv[0])
        kinds = {}
        for k, vs in groups.items():
            if len(vs) < 2:
                continue
            m = np.stack(vs)
            cen = m.mean(0)
            cen /= np.linalg.norm(cen) or 1
            kinds[k] = {"vec": cen, "floor": float(np.percentile(m @ cen, 10)) - 0.02, "n": len(vs)}
        if kinds:
            best = max(kinds.values(), key=lambda x: x["n"])
            out[t["id"]] = {"name": t["name"], "color": t["color"], "kinds": kinds, "vec": best["vec"]}
    _tag_cent.update(t=time.time(), data=out)
    return out


@app.get("/api/suggest-tags/{fid}")
def suggest_tags(fid: int):
    c = db()
    fv = vindex.file_vector(fid)
    if fv is None:
        return []
    kind = "visual" if 2 in fv[1] else "text"
    have = {r[0] for r in c.execute("SELECT tag_id FROM file_tags WHERE file_id=?", (fid,))}
    out = []
    for tid, t in _tag_centroids(c).items():
        k = t["kinds"].get(kind)
        if tid in have or not k:
            continue
        sim = float(fv[0] @ k["vec"])
        if sim >= max(0.55, k["floor"]):  # about as close as the files you already tagged
            out.append({"id": tid, "name": t["name"], "color": t["color"], "sim": round(sim, 3)})
    return sorted(out, key=lambda x: -x["sim"])[:3]


# tags -------------------------------------------------------------------
@app.get("/api/tags")
def tags():
    c = db()
    return [dict(r) for r in c.execute(
        "SELECT t.id, t.name, t.color, (SELECT count(*) FROM file_tags ft WHERE ft.tag_id=t.id) AS n "
        "FROM tags t ORDER BY t.id=1 DESC, t.name COLLATE NOCASE")]


@app.post("/api/tags")
def tag_create(d: dict = Body(...)):
    c = connect()
    name = (d.get("name") or "").strip()[:60]
    if not name:
        raise HTTPException(400)
    c.execute("INSERT OR IGNORE INTO tags(name,color) VALUES(?,?)", (name, d.get("color") or "#7c8cff"))
    c.commit()
    return dict(c.execute("SELECT * FROM tags WHERE name=?", (name,)).fetchone())


@app.patch("/api/tags/{tid}")
def tag_update(tid: int, d: dict = Body(...)):
    c = connect()
    if d.get("name"):
        c.execute("UPDATE tags SET name=? WHERE id=?", (d["name"].strip()[:60], tid))
    if d.get("color"):
        c.execute("UPDATE tags SET color=? WHERE id=?", (d["color"], tid))
    c.commit()
    return {"ok": True}


@app.delete("/api/tags/{tid}")
def tag_delete(tid: int):
    c = connect()
    c.execute("DELETE FROM file_tags WHERE tag_id=?", (tid,))
    c.execute("DELETE FROM tags WHERE id=?", (tid,))
    c.commit()
    return {"ok": True}


@app.post("/api/tags/{tid}/files")
def tag_files(tid: int, d: dict = Body(...)):
    c = connect()
    ids = [int(i) for i in d.get("ids", [])]
    if d.get("add", True):
        c.executemany("INSERT OR IGNORE INTO file_tags(file_id,tag_id) VALUES(?,?)", [(i, tid) for i in ids])
    else:
        c.executemany("DELETE FROM file_tags WHERE file_id=? AND tag_id=?", [(i, tid) for i in ids])
    c.commit()
    return {"ok": True}


# collections --------------------------------------------------------------
@app.get("/api/collections")
def collections():
    return [dict(r) for r in db().execute("SELECT * FROM collections ORDER BY pos, id")]


@app.post("/api/collections")
def collection_create(d: dict = Body(...)):
    c = connect()
    c.execute("INSERT OR REPLACE INTO collections(name,query,kinds,icon,pos) VALUES(?,?,?,?,999)",
              (d["name"].strip()[:60], d["query"], d.get("kinds", ""), d.get("icon") or "🔎"))
    c.commit()
    return {"ok": True}


@app.delete("/api/collections/{cid}")
def collection_delete(cid: int):
    c = connect()
    c.execute("DELETE FROM collections WHERE id=?", (cid,))
    c.commit()
    return {"ok": True}


# status / indexer ---------------------------------------------------------
@app.get("/api/status")
def status():
    c = db()
    kinds = {r[0]: r[1] for r in c.execute("SELECT kind, count(*) FROM files GROUP BY kind")}
    stages = {r[0]: r[1] for r in c.execute("SELECT stage, count(*) FROM files GROUP BY stage")}
    st = json.loads(meta_get(c, "status", "{}") or "{}")
    st["running"] = indexer_running()
    return {
        "kinds": kinds, "labels": KIND_LABELS, "stages": stages, "vectors": len(vindex),
        "roots": [{"path": r, "online": _online.get(r, root_online(r)),
                   "count": c.execute("SELECT count(*) FROM files WHERE root=?", (r,)).fetchone()[0]}
                  for r in CFG["roots"]],
        "formats": [{**fm, "count": c.execute(
            f"SELECT count(*) FROM files WHERE ext IN ({','.join('?' * len(fm['exts']))})", fm["exts"]).fetchone()[0]}
            for fm in CFG.get("formats", DEFAULT_FORMATS)],
        "places": [{**pl, "count": c.execute("SELECT count(*) FROM files WHERE path >= ? AND path < ?",
                                              (pl["path"] + "/", pl["path"] + "0")).fetchone()[0]}
                   for pl in CFG.get("places", [])],
        "indexer": st, "model_ready": _emb is not None,
        "last_index": float(meta_get(c, "last_index", "0") or 0),
    }


@app.post("/api/index/start")
def index_start(full: int = 0):
    return {"started": start_indexer(force_crawl=bool(full))}  # ?full=1 (UI button) = also rescan NAS


@app.post("/api/index/stop")
def index_stop():
    pidf = DATA / "indexer.pid"
    if pidf.exists():
        try:
            os.kill(int(pidf.read_text()), signal.SIGTERM)
        except (OSError, ValueError):
            pass
    return {"ok": True}


# ask your documents (RAG with a local Gemma via Ollama) ---------------------
ASK_SYSTEM = ("Jesteś asystentem wyszukiwarki plików Lupa. Odpowiadasz po polsku, zwięźle i konkretnie, WYŁĄCZNIE na "
              "podstawie podanych fragmentów dokumentów użytkownika. Po każdej informacji podaj numer źródła w nawiasie "
              "kwadratowym, np. [2]. Jeśli fragmenty nie zawierają odpowiedzi, napisz wprost: „Nie znalazłem tego w Twoich "
              "plikach” i podpowiedz, jak inaczej zapytać. Nie wymyślaj liczb, nazw, dat ani kwot.")


def _passage(c, fid, part):
    """The exact text chunk that the best-matching vector of this file was built from."""
    from .extract import chunks
    body = (c.execute("SELECT body FROM fts WHERE rowid=?", (fid,)).fetchone() or [""])[0] or ""
    body, _, asr = body.partition("\n[TRANSKRYPCJA]\n")
    main, _, ocr = body.partition("\n[OCR]\n")
    parts = chunks(main) if part < 10 else chunks(ocr) if part < 20 else chunks(asr)
    k = part if part < 10 else part - 10 if part < 20 else part - 20
    return parts[k] if 0 <= k < len(parts) else (main or ocr)[:1800]


_GENERIC = {"nip", "vat", "pdf", "regon", "krs", "pesel", "faktura", "umowa", "oferta", "pln", "zł", "pl", "jpg",
            "png", "psd", "ul", "sp", "zoo", "z", "o", "jaki", "jaka", "jakie", "kto", "gdzie", "kiedy", "ile", "czy"}


def _fold(t: str) -> str:
    import unicodedata
    t = unicodedata.normalize("NFKD", (t or "").lower())
    return "".join(ch for ch in t if not unicodedata.combining(ch)).replace("ł", "l")


def _salient(q: str):
    """Specific names/numbers in a question (AUTO-LESZEK, Ostrovia, 12/2025): words with capitals after the
    first word, ALL-CAPS words, or tokens with digits — minus generic words like NIP, VAT, faktura."""
    toks = re.findall(r"[\w][\w\-./]*", q, re.UNICODE)
    out = []
    for k, t in enumerate(toks):
        f = _fold(t)
        if len(f) < 3 or f in _GENERIC:
            continue
        if any(ch.isdigit() for ch in t):
            out.append(f)  # numbers must match exactly
        elif (t.isupper() and len(t) >= 3) or (k > 0 and t[0].isupper()):
            out.append(f[:max(4, len(f) - 2)])  # Polish inflection: Ostrovii ~ Ostrovia, Zinie ~ Zina
    return list(dict.fromkeys(out))


@app.post("/api/ask")
def ask(request: Request, d: dict = Body(...)):
    import json as _j
    import urllib.request
    from fastapi.responses import StreamingResponse
    from .common import V_TEXT
    question = str(d.get("q", "")).strip()[:500]
    if not question:
        raise HTTPException(400, "puste pytanie")
    p = {k: str(v) for k, v in (d.get("filters") or {}).items() if v}
    c = db()
    allowed = None
    if _has_filters(p):
        w, a = _filters_sql(p)
        allowed = np.fromiter((r[0] for r in c.execute(f"SELECT f.id FROM files f WHERE {w}", a)), np.int64)
    fids, z, rows = vindex.score(emb().query(question), allowed, vtypes={V_TEXT})
    hits = keyword_hits(c, question)
    if hits and len(fids):
        pos = {int(f): i for i, f in enumerate(fids)}
        for f, b in hits.items():
            i = pos.get(f)
            if i is not None and z[i] > -8:
                z[i] += b * 0.5
    top = [i for i in np.argsort(-z)[:40] if z[i] > 1.8]
    names = _salient(question)
    cands, seen = [], set()
    for i in top:
        fid, row = int(fids[i]), int(rows[i])
        r = c.execute("SELECT * FROM files f WHERE id=?", (fid,)).fetchone()
        if r:
            text = _passage(c, fid, int(vindex.part[row]))
            key = (r["name"], r["size"]) if r["size"] else text[:300]
            if key in seen or text[:300] in seen:  # the same document copied to several folders
                continue
            seen.update({key, text[:300]})
            if len(cands) >= 10:
                break
            hay = _fold(r["name"] + " " + r["path"] + " " + text)
            cands.append((r, text, sum(n in hay for n in names)))
    missing = []
    if names:
        # a question about a specific name/number must be answered from documents that contain it
        best = max((m for _, _, m in cands), default=0)
        if best == 0:
            missing = names
        cands = [x for x in cands if x[2] == best] if best else []
    srcs, ctx = [], []
    for r, text, _ in cands[:6]:
        item = _rows_to_items(c, [r])[0]
        item["n"] = len(srcs) + 1
        item["passage"] = text[:700]
        srcs.append(item)
        ctx.append(f"[{item['n']}] plik: {r['name']} (folder: {os.path.basename(os.path.dirname(r['path']))})\n{text}")
    model = CFG.get("ask_model", "gemma3:4b")

    def gen():
        yield _j.dumps({"type": "sources", "sources": srcs, "model": model}, ensure_ascii=False) + "\n"
        if not srcs:
            msg = ("Nie znalazłem w Twoich plikach dokumentów, które zawierają: " + ", ".join(missing).upper() + "."
                   if missing else "Nie znalazłem w Twoich plikach dokumentów pasujących do pytania.")
            msg += " Jeśli to skan, może jeszcze czekać na OCR — spróbuj później albo zapytaj inaczej."
            yield _j.dumps({"type": "token", "t": msg}, ensure_ascii=False) + "\n"
            yield '{"type":"done"}\n'
            return
        body = _j.dumps({"model": model, "stream": True, "keep_alive": "5m",
                         "options": {"temperature": 0.2, "num_ctx": 8192},
                         "messages": [{"role": "system", "content": ASK_SYSTEM},
                                      {"role": "user", "content": f"Pytanie: {question}\n\nFragmenty dokumentów "
                                                                  f"(używaj tylko numerów źródeł od 1 do {len(srcs)}):\n\n"
                                                                  + "\n\n".join(ctx)}]}).encode()
        req = urllib.request.Request("http://localhost:11434/api/chat", data=body,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                for line in resp:
                    if not line.strip():
                        continue
                    m = _j.loads(line)
                    t = (m.get("message") or {}).get("content", "")
                    if t:
                        yield _j.dumps({"type": "token", "t": t}, ensure_ascii=False) + "\n"
                    if m.get("done"):
                        break
        except Exception as e:  # noqa: BLE001
            yield _j.dumps({"type": "error", "error": f"Ollama nie odpowiada ({e}). Uruchom aplikację Ollama."},
                           ensure_ascii=False) + "\n"
        yield '{"type":"done"}\n'

    return StreamingResponse(gen(), media_type="application/x-ndjson")


# folder browser --------------------------------------------------------------
_browse_cache: dict = {}


@app.get("/api/browse")
def browse(path: str = ""):
    """Subfolders of `path` (from the index, works offline) with file counts and up to 4 preview thumbnails."""
    c = db()
    if not path and ("", int(time.time() // 300)) in _browse_cache:
        return _browse_cache[("", int(time.time() // 300))]
    if not path:
        tops = [(r, os.path.basename(r) or r) for r in CFG["roots"]]
        tops += [(pl["path"], pl["name"]) for pl in CFG.get("places", [])]
        tops += [(r["path"], r["name"]) for r in c.execute("SELECT path, name FROM pins ORDER BY pos")]
        folders = []
        for pth, name in tops:
            folders.append({"name": name, "path": pth, "count": _count_under(c, pth), "thumbs": _sample_thumbs(c, pth)})
        _browse_cache[("", int(time.time() // 300))] = res = {"path": "", "crumbs": [], "folders": folders}
        return res
    path = path.rstrip("/")
    key = (path, meta_get(c, "vec_gen", "0"), int(time.time() // 300))
    if key in _browse_cache:
        return _browse_cache[key]
    L = len(path) + 2
    seg = "substr(substr(path, ?), 1, instr(substr(path, ?), '/') - 1)"
    rows = c.execute(f"""SELECT {seg} AS seg, count(*) FROM files
                         WHERE path >= ? AND path < ? AND instr(substr(path, ?), '/') > 0
                         GROUP BY seg ORDER BY seg COLLATE NOCASE""", (L, L, path + "/", path + "0", L)).fetchall()
    thumbs = {}
    for seg_name, fid in c.execute(f"""SELECT seg, id FROM (
            SELECT {seg} AS seg, id, ROW_NUMBER() OVER (PARTITION BY {seg} ORDER BY mtime DESC) rn
            FROM files WHERE path >= ? AND path < ? AND instr(substr(path, ?), '/') > 0 AND thumb=1
            AND kind IN ('image','design','raw','video','pdf')) WHERE rn <= 4""",
                                   (L, L, L, L, path + "/", path + "0", L)):
        thumbs.setdefault(seg_name, []).append(fid)
    parts = [x for x in path.split("/") if x]
    crumbs, acc = [], ""
    for x in parts:
        acc += "/" + x
        if acc != "/Volumes":  # "Volumes" is just the mount point, not a place to browse
            crumbs.append({"name": x, "path": acc})
    direct = c.execute("SELECT count(*) FROM files WHERE path >= ? AND path < ? AND instr(substr(path, ?), '/') = 0",
                       (path + "/", path + "0", L)).fetchone()[0]
    res = {"path": path, "crumbs": crumbs, "parent": os.path.dirname(path) if len(parts) > 1 else "",
           "files": direct, "exists": os.path.isdir(path),
           "folders": [{"name": s_, "path": f"{path}/{s_}", "count": n, "thumbs": thumbs.get(s_, [])} for s_, n in rows]}
    if len(_browse_cache) > 200:
        _browse_cache.clear()
    _browse_cache[key] = res
    return res


def _sample_thumbs(c, path):
    return [r[0] for r in c.execute("""SELECT id FROM files WHERE path >= ? AND path < ? AND thumb=1
                                       AND kind IN ('image','design') LIMIT 4""",
                                    (path + "/", path + "0"))]


@app.get("/api/transcript/{fid}")
def transcript(fid: int):
    import json as _j
    try:
        r = db().execute("SELECT lang, segments FROM transcripts WHERE file_id=?", (fid,)).fetchone()
    except Exception:
        r = None
    return {"lang": r[0], "segments": _j.loads(r[1])} if r else {"segments": []}


@app.post("/api/open-folder")
def open_folder(d: dict = Body(...)):
    """Open an indexed folder in Finder (only folders Lupa knows about)."""
    path = str(d.get("path", "")).rstrip("/")
    if not path or not os.path.isdir(path) or not _count_under(db(), path):
        raise HTTPException(404)
    subprocess.Popen(["open", path])
    return {"ok": True}


@app.get("/api/colors/{fid}")
def file_colors(fid: int):
    """Palette of one file as hex + share (for the preview's colour chips)."""
    import json as _j
    r = db().execute("SELECT colors FROM files WHERE id=?", (fid,)).fetchone()
    out = []
    for L, A, B, f in (_j.loads(r[0]) if r and r[0] else []):
        out.append({"hex": _lab_to_hex(L, A, B), "share": f})
    return out


def _lab_to_hex(L, a, b):
    fy = (L + 16) / 116
    fx, fz = fy + a / 500, fy - b / 200
    def inv(t):
        return t ** 3 if t ** 3 > 0.008856 else (t - 16 / 116) / 7.787
    x, y, z = inv(fx) * 0.95047, inv(fy), inv(fz) * 1.08883
    r_ = 3.2404542 * x - 1.5371385 * y - 0.4985314 * z
    g_ = -0.9692660 * x + 1.8760108 * y + 0.0415560 * z
    b_ = 0.0556434 * x - 0.2040259 * y + 1.0572252 * z
    def g(u):
        u = 1.055 * u ** (1 / 2.4) - 0.055 if u > 0.0031308 else 12.92 * u
        return max(0, min(255, round(u * 255)))
    return "#%02x%02x%02x" % (g(r_), g(g_), g(b_))


# pinned folders ------------------------------------------------------------
def _count_under(c, path):
    return c.execute("SELECT count(*) FROM files WHERE path >= ? AND path < ?", (path + "/", path + "0")).fetchone()[0]


@app.get("/api/pins")
def pins():
    c = db()
    return [{**dict(r), "count": _count_under(c, r["path"]), "exists": os.path.isdir(r["path"])}
            for r in c.execute("SELECT * FROM pins ORDER BY pos, id")]


@app.post("/api/pins")
def pin_add(d: dict = Body(...)):
    path = os.path.abspath(os.path.expanduser(str(d.get("path", "")))).rstrip("/")
    if not path or not os.path.isdir(path):
        raise HTTPException(400, "to nie jest folder")
    c = connect()
    c.execute("INSERT OR IGNORE INTO pins(path,name,icon,pos) VALUES(?,?,?,(SELECT coalesce(max(pos),0)+1 FROM pins))",
              (path, d.get("name") or os.path.basename(path), d.get("icon") or "📌"))
    c.commit()
    return {"ok": True, "path": path}


@app.patch("/api/pins/{pid}")
def pin_edit(pid: int, d: dict = Body(...)):
    c = connect()
    for k in ("name", "icon"):
        if d.get(k):
            c.execute(f"UPDATE pins SET {k}=? WHERE id=?", (str(d[k])[:60], pid))
    c.commit()
    return {"ok": True}


@app.post("/api/pins/{pid}/reveal")
def pin_reveal(pid: int):
    r = db().execute("SELECT path FROM pins WHERE id=?", (pid,)).fetchone()
    if not r:
        raise HTTPException(404)
    subprocess.Popen(["open", r[0]])
    return {"ok": True}


@app.delete("/api/pins/{pid}")
def pin_delete(pid: int):
    c = connect()
    c.execute("DELETE FROM pins WHERE id=?", (pid,))
    c.commit()
    return {"ok": True}


def _osascript(script: str, timeout=600):
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=timeout)
    return r.stdout.strip(), r.returncode


@app.post("/api/pick-folder")
def pick_folder(d: dict = Body(default={})):
    """Native macOS folder chooser (the web page can't see real paths)."""
    prompt = str(d.get("prompt") or "Wybierz folder").replace('"', "'")
    out, rc = _osascript(f'tell application "System Events" to activate\n'
                         f'POSIX path of (choose folder with prompt "{prompt}")')
    if rc != 0 or not out:
        return {"path": None}
    return {"path": out.rstrip("/")}


@app.get("/api/folder-counts")
def folder_counts(paths: str):
    c = db()
    return {p: _count_under(c, p.rstrip("/")) for p in paths.split("|") if p}


# collect selected files ----------------------------------------------------
def _paths_for(ids):
    ids = [int(i) for i in ids][:5000]
    if not ids:
        return []
    q = ",".join("?" * len(ids))
    return [r[0] for r in db().execute(f"SELECT path FROM files WHERE id IN ({q})", ids)]


def _unique(dest_dir, name):
    base, ext = os.path.splitext(name)
    cand, n = os.path.join(dest_dir, name), 2
    while os.path.exists(cand):
        cand = os.path.join(dest_dir, f"{base} ({n}){ext}")
        n += 1
    return cand


@app.post("/api/collect/zip")
def collect_zip(d: dict = Body(...)):
    import zipfile
    paths = [p for p in _paths_for(d.get("ids", [])) if os.path.isfile(p)]
    if not paths:
        raise HTTPException(400, "brak dostępnych plików (dysk offline?)")
    dest = _unique(os.path.expanduser("~/Downloads"), f"Lupa {time.strftime('%Y-%m-%d %H.%M')}.zip")
    used = set()
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for p in paths:
            arc = os.path.basename(p)
            b, e = os.path.splitext(arc)
            n = 2
            while arc in used:
                arc = f"{b} ({n}){e}"
                n += 1
            used.add(arc)
            z.write(p, arc)
    subprocess.Popen(["open", "-R", dest])
    return {"ok": True, "path": dest, "count": len(paths), "size": os.path.getsize(dest)}


@app.post("/api/collect/copy")
def collect_copy(d: dict = Body(...)):
    import shutil
    dest_dir = str(d.get("dest") or "")
    if not dest_dir:
        dest_dir = pick_folder({"prompt": "Skopiuj zaznaczone pliki do…"})["path"]
    if not dest_dir or not os.path.isdir(dest_dir):
        return {"ok": False, "cancelled": True}
    paths = _paths_for(d.get("ids", []))
    done, failed = 0, []
    for p in paths:
        try:
            shutil.copy2(p, _unique(dest_dir, os.path.basename(p)))
            done += 1
        except OSError as e:
            failed.append(f"{os.path.basename(p)}: {e.strerror or e}")
    subprocess.Popen(["open", dest_dir])
    return {"ok": not failed, "dest": dest_dir, "count": done, "failed": failed[:5]}


# Porządki (cleanup) --------------------------------------------------------
from . import cleanup  # noqa: E402

_cleanup_busy = threading.Lock()
_cleanup_last = 0.0


def _cleanup_periodic():
    global _cleanup_last
    if time.time() - _cleanup_last < 3600 or not _cleanup_busy.acquire(blocking=False):
        return
    try:
        _cleanup_last = time.time()
        cleanup.periodic()
    except Exception as e:  # noqa: BLE001
        print("cleanup periodic:", e, file=sys.stderr)
    finally:
        _cleanup_busy.release()


@app.get("/api/cleanup")
def cleanup_report():
    return cleanup.report()


@app.post("/api/cleanup/scan")
def cleanup_scan():
    threading.Thread(target=cleanup.scan, daemon=True).start()
    return {"started": True}


@app.post("/api/cleanup/trash")
def cleanup_trash(d: dict = Body(...)):
    return cleanup.trash([str(p) for p in d.get("paths", [])][:5000])


@app.post("/api/cleanup/uninstaller")
def cleanup_uninstaller(d: dict = Body(...)):
    return cleanup.open_uninstaller(str(d.get("path", "")))


@app.get("/api/uninstall/apps")
def uninstall_apps():
    return cleanup.uninstall_targets()


@app.get("/api/uninstall/plan")
def uninstall_plan(path: str):
    try:
        return cleanup.uninstall_plan(path)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/uninstall/run")
def uninstall_run(d: dict = Body(...)):
    try:
        return cleanup.uninstall_execute(str(d["app"]), [str(p) for p in d.get("paths", [])])
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/cleanup/empty-trash")
def cleanup_empty_trash():
    return cleanup.empty_trash()


@app.post("/api/cleanup/open-trash")
def cleanup_open_trash():
    subprocess.Popen(["open", os.path.expanduser("~/.Trash")])
    return {"ok": True}


@app.post("/api/cleanup/reveal")
def cleanup_reveal(d: dict = Body(...)):
    p = str(d.get("path", ""))
    if p not in cleanup._candidates():
        raise HTTPException(404)
    subprocess.Popen(["open", "-R", p])
    return {"ok": True}


@app.post("/api/cleanup/protect")
def cleanup_protect(d: dict = Body(...)):
    cleanup.set_protect(str(d["path"]), bool(d.get("add", True)))
    return {"ok": True}


@app.post("/api/cleanup/settings")
def cleanup_settings(d: dict = Body(...)):
    c = connect()
    if "auto_caches" in d:
        meta_set(c, "cleanup_auto_caches", "1" if d["auto_caches"] else "0")
    c.commit()
    return {"ok": True}


@app.get("/api/cleanup/icon")
def cleanup_icon(path: str):
    """App icon (via Quick Look) for an installed .app — only real app bundles are accepted."""
    import hashlib
    from .extract import quicklook
    p = os.path.abspath(path)
    if not (p.endswith(".app") and os.path.isdir(p) and p.startswith(
            ("/Applications/", "/System/Applications/", "/System/Library/CoreServices/", os.path.expanduser("~/Applications/")))):
        raise HTTPException(404)
    dest = DATA / "thumbs" / "icons"
    dest.mkdir(exist_ok=True)
    f = dest / (hashlib.md5(p.encode()).hexdigest() + ".png")
    miss = f.with_suffix(".none")
    if miss.exists():
        raise HTTPException(404)
    if not f.exists():
        im = _bundle_icon(p) or quicklook(p, 128, timeout=4)
        if im is None:
            miss.touch()  # don't retry a slow failure on every page load
            raise HTTPException(404)
        im.save(f)
    return FileResponse(f, media_type="image/png", headers={"Cache-Control": "max-age=604800"})


def _bundle_icon(app: str):
    """Read the app's .icns straight from the bundle (fast); None if it only ships an asset catalog."""
    import plistlib
    from PIL import Image
    try:
        with open(os.path.join(app, "Contents", "Info.plist"), "rb") as fh:
            info = plistlib.load(fh)
        name = info.get("CFBundleIconFile") or info.get("CFBundleIconName") or "AppIcon"
        res = os.path.join(app, "Contents", "Resources")
        for cand in (name, name + ".icns"):
            fp = os.path.join(res, cand)
            if os.path.isfile(fp):
                im = Image.open(fp)
                im.load()
                im = im.convert("RGBA")
                im.thumbnail((128, 128))
                return im
    except Exception:
        return None
    return None


app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")


def main():
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
