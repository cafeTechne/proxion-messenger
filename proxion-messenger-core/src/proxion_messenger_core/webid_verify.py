"""WebID key verification with 24-hour TTL cache.

Verifies that a Solid WebID's profile card authorizes a given Ed25519 public key.
Used by the relay layer to authenticate messages from non-did:key senders.

Lookup strategy (in order):
  1. Proxion discovery file: ``{pod_url}profile/proxion-discovery.json``
     contains ``identity_pub_hex`` written by :func:`publish_proxion_discovery`.
  2. WebID profile card (Turtle or JSON-LD): scans for hex-encoded key material
     in ``publicKey`` / ``verificationMethod`` predicates via substring match.

Results are cached for *_TTL_SECONDS* (default 86400 = 24 h) to avoid repeated
HTTP fetches during a relay burst.
"""
from __future__ import annotations

import logging
import time
from collections import deque
from typing import Optional
from urllib.parse import urlparse, urljoin

from .network import safe_get, NetworkError

logger = logging.getLogger(__name__)

_TTL_SECONDS = 86400  # 24 hours

# Cache: webid → (pub_hex or None, fetched_at)
_cache: dict[str, tuple[Optional[str], float]] = {}

# Rate-limit: per-peer-IP sliding window of new resolution timestamps
_rate_windows: dict[str, deque] = {}
_RATE_LIMIT_WINDOW = 60.0   # seconds
_RATE_LIMIT_MAX = 10        # max new WebID resolutions per IP per window


def _check_webid_rate_limit(peer_ip: str, now: float) -> bool:
    """Return True if the resolution is within rate limits, False if exceeded."""
    if not peer_ip:
        return True  # internal / unknown caller — always allow
    window = _rate_windows.setdefault(peer_ip, deque())
    while window and now - window[0] >= _RATE_LIMIT_WINDOW:
        window.popleft()
    if len(window) >= _RATE_LIMIT_MAX:
        return False
    window.append(now)
    return True


def reset_rate_limits() -> None:
    """Clear all rate-limit windows (test / admin helper)."""
    _rate_windows.clear()


def _pod_url_from_webid(webid: str) -> Optional[str]:
    """Best-effort derivation of pod root URL from a WebID.

    For CSS-style WebIDs like ``http://localhost:3000/alice/profile/card#me``
    this returns ``http://localhost:3000/alice/``.  Falls back to the scheme+host
    for unknown layouts.
    """
    try:
        parsed = urlparse(webid)
        path = parsed.path.split("#")[0]
        # Strip /profile/card or /profile/card.ttl
        if "/profile/" in path:
            pod_path = path[: path.index("/profile/") + 1]
        else:
            pod_path = "/"
        return f"{parsed.scheme}://{parsed.netloc}{pod_path}"
    except Exception:
        return None


def _fetch_proxion_discovery(pod_url: str, timeout: float = 5.0) -> Optional[str]:
    """Try to GET {pod_url}profile/proxion-discovery.json and extract identity_pub_hex."""
    try:
        url = urljoin(pod_url, "profile/proxion-discovery.json")
        data_bytes = safe_get(url, timeout=timeout, max_bytes=65_536)
        import json as _json
        data = _json.loads(data_bytes)
        return data.get("identity_pub_hex") or None
    except Exception:
        return None


def _fetch_profile_card(webid: str, timeout: float = 5.0) -> Optional[str]:
    """GET the WebID profile card and search for a hex-encoded 32-byte public key.

    Requests JSON-LD first (simpler to parse); falls back to Turtle.
    Returns the first 64-char lowercase hex string found near a key predicate.
    """
    try:
        profile_url = webid.split("#")[0]
        body_bytes = safe_get(
            profile_url,
            headers={"Accept": "application/ld+json, text/turtle;q=0.9"},
            timeout=timeout,
            max_bytes=524_288,
        )
        body = body_bytes.decode("utf-8", errors="replace")

        # Scan for 64-char hex sequences near key predicates.
        # Comment lines (Turtle: # ...) are skipped to prevent false matches.
        import re
        _HEX64 = re.compile(r'\b([0-9a-fA-F]{64})\b')
        _KEY_PREDICATES = (
            "publicKey", "verificationMethod", "publicKeyHex",
            "identity_pub_hex", "proxion:publicKey",
        )
        lines = body.splitlines()
        for i, line in enumerate(lines):
            if line.strip().startswith("#"):  # skip Turtle/JSON-LD comments
                continue
            if any(pred in line for pred in _KEY_PREDICATES):
                # Search this line and the next two, excluding comment lines
                window_lines = [
                    l for l in lines[i: i + 3]
                    if not l.strip().startswith("#")
                ]
                window = "\n".join(window_lines)
                match = _HEX64.search(window)
                if match:
                    return match.group(1).lower()
        return None
    except Exception:
        return None


def get_webid_pub_hex(
    webid: str,
    _now: Optional[float] = None,
    peer_ip: str = "",
) -> Optional[str]:
    """Return the Ed25519 public key hex authorized by *webid*, or None.

    Results are cached for 24 hours.  Cache hits bypass the rate limiter.
    *peer_ip* is used for the sliding-window rate limiter (max
    ``_RATE_LIMIT_MAX`` new resolutions per ``_RATE_LIMIT_WINDOW`` seconds per IP).
    Pass *_now* (float timestamp) to override the current time in tests.
    """
    now = _now if _now is not None else time.time()

    cached_hex, fetched_at = _cache.get(webid, (None, 0.0))
    if fetched_at and now - fetched_at < _TTL_SECONDS:
        return cached_hex

    # Cache miss → new network resolution: apply rate limit
    if not _check_webid_rate_limit(peer_ip, now):
        logger.warning("webid_verify: rate limit exceeded for peer IP %s", peer_ip or "<unknown>")
        return None

    result: Optional[str] = None

    pod_url = _pod_url_from_webid(webid)
    if pod_url:
        result = _fetch_proxion_discovery(pod_url)

    if not result:
        result = _fetch_profile_card(webid)

    _cache[webid] = (result, now)
    if result:
        logger.debug("webid_verify: resolved %s → %s…", webid, result[:16])
    else:
        logger.warning("webid_verify: could not resolve public key for %s", webid)
    return result


def verify_webid_key(webid: str, pub_hex: str, _now: Optional[float] = None) -> bool:
    """Return True if *pub_hex* is authorized by *webid*'s profile.

    Uses :func:`get_webid_pub_hex` with caching.  The check is case-insensitive.
    """
    try:
        resolved = get_webid_pub_hex(webid, _now=_now)
        if not resolved:
            return False
        return resolved.lower() == pub_hex.lower()
    except Exception:
        return False


def invalidate_cache(webid: Optional[str] = None) -> None:
    """Remove *webid* from the cache (or clear the whole cache if None)."""
    if webid is None:
        _cache.clear()
    else:
        _cache.pop(webid, None)
