"""WebPush VAPID notification bridge.

Sends privacy-preserving push notifications to offline users.  The push payload
contains only a type + thread_id + display_name — never message content — so
that push provider infrastructure sees minimal metadata.

Falls back gracefully when ``py_vapid`` / ``pywebpush`` is not installed.

Usage
-----
    priv_pem, pub_b64 = generate_vapid_keypair()
    ok = send_web_push(
        subscription={"endpoint": "...", "keys": {"p256dh": "...", "auth": "..."}},
        payload={"type": "message", "thread_id": "...", "display_name": "Alice"},
        vapid_private_pem=priv_pem,
        vapid_subject="mailto:admin@example.com",
    )
"""
from __future__ import annotations

import base64
import json
import logging
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Bounded network timeout for a single push delivery, so a slow or hanging push
# service can't wedge the caller (or the event loop, once offloaded).
_PUSH_TIMEOUT = 10.0


def is_safe_push_endpoint(endpoint: str) -> bool:
    """Return True only if *endpoint* is an https URL that resolves to a public IP.

    Reuses :func:`~proxion_messenger_core.network._resolve_safe_ip` — the SSRF
    validator the rest of the codebase uses — so loopback/private/link-local/
    reserved/metadata addresses are rejected across every resolved IP (guarding
    against split-horizon DNS).  Push endpoints must additionally be https.

    Set ``PROXION_ALLOW_PRIVATE_RELAY=1`` to permit private hosts in local tests.
    """
    if not endpoint or not isinstance(endpoint, str):
        return False
    try:
        if urlparse(endpoint).scheme != "https":
            return False
    except Exception:
        return False
    from .network import _resolve_safe_ip
    return _resolve_safe_ip(endpoint) is not None


def generate_vapid_keypair() -> tuple[str, str]:
    """Generate an ES256 VAPID keypair.

    Returns
    -------
    (private_pem: str, public_b64url: str)
        PEM-encoded private key and base64url-encoded uncompressed public key.
    """
    from cryptography.hazmat.primitives.asymmetric.ec import (
        generate_private_key,
        SECP256R1,
        EllipticCurvePublicKey,
    )
    from cryptography.hazmat.primitives import serialization

    priv = generate_private_key(SECP256R1())
    priv_pem = priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode()

    pub = priv.public_key()
    pub_bytes = pub.public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    pub_b64url = base64.urlsafe_b64encode(pub_bytes).rstrip(b"=").decode()
    return priv_pem, pub_b64url


def send_web_push(
    subscription: dict,
    payload: dict,
    vapid_private_pem: str,
    vapid_subject: str,
    ttl: int = 86400,
) -> bool:
    """Send an encrypted Web Push notification.

    Parameters
    ----------
    subscription:
        Browser push subscription: {endpoint, keys: {p256dh, auth}}.
    payload:
        Dict to send as JSON (privacy-preserving; no message content).
    vapid_private_pem:
        PEM-encoded ES256 private key.
    vapid_subject:
        VAPID ``sub`` claim — mailto: or https: URI identifying the sender.
    ttl:
        Message TTL in seconds (default 24 h).

    Returns
    -------
    True on successful delivery, False on any error.
    """
    # Defense in depth: reject a subscription whose endpoint fails the SSRF check
    # even if it was somehow stored without validation.
    if not is_safe_push_endpoint((subscription or {}).get("endpoint", "")):
        logger.warning("WebPush endpoint failed SSRF validation — skipped")
        return False

    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        logger.warning("pywebpush not installed — WebPush notification skipped")
        return False

    try:
        payload_bytes = json.dumps(payload).encode("utf-8")
        resp = webpush(
            subscription_info=subscription,
            data=payload_bytes,
            vapid_private_key=vapid_private_pem,
            vapid_claims={"sub": vapid_subject},
            ttl=ttl,
            content_encoding="aes128gcm",
            timeout=_PUSH_TIMEOUT,
        )
        if resp and resp.status_code < 300:
            return True
        logger.warning("WebPush delivery failed: status %s", getattr(resp, "status_code", "?"))
        return False
    except Exception as exc:
        logger.warning("WebPush error: %s", exc)
        return False


def vapid_public_key_from_pem(private_pem: str) -> str:
    """Derive the base64url-encoded uncompressed public key from a PEM private key."""
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    from cryptography.hazmat.primitives import serialization

    priv = load_pem_private_key(private_pem.encode(), password=None)
    pub_bytes = priv.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    return base64.urlsafe_b64encode(pub_bytes).rstrip(b"=").decode()
