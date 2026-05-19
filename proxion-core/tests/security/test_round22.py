"""Round 22 security tests: SSRF closure across acp/profile/oidc/messaging,
receipt delivery uses safe helper, invite/accept cert signature verification."""
import json
import pytest
from unittest.mock import MagicMock, AsyncMock, patch, AsyncMock
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_gateway(tmp_path):
    from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
    from proxion_messenger_core.persist import AgentState
    agent = AgentState.generate()
    config = GatewayConfig(db_path=str(tmp_path / "store.db"))
    return ProxionGateway(agent=agent, dm_clients={}, room_memberships={}, config=config)


def _owner_did(gw):
    from proxion_messenger_core.didkey import pub_key_to_did
    return pub_key_to_did(gw.agent.identity_pub_bytes)


def _make_acceptor_cert(gw, acceptor_priv):
    """Create a cert signed by acceptor_priv with issuer=acceptor, subject=owner."""
    from proxion_messenger_core.federation import RelationshipCertificate, Capability
    acceptor_pub = acceptor_priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    owner_pub = gw.agent.identity_pub_bytes.hex()
    cert = RelationshipCertificate(
        issuer=acceptor_pub,
        subject=owner_pub,
        capabilities=[Capability(with_="stash://dm/", can="crud/write")],
    )
    cert.sign(acceptor_priv)
    return cert


def _save_invite(gw, invitation_id, acceptor_did):
    """Persist a fake pending invite into the store."""
    if gw._store:
        gw._store.save_pending_invite(
            {
                "invitation_id": invitation_id,
                "to_did": acceptor_did,
                "from_did": _owner_did(gw),
                "code": "code-test",
            },
            acceptor_did,
        )


# ---------------------------------------------------------------------------
# Finding 1: acp.detect_pod_type uses SSRF-safe head
# ---------------------------------------------------------------------------

class TestAcpUsesSSRFSafeHead:
    @pytest.mark.asyncio
    async def test_detect_pod_type_blocks_private_ip(self):
        """detect_pod_type must not probe private/loopback addresses."""
        from proxion_messenger_core.acp import detect_pod_type
        # 192.168.1.1 is a private IP — should return "unknown" without network I/O
        result = await detect_pod_type("http://192.168.1.1/")
        assert result == "unknown"

    @pytest.mark.asyncio
    async def test_detect_pod_type_does_not_call_raw_httpx(self):
        """detect_pod_type must not import httpx directly for network calls."""
        import httpx as _httpx
        from proxion_messenger_core.acp import detect_pod_type

        call_count = 0
        original_get = _httpx.AsyncClient

        class _SentinelClient:
            async def __aenter__(self):
                nonlocal call_count
                call_count += 1
                return self
            async def __aexit__(self, *a): pass
            async def head(self, *a, **kw):
                raise AssertionError("raw httpx.AsyncClient used in detect_pod_type")

        with patch.object(_httpx, "AsyncClient", _SentinelClient):
            # Will fail resolution → "unknown"; the point is no raw client is used
            result = await detect_pod_type("http://192.168.1.1/")

        assert result == "unknown"
        assert call_count == 0, "raw httpx.AsyncClient was invoked"


# ---------------------------------------------------------------------------
# Finding 2: profile.get_profile uses SSRF-safe get
# ---------------------------------------------------------------------------

class TestProfileUsesSSRFSafeGet:
    @pytest.mark.asyncio
    async def test_get_profile_blocks_private_ip(self):
        """get_profile must return a minimal profile for private-IP WebIDs."""
        from proxion_messenger_core.profile import get_profile
        result = await get_profile("http://10.0.0.1/profile#me")
        assert result.webid == "http://10.0.0.1/profile#me"
        assert result.name is None

    @pytest.mark.asyncio
    async def test_get_profile_does_not_call_raw_httpx(self):
        """get_profile must not use raw httpx.AsyncClient."""
        import httpx as _httpx
        from proxion_messenger_core.profile import get_profile

        class _SentinelClient:
            async def __aenter__(self):
                raise AssertionError("raw httpx.AsyncClient used in get_profile")
            async def __aexit__(self, *a): pass

        with patch.object(_httpx, "AsyncClient", _SentinelClient):
            result = await get_profile("http://10.0.0.1/profile#me")
        assert result.name is None


# ---------------------------------------------------------------------------
# Finding 3: oidc functions use SSRF-safe helpers
# ---------------------------------------------------------------------------

class TestOidcUsesSSRFSafeHelpers:
    @pytest.mark.asyncio
    async def test_fetch_oidc_config_raises_on_private_ip(self):
        """fetch_oidc_config must raise (NetworkError-wrapped) for private IPs."""
        from proxion_messenger_core.oidc import fetch_oidc_config
        with pytest.raises(Exception):
            await fetch_oidc_config("http://172.16.0.1")

    @pytest.mark.asyncio
    async def test_webid_to_issuer_returns_none_on_private_ip(self):
        """webid_to_issuer must return None for private-IP WebIDs."""
        from proxion_messenger_core.oidc import webid_to_issuer
        result = await webid_to_issuer("http://192.168.100.5/profile#me")
        assert result is None

    @pytest.mark.asyncio
    async def test_dynamic_register_raises_on_private_ip(self):
        """dynamic_register must raise for private-IP registration endpoints."""
        from proxion_messenger_core.oidc import dynamic_register
        with pytest.raises(Exception):
            await dynamic_register("http://10.20.30.40/register", ["http://127.0.0.1:8080/cb"])


