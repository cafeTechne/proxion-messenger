"""Tests for R7 session-bound auth context hash in _gateway_auth.py."""
import asyncio
import json
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


@pytest.fixture
def gateway(tmp_path):
    import os
    from proxion_messenger_core.persist import AgentState
    from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
    agent = AgentState.generate()
    gw = ProxionGateway(
        agent=agent,
        dm_clients={},
        room_memberships={},
        config=GatewayConfig(db_path=str(tmp_path / "test.db")),
    )
    return gw


def make_ws(ip="1.2.3.4", ua_hash="abc123"):
    ws = MagicMock()
    ws.send = AsyncMock()
    ws.close = AsyncMock()
    ws.remote_address = (ip, 12345)
    return ws


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestAuthContextBinding:
    def test_auth_challenge_stores_ctx(self, gateway):
        import os
        os.environ["PROXION_REQUIRE_AUTH"] = "1"
        ws = make_ws()
        gateway._session_meta[ws] = {"ip_addr": "1.2.3.4", "user_agent_hash": "abc"}
        run(gateway._handle_register(ws, {
            "did": "did:key:z6Mk" + "a" * 44,
            "webid": "",
            "display_name": "",
            "gateway_url": "",
        }))
        # Challenge should have been issued
        pending = gateway._pending_auth.get(ws)
        assert pending is not None
        assert "auth_ctx" in pending
        assert isinstance(pending["auth_ctx"], str)
        assert len(pending["auth_ctx"]) == 64  # sha256 hex
        os.environ.pop("PROXION_REQUIRE_AUTH", None)

    def test_auth_response_accepted_on_matching_context(self, gateway):
        """Auth succeeds when IP/UA hash matches stored context."""
        import os, time, base64, hashlib
        key = Ed25519PrivateKey.generate()
        pub = key.public_key()
        pub_bytes = pub.public_bytes_raw()
        from proxion_messenger_core.didkey import pub_key_to_did
        did = pub_key_to_did(pub_bytes)
        nonce = "testnonce123"
        ip = "1.2.3.4"
        ua = "hashvalue"
        ctx = hashlib.sha256(f"{ip}|{ua}|{nonce}".encode()).hexdigest()
        ws = make_ws(ip=ip)
        gateway._session_meta[ws] = {"ip_addr": ip, "user_agent_hash": ua}
        gateway._pending_auth[ws] = {
            "did": did, "webid": "", "display_name": "", "gateway_url": "",
            "nonce": nonce, "expires_at": time.time() + 30, "auth_ctx": ctx,
        }
        sig = key.sign(nonce.encode())
        sig_b64 = base64.urlsafe_b64encode(sig).rstrip(b"=").decode()
        # Should not raise; auth should proceed past context check
        # (it may fail on register step, but not on context check)
        run(gateway._handle_auth_response(ws, {"signature": sig_b64}))
        # Context mismatch should NOT have been sent
        calls = [json.loads(c.args[0]) for c in ws.send.call_args_list if c.args]
        mismatch = [c for c in calls if c.get("reason") == "context_mismatch"]
        assert len(mismatch) == 0

    def test_auth_response_rejected_on_context_mismatch(self, gateway):
        """Auth fails with context_mismatch when IP differs."""
        import os, time, base64, hashlib
        ip_at_challenge = "1.2.3.4"
        ip_at_response = "9.9.9.9"  # different
        nonce = "testnonce123"
        ctx = hashlib.sha256(f"{ip_at_challenge}||{nonce}".encode()).hexdigest()
        ws = make_ws(ip=ip_at_response)
        gateway._session_meta[ws] = {"ip_addr": ip_at_response, "user_agent_hash": ""}
        gateway._pending_auth[ws] = {
            "did": "did:key:z6Mktest", "webid": "", "display_name": "", "gateway_url": "",
            "nonce": nonce, "expires_at": time.time() + 30, "auth_ctx": ctx,
        }
        run(gateway._handle_auth_response(ws, {"signature": "AAAA"}))
        calls = [json.loads(c.args[0]) for c in ws.send.call_args_list if c.args]
        mismatch = [c for c in calls if c.get("reason") == "context_mismatch"]
        assert len(mismatch) == 1

    def test_context_mismatch_closes_socket_1008(self, gateway):
        """Context mismatch closes the socket with code 1008."""
        import time, hashlib
        ws = make_ws()
        gateway._session_meta[ws] = {"ip_addr": "9.9.9.9", "user_agent_hash": ""}
        ctx = hashlib.sha256(b"1.2.3.4||nonce").hexdigest()
        gateway._pending_auth[ws] = {
            "did": "did:key:z6Mktest", "webid": "", "display_name": "", "gateway_url": "",
            "nonce": "nonce", "expires_at": time.time() + 30, "auth_ctx": ctx,
        }
        run(gateway._handle_auth_response(ws, {"signature": "AAAA"}))
        ws.close.assert_called_once_with(1008, "auth_failed")
