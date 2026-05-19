"""Federated messaging layer for the Proxion protocol.

Two :class:`~proxion_messenger_core.persist.AgentState` instances exchange signed
messages via their Solid Pods, gated by capability tokens derived from a
shared :class:`~proxion_messenger_core.federation.RelationshipCertificate`.

Design
------
Messages live on the **sender's** Pod at a deterministic path derived from
the relationship cert ID::

    stash://messages/thread/{cert_id}/{message_id}.json

The recipient reads those messages using a capability token that covers the
``read`` action on ``stash://messages/thread/{cert_id}/``.  No central server
is required — each party polls the other's Pod directly.

Spec gaps surfaced by this module
----------------------------------
- **J-006** — ``stash://`` URIs are resolver-local, not globally unique.
  ``receive()`` works around this by reconstructing the stash path from the
  filename embedded in the absolute HTTP URI returned by ``list()``.  A real
  app would need a stable URL-to-stash mapping layer or a globally scoped URI
  scheme.

- **J-007** — Cert capabilities cannot reference the cert ID at invite time,
  because the cert ID is generated *during* the handshake.  The capability
  resource must use a stable prefix (e.g. ``stash://messages/``) rather than
  ``stash://messages/thread/{cert_id}/``.

- **J-008** — :class:`~proxion_messenger_core.solid_auth.AuthenticatedSolidClient`
  defaults to ``aud=""``, but tokens issued via
  :func:`~proxion_messenger_core.certtoken.issue_from_certificate` set ``aud`` to the
  issuer's identity pub hex.  These will never match; callers must pass
  ``aud=cert.issuer`` explicitly when constructing
  ``AuthenticatedSolidClient``.

- **J-009** — :class:`~proxion_messenger_core.solid_client.SolidClient` makes
  unauthenticated HTTP requests.  The Pod server must permit unauthenticated
  reads on the thread container, or ``receive()`` will return an empty list.
"""

from __future__ import annotations

import datetime
import json
import secrets
from dataclasses import dataclass
from typing import Callable, Optional

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from .attenuation import derive_token
from .certtoken import issue_from_certificate
from .federation import RelationshipCertificate
from .solid_client import SolidClient, SolidError
from .tokens import Token


