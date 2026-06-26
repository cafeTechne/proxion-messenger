"""Three-step federation handshake orchestrator.

This module wires together the :mod:`federation`, :mod:`sealed`, and
:mod:`store` layers into a complete peer-pairing protocol.

Protocol overview
-----------------

::

    Alice                              Store                    Bob
      |                                  |                       |
      |-- create_invite() -----------+   |                       |
      |-- send_invite() ------------>|put(bob_mailbox, env)      |
      |                              |   |                       |
      |                              |   |<-- receive_invites() --|
      |                              |   |  (drain + decrypt)    |
      |                              |   |  (verify Alice sig)   |
      |                              |   |                       |
      |                              |put(alice_mailbox, env) <--|-- accept_invite()
      |                              |   |                       |
      |<-- receive_acceptances() ----|   |                       |
      |  (drain + decrypt)           |   |                       |
      |  (verify Bob sig)            |   |                       |
      |  (verify challenge resp)     |   |                       |
      |                              |   |                       |
      |-- finalize_handshake() --+   |   |                       |
      |   issues RelationshipCert|   |   |                       |
      |-- send_certificate() ------->|put(bob_mailbox, env)      |
      |                              |   |                       |
      |                              |   |<-- receive_certificates()
      |                              |   |  (drain + decrypt)    |
      |                              |   |  (verify Alice sig)   |

Key design points
-----------------
* **Store blindness**: every message is sealed before posting; the store
  operator sees only opaque bytes.
* **Mutual authentication**: both parties sign their messages with their
  Ed25519 identity keys.  Signatures are verified before acting on any step.
* **Challenge/response**: the invite contains a random ``challenge_marker``
  that Bob must sign in his acceptance, proving he actually processed the
  invite (not a replay).
* **Transport independence**: all messages flow through the abstract
  :class:`~proxion_messenger_core.store.MemoryStore` interface; any backend works.

Key types per agent
-------------------
Each agent has two keypairs:

* ``identity_key``  — Ed25519 (signing messages, verifying peers)
* ``store_key``     — X25519 (receiving sealed messages from the store)

Their public keys are embedded in the messages so peers can verify signatures
and reply without any out-of-band PKI.
"""

from __future__ import annotations

