"""Thin wrapper around EmbeddingGemma 2 (sentence-transformers) on Apple Silicon."""
import os
import threading
import warnings

import numpy as np

from .common import CFG, DIM, nfc

warnings.filterwarnings("ignore")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
# Cap how much unified memory PyTorch may hold on the GPU (16 GB Mac shared with everything else).
os.environ.setdefault("PYTORCH_MPS_HIGH_WATERMARK_RATIO", "0.35")
os.environ.setdefault("PYTORCH_MPS_LOW_WATERMARK_RATIO", "0.25")  # must be <= high ratio

MODEL_ID = "google/embeddinggemma-2"


class Embedder:
    def __init__(self, audio=True, vision=True, device=None):
        import torch
        from sentence_transformers import SentenceTransformer

        cfg = {}
        if not audio:
            cfg["audio_config"] = None
        if not vision:
            cfg["vision_config"] = None
        dev = device or ("mps" if torch.backends.mps.is_available() else "cpu")
        # bf16 on the GPU; plain fp32 on CPU (bf16 matmuls are slow there). Never fp16 (overflows).
        dtype = torch.float32 if dev == "cpu" else torch.bfloat16
        kw = dict(device=dev, config_kwargs=cfg or None, model_kwargs={"torch_dtype": dtype})
        try:  # use the local copy without touching the network
            self.model = SentenceTransformer(MODEL_ID, local_files_only=True, **kw)
        except Exception:
            self.model = SentenceTransformer(MODEL_ID, **kw)
        self.lock = threading.Lock()
        self.set_image_tokens(CFG.get("image_tokens", 140))

    def set_image_tokens(self, n: int):
        proc = getattr(self.model[0], "processor", None)
        ip = getattr(proc, "image_processor", None)
        if ip is not None:
            ip.max_soft_tokens = ip.image_seq_length = proc.image_seq_length = int(n)

    def _enc(self, items, **kw):
        if not items:
            return np.zeros((0, DIM), np.float16)
        with self.lock:
            v = self.model.encode(items, truncate_dim=DIM, normalize_embeddings=True,
                                  convert_to_numpy=True, show_progress_bar=False, **kw)
        return v.astype(np.float16)

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