# ---------------------------------------------------------------------------
# Message dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Message:
    """A signed message in a federated conversation thread.

    Parameters
    ----------
    message_id:
        Unique random identifier (URL-safe base64, 16 bytes).
    cert_id:
        The :attr:`~proxion_messenger_core.federation.RelationshipCertificate.certificate_id`
        of the relationship this message belongs to.
    from_pub_hex:
        Hex-encoded Ed25519 public key of the sender.
    content:
        Plaintext message body.
    timestamp:
        Unix epoch seconds (UTC) at time of composition.
    signature:
        Hex-encoded Ed25519 signature over :meth:`canonical_bytes`.
        Empty string if not yet signed (internal use only).
    """

    message_id: str
    cert_id: str
    from_pub_hex: str
    content: str
    timestamp: int
    signature: str
    reply_to_id: Optional[str] = None
    message_type: str = "text"
    in_reply_to: str = ""  # legacy
    seq_num: int = 0  # monotonic per-thread sequence; 0 = legacy/unset
    prev_hash: str = ""  # SHA-256 of previous message's canonical_bytes; "" = genesis

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        d = {
            "message_id": self.message_id,
            "cert_id": self.cert_id,
            "from_pub_hex": self.from_pub_hex,
            "content": self.content,
            "timestamp": self.timestamp,
            "reply_to_id": self.reply_to_id,
            "message_type": self.message_type,
            "signature": self.signature,
        }
        if self.seq_num:
            d["seq_num"] = self.seq_num
        if self.prev_hash:
            d["prev_hash"] = self.prev_hash
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Message":
        reply_id = d.get("reply_to_id") or d.get("in_reply_to") or None
        return cls(
            message_id=d["message_id"],
            cert_id=d["cert_id"],
            from_pub_hex=d["from_pub_hex"],
            content=d["content"],
            timestamp=int(d["timestamp"]),
            reply_to_id=reply_id,
            message_type=d.get("message_type") or "text",
            signature=d["signature"],
            seq_num=int(d.get("seq_num") or 0),
            prev_hash=d.get("prev_hash") or "",
        )

    # ------------------------------------------------------------------
    # Signing
    # ------------------------------------------------------------------

    def canonical_bytes(self) -> bytes:
        """Deterministic bytes that the sender signs.

        Includes ``prev_hash`` so the chain is cryptographically bound.
        Excludes ``signature`` and ``seq_num`` (seq_num is monotonic metadata,
        not part of the authenticated payload).
        """
        payload = {
            "message_id": self.message_id,
            "cert_id": self.cert_id,
            "from_pub_hex": self.from_pub_hex,
            "content": self.content,
            "timestamp": self.timestamp,
            "reply_to_id": self.reply_to_id,
            "message_type": self.message_type,
            "prev_hash": self.prev_hash,
        }
        return json.dumps(payload, sort_keys=True).encode("utf-8")

    def _legacy_canonical_bytes(self) -> bytes:
        """Canonical bytes without prev_hash — used to verify pre-Round-14 signatures."""
        payload = {
            "message_id": self.message_id,
            "cert_id": self.cert_id,
            "from_pub_hex": self.from_pub_hex,
            "content": self.content,
            "timestamp": self.timestamp,
            "reply_to_id": self.reply_to_id,
            "message_type": self.message_type,
        }
        return json.dumps(payload, sort_keys=True).encode("utf-8")

    def verify(self, sender_pub_bytes: bytes) -> bool:
        """Return True if the signature is valid for *sender_pub_bytes*.

        Tries current canonical bytes first; falls back to legacy format (without
        prev_hash) so messages signed before Round 14 remain verifiable.
        """
        if not self.signature:
            return False
        try:
            pub: Ed25519PublicKey = Ed25519PublicKey.from_public_bytes(sender_pub_bytes)
            sig_bytes = bytes.fromhex(self.signature)
            try:
                pub.verify(sig_bytes, self.canonical_bytes())
                return True
            except Exception:
                # Backward compat: try legacy format (no prev_hash in payload)
                pub.verify(sig_bytes, self._legacy_canonical_bytes())
                return True
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def thread_path(cert_id: str) -> str:
    """Canonical ``stash://`` container path for a conversation thread.

    NOTE [J-007]: This uses a fixed ``stash://messages/`` prefix rather than
    a cert-scoped prefix because the cert ID is not known at invite time.
    The capability resource in the cert must cover ``stash://messages/`` (or a
    sub-path of it) to satisfy :func:`receive`.
    """
    return f"stash://messages/thread/{cert_id}/"


def message_path(cert_id: str, message_id: str) -> str:
    """Canonical ``stash://`` path for a single message file."""
    return f"stash://messages/thread/{cert_id}/{message_id}.json"


def narrow_to_thread(
    cert: RelationshipCertificate,
    holder_state: "AgentState",
    signing_key: bytes,
    now: Optional[datetime.datetime] = None,
    ttl_seconds: int = 300,
) -> Token:
    """Mint a capability token scoped to exactly this thread's container.

    Closes J-007: the cert must grant ``read`` on ``stash://messages/``
    (or a covering prefix) because the cert ID is not known at invite time.
    This function derives a narrower token restricted to the specific thread
    path, preserving least-authority for the actual read operation.
    """
    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc)

    container = thread_path(cert.certificate_id)

    broad_token = issue_from_certificate(
        cert=cert,
        requested_permissions=[("read", container)],
        holder_pub_key=holder_state.identity_key.public_key(),
        signing_key=signing_key,
        ttl_seconds=ttl_seconds,
        now=now,
    )

    return derive_token(
        parent=broad_token,
        narrower_perms=[("read", container)],
        extra_caveats=[],
        signing_key=signing_key,
        now=now,
    )


