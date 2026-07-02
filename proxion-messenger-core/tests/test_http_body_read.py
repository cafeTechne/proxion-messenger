"""_read_http_body must assemble a body that arrives across multiple reads.

Reproduces the truncation bug: asyncio reader.read(n) returns as soon as ANY
data is available, so a multi-segment body was silently cut short.
"""
from __future__ import annotations

import asyncio

import pytest

from proxion_messenger_core._gateway_http import _read_http_body


class ChunkedReader:
    """Fake StreamReader that hands out the body a few bytes at a time, the way
    a real socket delivers a large body across TCP segments."""

    def __init__(self, data: bytes, chunk: int = 8):
        self._data = data
        self._chunk = chunk
        self._pos = 0

    async def read(self, n: int) -> bytes:
        if self._pos >= len(self._data):
            return b""
        take = min(n, self._chunk, len(self._data) - self._pos)
        out = self._data[self._pos:self._pos + take]
        self._pos += take
        return out


@pytest.mark.asyncio
async def test_reads_full_body_across_chunks():
    body = b"x" * 5000  # far larger than the 8-byte chunk
    r = ChunkedReader(body, chunk=8)
    got = await _read_http_body(r, len(body))
    assert got == body, f"truncated: got {len(got)} of {len(body)} bytes"


@pytest.mark.asyncio
async def test_stops_at_requested_length_not_more():
    body = b"A" * 100 + b"TRAILER"
    r = ChunkedReader(body, chunk=8)
    got = await _read_http_body(r, 100)
    assert got == b"A" * 100  # does not consume the trailer/next request


@pytest.mark.asyncio
async def test_short_stream_returns_what_arrived():
    r = ChunkedReader(b"partial", chunk=3)
    got = await _read_http_body(r, 1000)  # asks for more than exists
    assert got == b"partial"  # EOF ends it, no hang


@pytest.mark.asyncio
async def test_zero_length_is_empty():
    r = ChunkedReader(b"whatever")
    assert await _read_http_body(r, 0) == b""
