"""Dominant colours of every thumbnail → search by colour.

Run:  uv run python -m lupa.colors      (CPU only; the server starts it in the background when needed)
Each file gets up to 6 palette entries [L, a, b, fraction] (CIE Lab, perceptual), computed from its thumbnail.
"""
import json
import os
import sys
import time

import numpy as np
from PIL import Image

from .common import DATA, connect, thumb_path

PID = DATA / "colors.pid"


def srgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """rgb: (..., 3) in 0-255 → CIE Lab (D65)."""
    c = rgb.astype(np.float32) / 255.0
    c = np.where(c > 0.04045, ((c + 0.055) / 1.055) ** 2.4, c / 12.92)
    m = np.array([[0.4124564, 0.3575761, 0.1804375],
                  [0.2126729, 0.7151522, 0.0721750],
                  [0.0193339, 0.1191920, 0.9503041]], np.float32)
    xyz = c @ m.T / np.array([0.95047, 1.0, 1.08883], np.float32)
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    L = 116 * f[..., 1] - 16
    a = 500 * (f[..., 0] - f[..., 1])
    b = 200 * (f[..., 1] - f[..., 2])
    return np.stack([L, a, b], -1)


def hex_to_lab(h: str) -> np.ndarray:
    h = h.lstrip("#")
    rgb = np.array([int(h[i:i + 2], 16) for i in (0, 2, 4)], np.float32)
    return srgb_to_lab(rgb)


def palette(path) -> list:
    im = Image.open(path)
    im.thumbnail((72, 72))
    im = im.convert("RGBA")
    px = np.asarray(im).reshape(-1, 4)
    px = px[px[:, 3] >= 128][:, :3]  # ignore transparent background
    if len(px) < 20:
        return []
    q = Image.fromarray(px.reshape(-1, 1, 3).astype(np.uint8), "RGB").quantize(colors=6, method=Image.Quantize.MEDIANCUT)
    pal = np.array(q.getpalette()[:18], np.float32).reshape(-1, 3)
    counts = np.bincount(np.asarray(q).ravel(), minlength=len(pal))[:len(pal)]
    total = counts.sum()
    out = []
    for i in np.argsort(-counts):
        frac = counts[i] / total
        if frac < 0.04:
            continue
        L, A, B = srgb_to_lab(pal[i])
        out.append([round(float(L), 1), round(float(A), 1), round(float(B), 1), round(float(frac), 3)])
    return out


def run():
    PID.write_text(str(os.getpid()))
    con = connect()
    done = 0
    t0 = time.time()
    try:
        # pinned folders first (what you browse most), then newest files
        scopes = [(r[0] + "/", r[0] + "0") for r in con.execute("SELECT path FROM pins ORDER BY pos")] + [("", "\uffff")]
        while True:
            rows = []
            for lo, hi in scopes:
                rows = con.execute("""SELECT id FROM files WHERE colors IS NULL AND thumb=1 AND path >= ? AND path < ?
                                      AND kind IN ('image','design','raw','video','pdf')
                                      ORDER BY mtime DESC LIMIT 500""", (lo, hi)).fetchall()
                if rows:
                    break
            if not rows:
                break
            upd = []
            for (fid,) in rows:
                tp = thumb_path(fid)
                try:
                    pal = palette(tp) if tp.exists() else []
                except Exception:
                    pal = []
                upd.append((json.dumps(pal, separators=(",", ":")), fid))
            con.executemany("UPDATE files SET colors=? WHERE id=?", upd)
            con.commit()
            done += len(rows)
            if done % 5000 < 500:
                print(f"kolory: {done} ({done / max(time.time() - t0, 1):.0f}/s)", flush=True)
    finally:
        PID.unlink(missing_ok=True)
    print(f"kolory: gotowe, {done} plików", flush=True)


class ColorIndex:
    """All palettes in memory: (N, 6, 3) Lab + (N, 6) fractions, for fast colour scoring."""

    def __init__(self):
        self.ids = np.zeros(0, np.int64)
        self.lab = np.zeros((0, 6, 3), np.float32)
        self.frac = np.zeros((0, 6), np.float32)
        self.n_loaded = -1

    def refresh(self, con):
        n = con.execute("SELECT count(*) FROM files WHERE colors IS NOT NULL AND colors != '[]'").fetchone()[0]
        if n == self.n_loaded:
            return
        rows = con.execute("SELECT id, colors FROM files WHERE colors IS NOT NULL AND colors != '[]'").fetchall()
        ids = np.zeros(len(rows), np.int64)
        lab = np.full((len(rows), 6, 3), 999, np.float32)
        frac = np.zeros((len(rows), 6), np.float32)
        for i, (fid, js) in enumerate(rows):
            ids[i] = fid
            for k, (L, A, B, f) in enumerate(json.loads(js)[:6]):
                lab[i, k] = (L, A, B)
                frac[i, k] = f
        self.ids, self.lab, self.frac, self.n_loaded = ids, lab, frac, n

    def score(self, hex_color: str):
        """Share of each image that is close to the colour (0-1)."""
        if not len(self.ids):
            return self.ids, np.zeros(0, np.float32)
        t = hex_to_lab(hex_color)
        d = np.linalg.norm(self.lab - t, axis=2)  # CIE76 ΔE
        w = np.exp(-((d / 18.0) ** 2))
        return self.ids, (self.frac * w).sum(1)


if __name__ == "__main__":
    sys.exit(run())