def make_pod_receipt_writer(
    pod_client: SolidClient,
    agent_pub_hex: str,
    receipt_container: str = "stash://receipts/",
) -> Callable[["Token", "RequestContext", "Decision"], None]:
    """Return a receipt writer callback that writes JSON-LD access receipts."""
    import time as _time

    def _writer(token: "Token", ctx: "RequestContext", decision: "Decision") -> None:
        if not decision.allowed:
            return
        ts_ms = int(_time.time() * 1000)
        filename = f"{token.token_id[:8]}-{ts_ms}.json"
        path = receipt_container.rstrip("/") + "/" + filename
        receipt = {
            "@context": "https://proxion.protocol/ontology/v1#",
            "@type": "AccessReceipt",
            "token_id": token.token_id,
            "agent": agent_pub_hex,
            "action": ctx.action,
            "resource": ctx.resource,
            "timestamp": ctx.now.isoformat(),
            "allowed": decision.allowed,
        }
        data = json.dumps(receipt, indent=2).encode("utf-8")
        try:
            pod_client.put(path, data, content_type="application/ld+json")
        except Exception:
            pass

    return _writer


def renew_thread_token(
    old_token: "Token",
    cert: RelationshipCertificate,
    holder_state: "AgentState",
    signing_key: bytes,
    validator_url: str,
    ttl_seconds: int = 300,
    now: Optional[datetime.datetime] = None,
) -> "Token":
    """Renew a nearly-expired thread token via POST /token/renew."""
    import secrets as _secrets
    from .pop import sign_challenge
    from .validator_server import token_from_wire, token_to_wire
    from .network import safe_post as _safe_post, NetworkError as _NE

    _ = signing_key
    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc)

    nonce = _secrets.token_hex(16)
    proof = sign_challenge(holder_state.identity_key, old_token.token_id, nonce)
    body = {
        "token": token_to_wire(old_token),
        "proof": {
            "public_key_bytes": proof.public_key_bytes.hex(),
            "nonce": proof.nonce,
            "signature": proof.signature.hex(),
        },
        "context": {
            "action": "read",
            "resource": thread_path(cert.certificate_id),
            "aud": cert.issuer,
            "device_nonce": nonce,
            "now": now.isoformat(),
        },
        "ttl_seconds": ttl_seconds,
    }

    try:
        raw = _safe_post(f"{validator_url.rstrip('/')}/token/renew", body, timeout=10)
    except _NE as exc:
        raise RuntimeError(f"renewal failed: {exc}") from exc
    return token_from_wire(json.loads(raw))


# ---------------------------------------------------------------------------
# Compose (pure, no I/O)
# ---------------------------------------------------------------------------

def compose(
    identity_key: Ed25519PrivateKey,
    cert: RelationshipCertificate,
    content: str,
    now: Optional[datetime.datetime] = None,
    reply_to_id: Optional[str] = None,
    message_type: str = "text",
    encrypt: bool = False,
    prev_msg: Optional["Message"] = None,
) -> Message:
    """Create and sign a message.  No I/O — call :func:`send` to persist it.

    Parameters
    ----------
    identity_key:
        Sender's Ed25519 private key (from
        :attr:`~proxion_messenger_core.persist.AgentState.identity_key`).
    cert:
        The :class:`~proxion_messenger_core.federation.RelationshipCertificate` for the
        conversation.  Used to bind the message to this relationship.
    content:
        Plaintext message body.
    now:
        Timestamp to use (defaults to ``datetime.now(UTC)``).
    in_reply_to:
        Optional message_id of the parent message (empty string = top-level).
    encrypt:
        If True, encrypt content with AES-256-GCM before composing (default: False).
    """
    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc)

    if encrypt:
        from .msgcrypto import derive_message_key, encrypt_message
        key = derive_message_key(cert)
        content = encrypt_message(content, key)

    import hashlib as _hashlib
    prev_hash = _hashlib.sha256(prev_msg.canonical_bytes()).hexdigest() if prev_msg else ""

    pub_bytes = identity_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)

    unsigned = Message(
        message_id=secrets.token_urlsafe(16),
        cert_id=cert.certificate_id,
        from_pub_hex=pub_bytes.hex(),
        content=content,
        timestamp=int(now.timestamp()),
        reply_to_id=reply_to_id,
        prev_hash=prev_hash,
        message_type=message_type,
        signature="",
    )
    sig = identity_key.sign(unsigned.canonical_bytes())
    return Message(
        message_id=unsigned.message_id,
        cert_id=unsigned.cert_id,
        from_pub_hex=unsigned.from_pub_hex,
        content=unsigned.content,
        timestamp=unsigned.timestamp,
        reply_to_id=unsigned.reply_to_id,
        message_type=unsigned.message_type,
        prev_hash=unsigned.prev_hash,
        signature=sig.hex(),
    )


