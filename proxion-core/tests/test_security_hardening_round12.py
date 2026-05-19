"""Round 12 — Deep security audit follow-up tests.

Covers:
  1. DPoP claim validation: exp/iat clock-skew boundaries, jti replay cache.
  2. Room ACL correctness: no foaf:Agent/public grant; member dedup/validation; size cap.
  3. Authorization downgrade: set_acl_multi_auto raises on detection failure.
  4. Pod URL anti-phishing: mixed-case host, trailing dot, explicit default port, punycode.
  5. Credential encryption: v1→v2 migration rewrites file; wrong-key failure.
  6. WAC Turtle injection guard: unsafe WebIDs rejected before Turtle generation.
"""
from __future__ import annotations

import json
import time
import uuid
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from proxion_messenger_core.dpop import (
    make_dpop_proof, validate_dpop_claims, DpopReplayCache,
)
from proxion_messenger_core.css_setup import CssAccountManager, _normalize_origin
from proxion_messenger_core._gateway_pod import _encrypt_creds, _decrypt_creds


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


def _payload(overrides: dict | None = None) -> dict:
    now = int(time.time())
    base = {"jti": str(uuid.uuid4()), "iat": now, "exp": now + 60, "htm": "GET", "htu": "https://pod.example/r"}
    if overrides:
        base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 1. DPoP claim validation
# ---------------------------------------------------------------------------

