"""Lupa web server: http://localhost:7766"""
import io
import os
import signal
import subprocess
import sys
import threading
import time
import json

import numpy as np
from fastapi import Body, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .common import (CFG, DATA, KIND_LABELS, PROJECT, V_VISUAL, connect, meta_get, meta_set, root_online,
                     thumb_path)
from .search import REASONS, VectorIndex, keyword_hits

PORT = CFG.get("port", 7766)
STATIC = PROJECT / "static"
app = FastAPI(title="Lupa")
con = connect()
vindex = VectorIndex()
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
            if not _cleanup_busy.locked():  # weekly Porządki scan / low-space alert, off the main loop
                threading.Thread(target=_cleanup_periodic, daemon=True).start()
            if came_back and not indexer_running():  # e.g. NAS mounted again
                start_indexer()
            elif every and not indexer_running() and time.time() - max(last, last_try) > every * 3600:
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


def start_indexer():
    global _indexer
    if indexer_running():
        return False
    logf = open(DATA / "indexer.log", "a")
    _indexer = subprocess.Popen(
        ["nice", "-n", "10", sys.executable, "-m", "lupa.indexer"], cwd=PROJECT,
        stdout=logf, stderr=subprocess.STDOUT, start_new_session=True)  # survives server restarts
    return True


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
    if p.get("minsize"):
        where.append("f.size >= ?")
        args.append(int(p["minsize"]))
    if p.get("folder"):
        where.append("f.path LIKE ? ESCAPE '\\'")
        args.append(p["folder"].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_").rstrip("/") + "/%")
    if p.get("tags"):
        for t in [int(x) for x in p["tags"].split(",") if x]:
            where.append("f.id IN (SELECT file_id FROM file_tags WHERE tag_id=?)")
            args.append(t)
    return " AND ".join(where), args


def _has_filters(p):
    return any(p.get(k) for k in ("kinds", "roots", "since", "minsize", "folder", "tags"))


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
    off, lim = int(p.get("offset", 0)), int(p.get("limit", 120))
    return items[off:off + lim], len(items)


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
    else:
        w, a = _filters_sql(p)
        sort = SORTS.get(p.get("sort"), "f.mtime")
        order = "ASC" if p.get("order") == "asc" else "DESC"
        off, lim = int(p.get("offset", 0)), int(p.get("limit", 120))
        rows = c.execute(f"SELECT * FROM files f WHERE {w} ORDER BY {sort} {order} LIMIT ? OFFSET ?",
                         a + [lim, off]).fetchall()
        total = c.execute(f"SELECT count(*) FROM files f WHERE {w}", a).fetchone()[0]
        items = _rows_to_items(c, rows)
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
        "places": [{**pl, "count": c.execute("SELECT count(*) FROM files WHERE path >= ? AND path < ?",
                                              (pl["path"] + "/", pl["path"] + "0")).fetchone()[0]}
                   for pl in CFG.get("places", [])],
        "indexer": st, "model_ready": _emb is not None,
        "last_index": float(meta_get(c, "last_index", "0") or 0),
    }


@app.post("/api/index/start")
def index_start():
    return {"started": start_indexer()}


@app.post("/api/index/stop")
def index_stop():
    pidf = DATA / "indexer.pid"
    if pidf.exists():
        try:
            os.kill(int(pidf.read_text()), signal.SIGTERM)
        except (OSError, ValueError):
            pass
    return {"ok": True}


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
