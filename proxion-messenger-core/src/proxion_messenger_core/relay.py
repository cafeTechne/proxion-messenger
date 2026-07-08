"""
Cross-gateway relay protocol for Proxion.

Enables two users on different gateways to exchange messages directly using
Ed25519 signatures derived from their did:key identities — no central authority
required.

Proxion Address format: did:key:z6Mk...@https://gateway.example.com
  - The DID encodes the public key (self-certifying identity)
  - The gateway URL is where the user's WebSocket server runs

Protocol:
  1. Sender's gateway signs the message with the sender's Ed25519 private key
  2. POST the signed payload to receiver's gateway /relay endpoint
  3. Receiver's gateway extracts the public key from the sender's DID, verifies
  4. Delivers to target WebSocket client, or stores for later pickup
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING, Optional

from .network import _resolve_safe_ip, async_safe_post as _async_safe_post

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


# ── Canonical message string ──────────────────────────────────────────────────

def _canonical(payload: dict) -> str:
    relay_nonce = payload.get("relay_nonce", "")
    sender_webid = payload.get("sender_webid", "")
    message_scope = payload.get("message_scope", "")
    sig_key_version = payload.get("sig_key_version")
    parts = [
        payload["from_webid"], payload["to_webid"],
        payload["message_id"], payload["content"],
        str(payload["timestamp"]),
    ]
    if relay_nonce:
        parts.append(relay_nonce)
    if sender_webid:
        parts.append(sender_webid)
    if message_scope:
        parts.append(message_scope)
    if sig_key_version is not None:
        parts.append(str(int(sig_key_version)))
    return "\n".join(parts)


# ── Signing ───────────────────────────────────────────────────────────────────

def sign_relay_message(
    identity_key: "Ed25519PrivateKey",
    from_webid: str,
    to_webid: str,
    message_id: str,
    content: str,
    timestamp: str,
    relay_nonce: str = "",
    sender_webid: str = "",
    message_scope: str = "",
    sig_key_version: Optional[int] = None,
) -> str:
    """Sign a relay message. Returns base64url-encoded Ed25519 signature.

    ``sender_webid`` binds the signing gateway's DID to the canonical string
    (RFC 9449-style sender binding).  Omit for backward-compatible messages.
    ``message_scope`` binds the signature to a named endpoint context
    (e.g. ``"relay-v2"``) to prevent cross-context reuse.
    ``sig_key_version`` tracks which signing key version was used for rotation tracking.
    """
    payload = {
        "from_webid": from_webid,
        "to_webid": to_webid,
        "message_id": message_id,
        "content": content,
        "timestamp": timestamp,
    }
    if relay_nonce:
        payload["relay_nonce"] = relay_nonce
    if sender_webid:
        payload["sender_webid"] = sender_webid
    if message_scope:
        payload["message_scope"] = message_scope
    if sig_key_version is not None:
        if not isinstance(sig_key_version, int) or sig_key_version < 1:
            raise ValueError("sig_key_version must be a positive integer")
        payload["sig_key_version"] = sig_key_version
    msg = _canonical(payload).encode()
    raw_sig = identity_key.sign(msg)
    return base64.urlsafe_b64encode(raw_sig).rstrip(b"=").decode()


# ── Verification ──────────────────────────────────────────────────────────────

def verify_relay_message(
    from_webid: str,
    to_webid: str,
    message_id: str,
    content: str,
    timestamp: str,
    signature: str,
    relay_nonce: str = "",
    clock_skew_window: Optional[timedelta] = None,
    sender_webid: str = "",
    message_scope: str = "",
    sig_key_version: Optional[int] = None,
    store=None,
) -> bool:
    """
    Verify a relay message signature.

    Extracts the Ed25519 public key from ``from_webid`` (a did:key DID) and
    verifies the signature over the canonical payload.  Returns ``False`` on
    any failure — never raises.

    Parameters
    ----------
    clock_skew_window:
        Maximum allowed age/future-dating for the message timestamp.  Defaults
        to ±5 minutes.  Pass ``timedelta.max`` to disable the check (useful in
        tests that use hardcoded past timestamps).
    sender_webid:
        Optional gateway-level sender DID included in the canonical string for
        sender binding (backward-compatible: omit for old-format messages).
    message_scope:
        Optional context label (e.g. ``"relay-v2"``) bound into the canonical
        string.  Prevents signature reuse across endpoints.  Backward-compatible:
        only included if the incoming payload actually contains this field.
    sig_key_version:
        Optional signing key version for rotation tracking.
    store:
        Optional LocalStore for R10 identity continuity checks.
    """
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        from cryptography.exceptions import InvalidSignature

        if from_webid.startswith("did:key:"):
            from .didkey import did_to_pub_key
            pub_bytes = did_to_pub_key(from_webid)
        else:
            # HTTPS WebID: resolve public key from profile or discovery document
            from .webid_verify import get_webid_pub_hex
            pub_hex = get_webid_pub_hex(from_webid)
            if not pub_hex:
                return False
            pub_bytes = bytes.fromhex(pub_hex)

        # Reject non-32-byte keys early
        if len(pub_bytes) != 32:
            return False

        # R11: validate algorithm via crypto policy registry
        try:
            from .crypto_policy import validate_signature_policy
            validate_signature_policy(alg="EdDSA", key_meta={"crv": "Ed25519"}, context="relay")
        except Exception:
            return False

        pub_key = Ed25519PublicKey.from_public_bytes(pub_bytes)

        # R10: identity key continuity check for HTTPS WebIDs
        if store is not None and not from_webid.startswith("did:key:"):
            _pub_hex_seen = pub_bytes.hex()
            try:
                _trust_status = store.is_trusted_identity_key(from_webid, _pub_hex_seen)
                if _trust_status is None:
                    # First time we see this key for this identity — record and trust
                    store.record_identity_key_seen(from_webid, _pub_hex_seen, trusted=True)
                elif _trust_status is False:
                    # Known key that was explicitly distrusted
                    import os as _os_relay
                    if _os_relay.environ.get("PROXION_ALLOW_UNVERIFIED_KEY_ROLLOVER") != "1":
                        return False
                else:
                    # Already trusted — update last_seen
                    store.record_identity_key_seen(from_webid, _pub_hex_seen, trusted=True)
                    # Check if the previously trusted key is different from this one
                    _history = store.get_identity_key_history(from_webid)
                    _other_trusted = [h for h in _history
                                      if h["pubkey_hex"] != _pub_hex_seen and h["trusted"]]
                    if _other_trusted:
                        # Multiple trusted keys — might be a rollover; open rollover event
                        import uuid as _uuid_rc, os as _os_rc
                        _pending = store.get_pending_rollover_for_identity(from_webid)
                        if _pending is None:
                            store.open_identity_rollover_event(
                                id=str(_uuid_rc.uuid4()),
                                identity=from_webid,
                                old_pubkey_hex=_other_trusted[-1]["pubkey_hex"],
                                new_pubkey_hex=_pub_hex_seen,
                            )
                            if _os_rc.environ.get("PROXION_ALLOW_UNVERIFIED_KEY_ROLLOVER") != "1":
                                return False
            except Exception:
                pass  # store errors are non-fatal

        # Restore padding stripped during encoding
        padded = signature + "=" * (-len(signature) % 4)
        raw_sig = base64.urlsafe_b64decode(padded)

        payload = {
            "from_webid": from_webid,
            "to_webid": to_webid,
            "message_id": message_id,
            "content": content,
            "timestamp": timestamp,
        }
        if relay_nonce:
            payload["relay_nonce"] = relay_nonce
        if sender_webid:
            payload["sender_webid"] = sender_webid
        if message_scope:
            payload["message_scope"] = message_scope
        if sig_key_version is not None:
            if not isinstance(sig_key_version, int) or sig_key_version < 1 or sig_key_version > 2**31 - 1:
                return False
            payload["sig_key_version"] = sig_key_version
        msg = _canonical(payload).encode()
        pub_key.verify(raw_sig, msg)

        # Clock-skew check: reject messages timestamped too far from now.
        # Unparseable timestamps fail closed — we never skip the check.
        _window = timedelta(minutes=5) if clock_skew_window is None else clock_skew_window
        _MAX_ALLOWED_WINDOW = timedelta(minutes=15)
        if _window > _MAX_ALLOWED_WINDOW:
            _window = _MAX_ALLOWED_WINDOW
        try:
            msg_ts = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except Exception:
            return False  # malformed timestamp → fail closed
        if msg_ts.tzinfo is None:
            return False  # timezone required
        now = datetime.now(timezone.utc)
        if abs(now - msg_ts) > _window:
            return False

        return True
    except Exception:
        return False


# ── Full-payload envelope signature (ephemeral content_type relays) ─────────────
#
# sign_relay_message() above binds only a FIXED field set (from/to/msg/content/
# ts). The ephemeral relays (room/voice/file/dm secondary ops) carry op-specific
# fields (emoji, action, ms, room_id, channel_id, …) that must ALSO be bound, or
# a relaying gateway could tamper with them. The envelope signs the whole payload.
# The signer is the RELAYING GATEWAY (relay_sig_did), not necessarily from_webid,
# so room relays (from_webid = a member did) can be signed by the member's gateway
# and the receiver can bind the two separately.

def _envelope_canonical(payload: dict) -> bytes:
    """Deterministic bytes over every field except ``signature``."""
    import json as _json
    body = {k: v for k, v in payload.items() if k != "signature"}
    return _json.dumps(body, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False).encode("utf-8")


def sign_relay_envelope(identity_key: "Ed25519PrivateKey", payload: dict) -> str:
    """Sign the full ephemeral relay *payload* with the gateway identity key.

    The payload MUST already carry ``relay_sig_did`` (the signing gateway's
    did:key) and ``relay_ts`` (ISO-8601) — they are part of the signed bytes.
    Returns a base64url-encoded Ed25519 signature.
    """
    raw = identity_key.sign(_envelope_canonical(payload))
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def verify_relay_envelope(payload: dict,
                          clock_skew_window: Optional[timedelta] = None) -> bool:
    """Verify a full-payload envelope signature. Never raises → False on failure.

    The signer's key is derived from ``relay_sig_did`` (a did:key). Verifies the
    Ed25519 signature over every field except ``signature`` and enforces a
    clock-skew window (default ±5 min, hard cap 15) on ``relay_ts``.
    """
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        sig    = payload.get("signature", "")
        signer = payload.get("relay_sig_did", "")
        ts     = payload.get("relay_ts", "")
        if not sig or not signer or not ts or not signer.startswith("did:key:"):
            return False

        from .didkey import did_to_pub_key
        pub_bytes = did_to_pub_key(signer)
        if len(pub_bytes) != 32:
            return False
        try:
            from .crypto_policy import validate_signature_policy
            validate_signature_policy(alg="EdDSA", key_meta={"crv": "Ed25519"}, context="relay")
        except Exception:
            return False

        pub_key = Ed25519PublicKey.from_public_bytes(pub_bytes)
        padded = sig + "=" * (-len(sig) % 4)
        raw_sig = base64.urlsafe_b64decode(padded)
        pub_key.verify(raw_sig, _envelope_canonical(payload))

        _window = timedelta(minutes=5) if clock_skew_window is None else clock_skew_window
        if _window > timedelta(minutes=15):
            _window = timedelta(minutes=15)
        try:
            msg_ts = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        except Exception:
            return False
        if msg_ts.tzinfo is None:
            return False
        if abs(datetime.now(timezone.utc) - msg_ts) > _window:
            return False
        return True
    except Exception:
        return False


# ── HTTP relay delivery ───────────────────────────────────────────────────────

async def post_relay(target_url: str, payload: dict, timeout: float = 10.0) -> bool:
    """POST a signed relay payload to *target_url*.

    Uses :func:`~proxion_messenger_core.network.async_safe_post` for IP-pinning
    and private-range blocking.  Returns ``True`` on HTTP 2xx, ``False`` otherwise.
    Never raises.
    """
    return await _async_safe_post(target_url, payload, timeout=timeout)


def _validate_relay_target(url: str) -> bool:
    """Return True only if *url* is a safe gateway to relay to.

    Set ``PROXION_ALLOW_PRIVATE_RELAY=1`` to permit loopback/private addresses
    in local test environments.
    """
    return _resolve_safe_ip(url) is not None


# ── Address parsing ───────────────────────────────────────────────────────────

def parse_proxion_address(addr: str) -> tuple[str, str | None]:
    """
    Parse a Proxion address of the form ``did:key:z6Mk...@https://gateway.example.com``.

    Returns ``(did, gateway_url)`` where ``gateway_url`` is ``None`` if no ``@``
    separator was found.
    """
    addr = addr.strip()
    # Find the last @ so we don't split inside the DID itself
    at = addr.rfind("@")
    if at == -1 or not addr[at + 1:].startswith(("http://", "https://")):
        return addr, None
    return addr[:at].strip(), addr[at + 1:].strip()


def format_proxion_address(did: str, gateway_url: str) -> str:
    """Format a full Proxion address string."""
    return f"{did}@{gateway_url}"
