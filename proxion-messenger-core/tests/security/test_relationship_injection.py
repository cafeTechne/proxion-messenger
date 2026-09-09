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


def _signed_cert(issuer_priv, subject_hex: str, cert_id: str = "cert-x", subject_priv=None):
    from proxion_messenger_core.federation import RelationshipCertificate, Capability
    issuer_hex = issuer_priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    cert = RelationshipCertificate(
        issuer=issuer_hex,
        subject=subject_hex,
        capabilities=[Capability(with_="stash://dm/", can="crud/write")],
        certificate_id=cert_id,
    )
    if subject_priv is not None:
        cert.attach_subject_consent(subject_priv)
    cert.sign(issuer_priv)
    return cert


# ---------------------------------------------------------------------------
# C2: pod handshake cert-receive subject check
# ---------------------------------------------------------------------------

class TestPodCertReceiveSubjectCheck:
    @pytest.mark.asyncio
    async def test_mutually_signed_cert_naming_owner_as_subject_is_saved(self, tmp_path):
        """A cert naming the owner as subject is saved only when it carries the
        owner's durable subject consent signature (R113)."""
        gw = _make_gateway(tmp_path)
        gw.broadcast = AsyncMock()
        owner_hex = gw.agent.identity_pub_bytes.hex()
        peer_priv = Ed25519PrivateKey.generate()
        cert = _signed_cert(
            peer_priv, owner_hex, cert_id="cert-inbound-ok",
            subject_priv=gw.agent.identity_key,
        )
        with patch("proxion_messenger_core.handshake.receive_acceptances", return_value=[]), \
             patch("proxion_messenger_core.handshake.receive_certificates",
                   return_value=[(cert, True)]), \
             patch.object(gw, "_sync_cert_to_pod", new=AsyncMock()):
            await gw._poll_handshake_completions()
        saved = gw._store.list_relationships()
        assert any(r.get("certificate_id") == "cert-inbound-ok" for r in saved)

    @pytest.mark.asyncio
    async def test_issuer_only_cert_naming_owner_as_subject_is_rejected(self, tmp_path):
        """An issuer-only cert naming the owner as subject (no owner consent) must
        be refused — the R113 residual the mutual signature closes."""
        gw = _make_gateway(tmp_path)
        gw.broadcast = AsyncMock()
        owner_hex = gw.agent.identity_pub_bytes.hex()
        attacker_priv = Ed25519PrivateKey.generate()
        cert = _signed_cert(attacker_priv, owner_hex, cert_id="cert-no-consent")
        with patch("proxion_messenger_core.handshake.receive_acceptances", return_value=[]), \
             patch("proxion_messenger_core.handshake.receive_certificates",
                   return_value=[(cert, True)]), \
             patch.object(gw, "_sync_cert_to_pod", new=AsyncMock()):
            await gw._poll_handshake_completions()
        saved = gw._store.list_relationships()
        assert not any(r.get("certificate_id") == "cert-no-consent" for r in saved)

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


# ---------------------------------------------------------------------------
# FIND-3: revoked-relationship resurrection via consent replay
# ---------------------------------------------------------------------------

class TestPodCertReceiveResurrection:
    def _active_ids(self, gw):
        return {r.get("certificate_id") for r in gw._store.list_relationships()}

    async def _ingest(self, gw, cert):
        with patch("proxion_messenger_core.handshake.receive_acceptances", return_value=[]), \
             patch("proxion_messenger_core.handshake.receive_certificates",
                   return_value=[(cert, True)]), \
             patch.object(gw, "_sync_cert_to_pod", new=AsyncMock()):
            await gw._poll_handshake_completions()

    @pytest.mark.asyncio
    async def test_revoked_cert_id_is_not_resurrected(self, tmp_path):
        """A cert whose id was revoked cannot be re-saved as active, even mutually
        signed: save_relationship is INSERT OR REPLACE, so the ingest must refuse
        a revoked id rather than clear the revoked flag."""
        gw = _make_gateway(tmp_path)
        gw.broadcast = AsyncMock()
        owner_hex = gw.agent.identity_pub_bytes.hex()
        peer_priv = Ed25519PrivateKey.generate()
        cert = _signed_cert(
            peer_priv, owner_hex, cert_id="cert-revoked-res",
            subject_priv=gw.agent.identity_key,
        )
        gw._store.save_relationship(cert.to_dict(), peer_did="did:key:peer")
        gw._store.revoke_relationship("cert-revoked-res")
        assert gw._store.relationship_is_revoked("cert-revoked-res")

        await self._ingest(gw, cert)

        assert "cert-revoked-res" not in self._active_ids(gw)
        assert gw._store.relationship_is_revoked("cert-revoked-res")

    @pytest.mark.asyncio
    async def test_legacy_consent_cert_rejected_at_network_ingest(self, tmp_path):
        """A legacy (pair-only) subject consent, though it satisfies verify_mutual's
        fallback, is refused for a network-delivered owner-as-subject cert so a
        retained pair signature cannot be replayed onto a fresh certificate."""
        from proxion_messenger_core.federation import (
            RelationshipCertificate, Capability, subject_consent_message,
        )
        from proxion_messenger_core.handshake import _ed25519_verify
        gw = _make_gateway(tmp_path)
        gw.broadcast = AsyncMock()
        owner_hex = gw.agent.identity_pub_bytes.hex()
        issuer_priv = Ed25519PrivateKey.generate()
        issuer_hex = issuer_priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
        cert = RelationshipCertificate(
            issuer=issuer_hex, subject=owner_hex,
            capabilities=[Capability(with_="stash://dm/", can="crud/write")],
            certificate_id="cert-legacy-inbound",
        )
        cert.subject_signature = gw.agent.identity_key.sign(
            subject_consent_message(issuer_hex, owner_hex)
        ).hex()
        cert.consent_version = None      # predates R115
        cert.sign(issuer_priv)
        # verify_mutual's fallback accepts it, so the rejection is the ingest guard.
        assert cert.verify_mutual(_ed25519_verify)

        await self._ingest(gw, cert)

        assert "cert-legacy-inbound" not in self._active_ids(gw)
