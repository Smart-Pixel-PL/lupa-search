"""Live indexing of local disks: macOS FSEvents → a new/changed/moved/deleted file is searchable in seconds.

The server runs this in a thread. New files get their name+folder vector and full-text entry right away
(text model on CPU) and a thumbnail; the content analysis (images, documents…) is picked up by the
regular indexer. Network drives don't deliver FSEvents reliably, so they keep the scheduled scans.
"""
import os
import threading
import time

from .common import CFG, KIND_LABELS, PROJECT, V_NAME, connect, kind_of, nfc, root_of, thumb_path

DEBOUNCE = 2.0


def should_skip(path: str) -> bool:
    """Same rules as the crawler: hidden files, excluded folders, ~/Library (except iCloud), packages, temp files."""
    if path.startswith(str(PROJECT) + "/") or path == str(PROJECT):
        return True
    for inc in CFG.get("include_paths", []):
        if path.startswith(inc.rstrip("/") + "/"):
            rel = path[len(inc):]
            break
    else:
        rel = None
    parts = path.split("/")
    for i, part in enumerate(parts[1:-1], 1):
        if part.startswith("."):
            return True
        if part in CFG["exclude_dirs"]:
            return True
        if part.lower().endswith(CFG["package_exts"]):
            return True  # inside an app bundle / photos library…
    if rel is None and len(parts) > 3 and parts[1] == "Users" and parts[3] == "Library":
        return True
    if any(path == p or path.startswith(p.rstrip("/") + "/") for p in CFG.get("exclude_paths", [])):
        return True
    name = parts[-1]
    if name.startswith(".") or name.startswith("._"):
        return True
    ext = os.path.splitext(name)[1].lower()
    return ext in CFG["skip_exts"] or name.lower() in CFG["skip_exts"]


class LiveIndexer:
    def __init__(self, get_emb, vindex):
        self.get_emb = get_emb
        self.vindex = vindex
        self.pending: dict[str, float] = {}
        self.lock = threading.Lock()
        self.observer = None
        self.stats = {"indexed": 0, "removed": 0, "last": 0.0}

    # -------- watching
    def start(self):
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer

        live = self

        class H(FileSystemEventHandler):
            def on_any_event(self, ev):
                for p in (getattr(ev, "src_path", None), getattr(ev, "dest_path", None)):
                    if p and not ev.is_directory:
                        live.touch(os.fsdecode(p))

        self.observer = Observer()
        for r in CFG["roots"]:
            if not r.startswith("/Volumes/") and os.path.isdir(r):
                self.observer.schedule(H(), r, recursive=True)
        for inc in CFG.get("include_paths", []):
            if os.path.isdir(inc):
                self.observer.schedule(H(), inc, recursive=True)
        self.observer.daemon = True
        self.observer.start()
        # finish local files that only have a bare row (e.g. created while nothing was watching)
        c = connect(readonly=True)
        now = time.time() - DEBOUNCE
        with self.lock:
            for (p,) in c.execute("SELECT path FROM files WHERE stage=0 AND root NOT LIKE '/Volumes/%'"):
                self.pending[p] = now
        threading.Thread(target=self._loop, daemon=True).start()

    def touch(self, path):
        if should_skip(path):
            return
        with self.lock:
            self.pending[path] = time.time()

    # -------- processing
    def _loop(self):
        while True:
            time.sleep(1.0)
            now = time.time()
            with self.lock:
                ready = [p for p, t in self.pending.items() if now - t >= DEBOUNCE]
                for p in ready:
                    del self.pending[p]
            if ready:
                try:
                    self.process(ready)
                except Exception as e:  # noqa: BLE001
                    print("live:", e, flush=True)

    def process(self, paths):
        from .extract import quick_thumb, save_thumb
        from .indexer import folder_label, purge, wants_content
        c = connect()
        gone, fresh = [], []
        for p in paths:
            r = c.execute("SELECT id, size, mtime, stage FROM files WHERE path=?", (p,)).fetchone()
            if not os.path.isfile(p):
                if r:
                    gone.append(r["id"])
                continue
            st = os.stat(p)
            if r and r["stage"] != 0 and r["size"] == st.st_size and abs((r["mtime"] or 0) - st.st_mtime) < 1:
                continue  # touched but unchanged (and already indexed)
            root = root_of(p)
            if not root:
                continue
            name = nfc(os.path.basename(p))
            c.execute("""INSERT INTO files(path,root,name,ext,kind,size,mtime,ctime,scan,stage,missing)
                         VALUES(?,?,?,?,?,?,?,?,?,0,0)
                         ON CONFLICT(path) DO UPDATE SET size=excluded.size, mtime=excluded.mtime, stage=0, missing=0,
                         error=NULL, thumb=0, colors=NULL, ocr=NULL""",
                      (p, root, name, os.path.splitext(name)[1].lower(), kind_of(p), st.st_size, st.st_mtime,
                       getattr(st, "st_birthtime", st.st_ctime), int(time.time())))
            fresh.append(c.execute("SELECT id, path, root, name, kind, 0 AS dataless FROM files WHERE path=?", (p,)).fetchone())
        if gone:
            purge(c, gone)
            self.stats["removed"] += len(gone)
        if fresh:
            ids = [r["id"] for r in fresh]
            q = ",".join("?" * len(ids))
            c.execute(f"DELETE FROM vectors WHERE file_id IN ({q})", ids)
            c.execute(f"DELETE FROM fts WHERE rowid IN ({q})", ids)
            folders = [folder_label(r["path"], r["root"]) for r in fresh]
            texts = [f"{KIND_LABELS.get(r['kind'], '')} w folderze: {f}" for r, f in zip(fresh, folders)]
            vecs = self.get_emb().documents([os.path.splitext(r["name"])[0] for r in fresh], texts)
            c.executemany("INSERT INTO vectors(file_id,vtype,part,vec) VALUES(?,?,?,?)",
                          [(r["id"], V_NAME, 0, v.tobytes()) for r, v in zip(fresh, vecs)])
            c.executemany("INSERT INTO fts(rowid,name,folder,body) VALUES(?,?,?,'')",
                          [(r["id"], r["name"], f) for r, f in zip(fresh, folders)])
            c.executemany("UPDATE files SET stage=? WHERE id=?", [(1 if wants_content(r) else 3, r["id"]) for r in fresh])
            c.commit()
            for r in fresh:  # instant visual preview; full analysis follows in the indexer
                if r["kind"] in ("image", "video", "pdf"):
                    try:
                        save_thumb(quick_thumb(r["path"], r["kind"]), thumb_path(r["id"]))
                        c.execute("UPDATE files SET thumb=1 WHERE id=?", (r["id"],))
                    except Exception:
                        pass
            c.commit()
            self.stats["indexed"] += len(fresh)
        self.stats["last"] = time.time()
        if fresh or gone:
            self.vindex.refresh(c)
            print(f"live: +{len(fresh)} −{len(gone)}", flush=True)