# ---------------------------------------------------------------------------
# Finding 4: invite/accept cert signature verification
# ---------------------------------------------------------------------------

class TestInviteAcceptCertVerification:
    def _post_accept(self, gw, body_dict):
        import asyncio
        return asyncio.get_event_loop().run_until_complete(
            gw._handle_invite_accept_post(json.dumps(body_dict).encode())
        )

    def test_accept_with_valid_signed_cert_succeeds(self, tmp_path):
        """A properly signed acceptor cert passes and completes the handshake."""
        gw = _make_gateway(tmp_path)
        acceptor_priv = Ed25519PrivateKey.generate()
        acceptor_pub = acceptor_priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        from proxion_messenger_core.didkey import pub_key_to_did
        acceptor_did = pub_key_to_did(acceptor_pub)
        invitation_id = "inv-valid-01"
        _save_invite(gw, invitation_id, acceptor_did)

        cert = _make_acceptor_cert(gw, acceptor_priv)
        status, _ = self._post_accept(gw, {
            "@type": "InviteAcceptance",
            "invitation_id": invitation_id,
            "from_pub_hex": acceptor_pub.hex(),
            "from_did": acceptor_did,
            "certificate": cert.to_dict(),
        })
        assert status.startswith("200"), f"Expected 200, got {status}"

    def test_accept_with_tampered_cert_rejected(self, tmp_path):
        """A cert with an invalid signature is rejected with 400."""
        gw = _make_gateway(tmp_path)
        acceptor_priv = Ed25519PrivateKey.generate()
        acceptor_pub = acceptor_priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        from proxion_messenger_core.didkey import pub_key_to_did
        acceptor_did = pub_key_to_did(acceptor_pub)
        invitation_id = "inv-tampered-02"
        _save_invite(gw, invitation_id, acceptor_did)

        cert = _make_acceptor_cert(gw, acceptor_priv)
        cert_dict = cert.to_dict()
        # Tamper with the signature
        cert_dict["signature"] = "deadbeef" * 8

        status, body = self._post_accept(gw, {
            "@type": "InviteAcceptance",
            "invitation_id": invitation_id,
            "from_pub_hex": acceptor_pub.hex(),
            "from_did": acceptor_did,
            "certificate": cert_dict,
        })
        assert status.startswith("400"), f"Expected 400, got {status}"
        assert "signature" in body

    def test_accept_with_wrong_issuer_rejected(self, tmp_path):
        """A cert whose issuer doesn't match acceptor's pub key is rejected."""
        gw = _make_gateway(tmp_path)
        acceptor_priv = Ed25519PrivateKey.generate()
        acceptor_pub = acceptor_priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        other_priv = Ed25519PrivateKey.generate()
        other_pub = other_priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        from proxion_messenger_core.didkey import pub_key_to_did
        acceptor_did = pub_key_to_did(acceptor_pub)
        invitation_id = "inv-issuer-03"
        _save_invite(gw, invitation_id, acceptor_did)

        # Build cert where issuer is OTHER key, not acceptor
        from proxion_messenger_core.federation import RelationshipCertificate, Capability
        owner_pub = gw.agent.identity_pub_bytes.hex()
        cert = RelationshipCertificate(
            issuer=other_pub.hex(),  # wrong issuer
            subject=owner_pub,
            capabilities=[Capability(with_="stash://dm/", can="crud/write")],
        )
        cert.sign(other_priv)

        status, body = self._post_accept(gw, {
            "@type": "InviteAcceptance",
            "invitation_id": invitation_id,
            "from_pub_hex": acceptor_pub.hex(),
            "from_did": acceptor_did,
            "certificate": cert.to_dict(),
        })
        assert status.startswith("400"), f"Expected 400, got {status}"
        assert "issuer" in body

    def test_accept_without_cert_still_works(self, tmp_path):
        """Accepting without an acceptor cert (legacy flow) is still accepted."""
        gw = _make_gateway(tmp_path)
        acceptor_priv = Ed25519PrivateKey.generate()
        acceptor_pub = acceptor_priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        from proxion_messenger_core.didkey import pub_key_to_did
        acceptor_did = pub_key_to_did(acceptor_pub)
        invitation_id = "inv-nocert-04"
        _save_invite(gw, invitation_id, acceptor_did)

        status, _ = self._post_accept(gw, {
            "@type": "InviteAcceptance",
            "invitation_id": invitation_id,
            "from_pub_hex": acceptor_pub.hex(),
            "from_did": acceptor_did,
            # no "certificate" key
        })
        assert status.startswith("200"), f"Expected 200, got {status}"
