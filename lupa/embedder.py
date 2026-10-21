"""Thin wrapper around EmbeddingGemma 2 (sentence-transformers) on Apple Silicon."""
import os
import threading
import time
import warnings

import numpy as np

from .common import CFG, DIM, nfc

warnings.filterwarnings("ignore")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
# Cap how much unified memory PyTorch may hold on the GPU (16 GB Mac shared with everything else).
os.environ.setdefault("PYTORCH_MPS_HIGH_WATERMARK_RATIO", "0.45")
os.environ.setdefault("PYTORCH_MPS_LOW_WATERMARK_RATIO", "0.35")  # must be <= high ratio

MODEL_ID = "google/embeddinggemma-2"


class Embedder:
    """The model is loaded on first use and can be unloaded (`unload()`, e.g. after idle time); it reloads by itself
    on the next call, from the local copy in a second or two."""

    def __init__(self, audio=True, vision=True, device=None, lazy=False):
        import torch

        self.cfg = {}
        if not audio:
            self.cfg["audio_config"] = None
        if not vision:
            self.cfg["vision_config"] = None
        self.device = device or ("mps" if torch.backends.mps.is_available() else "cpu")
        self.model = None
        self.lock = threading.Lock()
        self.used = time.time()
        if not lazy:
            with self.lock:
                self._load()

    def _load(self):
        import torch
        from sentence_transformers import SentenceTransformer

        # bf16 everywhere: on Apple Silicon CPU it is as fast as fp32 (~50 ms/query), half the RAM,
        # cosine 0.999 vs fp32. Never fp16 (activations overflow).
        dtype = torch.bfloat16
        kw = dict(device=self.device, config_kwargs=self.cfg or None, model_kwargs={"torch_dtype": dtype})
        try:  # use the local copy without touching the network
            self.model = SentenceTransformer(MODEL_ID, local_files_only=True, **kw)
        except Exception:
            self.model = SentenceTransformer(MODEL_ID, **kw)
        self.set_image_tokens(CFG.get("image_tokens", 140))

    def unload(self):
        """Give the model's RAM / GPU memory back; the next encode loads it again."""
        with self.lock:
            if self.model is None:
                return
            self.model = None
        self.free()

    def idle_unload(self, seconds: float) -> bool:
        if seconds and self.model is not None and time.time() - self.used > seconds:
            self.unload()
            return True
        return False

    def set_image_tokens(self, n: int):
        proc = getattr(self.model[0], "processor", None)
        ip = getattr(proc, "image_processor", None)
        if ip is not None:
            ip.max_soft_tokens = ip.image_seq_length = proc.image_seq_length = int(n)

    def _enc(self, items, batch_size=32, **kw):
        if not items:
            return np.zeros((0, DIM), np.float16)
        while True:
            try:
                with self.lock:
                    if self.model is None:
                        self._load()
                    self.used = time.time()
                    v = self.model.encode(items, truncate_dim=DIM, normalize_embeddings=True, batch_size=batch_size,
                                          convert_to_numpy=True, show_progress_bar=False, **kw)
                self.used = time.time()
                return v.astype(np.float16)
            except RuntimeError as e:
                # GPU memory is shared with the whole Mac: back off instead of crashing the run
                if "out of memory" not in str(e) or batch_size <= 1:
                    raise
                self.free()
                batch_size = max(1, batch_size // 2)

    def free(self):
        import gc
        import torch
        gc.collect()
        if torch.backends.mps.is_available():
            torch.mps.empty_cache()

    def query(self, text: str) -> np.ndarray:
        return self._enc([nfc(text)], prompt_name="SearchQuery")[0]

    def documents(self, titles, texts, batch_size=16) -> np.ndarray:
        # Model's document format is "title: {title} | text: {text}"; we fill the title ourselves.
        items = [nfc(f"title: {t or 'none'} | text: {x}") for t, x in zip(titles, texts)]
        return self._enc(items, batch_size=batch_size)

    def images(self, imgs, batch_size=8) -> np.ndarray:
        return self._enc([{"image": im} for im in imgs], batch_size=batch_size)

    def audio(self, arrays, batch_size=4) -> np.ndarray:
        return self._enc([{"audio": a} for a in arrays], batch_size=batch_size)
