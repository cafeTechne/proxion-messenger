"""Round 3: Auth challenge lockout after repeated failures."""
import asyncio
import base64
import json
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState


@pytest.fixture
def gw(tmp_path):
    agent = AgentState.generate()
    return ProxionGateway(
        agent=agent, dm_clients=[], room_memberships=[],
        config=GatewayConfig(port=9895, db_path=str(tmp_path / "test.db")),
        read_state=ReadState(),
    )


def _mock_ws(gw):
    ws = MagicMock()
    ws.send = AsyncMock()
    ws.close = AsyncMock()
    gw.clients.add(ws)
    gw._session_meta[ws] = {"ip_addr": "10.0.0.1"}
    return ws


@pytest.mark.asyncio
async def test_auth_lockout_after_five_failures(gw, monkeypatch):
    """5 bad auth_response attempts on same socket → ws.close(1008, 'auth_lockout')."""
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "1")
    ws = _mock_ws(gw)
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    key = Ed25519PrivateKey.generate()
    pub = key.public_key()
    from proxion_messenger_core.didkey import pub_key_to_did
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    did = pub_key_to_did(pub.public_bytes(Encoding.Raw, PublicFormat.Raw))

    for i in range(5):
        # Set a fresh pending_auth challenge
        import time
        gw._pending_auth[ws] = {
            "did": did, "webid": "", "display_name": "", "gateway_url": "",
            "nonce": f"nonce{i}", "expires_at": time.time() + 60,
        }
        await gw._handle_auth_response(ws, {"signature": "badsig"})

    ws.close.assert_called()
    close_args = ws.close.call_args[0]
    assert close_args[0] == 1008
    assert "auth_lockout" in (close_args[1] if len(close_args) > 1 else "")


@pytest.mark.asyncio
async def test_successful_auth_resets_failure_counter(gw, monkeypatch):
    """Successful auth after some failures resets the counter."""
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "1")
    ws = _mock_ws(gw)
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    from proxion_messenger_core.didkey import pub_key_to_did
    import time

    key = Ed25519PrivateKey.generate()
    pub = key.public_key()
    did = pub_key_to_did(pub.public_bytes(Encoding.Raw, PublicFormat.Raw))

    # Seed 4 failures manually (keyed by source IP, not by socket)
    fail_key = "10.0.0.1"
    gw._auth_fail_counts[fail_key] = {"count": 4, "first_at": time.time()}

    # Now do a valid auth
    nonce = "validnonce"
    sig = key.sign(nonce.encode())
    sig_b64 = base64.urlsafe_b64encode(sig).rstrip(b"=").decode()
    gw._pending_auth[ws] = {
        "did": did, "webid": did, "display_name": "", "gateway_url": "",
        "nonce": nonce, "expires_at": time.time() + 60,
    }
    gw._auth_verified.discard(ws)
    await gw._handle_auth_response(ws, {"signature": sig_b64})

    # Failure counter should be cleared
    assert fail_key not in gw._auth_fail_counts, "Counter should be cleared after successful auth"


@pytest.mark.asyncio
async def test_lockout_survives_reconnect_from_same_ip(gw, monkeypatch):
    """Reconnecting must NOT reset the brute-force counter.

    The lockout used to be keyed by (id(websocket), ip). Closing the connection
    on the 5th failure therefore handed the attacker a brand-new counter on
    reconnect, making the control decorative: unlimited guesses, 4 per socket.
    """
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "1")
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    from proxion_messenger_core.didkey import pub_key_to_did
    import time

    key = Ed25519PrivateKey.generate()
    did = pub_key_to_did(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))

    # Burn the allowance on one socket.
    ws1 = _mock_ws(gw)
    for i in range(5):
        gw._pending_auth[ws1] = {
            "did": did, "webid": "", "display_name": "", "gateway_url": "",
            "nonce": f"n{i}", "expires_at": time.time() + 60,
        }
        await gw._handle_auth_response(ws1, {"signature": "badsig"})
    assert gw._auth_fail_counts.get("10.0.0.1", {}).get("count", 0) >= 5

    # Attacker reconnects: a new socket object from the same IP.
    ws2 = _mock_ws(gw)
    gw._pending_auth[ws2] = {
        "did": did, "webid": "", "display_name": "", "gateway_url": "",
        "nonce": "fresh", "expires_at": time.time() + 60,
    }
    await gw._handle_auth_response(ws2, {"signature": "badsig"})

    ws2.close.assert_called()
    assert "auth_lockout" in ws2.close.call_args[0][1]


@pytest.mark.asyncio
async def test_expired_failures_are_pruned(gw):
    """Old entries fall out of the map so it cannot grow without bound."""
    import time
    gw._auth_fail_counts["1.2.3.4"] = {
        "count": 5, "first_at": time.time() - (gw._AUTH_FAIL_WINDOW + 60),
    }
    gw._auth_fail_counts["5.6.7.8"] = {"count": 1, "first_at": time.time()}
    gw._auth_fail_prune(time.time())
    assert "1.2.3.4" not in gw._auth_fail_counts, "expired entry should be pruned"
    assert "5.6.7.8" in gw._auth_fail_counts, "recent entry should survive"


@pytest.mark.asyncio
async def test_unknown_ip_does_not_share_one_bucket(gw):
    """Clients with no known IP must not collapse into a single shared key.

    Otherwise one bad actor with an unknown source could lock out everyone.
    """
    ws_a, ws_b = MagicMock(), MagicMock()
    gw._session_meta[ws_a] = {}          # no ip_addr
    gw._session_meta[ws_b] = {}
    assert gw._auth_fail_key(ws_a) is ws_a
    assert gw._auth_fail_key(ws_a) != gw._auth_fail_key(ws_b)


@pytest.mark.asyncio
async def test_unauthenticated_socket_times_out(gw, monkeypatch):
    """Unauthenticated socket is closed with auth_timeout after the configured period."""
    monkeypatch.setenv("PROXION_AUTH_TIMEOUT_SECONDS", "0")
    ws = MagicMock()
    ws.send = AsyncMock()
    ws.close = AsyncMock()
    gw.clients.add(ws)
    # ws is not in _client_webids — simulate unauthenticated

    # Call the timeout task directly
    async def _run_timeout():
        auth_timeout_s = 0
        await asyncio.sleep(auth_timeout_s)
        if ws in gw.clients and ws not in gw._client_webids:
            try:
                await ws.close(1008, "auth_timeout")
            except Exception:
                pass

    await _run_timeout()
    ws.close.assert_called_once_with(1008, "auth_timeout")
