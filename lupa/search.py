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
                self.cache_n = -1
                self.gen, self.last_full = gen, time.time()
            else:
                self.mat = np.vstack([self.mat, mat])
                self.fid = np.concatenate([self.fid, fid])
                self.vtype = np.concatenate([self.vtype, vt])
                self.part = np.concatenate([self.part, pt])
            if len(ids):
                self.max_id = int(ids[-1])
            self._prepare()  # done here (background thread), not on the first query
            self.cache_n = len(self.fid)

    def __len__(self):
        return len(self.fid)

    def _prepare(self):
        """Per-refresh caches so a query is one fp16 matmul plus O(n) vector ops (no per-query sort/convert)."""
        import torch
        order = np.argsort(self.fid, kind="stable")
        fs = self.fid[order]
        starts = np.flatnonzero(np.r_[True, fs[1:] != fs[:-1]]) if len(fs) else np.zeros(0, np.int64)
        self.cache = {
            "tmat": torch.from_numpy(self.mat if self.mat.flags.writeable else self.mat.copy()),
            "order": order, "starts": starts, "ufid": fs[starts] if len(fs) else fs,
            "masks": {t: self.vtype == t for t in (V_NAME, V_TEXT, V_VISUAL, V_AUDIO)},
        }

    def score(self, q: np.ndarray, allowed: np.ndarray | None = None, exclude: int | None = None,
              name_weight=1.0, vtypes=None):
        """Return (file_ids, best_z, best_row) for every file, z-normalised per vector type."""
        import torch
        with self.lock:
            if getattr(self, "cache_n", -1) != len(self.fid):
                self._prepare()
                self.cache_n = len(self.fid)
            fid, vt, cache = self.fid, self.vtype, self.cache
        if not len(fid):
            return np.zeros(0, np.int64), np.zeros(0), np.zeros(0, np.int64)
        s = (cache["tmat"] @ torch.from_numpy(q.astype(np.float16))).float().numpy()
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
