"""In-memory vector index + hybrid (semantic + keyword) ranking."""
import re
import threading
import time

import numpy as np

from .common import DIM, V_AUDIO, V_NAME, V_TEXT, V_VISUAL, meta_get

REASONS = {V_NAME: "nazwa / folder", V_TEXT: "treść", V_VISUAL: "obraz", V_AUDIO: "dźwięk"}


class VectorIndex:
    def __init__(self):
        self.lock = threading.RLock()
        self.mat = np.zeros((0, DIM), np.float16)
        self.fid = np.zeros(0, np.int64)
        self.vtype = np.zeros(0, np.int8)
        self.part = np.zeros(0, np.int16)
        self.max_id = 0
        self.gen = None
        self.last_full = 0.0

    def refresh(self, con, force=False):
        gen = meta_get(con, "vec_gen", "0")
        full = force or gen != self.gen
        if full and not force and time.time() - self.last_full < 45 and self.gen is not None:
            full = False  # throttle full reloads while the indexer is churning
        since = 0 if full else self.max_id
        rows = con.execute(
            "SELECT id, file_id, vtype, part, vec FROM vectors WHERE id>? ORDER BY id", (since,)).fetchall()
        if not rows and not full:
            return
        ids = np.fromiter((r[0] for r in rows), np.int64, len(rows))
        fid = np.fromiter((r[1] for r in rows), np.int64, len(rows))
        vt = np.fromiter((r[2] for r in rows), np.int8, len(rows))
        pt = np.fromiter((r[3] for r in rows), np.int16, len(rows))
        mat = np.frombuffer(b"".join(r[4] for r in rows), np.float16).reshape(-1, DIM) if rows \
            else np.zeros((0, DIM), np.float16)
        with self.lock:
            if full:
                self.mat, self.fid, self.vtype, self.part = mat, fid, vt, pt
                self.gen, self.last_full = gen, time.time()
            else:
                self.mat = np.vstack([self.mat, mat])
                self.fid = np.concatenate([self.fid, fid])
                self.vtype = np.concatenate([self.vtype, vt])
                self.part = np.concatenate([self.part, pt])
            if len(ids):
                self.max_id = int(ids[-1])

    def __len__(self):
        return len(self.fid)

    def score(self, q: np.ndarray, allowed: np.ndarray | None = None, exclude: int | None = None,
              name_weight=1.0, vtypes=None):
        """Return (file_ids, best_z, best_row) for every file, z-normalised per vector type."""
        with self.lock:
            mat, fid, vt, part = self.mat, self.fid, self.vtype, self.part
        if not len(fid):
            return np.zeros(0, np.int64), np.zeros(0), np.zeros(0, np.int64)
        q = q.astype(np.float32)
        s = np.empty(len(fid), np.float32)
        step = 200_000
        for i in range(0, len(fid), step):
            s[i:i + step] = mat[i:i + step].astype(np.float32) @ q
        z = np.full(len(fid), -9.0, np.float32)
        rng = np.random.default_rng(0)
        for t in (V_NAME, V_TEXT, V_VISUAL, V_AUDIO):
            m = vt == t
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
        order = np.lexsort((-z, fid))  # group by file, best first
        f_sorted = fid[order]
        first = np.ones(len(order), bool)
        first[1:] = f_sorted[1:] != f_sorted[:-1]
        best_rows = order[first]
        return fid[best_rows], z[best_rows], best_rows

    def file_vector(self, file_id: int):
        with self.lock:
            m = self.fid == file_id
            if not m.any():
                return None
            rows, vt = self.mat[m].astype(np.float32), self.vtype[m]
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
