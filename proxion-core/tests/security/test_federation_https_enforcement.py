"""Tests for R7 HTTPS enforcement on federation endpoints."""
import asyncio
import json
import os
import pytest
from unittest.mock import MagicMock, AsyncMock, patch


@pytest.fixture
def gateway(tmp_path):
    from proxion_messenger_core.persist import AgentState
    from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
    agent = AgentState.generate()
    return ProxionGateway(
        agent=agent, dm_clients={}, room_memberships={},
        config=GatewayConfig(db_path=str(tmp_path / "fed.db")),
    )


def make_ws():
    ws = MagicMock()
    ws.send = AsyncMock()
    ws.close = AsyncMock()
    ws.remote_address = ("127.0.0.1", 9000)
    return ws


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestFederationHttpsEnforcement:
    def test_friend_request_rejects_http_endpoint_by_default(self, gateway):
        """send_friend_request rejects http:// target unless override env set."""
        os.environ.pop("PROXION_ALLOW_INSECURE_FEDERATION", None)
        ws = make_ws()
        identity = "did:key:z6MkSomeKey123456789012345678901234567890123456"
        gateway._client_webids[ws] = "did:key:z6MkCallerKey"
        gateway._session_meta[ws] = {"ip_addr": "127.0.0.1"}
        # A real DID is needed; use a minimal valid-looking one
        # The address format is did@gateway_url
        with patch("proxion_messenger_core.gateway._is_safe_gateway_url", return_value=True):
            from proxion_messenger_core.didkey import pub_key_to_did
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
            key = Ed25519PrivateKey.generate()
            pub = key.public_key().public_bytes_raw()
            target_did = pub_key_to_did(pub)
            run(gateway._handle_send_friend_request(ws, {
                "target_address": f"{target_did}@http://evil.example.com",
            }))
        calls = [json.loads(c.args[0]) for c in ws.send.call_args_list if c.args]
        errors = [c for c in calls if c.get("message") == "insecure_federation_endpoint"]
        assert len(errors) >= 1

    def test_friend_request_allows_https_by_default(self, gateway):
        """send_friend_request does not reject https:// endpoints."""
        os.environ.pop("PROXION_ALLOW_INSECURE_FEDERATION", None)
        ws = make_ws()
        gateway._client_webids[ws] = "did:key:z6MkCallerKey"
        gateway._session_meta[ws] = {"ip_addr": "127.0.0.1"}
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from proxion_messenger_core.didkey import pub_key_to_did
        key = Ed25519PrivateKey.generate()
        pub = key.public_key().public_bytes_raw()
        target_did = pub_key_to_did(pub)
        with patch("proxion_messenger_core.gateway._is_safe_gateway_url", return_value=False):
            # Will fail on safe URL check, not HTTPS check
            run(gateway._handle_send_friend_request(ws, {
                "target_address": f"{target_did}@https://secure.example.com",
            }))
        calls = [json.loads(c.args[0]) for c in ws.send.call_args_list if c.args]
        insecure_errors = [c for c in calls if c.get("message") == "insecure_federation_endpoint"]
        assert len(insecure_errors) == 0

    def test_http_allowed_when_override_env_set(self, gateway):
        """With PROXION_ALLOW_INSECURE_FEDERATION=1, http:// endpoints pass the HTTPS check."""
        os.environ["PROXION_ALLOW_INSECURE_FEDERATION"] = "1"
        ws = make_ws()
        gateway._client_webids[ws] = "did:key:z6MkCallerKey"
        gateway._session_meta[ws] = {"ip_addr": "127.0.0.1"}
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from proxion_messenger_core.didkey import pub_key_to_did
        key = Ed25519PrivateKey.generate()
        pub = key.public_key().public_bytes_raw()
        target_did = pub_key_to_did(pub)
        with patch("proxion_messenger_core.gateway._is_safe_gateway_url", return_value=False):
            # Fails at safe URL check, not at HTTPS check
            run(gateway._handle_send_friend_request(ws, {
                "target_address": f"{target_did}@http://local.example.com",
            }))
        calls = [json.loads(c.args[0]) for c in ws.send.call_args_list if c.args]
        insecure_errors = [c for c in calls if c.get("message") == "insecure_federation_endpoint"]
        assert len(insecure_errors) == 0
        os.environ.pop("PROXION_ALLOW_INSECURE_FEDERATION", None)

    def test_invite_post_rejects_http_hint_by_default(self, gateway):
        """_handle_invite_post rejects inbound invites with http:// endpoint hints."""
        os.environ.pop("PROXION_ALLOW_INSECURE_FEDERATION", None)
        from proxion_messenger_core.federation import FederationInvite, Capability
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from proxion_messenger_core.didkey import pub_key_to_did
        key = Ed25519PrivateKey.generate()
        pub = key.public_key().public_bytes_raw()
        did = pub_key_to_did(pub)
        caps = [Capability(with_="stash://dm/", can="crud/write")]
        invite = FederationInvite(
            issuer={"did": did, "public_key": pub.hex()},
            endpoint_hints=["http://attacker.example.com"],
            capabilities=caps,
        )
        invite.sign(key)
        body = json.dumps(invite.to_dict()).encode()
        import asyncio
        status, response = asyncio.get_event_loop().run_until_complete(
            gateway._handle_invite_post(body)
        )
        assert status.startswith("400")
        resp_data = json.loads(response)
        assert resp_data.get("error") == "insecure_endpoint_hint"