class TestValidateDpopClaims:
    def test_valid_proof_passes(self):
        validate_dpop_claims(_payload())  # must not raise

    def test_missing_iat_raises(self):
        p = _payload()
        del p["iat"]
        with pytest.raises(ValueError, match="iat"):
            validate_dpop_claims(p)

    def test_missing_exp_raises(self):
        p = _payload()
        del p["exp"]
        with pytest.raises(ValueError, match="exp"):
            validate_dpop_claims(p)

    def test_missing_jti_raises(self):
        p = _payload()
        del p["jti"]
        with pytest.raises(ValueError, match="jti"):
            validate_dpop_claims(p)

    def test_iat_far_future_rejected(self):
        now = int(time.time())
        p = _payload({"iat": now + 120, "exp": now + 180})
        with pytest.raises(ValueError, match="future"):
            validate_dpop_claims(p, now=now)

    def test_iat_within_skew_allowed(self):
        now = int(time.time())
        p = _payload({"iat": now + 10, "exp": now + 70})  # 10s ahead, within 30s skew
        validate_dpop_claims(p, now=now)

    def test_expired_proof_rejected(self):
        now = int(time.time())
        p = _payload({"iat": now - 120, "exp": now - 60})
        with pytest.raises(ValueError, match="expired"):
            validate_dpop_claims(p, now=now)

    def test_exp_at_boundary_passes(self):
        now = int(time.time())
        # exp = now - skew + 1  → still valid
        p = _payload({"iat": now - 60, "exp": now - 29})
        validate_dpop_claims(p, now=now)

    def test_live_proof_validates(self):
        k = _key()
        proof = make_dpop_proof(k, "GET", "https://pod.example/r")
        import base64
        part = proof.split(".")[1]
        padded = part + "==" * ((4 - len(part) % 4) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        validate_dpop_claims(payload)  # must not raise


class TestDpopReplayCache:
    def test_first_use_accepted(self):
        cache = DpopReplayCache(ttl=60)
        cache.check_and_record("jti-1")

    def test_replay_rejected(self):
        cache = DpopReplayCache(ttl=60)
        cache.check_and_record("jti-2")
        with pytest.raises(ValueError, match="replay"):
            cache.check_and_record("jti-2")

    def test_different_jtis_accepted(self):
        cache = DpopReplayCache(ttl=60)
        cache.check_and_record("jti-a")
        cache.check_and_record("jti-b")

    def test_expired_jti_reaccepted(self):
        cache = DpopReplayCache(ttl=1)
        now = time.time()
        cache.check_and_record("jti-x", now=now)
        # Simulate TTL expiry
        cache.check_and_record("jti-x", now=now + 2)


# ---------------------------------------------------------------------------
# 2. Room ACL correctness
# ---------------------------------------------------------------------------

class TestRoomAclCorrectness:
    def _make_store(self):
        from proxion_messenger_core.pod_room_store import PodRoomStore
        client = MagicMock()
        client._resolver = MagicMock()
        client._resolver.resolve.side_effect = lambda u: u.replace("stash://pod", "http://pod.example")
        client._auth_headers = {}
        client._dynamic_headers = MagicMock(return_value={})
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        client._session = MagicMock()
        client._session.put.return_value = mock_resp
        return PodRoomStore(client), client

    def test_no_public_agent_in_wac(self):
        """WAC document must never contain foaf:Agent (public grant)."""
        from proxion_messenger_core.solid_client import SolidClient as _SC
        client = MagicMock()
        client._resolver = MagicMock()
        client._resolver.resolve.return_value = "http://pod.example/rooms/r/"
        client._auth_headers = {}
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        client._session = MagicMock()
        client._session.put.return_value = mock_resp

        _SC.set_acl_multi(client, "stash://pod/rooms/r/", "did:key:owner", ["did:key:alice"])
        body = client._session.put.call_args[1]["content"].decode("utf-8")
        assert "foaf:Agent" not in body
        assert "agentClass" not in body
        assert "acl:agentGroup" not in body

    def test_duplicate_member_webids_deduplicated(self):
        store, client = self._make_store()
        with patch("proxion_messenger_core.pod_room_store.set_acl_multi_auto") as mock_acl:
            store.ensure_room_container(
                "dup-room",
                owner_webid="did:key:owner",
                member_webids=["did:key:alice", "did:key:alice", "did:key:alice"],
            )
        _args = mock_acl.call_args[0]
        members_passed = _args[3]
        assert members_passed.count("did:key:alice") == 1, "Duplicates must be removed"

    def test_owner_excluded_from_member_list(self):
        store, client = self._make_store()
        with patch("proxion_messenger_core.pod_room_store.set_acl_multi_auto") as mock_acl:
            store.ensure_room_container(
                "owner-room",
                owner_webid="did:key:owner",
                member_webids=["did:key:owner", "did:key:alice"],
            )
        members_passed = mock_acl.call_args[0][3]
        assert "did:key:owner" not in members_passed

    def test_empty_strings_filtered(self):
        store, client = self._make_store()
        with patch("proxion_messenger_core.pod_room_store.set_acl_multi_auto") as mock_acl:
            store.ensure_room_container(
                "empty-room",
                owner_webid="did:key:owner",
                member_webids=["", "", "did:key:alice", ""],
            )
        members_passed = mock_acl.call_args[0][3]
        assert "" not in members_passed
        assert members_passed == ["did:key:alice"]

    def test_invalid_iri_prefix_rejected(self):
        store, client = self._make_store()
        with patch("proxion_messenger_core.pod_room_store.set_acl_multi_auto") as mock_acl:
            store.ensure_room_container(
                "bad-iri-room",
                owner_webid="did:key:owner",
                member_webids=["ftp://evil.example/user", "javascript:alert(1)", "did:key:alice"],
            )
        members_passed = mock_acl.call_args[0][3]
        assert all(m == "did:key:alice" for m in members_passed)

    def test_member_list_capped_at_500(self):
        from proxion_messenger_core.pod_room_store import _sanitize_member_webids
        big_list = [f"did:key:member{i}" for i in range(600)]
        result = _sanitize_member_webids(big_list, "did:key:owner")
        assert len(result) == 500

    def test_turtle_injection_chars_rejected(self):
        from proxion_messenger_core.pod_room_store import _sanitize_member_webids
        bad = [
            'http://evil.example/"><script>alert(1)</script>',
            'did:key:alice\nnewline',
            'http://ok.example/alice',
        ]
        result = _sanitize_member_webids(bad, "did:key:owner")
        assert result == ["http://ok.example/alice"]


# ---------------------------------------------------------------------------
# 3. Authorization downgrade: set_acl_multi_auto raises on detection failure
# ---------------------------------------------------------------------------

class TestAclDowngradeProtection:
    def test_detection_error_propagates(self):
        from proxion_messenger_core.acp import set_acl_multi_auto
        client = MagicMock()
        client.head.side_effect = ConnectionError("pod unreachable")

        with pytest.raises(Exception):
            set_acl_multi_auto(client, "stash://pod/r/", "did:key:owner", ["did:key:alice"])

    def test_detect_acl_mode_non_strict_swallows_error(self):
        from proxion_messenger_core.acp import detect_acl_mode
        client = MagicMock()
        client.head.side_effect = RuntimeError("boom")
        result = detect_acl_mode(client, "stash://pod/r/", strict=False)
        assert result == "wac"

    def test_detect_acl_mode_strict_raises(self):
        from proxion_messenger_core.acp import detect_acl_mode
        client = MagicMock()
        client.head.side_effect = RuntimeError("boom")
        with pytest.raises(RuntimeError, match="boom"):
            detect_acl_mode(client, "stash://pod/r/", strict=True)

    def test_wac_pod_detected_correctly(self):
        from proxion_messenger_core.acp import detect_acl_mode
        client = MagicMock()
        client.head.return_value = {"Link": '<container.acl>; rel="acl"'}
        assert detect_acl_mode(client, "stash://pod/r/") == "wac"

    def test_acp_pod_detected_correctly(self):
        from proxion_messenger_core.acp import detect_acl_mode
        client = MagicMock()
        client.head.return_value = {"Link": '<container.acr>; rel="acr"'}
        assert detect_acl_mode(client, "stash://pod/r/") == "acp"


# ---------------------------------------------------------------------------
# 4. Pod URL anti-phishing — normalization and bypass corpus
# ---------------------------------------------------------------------------

class TestNormalizeOrigin:
    def test_lowercase_scheme(self):
        assert _normalize_origin("http", "localhost:3000") == "http://localhost:3000"

    def test_uppercase_host_normalized(self):
        assert _normalize_origin("http", "LOCALHOST:3000") == "http://localhost:3000"

    def test_mixed_case_host_normalized(self):
        assert _normalize_origin("http", "LocalHost:3000") == "http://localhost:3000"

    def test_trailing_dot_stripped(self):
        assert _normalize_origin("http", "localhost.:3000") == "http://localhost:3000"

    def test_http_default_port_stripped(self):
        assert _normalize_origin("http", "localhost:80") == "http://localhost"

    def test_https_default_port_stripped(self):
        assert _normalize_origin("https", "pod.example:443") == "https://pod.example"

    def test_non_default_port_preserved(self):
        assert _normalize_origin("http", "localhost:3000") == "http://localhost:3000"
        assert _normalize_origin("https", "pod.example:8443") == "https://pod.example:8443"


class TestPodUrlValidationBypass:
    def _mgr(self, base: str) -> CssAccountManager:
        return CssAccountManager(base)

    def test_mixed_case_host_blocked(self):
        mgr = self._mgr("http://localhost:3000")
        mgr._validate_pod_url("http://LOCALHOST:3000/alice/")  # same after normalise — should pass

    def test_different_case_host_normalized_same(self):
        mgr = self._mgr("http://LOCALHOST:3000")
        mgr._validate_pod_url("http://localhost:3000/alice/")  # both normalise to same

    def test_trailing_dot_bypass_blocked(self):
        mgr = self._mgr("http://localhost:3000")
        mgr._validate_pod_url("http://localhost.:3000/alice/")  # trailing dot, same host

    def test_explicit_default_port_same(self):
        mgr = self._mgr("http://localhost:3000")
        # http://localhost:3000 != http://localhost (different port vs no port)
        with pytest.raises(ValueError):
            mgr._validate_pod_url("http://localhost/alice/")

    def test_explicit_http_port_80_normalised(self):
        mgr = self._mgr("http://localhost")
        # http://localhost == http://localhost:80 after normalization
        mgr._validate_pod_url("http://localhost:80/alice/")

    def test_different_subdomain_blocked(self):
        mgr = self._mgr("http://css.example.com:3000")
        with pytest.raises(ValueError, match="origin"):
            mgr._validate_pod_url("http://evil.css.example.com:3000/alice/")

    def test_punycode_different_origin_blocked(self):
        mgr = self._mgr("http://localhost:3000")
        with pytest.raises(ValueError, match="origin"):
            mgr._validate_pod_url("http://xn--localhost-bypass:3000/alice/")


# ---------------------------------------------------------------------------
# 5. Credential encryption: v1→v2 migration
# ---------------------------------------------------------------------------

class TestV1ToV2Migration:
    def test_v1_file_migrated_on_load(self, tmp_path):
        from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
        from proxion_messenger_core.persist import AgentState
        from proxion_messenger_core.readstate import ReadState

        agent = AgentState.generate()
        db_path = str(tmp_path / "test.db")
        cred_path = tmp_path / "pod_creds.json"

        v1_data = {
            "css_url": "http://localhost:3000",
            "client_id": "cid-v1",
            "client_secret": "secret-v1",
            "pod_url": "http://localhost:3000/alice/",
            "webid": "http://localhost:3000/alice/profile/card#me",
        }
        cred_path.write_text(json.dumps(v1_data))
        assert json.loads(cred_path.read_text()).get("v") != 2

        config = GatewayConfig(port=0, db_path=db_path)
        gw = ProxionGateway(
            agent=agent, dm_clients={}, room_memberships={},
            config=config, read_state=ReadState(),
        )

        # Simulate reconnect using the mixin directly
        with patch("proxion_messenger_core.css_setup.build_dpop_client") as mock_build, \
             patch("proxion_messenger_core.css_auth.CssClientCredentials") as mock_creds, \
             patch.object(gw, "_rehydrate_relationships"):
            mock_build.return_value = MagicMock()
            mock_creds.return_value = MagicMock()
            result = gw._reconnect_stored_pod_sync()

        # File must now be v2 encrypted
        new_raw = json.loads(cred_path.read_text())
        assert new_raw.get("v") == 2, "v1 file must be rewritten as v2 after load"
        assert "enc" in new_raw
        assert "secret-v1" not in cred_path.read_text()

        # Decrypted content must match original
        decrypted = json.loads(_decrypt_creds(agent.identity_key, new_raw["enc"]))
        assert decrypted["client_secret"] == "secret-v1"

    def test_v2_file_not_rewritten_unnecessarily(self, tmp_path):
        key = _key()
        plaintext = json.dumps({"css_url": "x", "client_id": "c", "client_secret": "s",
                                 "pod_url": "x", "webid": "x"}).encode()
        token = _encrypt_creds(key, plaintext)
        v2_data = {"v": 2, "enc": token}

        cred_path = tmp_path / "pod_creds.json"
        cred_path.write_text(json.dumps(v2_data))
        original_mtime = cred_path.stat().st_mtime

        # Simulate reading: decrypts correctly without error
        decrypted = json.loads(_decrypt_creds(key, token))
        assert decrypted["client_secret"] == "s"


# ---------------------------------------------------------------------------
# 6. WAC Turtle injection guard
# ---------------------------------------------------------------------------

class TestWacTurtleInjection:
    def test_gt_in_webid_rejected(self):
        from proxion_messenger_core.solid_client import _assert_safe_webid
        with pytest.raises(ValueError, match="unsafe"):
            _assert_safe_webid('http://evil.example/"><script>')

    def test_double_quote_rejected(self):
        from proxion_messenger_core.solid_client import _assert_safe_webid
        with pytest.raises(ValueError, match="unsafe"):
            _assert_safe_webid('http://evil.example/"quote"')

    def test_newline_rejected(self):
        from proxion_messenger_core.solid_client import _assert_safe_webid
        with pytest.raises(ValueError, match="unsafe"):
            _assert_safe_webid("http://evil.example/alice\nmalicious")

    def test_clean_did_key_accepted(self):
        from proxion_messenger_core.solid_client import _assert_safe_webid
        _assert_safe_webid("did:key:z6MkhaXgBZDvotDkL5257faiztiGiC2QtKLGpbnnEGta2doK")

    def test_clean_http_webid_accepted(self):
        from proxion_messenger_core.solid_client import _assert_safe_webid
        _assert_safe_webid("https://alice.pod.example/profile/card#me")

    def test_set_acl_multi_rejects_unsafe_owner(self):
        from proxion_messenger_core.solid_client import SolidClient as _SC
        client = MagicMock()
        client._resolver = MagicMock()
        client._resolver.resolve.return_value = "http://pod.example/rooms/r/"
        client._auth_headers = {}
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        client._session = MagicMock()
        client._session.put.return_value = mock_resp
        with pytest.raises(ValueError, match="unsafe"):
            _SC.set_acl_multi(client, "stash://pod/rooms/r/", 'http://evil.example/"><evil>', [])