def edit_message(
    identity_key: Ed25519PrivateKey,
    cert: RelationshipCertificate,
    original_message_id: str,
    new_content: str,
    encrypt: bool = False,
    now: Optional[datetime.datetime] = None,
) -> Message:
    """Compose an edit record: message_type='edit', reply_to_id=original_message_id."""
    return compose(
        identity_key=identity_key,
        cert=cert,
        content=new_content,
        now=now,
        reply_to_id=original_message_id,
        message_type="edit",
        encrypt=encrypt,
    )


def compose_reaction(
    identity_key: Ed25519PrivateKey,
    cert: RelationshipCertificate,
    emoji: str,
    target_message_id: str,
    prev_msg: Optional["Message"] = None,
    now: Optional[datetime.datetime] = None,
) -> Message:
    """Compose a signed reaction message (message_type='reaction').

    The reaction is cryptographically bound to *target_message_id* via
    ``reply_to_id`` and chained into the Merkle history via *prev_msg*.
    """
    return compose(
        identity_key=identity_key,
        cert=cert,
        content=emoji,
        now=now,
        reply_to_id=target_message_id,
        message_type="reaction",
        prev_msg=prev_msg,
    )


MAX_FORWARD_DEPTH = 5  # maximum allowed nesting depth for forwarded messages


def _forward_depth(msg: "Message", _current: int = 0) -> int:
    """Return the nesting depth of a forward chain.

    Short-circuits at MAX_FORWARD_DEPTH + 1 to avoid parsing arbitrarily deep JSON.
    """
    if msg.message_type != "forward":
        return _current
    if _current >= MAX_FORWARD_DEPTH:
        return _current + 1  # exceeded without further parsing
    try:
        nested = json.loads(msg.content)
        orig = Message.from_dict(nested["original"])
    except Exception:
        return _current + 1
    return _forward_depth(orig, _current + 1)


def compose_forward(
    identity_key: Ed25519PrivateKey,
    cert: RelationshipCertificate,
    original_msg: "Message",
    prev_msg: Optional["Message"] = None,
    now: Optional[datetime.datetime] = None,
) -> Message:
    """Compose a verifiable forward message (message_type='forward').

    The original message's full signed payload is embedded in ``content`` as
    a JSON object under the key ``"original"``.  Use :func:`verify_forward`
    to authenticate both the forwarder and the original author.
    """
    nested = json.dumps({"original": original_msg.to_dict()}, separators=(",", ":"))
    return compose(
        identity_key=identity_key,
        cert=cert,
        content=nested,
        now=now,
        message_type="forward",
        prev_msg=prev_msg,
    )


def verify_forward(
    fwd_msg: "Message",
    forwarder_pub_bytes: bytes,
    original_pub_bytes: bytes,
) -> bool:
    """Verify both signatures in a forwarded message.

    Rejects chains deeper than ``MAX_FORWARD_DEPTH`` to prevent resource-
    exhaustion (Zip-bomb style nested JSON) attacks.  Returns True only when
    the forwarder's outer signature is valid *and* the embedded original
    message's signature is also valid.
    """
    if not fwd_msg.verify(forwarder_pub_bytes):
        return False
    if _forward_depth(fwd_msg) > MAX_FORWARD_DEPTH:
        return False
    try:
        nested = json.loads(fwd_msg.content)
        orig = Message.from_dict(nested["original"])
    except Exception:
        return False
    return orig.verify(original_pub_bytes)


