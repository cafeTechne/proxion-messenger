"""Round 11 — Solid Protocol security hardening tests.

Covers:
  1. DPoP proof: exp claim present and equals iat + 60.
  2. Pod URL origin validation: rejects pod URLs on a different origin.
  3. Room container ACL: ensure_room_container writes ACL when owner_webid provided.
  4. Credential encryption: pod_creds.json is stored encrypted (v2) and round-trips.
  5. set_acl_multi: WAC Turtle contains one stanza per subject.
"""
from __future__ import annotations

import base64
import json
import uuid
import pytest
from unittest.mock import MagicMock, patch, call

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from proxion_messenger_core.dpop import make_dpop_proof
from proxion_messenger_core.css_setup import CssAccountManager
from proxion_messenger_core._gateway_pod import _encrypt_creds, _decrypt_creds


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _decode_jwt_part(part: str) -> dict:
    padded = part + "==" * ((4 - len(part) % 4) % 4)
    return json.loads(base64.urlsafe_b64decode(padded))


def _key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


# ---------------------------------------------------------------------------
# 1. DPoP exp claim
# ---------------------------------------------------------------------------

class TestDpopExpClaim:
    def test_exp_present(self):
        proof = make_dpop_proof(_key(), "GET", "https://pod.example/r")
        payload = _decode_jwt_part(proof.split(".")[1])
        assert "exp" in payload, "DPoP proof must include exp claim"

    def test_exp_equals_iat_plus_60(self):
        proof = make_dpop_proof(_key(), "GET", "https://pod.example/r", iat=1700000000)
        payload = _decode_jwt_part(proof.split(".")[1])
        assert payload["exp"] == 1700000000 + 60

    def test_exp_greater_than_iat(self):
        proof = make_dpop_proof(_key(), "GET", "https://pod.example/r")
        payload = _decode_jwt_part(proof.split(".")[1])
        assert payload["exp"] > payload["iat"]

    def test_existing_fields_unchanged(self):
        proof = make_dpop_proof(_key(), "PUT", "https://pod.example/r", iat=1700000000)
        payload = _decode_jwt_part(proof.split(".")[1])
        assert payload["htm"] == "PUT"
        assert payload["htu"] == "https://pod.example/r"
        assert payload["iat"] == 1700000000
        assert "jti" in payload


# ---------------------------------------------------------------------------
# 2. Pod URL origin validation
# ---------------------------------------------------------------------------

class TestPodUrlValidation:
    def test_same_origin_passes(self):
        mgr = CssAccountManager("http://localhost:3000")
        mgr._validate_pod_url("http://localhost:3000/alice/")  # should not raise

    def test_different_host_rejected(self):
        mgr = CssAccountManager("http://localhost:3000")
        with pytest.raises(ValueError, match="origin"):
            mgr._validate_pod_url("http://evil.example.com/alice/")

    def test_different_scheme_rejected(self):
        mgr = CssAccountManager("https://pod.example.com")
        with pytest.raises(ValueError, match="origin"):
            mgr._validate_pod_url("http://pod.example.com/alice/")

    def test_different_port_rejected(self):
        mgr = CssAccountManager("http://localhost:3000")
        with pytest.raises(ValueError, match="origin"):
            mgr._validate_pod_url("http://localhost:4000/alice/")

    def test_same_origin_different_path_passes(self):
        mgr = CssAccountManager("http://localhost:3000")
        mgr._validate_pod_url("http://localhost:3000/bob/profile/card")


# ---------------------------------------------------------------------------
# 3. Room container ACL — ensure_room_container calls set_acl_multi_auto
# ---------------------------------------------------------------------------

