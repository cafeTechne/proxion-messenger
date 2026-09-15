"""upload_prekeys caps one-time prekeys so a large frame can't grow dm_prekeys
without bound (F10)."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.didkey import pub_key_to_did
from proxion_messenger_core._gateway_dm import (
    MAX_ONE_TIME_PREKEYS_PER_UPLOAD,
    MAX_UNUSED_ONE_TIME_PREKEYS,
)


def _did(priv: Ed25519PrivateKey) -> str:
    return pub_key_to_did(priv.public_key().public_bytes_raw())


def _mock_ws():
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.close = AsyncMock()
    ws.__hash__ = lambda self: id(self)
    ws.__eq__ = lambda self, other: self is other
    ws.remote_address = ("127.0.0.1", 12345)
    return ws


@pytest.fixture
def noauth_env(monkeypatch):
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "0")


@pytest.fixture
def gateway(tmp_path):
    return ProxionGateway(
        agent=AgentState.generate(), dm_clients={}, room_memberships={},
        config=GatewayConfig(host="127.0.0.1", db_path=str(tmp_path / "prekeys.db")),
    )


async def _register(gw, ws, did):
    gw.clients.add(ws)
    await gw.process_command(ws, {"cmd": "register", "did": did, "display_name": "D"})


def _bundle(n: int, start: int = 0) -> dict:
    return {
        "signed_prekey_id": "spk-1",
        "signed_prekey_pub_b64": "spkpub",
        "signed_prekey_priv_b64": "spkpriv",
        "one_time_prekeys": [
            {"id": f"opk-{i}", "pub_b64": f"pub{i}", "priv_b64": f"priv{i}"}
            for i in range(start, start + n)
        ],
    }


@pytest.mark.asyncio
async def test_upload_prekeys_truncates_oversized_batch(gateway, noauth_env):
    owner = _did(Ed25519PrivateKey.generate())
    ws = _mock_ws()
    await _register(gateway, ws, owner)

    # A single oversized frame packs far more than the per-request cap.
    await gateway.process_command(ws, {
        "cmd": "upload_prekeys",
        "bundle": _bundle(MAX_ONE_TIME_PREKEYS_PER_UPLOAD + 5000),
    })

    stored = gateway._store.count_unused_one_time_prekeys(owner)
    assert stored <= MAX_UNUSED_ONE_TIME_PREKEYS
    assert stored == MAX_ONE_TIME_PREKEYS_PER_UPLOAD


@pytest.mark.asyncio
async def test_upload_prekeys_pool_stays_bounded_across_uploads(gateway, noauth_env):
    owner = _did(Ed25519PrivateKey.generate())
    ws = _mock_ws()
    await _register(gateway, ws, owner)

    # Repeated max-sized uploads with fresh ids must not grow the pool past the
    # ceiling (INSERT OR REPLACE would otherwise let distinct ids accumulate).
    for _round in range(5):
        await gateway.process_command(ws, {
            "cmd": "upload_prekeys",
            "bundle": _bundle(MAX_ONE_TIME_PREKEYS_PER_UPLOAD,
                              start=_round * MAX_ONE_TIME_PREKEYS_PER_UPLOAD),
        })

    assert gateway._store.count_unused_one_time_prekeys(owner) <= MAX_UNUSED_ONE_TIME_PREKEYS


@pytest.mark.asyncio
async def test_upload_prekeys_small_bundle_stored_in_full(gateway, noauth_env):
    owner = _did(Ed25519PrivateKey.generate())
    ws = _mock_ws()
    await _register(gateway, ws, owner)

    await gateway.process_command(ws, {"cmd": "upload_prekeys", "bundle": _bundle(5)})
    assert gateway._store.count_unused_one_time_prekeys(owner) == 5