def apply_edits(messages: list[Message]) -> list[Message]:
    """Return messages with edits applied.

    Edit records are dropped from the output.  Only edits signed by the same
    key as the original message are applied — cross-user edits are silently
    discarded.  The newest authorized edit wins.
    """
    edits_by_id: dict[str, list[Message]] = {}
    for msg in messages:
        if msg.message_type == "edit" and msg.reply_to_id:
            edits_by_id.setdefault(msg.reply_to_id, []).append(msg)

    for edit_list in edits_by_id.values():
        edit_list.sort(key=lambda m: m.timestamp)

    applied_messages = []
    for msg in messages:
        if msg.message_type == "edit":
            continue

        applied_msg = msg
        if msg.message_id in edits_by_id:
            # Only apply edits from the same author as the original message
            authorized = [e for e in edits_by_id[msg.message_id]
                          if e.from_pub_hex == msg.from_pub_hex]
            if authorized:
                latest_edit = authorized[-1]
                import dataclasses as _dc
                applied_msg = _dc.replace(msg, content=latest_edit.content)
        applied_messages.append(applied_msg)

    return applied_messages


def apply_deletions(messages: list[Message]) -> list[Message]:
    """Return messages with authorized deletions removed.

    A ``message_type="delete"`` tombstone marks its ``reply_to_id`` as deleted.
    Deletion is authorized only when the tombstone's ``from_pub_hex`` matches the
    original message's ``from_pub_hex`` (same author).  Both the tombstone and the
    original are removed from the output; the Merkle chain should be validated on
    the *pre-deletion* list where the tombstone preserves continuity.
    """
    # Collect authorized deletions: tombstone's author must match original's author
    deleted_ids: set[str] = set()
    for msg in messages:
        if msg.message_type != "delete" or not msg.reply_to_id:
            continue
        for orig in messages:
            if orig.message_id == msg.reply_to_id and orig.from_pub_hex == msg.from_pub_hex:
                deleted_ids.add(msg.reply_to_id)
                break

    return [m for m in messages
            if m.message_type != "delete" and m.message_id not in deleted_ids]


def compose_unreaction(
    identity_key: Ed25519PrivateKey,
    cert: RelationshipCertificate,
    reaction_message_id: str,
    prev_msg: Optional["Message"] = None,
    now: Optional[datetime.datetime] = None,
) -> Message:
    """Compose a signed un-reaction message (message_type='unreaction').

    Targets a specific ``reaction_message_id``.  Only honored if the
    unreaction and the original reaction share the same ``from_pub_hex``.
    """
    return compose(
        identity_key=identity_key,
        cert=cert,
        content="",
        now=now,
        reply_to_id=reaction_message_id,
        message_type="unreaction",
        prev_msg=prev_msg,
    )


def apply_unreactions(messages: list[Message]) -> list[Message]:
    """Remove reaction messages that have been authoritatively un-reacted.

    An ``unreaction`` is honored only when its ``from_pub_hex`` matches the
    ``from_pub_hex`` of the targeted reaction.  Both the reaction and the
    unreaction are pruned from the output.
    """
    unreacted_ids: set[str] = set()
    for msg in messages:
        if msg.message_type != "unreaction" or not msg.reply_to_id:
            continue
        for orig in messages:
            if (orig.message_id == msg.reply_to_id
                    and orig.message_type == "reaction"
                    and orig.from_pub_hex == msg.from_pub_hex):
                unreacted_ids.add(msg.reply_to_id)
                break

    return [
        m for m in messages
        if m.message_type != "unreaction" and m.message_id not in unreacted_ids
    ]


# ---------------------------------------------------------------------------
# Send (writes to Pod)
# ---------------------------------------------------------------------------

def send(
    message: Message,
    pod_client: SolidClient,
) -> str:
    """Write a signed message to a Solid Pod.

    Parameters
    ----------
    message:
        A :class:`Message` produced by :func:`compose`.
    pod_client:
        A :class:`~proxion_messenger_core.solid_client.SolidClient` configured with the
        **sender's** resolver.

        NOTE [J-006]: The caller is responsible for passing the correct
        resolver-bound client.  There is no automatic resolver selection.

    Returns
    -------
    str
        The ``stash://`` path the message was written to.

    Raises
    ------
    SolidError
        On HTTP or resolution errors.
    """
    path = message_path(message.cert_id, message.message_id)
    data = json.dumps(message.to_dict(), indent=2).encode("utf-8")
    pod_client.put(path, data, content_type="application/json")
    return path


