"""Relationship-injection guards.

A stored relationships row IS the authorization (get_relationship_by_did grants
DM/reaction/file/voice on row presence), so every ingest path must verify owner
consent before writing one:

- C2: the pod handshake cert-receive must reject a cert that does not name this
  owner as subject.
- C3: /invite/accept must reject an acceptor whose DID is not the invited target.
"""
import json
import asyncio
import pytest
from unittest.mock import AsyncMock, patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


def _make_gateway(tmp_path):
    from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
    from proxion_messenger_core.persist import AgentState
    agent = AgentState.generate()
    config = GatewayConfig(db_path=str(tmp_path / "store.db"))
    return ProxionGateway(agent=agent, dm_clients={}, room_memberships={}, config=config)


def _signed_cert(issuer_priv, subject_hex: str, cert_id: str = "cert-x"):
    from proxion_messenger_core.federation import RelationshipCertificate, Capability
    issuer_hex = issuer_priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    cert = RelationshipCertificate(
        issuer=issuer_hex,
        subject=subject_hex,
        capabilities=[Capability(with_="stash://dm/", can="crud/write")],
        certificate_id=cert_id,
    )
    cert.sign(issuer_priv)
    return cert


# ---------------------------------------------------------------------------
# C2: pod handshake cert-receive subject check
# ---------------------------------------------------------------------------

class TestPodCertReceiveSubjectCheck:
    @pytest.mark.asyncio
    async def test_cert_naming_owner_as_subject_is_saved(self, tmp_path):
        gw = _make_gateway(tmp_path)
        gw.broadcast = AsyncMock()
        owner_hex = gw.agent.identity_pub_bytes.hex()
        peer_priv = Ed25519PrivateKey.generate()
        cert = _signed_cert(peer_priv, owner_hex, cert_id="cert-inbound-ok")
        with patch("proxion_messenger_core.handshake.receive_acceptances", return_value=[]), \
             patch("proxion_messenger_core.handshake.receive_certificates",
                   return_value=[(cert, True)]), \
             patch.object(gw, "_sync_cert_to_pod", new=AsyncMock()):
            await gw._poll_handshake_completions()
        saved = gw._store.list_relationships()
        assert any(r.get("certificate_id") == "cert-inbound-ok" for r in saved)

    @pytest.mark.asyncio
    async def test_cert_not_naming_owner_as_subject_is_rejected(self, tmp_path):
        gw = _make_gateway(tmp_path)
        gw.broadcast = AsyncMock()
        # Attacker seals a validly-signed cert into our mailbox naming a THIRD
        # party as subject (not us). It must not create a relationship row.
        attacker_priv = Ed25519PrivateKey.generate()
        third_party_priv = Ed25519PrivateKey.generate()
        third_hex = third_party_priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
        cert = _signed_cert(attacker_priv, third_hex, cert_id="cert-inbound-bad")
        with patch("proxion_messenger_core.handshake.receive_acceptances", return_value=[]), \
             patch("proxion_messenger_core.handshake.receive_certificates",
                   return_value=[(cert, True)]), \
             patch.object(gw, "_sync_cert_to_pod", new=AsyncMock()):
            await gw._poll_handshake_completions()
        saved = gw._store.list_relationships()
        assert not any(r.get("certificate_id") == "cert-inbound-bad" for r in saved)


# ---------------------------------------------------------------------------
# C3: /invite/accept must bind the acceptor to the invited target
# ---------------------------------------------------------------------------

class TestInviteAcceptTargetBind:
    def _pending(self, gw, invitation_id: str, target_did: str):
        gw._store.save_pending_invite(
            {"@type": "FederationInvite", "invitation_id": invitation_id,
             "issuer": {"public_key": gw.agent.identity_pub_bytes.hex(),
                        "did": gw._own_gateway_did()}},
            target_did,
        )

    def _acceptance_body(self, gw, invitation_id: str, acceptor_priv):
        acceptor_hex = acceptor_priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
        from proxion_messenger_core.didkey import pub_key_to_did
        acceptor_did = pub_key_to_did(
            acceptor_priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        )
        my_hex = gw.agent.identity_pub_bytes.hex()
        cert = _signed_cert(acceptor_priv, my_hex, cert_id="acc-cert")
        body = {
            "@type": "InviteAcceptance",
            "invitation_id": invitation_id,
            "certificate": cert.to_dict(),
            "from_did": acceptor_did,
            "from_pub_hex": acceptor_hex,
        }
        return acceptor_did, json.dumps(body).encode()

    @pytest.mark.asyncio
    async def test_wrong_acceptor_is_rejected(self, tmp_path):
        gw = _make_gateway(tmp_path)
        invited_priv = Ed25519PrivateKey.generate()
        from proxion_messenger_core.didkey import pub_key_to_did
        invited_did = pub_key_to_did(
            invited_priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        )
        self._pending(gw, "inv-1", invited_did)
        # A DIFFERENT identity tries to accept an invite meant for invited_did.
        wrong_priv = Ed25519PrivateKey.generate()
        _did, body = self._acceptance_body(gw, "inv-1", wrong_priv)
        status, _resp = await gw._handle_invite_accept_post(body)
        assert status.startswith("403")
        saved = gw._store.list_relationships()
        assert not any(r.get("certificate_id") == "acc-cert" for r in saved)

    @pytest.mark.asyncio
    async def test_correct_acceptor_is_accepted(self, tmp_path):
        gw = _make_gateway(tmp_path)
        gw.broadcast = AsyncMock()
        acceptor_priv = Ed25519PrivateKey.generate()
        acceptor_did, body = self._acceptance_body(gw, "inv-2", acceptor_priv)
        self._pending(gw, "inv-2", acceptor_did)
        with patch.object(gw, "_sync_cert_to_pod", new=AsyncMock()):
            status, _resp = await gw._handle_invite_accept_post(body)
        assert status.startswith("200")
