"""Background removal for logos and product images.

Two engines:
  • "logo"   — for graphics on a flat background (typical client logo on white): removes only the background
               that touches the image border (flood fill with tolerance), so white parts *inside* the logo
               (letters, stripes, outlines) stay. Edge pixels are un-blended from the background colour
               (colour-to-alpha) so there is no white halo.
  • "vision" — Apple Vision subject lift (on-device AI) for photos, products, people.
"auto" picks "logo" when the border is one flat colour, otherwise "vision".
"""
import os
import subprocess

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

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


def remove_logo_bg(im: Image.Image, tol: float = 28.0) -> Image.Image:
    rgba = im.convert("RGBA")
    a0 = np.asarray(rgba).astype(np.float32)
    rgb, alpha_in = a0[..., :3], a0[..., 3]
    bg, _ = border_stats(rgb)
    dist = np.abs(rgb - bg).max(2)
    # 1) background-like pixels connected to the border (flood fill on a mask)
    like = Image.fromarray(((dist < tol) & (alpha_in > 0)).astype(np.uint8) * 255, "L")
    h, w = dist.shape
    filled = like.copy()
    seeds = [(x, 0) for x in range(0, w, 2)] + [(x, h - 1) for x in range(0, w, 2)] + \
            [(0, y) for y in range(0, h, 2)] + [(w - 1, y) for y in range(0, h, 2)]
    for s in seeds:
        if filled.getpixel(s) == 255:
            ImageDraw.floodfill(filled, s, 128)
    region = np.asarray(filled) == 128  # background reachable from the edge
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


def remove_background(path: str, engine: str = "auto", trim: bool = False) -> dict:
    """Create '<name> bez tła.png' next to the original. Never modifies the original."""
    im = Image.open(path)
    im.load()
    rgb = np.asarray(im.convert("RGB")).astype(np.float32)
    bg, flat = border_stats(rgb)
    if engine == "auto":
        engine = "logo" if flat >= 0.85 else "vision"
    dest = output_path(path)
    if engine == "logo":
        out = remove_logo_bg(im)
        if trim and out.getbbox():
            out = out.crop(out.getchannel("A").getbbox())
        out.save(dest, "PNG", optimize=True)
    else:
        if not remove_vision(path, dest):
            out = remove_logo_bg(im)  # fallback
            out.save(dest, "PNG", optimize=True)
            engine = "logo"
    return {"ok": True, "path": dest, "engine": engine, "bg": "#%02x%02x%02x" % tuple(int(x) for x in bg), "flat": round(flat, 2)}