# ---------------------------------------------------------------------------
# Receive (reads from Pod)
# ---------------------------------------------------------------------------

def check_sequence_continuity(messages: list["Message"]) -> list[int]:
    """Return a list of missing seq_num values in *messages* (sorted by seq_num).

    Only examines messages where ``seq_num > 0`` (legacy messages with seq_num=0
    are skipped). An empty return value means no gaps were detected.

    Example: if seq_nums are [1, 2, 4, 5] then [3] is returned.
    """
    numbered = sorted(
        [m.seq_num for m in messages if m.seq_num > 0]
    )
    if len(numbered) < 2:
        return []
    gaps = []
    for a, b in zip(numbered, numbered[1:]):
        gaps.extend(range(a + 1, b))
    return gaps


def check_hash_chain(messages: list["Message"]) -> list[int]:
    """Return indices of messages with a broken prev_hash chain link.

    Messages with ``prev_hash=""`` are treated as genesis/legacy checkpoints —
    they reset the expected hash for subsequent chained messages without being
    flagged as breaks themselves.

    Only consecutive pairs of non-empty-prev_hash messages are validated.
    A non-empty ``prev_hash`` that doesn't match the SHA-256 of the preceding
    message's ``canonical_bytes()`` is reported as a break.

    Example: messages [A(genesis), B(→A), C(→WRONG)] → returns [2].
    """
    import hashlib as _hashlib
    breaks: list[int] = []
    prev_canonical_hash = ""

    for i, msg in enumerate(messages):
        if msg.prev_hash == "":
            # Genesis or legacy — update chain anchor, don't flag as break
            prev_canonical_hash = _hashlib.sha256(msg.canonical_bytes()).hexdigest()
            continue
        if msg.prev_hash != prev_canonical_hash:
            breaks.append(i)
        prev_canonical_hash = _hashlib.sha256(msg.canonical_bytes()).hexdigest()

    return breaks


def receive(
    cert: RelationshipCertificate,
    pod_client: SolidClient,
    verify_signatures: bool = True,
    pin_participants: bool = True,
    holder_state: Optional["AgentState"] = None,
    signing_key: Optional[bytes] = None,
    now: Optional[datetime.datetime] = None,
    since: Optional[int] = None,
    limit: Optional[int] = None,
    before: Optional[str] = None,
    offset: int = 0,
    decrypt: bool = True,
) -> list[Message]:
    """Fetch and return messages in a thread from a Solid Pod.

    Parameters
    ----------
    cert:
        The RelationshipCertificate whose thread to read.
    pod_client:
        A SolidClient configured with the sender's resolver.
    verify_signatures:
        If True (default), drop messages with invalid signatures.
    holder_state:
        Optional reader AgentState for authenticated reads.
    signing_key:
        HMAC signing key for capability tokens.
    since:
        Optional minimum timestamp (Unix seconds).
    limit:
        Optional max number of most-recent messages to return.
    before:
        Optional message_id. Excludes this message and all newer messages.
    decrypt:
        If True (default), decrypts encrypted message content.
    """
    _read_client = pod_client

    if holder_state is not None:
        if signing_key is None:
            raise ValueError("signing_key is required when holder_state is provided")
        if now is None:
            now = datetime.datetime.now(datetime.timezone.utc)
        narrow_token = narrow_to_thread(
            cert=cert,
            holder_state=holder_state,
            signing_key=signing_key,
            now=now,
        )
        from .solid_auth import AuthenticatedSolidClient
        _read_client = AuthenticatedSolidClient(
            solid_client=pod_client,
            token=narrow_token,
            identity_key=holder_state.identity_key,
            signing_key=signing_key,
            cert=cert,
            now=now,
        )

    container = thread_path(cert.certificate_id)

    try:
        member_uris = _read_client.list(container)
    except SolidError:
        return []

    messages: list[Message] = []
    for uri in member_uris:
        try:
            if not uri.endswith(".json"):
                continue
            raw = _read_client.get(uri)
            msg = Message.from_dict(json.loads(raw.decode("utf-8")))
        except Exception:
            continue

        if verify_signatures:
            try:
                sender_bytes = bytes.fromhex(msg.from_pub_hex)
            except ValueError:
                continue
            if not msg.verify(sender_bytes):
                continue

        if pin_participants:
            # Reject messages from keys not in this cert — prevents a malicious
            # pod operator from injecting messages into threads they don't own.
            allowed = {cert.issuer, cert.subject}
            if msg.from_pub_hex not in allowed:
                continue

        if decrypt and msg.content.startswith("enc1:"):
            try:
                from .msgcrypto import decrypt_message, derive_message_key
                key = derive_message_key(cert)
                msg = __import__("dataclasses").replace(
                    msg, content=decrypt_message(msg.content, key)
                )
            except Exception:
                raise SolidError(f"failed to decrypt message: {msg.message_id}")

        messages.append(msg)

    # Sort by timestamp ascending
    messages.sort(key=lambda m: m.timestamp)

    # Apply 'since'
    if since is not None:
        messages = [m for m in messages if m.timestamp >= since]

    # Apply 'before' (exclusive: drop targeted message and all newer ones)
    if before:
        found_idx = -1
        for i, m in enumerate(messages):
            if m.message_id == before:
                found_idx = i
                break
        if found_idx != -1:
            messages = messages[:found_idx]

    # Apply 'offset'
    if offset > 0:
        messages = messages[offset:]

    # Apply 'limit':
    # - with `since`: return the N oldest from the filtered set (read forward from a point)
    # - without `since`: return the N most-recent (tail of the full history)
    if limit is not None and limit > 0:
        if since is not None:
            messages = messages[:limit]
        elif len(messages) > limit:
            messages = messages[-limit:]

    return messages


