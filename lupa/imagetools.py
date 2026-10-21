"""Image compression and format conversion (PNG, JPG, WebP, AVIF, HEIC, TIFF), plus a safe "replace the original".

Nothing is overwritten in place: a replaced original goes to the Trash first (Finder on local disks, so "Put Back"
works; on network drives it is moved into ~/.Trash, because Finder would delete it from a NAS immediately).
"""
import os
import shutil
import subprocess
import time

from PIL import Image, ImageOps

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:  # noqa: BLE001
    pillow_heif = None

IN_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".heic", ".heif", ".tif", ".tiff", ".bmp", ".gif", ".avif"}
FORMATS = {  # key → (extension, Pillow format, lossy)
    "jpg": (".jpg", "JPEG", True),
    "png": (".png", "PNG", False),
    "webp": (".webp", "WEBP", True),
    "avif": (".avif", "AVIF", True),
    "heic": (".heic", "HEIF", True),
    "tiff": (".tiff", "TIFF", False),
}
EXT_FORMAT = {".jpg": "jpg", ".jpeg": "jpg", ".png": "png", ".webp": "webp", ".avif": "avif",
              ".heic": "heic", ".heif": "heic", ".tif": "tiff", ".tiff": "tiff"}


def _as(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def unique(path: str) -> str:
    base, ext = os.path.splitext(path)
    cand, n = path, 2
    while os.path.exists(cand):
        cand = f"{base} ({n}){ext}"
        n += 1
    return cand


def to_trash(path: str) -> None:
    """Move a file to the Trash. Raises when it could not be moved (the file is then left untouched)."""
    if path.startswith("/Volumes/"):
        dest = unique(os.path.join(os.path.expanduser("~/.Trash"), os.path.basename(path)))
        shutil.move(path, dest)
        return
    r = subprocess.run(["osascript", "-e", f'tell application "Finder" to delete (POSIX file "{_as(path)}" as alias)'],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0 or os.path.exists(path):
        raise RuntimeError("Finder nie przeniósł oryginału do Kosza: " + (r.stderr.strip() or "nieznany błąd"))


def convert(src: str, dest: str, fmt: str, quality: int = 82, max_side: int = 0,
            colors: bool = False, strip: bool = True) -> None:
    """Write `src` as `fmt` to `dest`. quality 1-100 (lossy formats), max_side shrinks the longer edge,
    colors=True reduces a PNG to a 256-colour palette (like TinyPNG), strip drops EXIF/GPS (the colour profile stays)."""
    _, pil_fmt, _ = FORMATS[fmt]
    im = Image.open(src)
    im.seek(0)
    im.load()
    icc = im.info.get("icc_profile")
    im = ImageOps.exif_transpose(im)  # bake the camera rotation in, so dropping EXIF never turns the photo sideways
    exif = None if strip else im.getexif().tobytes()
    if max_side and max(im.size) > max_side:
        im.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    has_alpha = im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info)
    if fmt == "jpg":
        im = im.convert("RGBA") if has_alpha else im.convert("RGB")
        if has_alpha:  # JPG has no transparency: put it on white
            bg = Image.new("RGB", im.size, (255, 255, 255))
            bg.paste(im, mask=im.getchannel("A"))
            im = bg
    elif fmt == "png" and colors:
        im = im.convert("RGBA").quantize(256, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.FLOYDSTEINBERG)
    elif im.mode not in ("RGB", "RGBA", "L", "LA") and not (fmt == "png" and im.mode == "P"):
        im = im.convert("RGBA" if has_alpha else "RGB")
    kw = {}
    if icc:
        kw["icc_profile"] = icc
    if exif:
        kw["exif"] = exif
    q = max(1, min(100, int(quality)))
    if fmt == "jpg":
        kw.update(quality=q, optimize=True, progressive=True, subsampling=0 if q >= 90 else 2)
    elif fmt == "png":
        kw.update(optimize=True)
    elif fmt == "webp":
        kw.update(quality=q, method=6, lossless=q >= 100)
    elif fmt == "avif":
        kw.update(quality=q, speed=6)
    elif fmt == "heic":
        if pillow_heif is None:
            raise RuntimeError("brak obsługi HEIC (pillow-heif)")
        kw.update(quality=q)
    elif fmt == "tiff":
        kw.update(compression="tiff_lzw")
    tmp = os.path.join(os.path.dirname(dest), f".lupa-{os.getpid()}-{time.time_ns()}{FORMATS[fmt][0]}")
    try:
        im.save(tmp, pil_fmt, **kw)
        os.replace(tmp, dest)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def copy_name(src: str, fmt: str) -> str:
    """Where a copy goes: 'photo.webp' for a new format, 'photo (mniejszy).jpg' for the same format."""
    base, ext = os.path.splitext(src)
    new_ext = FORMATS[fmt][0]
    same = EXT_FORMAT.get(ext.lower()) == fmt
    return unique(f"{base} (mniejszy){ext}" if same else base + new_ext)
