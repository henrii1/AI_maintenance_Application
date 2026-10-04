"""Test doubles for the Anthropic SDK and the embedding model."""
from __future__ import annotations

import asyncio
import hashlib
import re
from types import SimpleNamespace as NS

import numpy as np


def usage(i: int = 100, o: int = 20, searches: int = 0):
    return NS(input_tokens=i, output_tokens=o, cache_read_input_tokens=0, cache_creation_input_tokens=0,
              server_tool_use=NS(web_search_requests=searches) if searches else None)


def text_block(t: str):
    return NS(type="text", text=t)


def tool_use(name: str, input: dict):
    return NS(type="tool_use", name=name, input=input, id="toolu_test")


def message(*blocks, stop_reason: str = "end_turn", u=None):
    return NS(content=list(blocks), stop_reason=stop_reason, usage=u or usage())


class _SyncMessages:
    def __init__(self, handler):
        self.handler, self.calls = handler, []

    def create(self, **kw):
        self.calls.append(kw)
        r = self.handler(kw)
        if isinstance(r, Exception):
            raise r
        return r


class FakeAnthropic:
    """handler(kwargs) -> message | Exception"""

    def __init__(self, handler):
        self.messages = _SyncMessages(handler)


class _FakeStream:
    def __init__(self, chunks: list[str], final, delay: float):
        self._chunks, self._final, self._delay = chunks, final, delay

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for c in self._chunks:
            if self._delay:
                await asyncio.sleep(self._delay)
            if isinstance(c, Exception):
                raise c
            yield NS(type="text", text=c)

    async def get_final_message(self):
        return self._final


class _AsyncMessages:
    def __init__(self, handler, stream_handler):
        self.handler, self.stream_handler, self.calls = handler, stream_handler, []

    async def create(self, **kw):
        self.calls.append(kw)
        r = self.handler(kw)
        if isinstance(r, Exception):
            raise r
        return r

    def stream(self, **kw):
        self.calls.append(kw)
        chunks, final, delay = self.stream_handler(kw)
        return _FakeStream(chunks, final, delay)


class FakeAsyncAnthropic:
    """handler(kwargs) -> message | Exception for create();
    stream_handler(kwargs) -> (text_chunks, final_message, per_chunk_delay) for stream()."""

    def __init__(self, handler=None, stream_handler=None):
        self.messages = _AsyncMessages(handler or (lambda kw: AssertionError("unexpected create")),
                                       stream_handler or (lambda kw: ([], message(), 0)))


class HashEmbedder:
    """Deterministic bag-of-words vectors: lexical overlap ⇒ cosine similarity."""
    dim = 384

    def _vec(self, text: str) -> np.ndarray:
        v = np.zeros(self.dim, np.float32)
        for w in re.findall(r"[a-z0-9]+", text.lower()):
            v[int(hashlib.md5(w.encode()).hexdigest(), 16) % self.dim] += 1.0
        n = np.linalg.norm(v)
        return v / n if n else v

    def embed_passages(self, texts: list[str]) -> np.ndarray:
        return np.stack([self._vec(t) for t in texts]) if texts else np.zeros((0, self.dim), np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        return self._vec(text)