# ---------------------------------------------------------------------------
# delete_message
# ---------------------------------------------------------------------------

def delete_message(
    message_id: str,
    cert_id: str,
    pod_client: SolidClient,
) -> str:
    """Delete a message from a Solid Pod.

    Parameters
    ----------
    message_id:
        The :attr:`Message.message_id` of the message to delete.
    cert_id:
        The :attr:`Message.cert_id` (used to compute the stash path).
    pod_client:
        A :class:`SolidClient` with write access to the sender's Pod.

    Returns
    -------
    str
        The ``stash://`` path that was deleted.
    """
    path = message_path(cert_id, message_id)
    pod_client.delete(path)
    return path


# ---------------------------------------------------------------------------
# thread_info
# ---------------------------------------------------------------------------

def thread_info(
    cert: RelationshipCertificate,
    pod_client: SolidClient,
) -> dict:
    """Return metadata about a thread without loading message bodies.

    Returns
    -------
    dict with keys:
        count (int): number of .json files in the thread container.
        latest_timestamp (int or None): always None (filenames contain no timestamps).
        message_ids (list[str]): all message_id values extracted from filenames.
    """
    container = thread_path(cert.certificate_id)
    try:
        uris = pod_client.list(container)
    except SolidError:
        return {"count": 0, "latest_timestamp": None, "message_ids": []}

    message_ids = []
    for uri in uris:
        if uri.endswith(".json"):
            filename = uri.rsplit("/", 1)[-1]  # e.g. "abc123.json"
            mid = filename[:-5]                # strip ".json"
            if mid:
                message_ids.append(mid)

    return {
        "count": len(message_ids),
        "latest_timestamp": None,
        "message_ids": message_ids,
    }


# ---------------------------------------------------------------------------
# compose_and_send
# ---------------------------------------------------------------------------

def compose_and_send(
    identity_key: Ed25519PrivateKey,
    cert: RelationshipCertificate,
    content: str,
    pod_client: SolidClient,
    now: Optional[datetime.datetime] = None,
    reply_to_id: Optional[str] = None,
    message_type: str = "text",
) -> Message:
    """Compose, sign, and send a message in one call.

    Returns the signed :class:`Message` (already persisted to *pod_client*).
    """
    msg = compose(identity_key, cert, content, now=now, reply_to_id=reply_to_id, message_type=message_type)
    send(msg, pod_client)
    return msg


# Alias used by gateway write-through sync
send_message = compose_and_send