class TestRoomContainerAcl:
    def test_acl_called_when_owner_provided(self):
        from proxion_messenger_core.pod_room_store import PodRoomStore
        from proxion_messenger_core.solid_client import SolidError

        client = MagicMock()
        client._resolver = MagicMock()
        client._auth_headers = {}
        client._dynamic_headers = MagicMock(return_value={})
        client._session = MagicMock()

        # Make _put_container_create_only succeed (return 201)
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        client._session.put.return_value = mock_resp

        store = PodRoomStore(client)

        with patch("proxion_messenger_core.pod_room_store.set_acl_multi_auto") as mock_acl:
            store.ensure_room_container(
                "room-123",
                owner_webid="did:key:owner",
                member_webids=["did:key:alice", "did:key:bob"],
            )
            assert mock_acl.called, "set_acl_multi_auto must be called when owner_webid provided"
            _, args, kwargs = mock_acl.mock_calls[0]
            # First positional arg is pod_client, second is stash_uri
            assert "room-123" in args[1]
            assert args[2] == "did:key:owner"
            assert "did:key:alice" in args[3]
            assert "did:key:bob" in args[3]

    def test_acl_not_called_without_owner(self):
        from proxion_messenger_core.pod_room_store import PodRoomStore

        client = MagicMock()
        client._resolver = MagicMock()
        client._auth_headers = {}
        client._dynamic_headers = MagicMock(return_value={})
        client._session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        client._session.put.return_value = mock_resp

        store = PodRoomStore(client)
        with patch("proxion_messenger_core.pod_room_store.set_acl_multi_auto") as mock_acl:
            store.ensure_room_container("room-456")  # no owner_webid
            assert not mock_acl.called

    def test_acl_failure_does_not_raise(self):
        """ACL errors are logged but must not abort room creation."""
        from proxion_messenger_core.pod_room_store import PodRoomStore

        client = MagicMock()
        client._resolver = MagicMock()
        client._auth_headers = {}
        client._dynamic_headers = MagicMock(return_value={})
        client._session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        client._session.put.return_value = mock_resp

        store = PodRoomStore(client)
        with patch(
            "proxion_messenger_core.pod_room_store.set_acl_multi_auto",
            side_effect=RuntimeError("pod down"),
        ):
            store.ensure_room_container("room-789", owner_webid="did:key:owner")
            # No exception should propagate


# ---------------------------------------------------------------------------
# 4. Credential encryption round-trip
# ---------------------------------------------------------------------------

class TestCredentialEncryption:
    def test_encrypt_decrypt_roundtrip(self):
        key = _key()
        plaintext = json.dumps({
            "css_url": "http://localhost:3000",
            "client_id": "cid-abc",
            "client_secret": "supersecret",
            "pod_url": "http://localhost:3000/alice/",
            "webid": "http://localhost:3000/alice/profile/card#me",
        }).encode()
        token = _encrypt_creds(key, plaintext)
        recovered = _decrypt_creds(key, token)
        assert recovered == plaintext

    def test_token_is_not_plaintext(self):
        key = _key()
        plaintext = b'{"client_secret": "supersecret"}'
        token = _encrypt_creds(key, plaintext)
        assert b"supersecret" not in token.encode("ascii")
        assert "supersecret" not in token

    def test_wrong_key_cannot_decrypt(self):
        from cryptography.fernet import InvalidToken
        key1 = _key()
        key2 = _key()
        plaintext = b"top secret"
        token = _encrypt_creds(key1, plaintext)
        with pytest.raises(Exception):  # InvalidToken or similar
            _decrypt_creds(key2, token)

    def test_different_keys_different_tokens(self):
        key = _key()
        plaintext = b"same plaintext"
        t1 = _encrypt_creds(key, plaintext)
        t2 = _encrypt_creds(key, plaintext)
        # Fernet tokens include a timestamp + random IV so should differ
        assert t1 != t2


# ---------------------------------------------------------------------------
# 5. set_acl_multi WAC Turtle structure
# ---------------------------------------------------------------------------

class TestSetAclMulti:
    def test_turtle_has_owner_stanza(self):
        from proxion_messenger_core.solid_client import SolidClient
        client = MagicMock(spec=SolidClient)
        client._resolver = MagicMock()
        client._resolver.resolve.return_value = "http://pod.example/rooms/r1/"
        client._auth_headers = {}
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        client._session = MagicMock()
        client._session.put.return_value = mock_resp

        # Call the real method via the class (not the mock spec)
        from proxion_messenger_core.solid_client import SolidClient as _SC
        _SC.set_acl_multi(
            client,
            "stash://pod/rooms/r1/",
            "did:key:owner",
            ["did:key:alice", "did:key:bob"],
        )
        assert client._session.put.called
        call_kwargs = client._session.put.call_args
        body = call_kwargs[1].get("content", b"").decode("utf-8")
        assert "did:key:owner" in body
        assert "did:key:alice" in body
        assert "did:key:bob" in body
        assert "acl:Read, acl:Write, acl:Control" in body
        assert body.count("acl:Authorization") >= 3

    def test_empty_members_only_owner_stanza(self):
        from proxion_messenger_core.solid_client import SolidClient as _SC
        client = MagicMock()
        client._resolver = MagicMock()
        client._resolver.resolve.return_value = "http://pod.example/rooms/r2/"
        client._auth_headers = {}
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        client._session = MagicMock()
        client._session.put.return_value = mock_resp

        _SC.set_acl_multi(client, "stash://pod/rooms/r2/", "did:key:owner", [])
        body = client._session.put.call_args[1]["content"].decode("utf-8")
        assert "did:key:owner" in body
        assert body.count("acl:Authorization") == 1
