"""Background removal for logos and product images.

Two engines:
  • "logo"   — for graphics on a flat background (typical client logo on white): removes the background that
               touches the image border (flood fill with tolerance) and the inside of letters (O, A, B…), while
               white artwork inside the logo (white text on a dark badge, stripes, outlines) stays. Edge pixels
               are un-blended from the background colour (colour-to-alpha) so there is no white halo.
  • "vision" — Apple Vision subject lift (on-device AI) for photos, products, people.
"auto" picks "logo" when the border is one flat colour, otherwise "vision".
"""
import os
import subprocess
import time

import numpy as np
from PIL import Image, ImageFilter
from scipy import ndimage

from .common import DATA, PROJECT

RMBG = DATA / "bin" / "lupa-rmbg"


def _ensure_rmbg():
    if RMBG.exists():
        return True
    RMBG.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["swiftc", "-O", str(PROJECT / "tools" / "rmbg.swift"), "-o", str(RMBG), "-framework", "Vision",
                        "-framework", "CoreImage", "-framework", "AppKit"], capture_output=True)
    return r.returncode == 0


def border_stats(rgb: np.ndarray):
    b = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]]).astype(np.float32)
    bg = np.median(b, axis=0)
    flat = float((np.abs(b - bg).max(1) < 18).mean())  # share of border pixels close to the median colour
    return bg, flat


def _counter_holes(like: np.ndarray, enclosed: np.ndarray) -> np.ndarray:
    """Enclosed background areas that are letter counters (the inside of O, A, B, D, P, R…), not white artwork.
    A counter is wide compared to the thin stroke around it; white text or shapes on a dark badge are narrow
    compared to the solid ink area that holds them, so those stay."""
    holes, n = ndimage.label(enclosed)
    if not n or n > 2000:  # nothing to do, or a noisy scan: leave it alone
        return np.zeros_like(enclosed)
    ink_mask = ~like
    ink, n_ink = ndimage.label(ink_mask, structure=np.ones((3, 3)))
    hole_r = ndimage.maximum(ndimage.distance_transform_edt(enclosed), holes, np.arange(1, n + 1))
    ink_r = np.zeros(n_ink + 1)
    ink_r[1:] = ndimage.maximum(ndimage.distance_transform_edt(ink_mask), ink, np.arange(1, n_ink + 1))
    is_counter = np.zeros(n + 1, bool)
    for k, sl in enumerate(ndimage.find_objects(holes), 1):
        y0, x0 = max(sl[0].start - 1, 0), max(sl[1].start - 1, 0)
        win = (slice(y0, sl[0].stop + 1), slice(x0, sl[1].stop + 1))
        h = holes[win] == k
        ids = ink[win][ndimage.binary_dilation(h) & ~h]
        ids = ids[ids > 0]
        if ids.size:  # the ink shape around this hole: is its stroke thin next to the hole?
            is_counter[k] = hole_r[k - 1] >= 0.5 * ink_r[np.bincount(ids).argmax()]
    return is_counter[holes]


def remove_logo_bg(im: Image.Image, tol: float = 28.0, holes: str = "auto") -> Image.Image:
    """holes: "auto" clears letter counters, "all" clears every enclosed background area, "keep" clears only
    the background that touches the image border."""
    rgba = im.convert("RGBA")
    a0 = np.asarray(rgba).astype(np.float32)
    rgb, alpha_in = a0[..., :3], a0[..., 3]
    bg, _ = border_stats(rgb)
    dist = np.abs(rgb - bg).max(2)
    h, w = dist.shape
    # 1) background-like pixels connected to the border, plus (optionally) enclosed ones such as the inside of an O
    like = (dist < tol) & (alpha_in > 0)
    lab, _ = ndimage.label(like)
    edge_ids = np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))
    region = np.isin(lab, edge_ids[edge_ids > 0])
    if holes != "keep":
        enclosed = like & ~region
        region |= enclosed if holes == "all" else _counter_holes(like, enclosed)
    # 2) soft edge: pixels just inside the cut get alpha from how much they look like the background
    near = np.asarray(Image.fromarray(region.astype(np.uint8) * 255).filter(ImageFilter.MaxFilter(5))) > 0
    edge = near & ~region
    alpha = np.ones((h, w), np.float32)
    alpha[region] = 0.0
    if edge.any():
        # colour-to-alpha against the background colour (GIMP-style), per pixel
        p = rgb[edge]
        ratios = np.where(p > bg, (p - bg) / np.maximum(255 - bg, 1e-3), (bg - p) / np.maximum(bg, 1e-3))
        a = np.clip(ratios.max(1), 0, 1)
        a = np.clip((a - 0.04) / 0.96, 0, 1)  # ignore tiny noise
        alpha[edge] = a
        safe = np.maximum(a, 1e-3)[:, None]
        rgb_e = (p - (1 - a)[:, None] * bg) / safe  # un-blend the background → no white halo
        rgb = rgb.copy()
        rgb[edge] = np.clip(rgb_e, 0, 255)
    out = np.dstack([rgb, alpha * alpha_in]).clip(0, 255).astype(np.uint8)
    return Image.fromarray(out, "RGBA")


def remove_vision(path: str, dest: str) -> bool:
    if not _ensure_rmbg():
        return False
    r = subprocess.run([str(RMBG), path, dest], capture_output=True, timeout=120)
    return r.returncode == 0 and os.path.exists(dest)


def output_path(path: str) -> str:
    base, _ = os.path.splitext(path)
    cand, n = f"{base} bez tła.png", 2
    while os.path.exists(cand):
        cand = f"{base} bez tła ({n}).png"
        n += 1
    return cand


def _save_atomic(write, dest: str) -> None:
    """Write to a hidden temp file, then rename: folder watchers (e.g. auto-compressors on Downloads) never see
    a half-written PNG."""
    tmp = os.path.join(os.path.dirname(dest), f".lupa-{os.getpid()}-{time.time_ns()}.png")
    try:
        write(tmp)
        os.replace(tmp, dest)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def remove_background(path: str, engine: str = "auto", trim: bool = False, holes: str = "auto") -> dict:
    """Create '<name> bez tła.png' next to the original. Never modifies the original."""
    im = Image.open(path)
    im.load()
    rgb = np.asarray(im.convert("RGB")).astype(np.float32)
    bg, flat = border_stats(rgb)
    if engine == "auto":
        engine = "logo" if flat >= 0.85 else "vision"
    dest = output_path(path)

    def logo(tmp):
        out = remove_logo_bg(im, holes=holes)
        if trim and out.getbbox():
            out = out.crop(out.getchannel("A").getbbox())
        out.save(tmp, "PNG", optimize=True)

    def vision(tmp):
        if not remove_vision(path, tmp):
            raise RuntimeError("Apple Vision nie znalazł obiektu")

    if engine == "vision":
        try:
            _save_atomic(vision, dest)
        except RuntimeError:
            engine = "logo"  # fallback
    if engine == "logo":
        _save_atomic(logo, dest)
    return {"ok": True, "path": dest, "engine": engine, "holes": holes,
            "bg": "#%02x%02x%02x" % tuple(int(x) for x in bg), "flat": round(flat, 2)}
