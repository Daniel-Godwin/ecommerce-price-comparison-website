"""Embedding service (design doc §5.2.4).

Pluggable backends behind one interface:

- SentenceTransformerEmbedder — semantic embeddings (all-MiniLM-L6-v2),
  used automatically when `sentence-transformers` is installed.
- HashingEmbedder — dependency-free character-n-gram hashing vectors.
  Deterministic and fast; captures lexical similarity well enough for
  dev/CI and keeps torch out of the default install and CI image.

Both produce L2-normalized float32 vectors, so FAISS inner-product
search equals cosine similarity either way.
"""
from __future__ import annotations

import hashlib
import logging

import numpy as np

logger = logging.getLogger(__name__)

DIM_HASHING = 256


class HashingEmbedder:
    name = "hashing-ngram-256"
    dim = DIM_HASHING

    def encode(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, text in enumerate(texts):
            t = f"  {text.lower().strip()}  "
            for n in (3, 4):
                for j in range(len(t) - n + 1):
                    gram = t[j : j + n]
                    h = int(hashlib.blake2s(gram.encode(), digest_size=4).hexdigest(), 16)
                    out[i, h % self.dim] += 1.0
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return out / norms


class SentenceTransformerEmbedder:
    name = "all-MiniLM-L6-v2"
    dim = 384

    def __init__(self) -> None:
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")

    def encode(self, texts: list[str]) -> np.ndarray:
        vecs = self._model.encode(texts, normalize_embeddings=True)
        return np.asarray(vecs, dtype=np.float32)


_embedder = None


def get_embedder():
    global _embedder
    if _embedder is None:
        try:
            _embedder = SentenceTransformerEmbedder()
            logger.info("using sentence-transformers embedder")
        except ImportError:
            _embedder = HashingEmbedder()
            logger.info("sentence-transformers not installed; using hashing embedder")
    return _embedder
