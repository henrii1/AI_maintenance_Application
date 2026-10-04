"""Local sentence embeddings (fastembed / ONNX, no torch)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Protocol

import numpy as np

MODEL_NAME = "BAAI/bge-small-en-v1.5"


class Embedder(Protocol):
    dim: int

    def embed_passages(self, texts: list[str]) -> np.ndarray: ...

    def embed_query(self, text: str) -> np.ndarray: ...


def _normalize_rows(m: np.ndarray) -> np.ndarray:
    return m / np.clip(np.linalg.norm(m, axis=1, keepdims=True), 1e-9, None)


class FastEmbedder:
    dim = 384

    def __init__(self, cache_dir: Path | None = None, model_name: str = MODEL_NAME):
        from fastembed import TextEmbedding
        self._model = TextEmbedding(model_name=model_name, cache_dir=str(cache_dir) if cache_dir else None)

    def embed_passages(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), np.float32)
        return _normalize_rows(np.asarray(list(self._model.passage_embed(texts)), dtype=np.float32))

    def embed_query(self, text: str) -> np.ndarray:
        return _normalize_rows(np.asarray(list(self._model.query_embed(text)), dtype=np.float32))[0]


@lru_cache(maxsize=1)
def get_embedder(cache_dir: Path | None = None) -> FastEmbedder:
    return FastEmbedder(cache_dir)


def to_blob(v: np.ndarray) -> bytes:
    return np.asarray(v, dtype=np.float32).tobytes()


def from_blob(b: bytes) -> np.ndarray:
    return np.frombuffer(b, dtype=np.float32)
