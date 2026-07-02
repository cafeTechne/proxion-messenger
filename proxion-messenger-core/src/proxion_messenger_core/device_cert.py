"""Account device certificates for multi-device linking (delegation model).

An account's canonical identity is its primary device's ``did:key``
(``account_did``). A secondary device generates its OWN Ed25519 keypair
(``device_did``) and is authorized to act for the account by a
``DeviceCertificate`` that the account's primary key *signs*. The gateway
verifies this certificate to admit the device as a session of the account.

This is delegation, NOT key-copying: the account's private key never leaves the
primary device (it only signs). That is a hard requirement here because device
identity keys are stored non-extractable by design (browser WebCrypto R9.1
hardening), so the account secret physically cannot be exported to a new device.

Canonical signing format
-------------------------
A length-prefixed concatenation of the four bound fields, identical byte-for-byte
to what the browser produces so a JS-issued cert verifies here:

    b"|".join( len(part).to_bytes(2,"big") + part for part in [
        account_did, device_did, str(issued_at), str(expires_at) ] )

Timestamps are integer seconds (no float formatting ambiguity across languages).
"""
from __future__ import annotations

import base64
import time
from typing import Optional

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .didkey import did_to_pub_key, pub_key_to_did

# A device certificate may not outlive this window; the primary can re-issue.
MAX_TTL_DAYS = 400


def _canonical(account_did: str, device_did: str, issued_at: int, expires_at: int) -> bytes:
    parts = [
        account_did.encode(),
        device_did.encode(),
        str(int(issued_at)).encode(),
        str(int(expires_at)).encode(),
    ]
    return b"|".join(len(p).to_bytes(2, "big") + p for p in parts)


def issue_device_cert(
    account_priv: Ed25519PrivateKey,
    device_did: str,
    ttl_days: int = 365,
    now: Optional[float] = None,
) -> dict:
    """Sign a certificate authorizing ``device_did`` to act for the account.

    ``account_priv`` is the account's (primary device's) Ed25519 private key;
    the account_did is derived from its public key so the two can never drift.
    """
    if ttl_days <= 0 or ttl_days > MAX_TTL_DAYS:
        raise ValueError(f"ttl_days must be in 1..{MAX_TTL_DAYS}")
    if not device_did.startswith("did:key:"):
        raise ValueError("device_did must be a did:key")
    account_pub = account_priv.public_key().public_bytes_raw()
    account_did = pub_key_to_did(account_pub)
    issued = int(now if now is not None else time.time())
    expires = issued + ttl_days * 86400
    sig = account_priv.sign(_canonical(account_did, device_did, issued, expires))
    return {
        "@type": "ProxionDeviceCert",
        "version": 1,
        "account_did": account_did,
        "device_did": device_did,
        "issued_at": issued,
        "expires_at": expires,
        "signature": base64.b64encode(sig).decode("ascii"),
    }


def verify_device_cert(
    cert: dict,
    expected_device_did: Optional[str] = None,
    expected_account_did: Optional[str] = None,
    now: Optional[float] = None,
) -> Optional[str]:
    """Verify a device certificate.

    Returns the ``account_did`` the certificate authorizes for on success, or
    ``None`` on any failure (bad shape, forged/absent signature, expired, or a
    mismatch against an expected device/account binding). Never raises.

    ``expected_device_did`` should be the DID the connection just proved control
    of via the auth challenge — this is what stops one device replaying another
    device's certificate.
    """
    try:
        if not isinstance(cert, dict):
            return None
        account_did = cert.get("account_did", "")
        device_did = cert.get("device_did", "")
        issued_at = int(cert.get("issued_at", 0))
        expires_at = int(cert.get("expires_at", 0))
        sig_b64 = cert.get("signature", "")
        if not account_did or not device_did or not sig_b64:
            return None
        if not account_did.startswith("did:key:") or not device_did.startswith("did:key:"):
            return None
        if expected_device_did is not None and device_did != expected_device_did:
            return None
        if expected_account_did is not None and account_did != expected_account_did:
            return None
        now_i = int(now if now is not None else time.time())
        if expires_at <= issued_at or expires_at <= now_i:
            return None
        if expires_at - issued_at > MAX_TTL_DAYS * 86400:
            return None
        pub = Ed25519PublicKey.from_public_bytes(did_to_pub_key(account_did))
        pub.verify(
            base64.b64decode(sig_b64),
            _canonical(account_did, device_did, issued_at, expires_at),
        )
        return account_did
    except Exception:
        # Any failure (InvalidSignature, malformed base64/DID, bad shape) → reject.
        return None