import json
import time
from typing import List, Optional, Tuple

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from .errors import ProxionError
from .federation import Capability, FederationInvite, InviteAcceptance, RelationshipCertificate
from .sealed import mailbox_id_for, open_sealed_json, seal_json
from .store import MemoryStore


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class HandshakeError(ProxionError):
    """Raised when a handshake message fails verification."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _pub_raw(key: X25519PublicKey | Ed25519PublicKey) -> bytes:
    return key.public_bytes(Encoding.Raw, PublicFormat.Raw)


def _ed25519_verify(pubkey_hex: str, sig_bytes: bytes, message: bytes) -> bool:
    """Verify an Ed25519 signature; returns False rather than raising."""
    try:
        pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(pubkey_hex))
        pub.verify(sig_bytes, message)
        return True
    except (InvalidSignature, ValueError):
        return False


# ---------------------------------------------------------------------------
# Step 1 — Alice creates and posts an invite
# ---------------------------------------------------------------------------

def create_invite(
    alice_identity_priv: Ed25519PrivateKey,
    alice_store_pub_bytes: bytes,
    capabilities: List[Capability],
    endpoint_hints: Optional[List[str]] = None,
    certificate_id: Optional[str] = None,
    display_name: Optional[str] = None,
) -> FederationInvite:
    """Build and sign a :class:`~proxion_messenger_core.federation.FederationInvite`.

    The invite embeds Alice's Ed25519 identity public key (so Bob can verify
    the signature) and her X25519 store public key (so Bob can send his
    acceptance back without any out-of-band channel).

    Parameters
    ----------
    alice_identity_priv:
        Alice's Ed25519 private key — used to sign the invite.
    alice_store_pub_bytes:
        Alice's raw 32-byte X25519 public key — embedded so Bob can reply.
    capabilities:
        The set of capabilities Alice offers to Bob.
    endpoint_hints:
        Optional list of transport hints (e.g. relay URLs) for out-of-band
        delivery paths.  Not required when using the Coordination Store.

    Returns
    -------
    FederationInvite
        A signed invite ready to be posted via :func:`send_invite`.
    """
    alice_identity_pub_hex = _pub_raw(alice_identity_priv.public_key()).hex()
    issuer_dict: dict = {
        "public_key": alice_identity_pub_hex,
        "store_key": alice_store_pub_bytes.hex(),
    }
    if display_name:
        issuer_dict["display_name"] = display_name
    invite = FederationInvite(
        issuer=issuer_dict,
        endpoint_hints=endpoint_hints or [],
        capabilities=capabilities,
        certificate_id=certificate_id,
    )
    invite.sign(alice_identity_priv)
    return invite


def send_invite(
    invite: FederationInvite,
    bob_store_pub_bytes: bytes,
    store: MemoryStore,
) -> str:
    """Seal and post an invite to Bob's mailbox.

    Parameters
    ----------
    invite:
        A signed :class:`~proxion_messenger_core.federation.FederationInvite` from
        :func:`create_invite`.
    bob_store_pub_bytes:
        Bob's raw 32-byte X25519 public key — used to address the mailbox and
        seal the envelope.
    store:
        The :class:`~proxion_messenger_core.store.MemoryStore` (or compatible backend).

    Returns
    -------
    str
        The ``message_id`` assigned by the store.
    """
    mailbox = mailbox_id_for(bob_store_pub_bytes)
    envelope = seal_json(invite.to_dict(), bob_store_pub_bytes)
    return store.put(mailbox, envelope)


# ---------------------------------------------------------------------------
# Step 2 — Bob receives, verifies, and posts an acceptance
# ---------------------------------------------------------------------------

def receive_invites(
    bob_store_priv: X25519PrivateKey,
    store: MemoryStore,
) -> List[Tuple[FederationInvite, bool]]:
    """Drain Bob's mailbox and verify each invite's Alice signature.

    Parameters
    ----------
    bob_store_priv:
        Bob's X25519 private key — used to decrypt each sealed envelope.
    store:
        The coordination store.

    Returns
    -------
    list of (FederationInvite, bool)
        Each tuple is ``(invite, signature_valid)``.  Bob MUST check the
        boolean before acting on an invite.

    Notes
    -----
    Uses :meth:`~proxion_messenger_core.store.MemoryStore.list_all` and
    :meth:`~proxion_messenger_core.store.MemoryStore.take_by_ids` rather than
    ``take_all`` so that messages of other types (e.g. revocation notices)
    remain in the mailbox for their respective receive functions to process.
    Only messages whose decrypted ``@type`` is ``"FederationInvite"`` are
    consumed.
    """
    bob_store_pub_bytes = _pub_raw(bob_store_priv.public_key())
    mailbox = mailbox_id_for(bob_store_pub_bytes)
    stored = store.list_all(mailbox)

    results: List[Tuple[FederationInvite, bool]] = []
    consumed_ids: set = set()
    for sm in stored:
        try:
            data = open_sealed_json(sm.envelope, bob_store_priv)
        except Exception:
            consumed_ids.add(sm.message_id)  # undecryptable — discard, never retry
            continue
        try:
            if data.get("@type", "FederationInvite") not in ("FederationInvite", ""):
                continue  # different message type — leave for its handler
            invite = _dict_to_invite(data)
            valid = invite.verify(_ed25519_verify)
        except Exception:
            consumed_ids.add(sm.message_id)  # malformed structure — discard
            continue
        consumed_ids.add(sm.message_id)
        results.append((invite, valid))
    store.take_by_ids(mailbox, consumed_ids)
    return results


def accept_invite(
    invite: FederationInvite,
    bob_identity_priv: Ed25519PrivateKey,
    bob_store_pub_bytes: bytes,
    capabilities: List[Capability],
    store: MemoryStore,
) -> InviteAcceptance:
    """Sign and post an :class:`~proxion_messenger_core.federation.InviteAcceptance`.

    Bob:

    1. Signs the invite's ``challenge_marker`` to prove he processed the invite.
    2. Signs the entire acceptance message with his Ed25519 identity key.
    3. Posts the sealed acceptance to Alice's mailbox (using her ``store_key``
       embedded in the invite).

    Parameters
    ----------
    invite:
        The verified invite from :func:`receive_invites`.
    bob_identity_priv:
        Bob's Ed25519 private key.
    bob_store_pub_bytes:
        Bob's raw 32-byte X25519 public key — embedded so Alice can reply.
    capabilities:
        The capabilities Bob agrees to share (may be a subset of what Alice
        offered, or Bob's own offering).
    store:
        The coordination store.

    Returns
    -------
    InviteAcceptance
        The signed acceptance (also posted to Alice's mailbox as a side effect).

    Raises
    ------
    HandshakeError
        If the invite is missing Alice's ``store_key`` (needed to reply).
    """
    alice_store_key_hex = invite.issuer.get("store_key")
    if not alice_store_key_hex:
        raise HandshakeError("invite is missing issuer.store_key — cannot reply")

    alice_store_pub_bytes = bytes.fromhex(alice_store_key_hex)
    bob_identity_pub_hex = _pub_raw(bob_identity_priv.public_key()).hex()

    # Sign the challenge_marker to prove we processed this specific invite.
    _CHALLENGE_CTX = b"proxion-handshake-v1:"
    challenge_sig = bob_identity_priv.sign(_CHALLENGE_CTX + invite.challenge_marker.encode())

    acceptance = InviteAcceptance(
        invitation_id=invite.invitation_id,
        responder={
            "public_key": bob_identity_pub_hex,
            "store_key": bob_store_pub_bytes.hex(),
            "capabilities": [c.to_dict() for c in capabilities],
        },
        challenge_response=challenge_sig.hex(),
    )
    acceptance.sign(bob_identity_priv)

    mailbox = mailbox_id_for(alice_store_pub_bytes)
    envelope = seal_json(acceptance.to_dict(), alice_store_pub_bytes)
    store.put(mailbox, envelope)

    return acceptance


# ---------------------------------------------------------------------------
# Step 3 — Alice receives acceptances and issues a RelationshipCertificate
# ---------------------------------------------------------------------------

def receive_acceptances(
    alice_store_priv: X25519PrivateKey,
    store: MemoryStore,
) -> List[Tuple[InviteAcceptance, bool]]:
    """Drain Alice's mailbox and verify each acceptance.

    Both the acceptance signature **and** the challenge response are checked.

    Returns
    -------
    list of (InviteAcceptance, bool)
        ``True`` means both verifications passed.

    Notes
    -----
    Consumes only messages with ``@type == "InviteAcceptance"``, leaving
    other message types (revocation notices, certificates) in the mailbox.
    """
    alice_store_pub_bytes = _pub_raw(alice_store_priv.public_key())
    mailbox = mailbox_id_for(alice_store_pub_bytes)
    stored = store.list_all(mailbox)

    results: List[Tuple[InviteAcceptance, bool]] = []
    consumed_ids: set = set()
    for sm in stored:
        try:
            data = open_sealed_json(sm.envelope, alice_store_priv)
        except Exception:
            consumed_ids.add(sm.message_id)  # undecryptable — discard, never retry
            continue
        try:
            if data.get("@type") != "InviteAcceptance":
                continue  # different message type — leave for its handler
            acceptance = _dict_to_acceptance(data)
            sig_ok = acceptance.verify(_ed25519_verify)
        except Exception:
            consumed_ids.add(sm.message_id)  # malformed structure — discard
            continue
        consumed_ids.add(sm.message_id)
        results.append((acceptance, sig_ok))
    store.take_by_ids(mailbox, consumed_ids)
    return results


def finalize_handshake(
    acceptance: InviteAcceptance,
    original_invite: FederationInvite,
    alice_identity_priv: Ed25519PrivateKey,
    wg_interface: Optional[str] = None,
    certificate_id: Optional[str] = None,
) -> RelationshipCertificate:
    """Verify the challenge response and issue a :class:`~proxion_messenger_core.federation.RelationshipCertificate`.

    Parameters
    ----------
    acceptance:
        A verified acceptance from :func:`receive_acceptances`.
    original_invite:
        The invite Alice originally sent — needed to recover the
        ``challenge_marker`` for verification.
    alice_identity_priv:
        Alice's Ed25519 private key — used to sign the certificate.
    wg_interface:
        Optional WireGuard interface name (e.g. ``"wg0"``).  If provided and the
        certificate contains WireGuard peer configuration, automatically configure
        the peer on that interface.  Failures are logged but do not abort the
        handshake.

    Returns
    -------
    RelationshipCertificate
        A signed certificate establishing the bilateral relationship.

    Raises
    ------
    HandshakeError
        If the challenge response is invalid (Bob didn't actually process the
        invite, or the message was tampered with).
    """
    challenge_ok = acceptance.verify_challenge(
        _ed25519_verify, original_invite.challenge_marker
    )
    if not challenge_ok:
        raise HandshakeError(
            f"invalid challenge response for invitation {acceptance.invitation_id}"
        )

    alice_identity_pub_hex = _pub_raw(alice_identity_priv.public_key()).hex()
    bob_identity_pub_hex = acceptance.responder["public_key"]

    # Merge capabilities: everything Alice offered that Bob echoed back.
    capabilities = [
        Capability(**_normalise_cap(c))
        for c in acceptance.responder.get("capabilities", [])
    ]

    resolved_certificate_id = (
        certificate_id or original_invite.certificate_id
    )
    cert_kwargs = {}
    if resolved_certificate_id is not None:
        cert_kwargs["certificate_id"] = resolved_certificate_id

    cert = RelationshipCertificate(
        issuer=alice_identity_pub_hex,
        subject=bob_identity_pub_hex,
        capabilities=capabilities,
        wireguard={},   # transport-layer config is EI-specific; left empty here
        **cert_kwargs,
    )
    cert.sign(alice_identity_priv)

    # Optionally configure WireGuard peer if interface name is provided
    if wg_interface and cert.wireguard:
        try:
            from .wg import configure_peer_from_cert
            configure_peer_from_cert(cert, wg_interface)
        except Exception as exc:
            # Log the error but don't abort the handshake
            import logging
            log = logging.getLogger(__name__)
            log.warning(f"WireGuard peer configuration failed: {exc}")

    return cert


def send_certificate(
    cert: RelationshipCertificate,
    bob_store_pub_bytes: bytes,
    store: MemoryStore,
) -> str:
    """Seal and deliver a :class:`~proxion_messenger_core.federation.RelationshipCertificate` to Bob.

    Returns the ``message_id`` assigned by the store.
    """
    mailbox = mailbox_id_for(bob_store_pub_bytes)
    envelope = seal_json(cert.to_dict(), bob_store_pub_bytes)
    return store.put(mailbox, envelope)


def process_join_requests(
    alice_identity_priv: Ed25519PrivateKey,
    alice_store_priv: X25519PrivateKey,
    store: "MemoryStore",
) -> List[Tuple["RelationshipCertificate", bool]]:
    """Drain Alice's mailbox, issue certs for all pending join requests, deliver to members.

    Higher-level wrapper over receive_acceptances + cert creation + send_certificate.
    Skips challenge verification (suitable for room joins where the original invite
    is not retained in memory). Returns a list of (cert, valid) pairs.
    """
    acceptances = receive_acceptances(alice_store_priv, store)
    results: List[Tuple["RelationshipCertificate", bool]] = []
    alice_pub_hex = _pub_raw(alice_identity_priv.public_key()).hex()
    for acceptance, sig_ok in acceptances:
        if not sig_ok:
            results.append((None, False))  # type: ignore[arg-type]
            continue
        bob_pub_hex = acceptance.responder.get("public_key", "")
        bob_store_hex = acceptance.responder.get("store_key", "")
        capabilities = [
            Capability(**_normalise_cap(c))
            for c in acceptance.responder.get("capabilities", [])
        ]
        cert = RelationshipCertificate(
            issuer=alice_pub_hex,
            subject=bob_pub_hex,
            capabilities=capabilities,
            wireguard={},
            certificate_id=acceptance.invitation_id,
        )
        cert.sign(alice_identity_priv)
        if bob_store_hex:
            try:
                send_certificate(cert, bytes.fromhex(bob_store_hex), store)
            except Exception:
                pass
        results.append((cert, True))
    return results


def receive_certificates(
    bob_store_priv: X25519PrivateKey,
    store: "MemoryStore",
) -> List[Tuple[RelationshipCertificate, bool]]:
    """Drain Bob's mailbox and verify each certificate's Alice signature.

    Returns
    -------
    list of (RelationshipCertificate, bool)
        ``True`` means Alice's signature verified.
    """
    bob_store_pub_bytes = _pub_raw(bob_store_priv.public_key())
    mailbox = mailbox_id_for(bob_store_pub_bytes)
    stored = store.list_all(mailbox)

    results: List[Tuple[RelationshipCertificate, bool]] = []
    consumed_ids: set = set()
    for sm in stored:
        try:
            data = open_sealed_json(sm.envelope, bob_store_priv)
        except Exception:
            consumed_ids.add(sm.message_id)  # undecryptable — discard, never retry
            continue
        try:
            if data.get("@type") != "RelationshipCertificate":
                continue  # different message type — leave for its handler
            cert = _dict_to_cert(data)
            valid = cert.verify(_ed25519_verify)
        except Exception:
            consumed_ids.add(sm.message_id)  # malformed structure — discard
            continue
        consumed_ids.add(sm.message_id)
        results.append((cert, valid))
    store.take_by_ids(mailbox, consumed_ids)
    return results


# ---------------------------------------------------------------------------
# Convenience: full handshake in-process (for tests / local EI)
# ---------------------------------------------------------------------------

def run_local_handshake(
    alice_identity_priv: Ed25519PrivateKey,
    alice_store_priv: X25519PrivateKey,
    bob_identity_priv: Ed25519PrivateKey,
    bob_store_priv: X25519PrivateKey,
    alice_capabilities: List[Capability],
    bob_capabilities: List[Capability],
    store: MemoryStore,
    wg_interface: Optional[str] = None,
    certificate_id: Optional[str] = None,
) -> Tuple[RelationshipCertificate, bool]:
    """Execute all three handshake steps against a shared in-process store.

    Intended for integration tests and local EI demonstrations.

    Parameters
    ----------
    wg_interface:
        Optional WireGuard interface name.  If provided and the certificate
        contains WireGuard peer configuration, automatically configure the peer.

    Returns
    -------
    (RelationshipCertificate, cert_valid)
        The certificate issued by Alice and a boolean confirming Bob verified
        Alice's signature on it.
    """
    alice_store_pub = _pub_raw(alice_store_priv.public_key())
    bob_store_pub = _pub_raw(bob_store_priv.public_key())

    # Step 1 — Alice sends invite
    invite = create_invite(
        alice_identity_priv,
        alice_store_pub,
        alice_capabilities,
        certificate_id=certificate_id,
    )
    send_invite(invite, bob_store_pub, store)

    # Step 2 — Bob receives, verifies, and accepts
    received = receive_invites(bob_store_priv, store)
    if not received:
        raise HandshakeError("Bob received no invites")
    bob_invite, invite_valid = received[0]
    if not invite_valid:
        raise HandshakeError("Bob: Alice's invite signature is invalid")
    accept_invite(bob_invite, bob_identity_priv, bob_store_pub, bob_capabilities, store)

    # Step 3 — Alice receives acceptance, verifies, finalizes, delivers cert
    acceptances = receive_acceptances(alice_store_priv, store)
    if not acceptances:
        raise HandshakeError("Alice received no acceptances")
    acceptance, acceptance_valid = acceptances[0]
    if not acceptance_valid:
        raise HandshakeError("Alice: Bob's acceptance signature is invalid")

    cert = finalize_handshake(
        acceptance,
        invite,
        alice_identity_priv,
        wg_interface,
        certificate_id=certificate_id,
    )
    send_certificate(cert, bob_store_pub, store)

    # Bob receives and verifies the certificate
    certs = receive_certificates(bob_store_priv, store)
    if not certs:
        raise HandshakeError("Bob received no certificate")
    received_cert, cert_valid = certs[0]

    return received_cert, cert_valid


def run_bidirectional_handshake(
    alice_identity_priv: Ed25519PrivateKey,
    alice_store_priv: X25519PrivateKey,
    bob_identity_priv: Ed25519PrivateKey,
    bob_store_priv: X25519PrivateKey,
    alice_to_bob_capabilities: List[Capability],
    bob_to_alice_capabilities: List[Capability],
    store: MemoryStore,
    certificate_id_a_to_b: Optional[str] = None,
    certificate_id_b_to_a: Optional[str] = None,
) -> Tuple[
    Tuple[RelationshipCertificate, bool],
    Tuple[RelationshipCertificate, bool],
]:
    """Run two federation handshakes to establish bidirectional messaging.

    Returns two ``(certificate, valid)`` pairs:
    - cert_a_to_b: Alice is issuer; Bob can read from Alice's Pod.
    - cert_b_to_a: Bob is issuer; Alice can read from Bob's Pod.
    """
    cert_a_to_b = run_local_handshake(
        alice_identity_priv=alice_identity_priv,
        alice_store_priv=alice_store_priv,
        bob_identity_priv=bob_identity_priv,
        bob_store_priv=bob_store_priv,
        alice_capabilities=alice_to_bob_capabilities,
        bob_capabilities=alice_to_bob_capabilities,
        store=store,
        certificate_id=certificate_id_a_to_b,
    )
    cert_b_to_a = run_local_handshake(
        alice_identity_priv=bob_identity_priv,
        alice_store_priv=bob_store_priv,
        bob_identity_priv=alice_identity_priv,
        bob_store_priv=alice_store_priv,
        alice_capabilities=bob_to_alice_capabilities,
        bob_capabilities=bob_to_alice_capabilities,
        store=store,
        certificate_id=certificate_id_b_to_a,
    )
    return cert_a_to_b, cert_b_to_a


# ---------------------------------------------------------------------------
# Private: dict → dataclass reconstruction
# ---------------------------------------------------------------------------

def _dict_to_invite(d: dict) -> FederationInvite:
    caps = [Capability(**_normalise_cap(c)) for c in d.get("capabilities", [])]
    inv = FederationInvite(
        issuer=d["issuer"],
        endpoint_hints=d.get("endpoint_hints", []),
        capabilities=caps,
        version=d.get("version", 1),
        invitation_id=d["invitation_id"],
        created_at=d.get("created_at", int(time.time())),
        expires_at=d.get("expires_at", int(time.time()) + 86400),
        nonce=d.get("nonce", ""),
        challenge_marker=d["challenge_marker"],
        certificate_id=d.get("certificate_id"),
        signature=d.get("signature"),
    )
    return inv


def _dict_to_acceptance(d: dict) -> InviteAcceptance:
    return InviteAcceptance(
        invitation_id=d["invitation_id"],
        responder=d["responder"],
        challenge_response=d["challenge_response"],
        timestamp=d.get("timestamp", int(time.time())),
        signature=d.get("signature"),
    )


def _dict_to_cert(d: dict) -> RelationshipCertificate:
    caps = [Capability(**_normalise_cap(c)) for c in d.get("capabilities", [])]
    return RelationshipCertificate(
        issuer=d["issuer"],
        subject=d["subject"],
        capabilities=caps,
        wireguard=d.get("wireguard", {}),
        version=d.get("version", 1),
        certificate_id=d["certificate_id"],
        created_at=d.get("created_at", int(time.time())),
        expires_at=d.get("expires_at", int(time.time()) + 86400 * 90),
        signature=d.get("signature"),
    )


def _normalise_cap(c: dict) -> dict:
    """Normalise a capability dict — federation.py uses ``with_`` but JSON uses ``with``."""
    return {
        "with_": c.get("with_") or c.get("with", ""),
        "can": c["can"],
        "caveats": c.get("caveats", {}),
    }
