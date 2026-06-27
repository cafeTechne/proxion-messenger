"""did:web resolution (Phase C4 — optional, gated).

Resolves a ``did:web:<domain>[:<path>]`` identity to its Ed25519 public key by
fetching the DID document at the conventional well-known URL and extracting an
Ed25519 verification method. This is the core primitive of did:web support: once
you can resolve a did:web to a verification key, you can verify its signatures.

Deliberately scoped and **gated behind ``PROXION_ENABLE_DID_WEB``** so it never
touches the default flow. ``did:key`` remains the primary, infrastructure-free
identity for the product thesis (a non-technical user has no domain); ``did:web``
is an optional, human-readable alias for users who own a domain. We do NOT thread
did:web through the 100+ did:key-coupled call sites — resolution is async (an HTTP
fetch) whereas ``did_to_pub_key`` is sync, and full integration is out of scope
for this lower-priority item.

An Ed25519 ``did.json`` verification method uses ``publicKeyMultibase: "z6Mk…"``
— the same base58btc-multicodec form as a ``did:key`` suffix — so we reuse the
existing :func:`didkey.did_to_pub_key` decoder.
"""
import json
import os
import time

from .didkey import did_to_pub_key

_CACHE: dict = {}              # did -> (pub_bytes, expiry_ts)
_CACHE_TTL = 3600.0           # 1 hour


def did_web_enabled() -> bool:
    """did:web resolution is opt-in to keep the default flow did:key-only."""
    return os.environ.get("PROXION_ENABLE_DID_WEB") == "1"


def did_web_to_url(did: str) -> str:
    """Map a did:web DID to its DID-document URL per the W3C did:web method.

    ``did:web:example.com``          -> ``https://example.com/.well-known/did.json``
    ``did:web:example.com:u:alice``  -> ``https://example.com/u/alice/did.json``

    Colons in the method-specific id are path separators; ``%3A`` in the first
    segment decodes back to a port colon.
    """
    if not did.startswith("did:web:"):
        raise ValueError(f"not a did:web DID: {did!r}")
    ident = did[len("did:web:"):]
    if not ident:
        raise ValueError("empty did:web identifier")
    parts = ident.split(":")
    domain = parts[0].replace("%3A", ":").replace("%3a", ":")
    if not domain or "/" in domain or ".." in domain:
        raise ValueError(f"invalid did:web domain: {domain!r}")
    if len(parts) == 1:
        return f"https://{domain}/.well-known/did.json"
    path = "/".join(p for p in parts[1:] if p)
    if not path or ".." in path:
        raise ValueError(f"invalid did:web path: {parts[1:]!r}")
    return f"https://{domain}/{path}/did.json"


def _extract_ed25519_pub(doc: dict, did: str) -> bytes:
    """Pull the 32-byte Ed25519 public key out of a DID document.

    Looks for a ``verificationMethod`` of an Ed25519 type carrying a
    ``publicKeyMultibase`` (``z…``). Raises ``ValueError`` if none is present.
    """
    if not isinstance(doc, dict):
        raise ValueError("DID document is not an object")
    doc_id = doc.get("id")
    if doc_id and doc_id != did:
        raise ValueError(f"DID document id mismatch: {doc_id!r} != {did!r}")
    vms = doc.get("verificationMethod") or []
    if not isinstance(vms, list):
        raise ValueError("verificationMethod is not a list")
    for vm in vms:
        if not isinstance(vm, dict):
            continue
        mb = vm.get("publicKeyMultibase")
        vtype = vm.get("type", "")
        if isinstance(mb, str) and mb.startswith("z") and "Ed25519" in str(vtype):
            # publicKeyMultibase == the base58btc-multicodec suffix of a did:key
            return did_to_pub_key(f"did:key:{mb}")
    raise ValueError("no Ed25519 publicKeyMultibase verification method found")


async def resolve_did_web(did: str, *, timeout: float = 5.0) -> bytes:
    """Resolve a did:web DID to its 32-byte Ed25519 public key.

    Cached for ``_CACHE_TTL`` seconds. Requires ``PROXION_ENABLE_DID_WEB=1``.
    Raises ``ValueError`` for a malformed DID/document or when disabled;
    ``NetworkError`` (from :func:`network.async_safe_get`, SSRF-guarded) on fetch
    failure.
    """
    if not did_web_enabled():
        raise ValueError("did:web resolution is disabled (set PROXION_ENABLE_DID_WEB=1)")
    now = time.time()
    cached = _CACHE.get(did)
    if cached and cached[1] > now:
        return cached[0]
    from .network import async_safe_get
    url = did_web_to_url(did)
    body = await async_safe_get(url, timeout=timeout, max_bytes=64 * 1024)
    try:
        doc = json.loads(body)
    except Exception as exc:
        raise ValueError(f"did:web document is not valid JSON: {exc}") from exc
    pub = _extract_ed25519_pub(doc, did)
    _CACHE[did] = (pub, now + _CACHE_TTL)
    return pub
