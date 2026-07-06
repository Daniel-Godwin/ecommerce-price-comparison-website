"""FAISS vector store (design doc §5.2.4).

IndexFlatIP over L2-normalized vectors = cosine similarity. Product ids
ride along via IndexIDMap. Persisted to disk (index + metadata) so the
semantic index survives restarts. The public interface (add/search/save/
load) is deliberately narrow so a managed store (Pinecone/Qdrant) can be
swapped in later without touching callers.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

import faiss
import numpy as np

from app.vector.embedder import get_embedder

logger = logging.getLogger(__name__)


class FaissStore:
    def __init__(self, index_dir: str = "./vector_index") -> None:
        self.dir = Path(index_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.embedder = get_embedder()
        self._index_path = self.dir / "products.faiss"
        self._meta_path = self.dir / "meta.json"
        self._index = self._load_or_create()

    # ------------------------------------------------------------- #
    def _load_or_create(self) -> faiss.Index:
        if self._index_path.exists() and self._meta_path.exists():
            meta = json.loads(self._meta_path.read_text())
            if meta.get("embedder") == self.embedder.name:
                logger.info("loaded FAISS index (%s vectors)", meta.get("count"))
                return faiss.read_index(str(self._index_path))
            logger.warning("embedder changed (%s -> %s); rebuilding index",
                           meta.get("embedder"), self.embedder.name)
        base = faiss.IndexFlatIP(self.embedder.dim)
        return faiss.IndexIDMap(base)

    def save(self) -> None:
        with self._lock:
            faiss.write_index(self._index, str(self._index_path))
            self._meta_path.write_text(
                json.dumps({"embedder": self.embedder.name,
                            "count": int(self._index.ntotal)})
            )

    # ------------------------------------------------------------- #
    @property
    def count(self) -> int:
        return int(self._index.ntotal)

    def upsert(self, ids: list[int], texts: list[str]) -> None:
        """Add or replace vectors for the given product ids."""
        if not ids:
            return
        vectors = self.embedder.encode(texts)
        id_array = np.asarray(ids, dtype=np.int64)
        with self._lock:
            self._index.remove_ids(id_array)      # replace if present
            self._index.add_with_ids(vectors, id_array)

    def search(self, query: str, k: int = 12) -> list[tuple[int, float]]:
        """Return [(product_id, similarity)] best-first."""
        if self.count == 0:
            return []
        vec = self.embedder.encode([query])
        with self._lock:
            scores, ids = self._index.search(vec, min(k, self.count))
        return [
            (int(pid), float(score))
            for pid, score in zip(ids[0], scores[0], strict=False)
            if pid != -1
        ]


_store: FaissStore | None = None


def get_store() -> FaissStore:
    global _store
    if _store is None:
        import os

        _store = FaissStore(os.getenv("VECTOR_INDEX_DIR", "./vector_index"))
    return _store
