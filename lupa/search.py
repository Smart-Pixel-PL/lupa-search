"""In-memory vector index + hybrid (semantic + keyword) ranking."""
import os
import re
import threading
import time
from pathlib import Path

import numpy as np

from .common import DATA, DIM, V_AUDIO, V_NAME, V_TEXT, V_VISUAL, meta_get

REASONS = {V_NAME: "nazwa / folder", V_TEXT: "treść", V_VISUAL: "obraz", V_AUDIO: "dźwięk"}


class VectorIndex:
    """All vectors for search. Most of them live in a snapshot file (data/vectors-<dim>.npy) that is memory-mapped:
    its pages are clean, so macOS can drop them under memory pressure and read them back, instead of compressing
    or swapping ~0.5 GB of private memory. Rows added since the snapshot are kept in RAM; rows deleted in SQLite are
    dropped by id (cheap), so the 0.5 GB of blobs never has to be parsed from SQLite again after the first run."""

    def __init__(self, path=None):
        self.lock = threading.RLock()       # guards the arrays below (searches read them)
        self.busy = threading.Lock()        # one refresh at a time (server loop + live indexer)
        self.path = Path(path) if path else DATA / f"vectors-{DIM}.npy"
        self.base = np.zeros((0, DIM), np.float16)   # snapshot rows (memory-mapped)
        self.extra = np.zeros((0, DIM), np.float16)  # rows added since the snapshot
        self.src = np.zeros(0, np.int64)    # alive row -> row in base + extra
        self.vid = np.zeros(0, np.int64)    # SQLite vector id of each alive row (ascending)
        self.fid = np.zeros(0, np.int64)
        self.vtype = np.zeros(0, np.int8)
        self.part = np.zeros(0, np.int16)
        self.max_id = 0
        self.gen = None
        self.loaded = False
        self.stale = False   # rows were added: some old ones may have been replaced (deleted) meanwhile
        self.checked = 0.0
        self.cache = None

    # ---------------------------------------------------------------- loading
    def refresh(self, con, force=False):
        with self.busy:
            if not self.loaded:
                self._load(con)
            gen = meta_get(con, "vec_gen", "0")
            if gen != self.gen or force or (self.stale and time.time() - self.checked > 60):
                self._drop_deleted(con)
                self.gen = gen
            self._append(con)
            if len(self.extra) > max(50_000, len(self.base) // 10) or len(self.src) < 0.9 * (len(self.base) + len(self.extra)):
                self._save()

    def _load(self, con):
        """Open the snapshot if it still matches the database, else build it once from SQLite (streamed)."""
        self.loaded = True
        try:
            meta = np.load(self.path.with_suffix(".npz"))
            base = np.load(self.path, mmap_mode="c")  # copy-on-write map: clean pages, writable for torch
            vid, fid, vt, pt = meta["vid"], meta["fid"], meta["vtype"], meta["part"]
            if base.shape != (len(vid), DIM) or not self._matches(con, base, vid):
                raise ValueError("snapshot does not match the database")
            with self.lock:
                self.base, self.vid, self.fid, self.vtype, self.part = base, vid, fid, vt, pt
                self.src = np.arange(len(vid), dtype=np.int64)
                self.max_id = int(meta["max_id"])
                self._prepare()
            return
        except FileNotFoundError:
            pass
        except Exception as e:  # noqa: BLE001
            print("vectors: rebuilding snapshot -", e, flush=True)
        self._build(con)

    @staticmethod
    def _matches(con, base, vid):
        if not len(vid):
            return True
        for i in {0, len(vid) // 2, len(vid) - 1}:
            r = con.execute("SELECT vec FROM vectors WHERE id=?", (int(vid[i]),)).fetchone()
            if r is not None and r[0] != base[i].tobytes():
                return False  # same id, different vector: the database was rebuilt
        return True

    def _build(self, con):
        top, n = con.execute("SELECT max(id), count(*) FROM vectors").fetchone()
        top, n = top or 0, n or 0
        vid, fid = np.zeros(n, np.int64), np.zeros(n, np.int64)
        vt, pt = np.zeros(n, np.int8), np.zeros(n, np.int16)
        mat = np.zeros((n, DIM), np.float16)
        i = 0
        cur = con.execute("SELECT id, file_id, vtype, part, vec FROM vectors WHERE id<=? ORDER BY id", (top,))
        while i < n:  # streamed: no 0.5 GB of Python row objects at once
            rows = cur.fetchmany(20_000)[:n - i]
            if not rows:
                break
            j = i + len(rows)
            vid[i:j] = [r[0] for r in rows]
            fid[i:j] = [r[1] for r in rows]
            vt[i:j] = [r[2] for r in rows]
            pt[i:j] = [r[3] for r in rows]
            mat[i:j] = np.frombuffer(b"".join(r[4] for r in rows), np.float16).reshape(-1, DIM)
            i = j
        with self.lock:
            self.base, self.extra = mat[:i], np.zeros((0, DIM), np.float16)
            self.vid, self.fid, self.vtype, self.part = vid[:i], fid[:i], vt[:i], pt[:i]
            self.src = np.arange(i, dtype=np.int64)
            self.max_id = top
            self._prepare()
        self._save()  # from now on the rows come from the memory-mapped file

    def _drop_deleted(self, con):
        # the small file_id index holds every row id: scanning it is ~50 ms, the table itself is 0.5 GB of blobs
        self.checked, self.stale = time.time(), False
        n = con.execute("SELECT count(*) FROM vectors INDEXED BY ix_vec_file WHERE id<=?", (self.max_id,)).fetchone()[0]
        if n == len(self.vid):
            return  # ids only ever grow, so the same count means nothing was deleted
        ids = np.fromiter((r[0] for r in con.execute("SELECT id FROM vectors INDEXED BY ix_vec_file WHERE id<=?",
                                                     (self.max_id,))), np.int64)
        keep = np.isin(self.vid, ids, assume_unique=True)
        with self.lock:
            self.src, self.vid, self.fid = self.src[keep], self.vid[keep], self.fid[keep]
            self.vtype, self.part = self.vtype[keep], self.part[keep]
            self._prepare()

    def _append(self, con):
        rows = con.execute("SELECT id, file_id, vtype, part, vec FROM vectors WHERE id>? ORDER BY id",
                           (self.max_id,)).fetchall()
        if not rows:
            return
        n = len(rows)
        mat = np.frombuffer(b"".join(r[4] for r in rows), np.float16).reshape(-1, DIM)
        with self.lock:
            start = len(self.base) + len(self.extra)
            self.extra = np.concatenate([self.extra, mat])
            self.src = np.concatenate([self.src, np.arange(start, start + n, dtype=np.int64)])
            self.vid = np.concatenate([self.vid, np.fromiter((r[0] for r in rows), np.int64, n)])
            self.fid = np.concatenate([self.fid, np.fromiter((r[1] for r in rows), np.int64, n)])
            self.vtype = np.concatenate([self.vtype, np.fromiter((r[2] for r in rows), np.int8, n)])
            self.part = np.concatenate([self.part, np.fromiter((r[3] for r in rows), np.int16, n)])
            self.max_id = int(self.vid[-1])
            self._prepare()
        self.stale = True

    def _save(self):
        """Write the alive rows as a new snapshot (atomic rename), then map it instead of the rows held in RAM."""
        src, base, extra = self.src, self.base, self.extra
        if not len(src):
            return
        tmp = self.path.with_name(self.path.stem + ".tmp.npy")
        try:
            out = np.lib.format.open_memmap(tmp, mode="w+", dtype=np.float16, shape=(len(src), DIM))
            for i in range(0, len(src), 50_000):
                out[i:i + 50_000] = self._take(src[i:i + 50_000], base, extra)
            out.flush()
            del out
            np.savez(self.path.with_suffix(".tmp.npz"), vid=self.vid, fid=self.fid, vtype=self.vtype,
                     part=self.part, max_id=self.max_id)
            os.replace(tmp, self.path)
            os.replace(self.path.with_suffix(".tmp.npz"), self.path.with_suffix(".npz"))
            mat = np.load(self.path, mmap_mode="c")
        except OSError as e:  # e.g. disk full: keep searching from RAM
            print("vectors: snapshot not saved -", e, flush=True)
            return
        with self.lock:  # only refresh() changes the arrays, and it holds self.busy: nothing changed meanwhile
            self.base, self.extra = mat, np.zeros((0, DIM), np.float16)
            self.src = np.arange(len(mat), dtype=np.int64)
            self._prepare()

    @staticmethod
    def _take(idx, base, extra):
        """Vectors at storage rows `idx` (base rows first, then extra)."""
        out = np.empty((len(idx), DIM), np.float16)
        b = idx < len(base)
        out[b] = base[idx[b]]
        out[~b] = extra[idx[~b] - len(base)]
        return out

    def __len__(self):
        return len(self.fid)

    def _prepare(self):
        """Per-refresh caches so a query is one fp16 matmul plus O(n) vector ops (no per-query sort/convert)."""
        import torch
        order = np.argsort(self.fid, kind="stable")
        fs = self.fid[order]
        starts = np.flatnonzero(np.r_[True, fs[1:] != fs[:-1]]) if len(fs) else np.zeros(0, np.int64)
        self.cache = {
            "tmats": [torch.from_numpy(m if m.flags.writeable else m.copy()) for m in (self.base, self.extra) if len(m)],
            "src": None if (len(self.src) == len(self.base) + len(self.extra)) else self.src,
            "fid": self.fid, "vtype": self.vtype,
            "order": order, "starts": starts, "ufid": fs[starts] if len(fs) else fs,
            "masks": {t: self.vtype == t for t in (V_NAME, V_TEXT, V_VISUAL, V_AUDIO)},
        }

    # ---------------------------------------------------------------- queries
    def score(self, q: np.ndarray, allowed: np.ndarray | None = None, exclude: int | None = None,
              name_weight=1.0, vtypes=None):
        """Return (file_ids, best_z, best_row) for every file, z-normalised per vector type."""
        import torch
        with self.lock:
            cache = self.cache
        if cache is None or not len(cache["fid"]):
            return np.zeros(0, np.int64), np.zeros(0), np.zeros(0, np.int64)
        fid, vt = cache["fid"], cache["vtype"]
        tq = torch.from_numpy(q.astype(np.float16))
        s = np.concatenate([(m @ tq).float().numpy() for m in cache["tmats"]])
        if cache["src"] is not None:  # some stored rows were deleted since the snapshot
            s = s[cache["src"]]
        z = np.full(len(fid), -9.0, np.float32)
        rng = np.random.default_rng(0)
        for t, m in cache["masks"].items():
            n = int(m.sum())
            if not n:
                continue
            sv = s[m]
            sample = sv if n <= 60_000 else rng.choice(sv, 60_000, replace=False)
            mu, sd = float(sample.mean()), float(sample.std()) or 1.0
            if n < 40:  # too few to calibrate — use a global-ish prior
                mu, sd = 0.45, 0.08
            zz = (sv - mu) / sd
            if t == V_NAME:
                zz *= name_weight
            z[m] = zz
        if vtypes is not None:
            z[~np.isin(vt, list(vtypes))] = -9.0
        if allowed is not None:
            z[~np.isin(fid, allowed)] = -9.0
        if exclude is not None:
            z[fid == exclude] = -9.0
        order, starts = cache["order"], cache["starts"]
        zs = z[order]
        best = np.maximum.reduceat(zs, starts)
        counts = np.diff(np.r_[starts, len(zs)])
        pos = np.where(zs == np.repeat(best, counts), np.arange(len(zs)), len(zs))
        best_rows = order[np.minimum.reduceat(pos, starts)]
        return cache["ufid"], best, best_rows

    def visual_vectors(self, file_ids):
        """First visual vector of each given file (for near-duplicate grouping) → {file_id: np.ndarray}."""
        with self.lock:
            fid, vt, src, base, extra = self.fid, self.vtype, self.src, self.base, self.extra
        want = np.flatnonzero(np.isin(fid, np.asarray(list(file_ids), np.int64)) & (vt == V_VISUAL))
        vecs = self._take(src[want], base, extra)
        out = {}
        for r, v in zip(want, vecs):
            f = int(fid[r])
            if f not in out:
                out[f] = v.astype(np.float32)
        return out

    def file_vector(self, file_id: int):
        with self.lock:
            m = self.fid == file_id
            if not m.any():
                return None
            rows, vt = self._take(self.src[m], self.base, self.extra).astype(np.float32), self.vtype[m]
        has_content = (vt != V_NAME).any()
        sel = vt != V_NAME if has_content else vt == V_NAME
        v = rows[sel].mean(0)
        return v / (np.linalg.norm(v) or 1.0), set(int(t) for t in np.unique(vt[sel]))


def fts_query(q: str):
    toks = [t for t in re.findall(r"[\w\-\.]+", q, re.UNICODE) if len(t) > 1][:8]
    if not toks:
        return None
    return " ".join('"' + t.replace('"', "") + '"*' for t in toks)


def keyword_hits(con, q: str):
    """Exact-ish keyword matches: name hits get a strong boost, body hits a mild one."""
    fq = fts_query(q)
    out = {}
    if not fq:
        return out
    try:
        for (rid,) in con.execute("SELECT rowid FROM fts WHERE fts MATCH ? LIMIT 3000",
                                  (f"{{name folder}} : ({fq})",)):
            out[rid] = 1.6
        for (rid,) in con.execute("SELECT rowid FROM fts WHERE fts MATCH ? LIMIT 3000", (f"body : ({fq})",)):
            out[rid] = max(out.get(rid, 0), 0.8)
    except Exception:
        pass
    return out
