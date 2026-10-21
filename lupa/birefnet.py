"""BiRefNet (ZhengPeng7/BiRefNet, MIT licence): AI background removal with fine edges (hair, fur, products).

The model (~450 MB) is downloaded to ~/.cache/huggingface on first use, loaded lazily on MPS (CPU fallback) and
unloaded again after IDLE_S seconds without use, so it costs no memory while nobody removes backgrounds.
"""
import gc
import os
import threading
import time

import numpy as np
from PIL import Image

REPO = "ZhengPeng7/BiRefNet"
REVISION = "e2bf8e4460fc8fa32bba5ea4d94b3233d367b0e4"  # pinned: the repo ships its own model code (trust_remote_code)
WEIGHTS = "model.safetensors"
WEIGHTS_BYTES = 444_473_596
SIZE = 1024
IDLE_S = 300
MEAN = np.array([0.485, 0.456, 0.406], np.float32)
STD = np.array([0.229, 0.224, 0.225], np.float32)

_lock = threading.Lock()
_model = None
_device = "cpu"
_last_use = 0.0
_timer = None
state = {"downloading": False}


def is_downloaded() -> bool:
    from huggingface_hub import try_to_load_from_cache
    p = try_to_load_from_cache(REPO, WEIGHTS, revision=REVISION)
    return isinstance(p, str) and os.path.exists(p)


def download_progress() -> float:
    """0..1 while the weights are being downloaded (size of the partial file in the HF cache)."""
    from huggingface_hub.constants import HF_HUB_CACHE
    blobs = os.path.join(HF_HUB_CACHE, "models--" + REPO.replace("/", "--"), "blobs")
    try:
        part = max((os.path.getsize(os.path.join(blobs, f)) for f in os.listdir(blobs) if f.endswith(".incomplete")),
                   default=0)
    except OSError:
        part = 0
    return min(1.0, part / WEIGHTS_BYTES)


def status() -> dict:
    return {"downloaded": is_downloaded(), "downloading": state["downloading"], "progress": round(download_progress(), 3),
            "loaded": _model is not None, "mb": round(WEIGHTS_BYTES / 1e6)}


def _load():
    global _model, _device
    if _model is not None:
        return _model
    import torch
    from huggingface_hub import snapshot_download
    from transformers import AutoModelForImageSegmentation
    if not is_downloaded():
        state["downloading"] = True
        try:
            snapshot_download(REPO, revision=REVISION, allow_patterns=["*.py", "*.json", WEIGHTS])
        except Exception as e:  # noqa: BLE001
            raise RuntimeError("nie udało się pobrać modelu AI BiRefNet - sprawdź połączenie z internetem") from e
        finally:
            state["downloading"] = False
    m = AutoModelForImageSegmentation.from_pretrained(REPO, revision=REVISION, trust_remote_code=True)
    _device = "mps" if torch.backends.mps.is_available() else "cpu"
    m = m.to(_device).float() if _device == "cpu" else m.to(_device)  # weights are fp16; CPU runs fp32
    m.eval()
    _model = m
    return m


def _schedule_unload():
    global _timer
    if _timer:
        _timer.cancel()
    _timer = threading.Timer(IDLE_S + 1, _unload_if_idle)
    _timer.daemon = True
    _timer.start()


def _unload_if_idle():
    global _model
    with _lock:
        if _model is None or time.time() - _last_use < IDLE_S:
            return
        _model = None
        gc.collect()
        try:
            import torch
            if torch.backends.mps.is_available():
                torch.mps.empty_cache()
        except Exception:
            pass


def predict_alpha(im: Image.Image) -> np.ndarray:
    """Foreground probability (float32 0..1) at the image's own size; inference runs at 1024×1024."""
    global _last_use
    import torch
    rgb = im.convert("RGB")
    x = (np.asarray(rgb.resize((SIZE, SIZE), Image.BILINEAR), np.float32) / 255.0 - MEAN) / STD
    with _lock:
        m = _load()
        dtype = next(m.parameters()).dtype
        t = torch.from_numpy(x.transpose(2, 0, 1)[None]).to(_device, dtype)
        with torch.no_grad():
            pred = m(t)[-1].sigmoid()
            pred = torch.nn.functional.interpolate(pred.float(), size=(rgb.height, rgb.width), mode="bilinear",
                                                   align_corners=False)
        a = pred[0, 0].cpu().numpy()
        del t, pred
        if _device == "mps":
            torch.mps.empty_cache()  # ~4 GB of activations; only the weights (~0.5 GB) stay until the idle unload
        _last_use = time.time()
    _schedule_unload()
    return a


def _box(x, r: int):
    """Mean over a (2r+1)² window of an (H, W, C) tensor (prefix sums); windows are clipped at the image border."""
    import torch
    for d in (0, 1):
        n = x.shape[d]
        c = torch.cumsum(x, d)
        size = lambda k: [k if j == d else s for j, s in enumerate(c.shape)]  # noqa: E731
        # padded prefix sums: cp[i + 2r + 1] - cp[i] = sum of x[max(i-r, 0) : min(i+r+1, n)]
        cp = torch.cat([c.new_zeros(size(r + 1)), c, c.narrow(d, n - 1, 1).expand(size(r))], d)
        i = torch.arange(n)
        cnt = (torch.clamp(i + r + 1, max=n) - torch.clamp(i - r, min=0)).float()
        x = (cp.narrow(d, 2 * r + 1, n) - cp.narrow(d, 0, n)) / cnt.view([n if j == d else 1 for j in range(x.ndim)])
    return x


def _fb_fusion(img, f, b, a, r):
    ba = _box(a, r)
    bf = _box(f * a, r) / (ba + 1e-5)
    bb = _box(b * (1 - a), r) / ((1 - ba) + 1e-5)
    return (bf + a * (img - a * bf - (1 - a) * bb)).clamp(0, 1), bb


def refine_foreground(img: np.ndarray, a: np.ndarray) -> np.ndarray:
    """Approximate fast foreground colour estimation (Forte & Pitié, as in BiRefNet's demo): removes the old
    background's colour from semi-transparent hair/fur pixels, so there is no coloured fringe on a new background."""
    import torch
    i, a3 = torch.from_numpy(img), torch.from_numpy(a)[..., None]
    f, bb = _fb_fusion(i, i, i, a3, 45)  # radius 45 = the 90 px window of the original
    return _fb_fusion(i, f, bb, a3, 3)[0].numpy()


def remove_ai(im: Image.Image) -> Image.Image:
    rgba = im.convert("RGBA")
    src = np.asarray(rgba)
    a = predict_alpha(im)
    img = src[..., :3].astype(np.float32) / 255.0
    fg = refine_foreground(img, a)
    alpha = a * (src[..., 3].astype(np.float32) / 255.0)
    out = np.dstack([fg * 255.0, alpha * 255.0]).round().clip(0, 255).astype(np.uint8)
    return Image.fromarray(out, "RGBA")
