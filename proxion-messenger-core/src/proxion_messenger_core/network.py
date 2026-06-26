"""SSRF-safe HTTP helpers with IP-pinning.

All outbound HTTP requests from Proxion that resolve user-supplied URLs should
use this module.  Resolves hostnames once, validates against private/loopback IP
ranges, then pins the actual request to the resolved IP to prevent DNS-rebinding
TOCTOU attacks.

Set ``PROXION_ALLOW_PRIVATE_RELAY=1`` to bypass private-IP blocking for local
development (e.g. a CSS pod on localhost).
"""
from __future__ import annotations

import ipaddress
import json
import os
import socket
from typing import Optional
from urllib.parse import urlparse

try:
    import httpx
    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False

_DEFAULT_TIMEOUT = 5.0
_DEFAULT_MAX_BYTES = 1_048_576  # 1 MB

# Optional audit callback: set via set_audit_fn() to route blocked-URL events
# to a persistent store.  Signature: fn(event_type: str, severity: str, detail: str)
_audit_fn = None


def set_audit_fn(fn) -> None:
    """Register a callable invoked on SSRF-blocked requests.

    ``fn`` receives ``(event_type: str, severity: str, detail: str)`` where
    *detail* is the blocked URL.  Exceptions from the callback are silently
    suppressed to avoid masking the original NetworkError.
    """
    global _audit_fn
    _audit_fn = fn


def clear_audit_fn() -> None:
    """Remove the registered audit callback."""
    global _audit_fn
    _audit_fn = None


class NetworkError(Exception):
    """Raised when a safe network request is blocked or fails."""


def _make_tls_context():
    """Return an SSLContext enforcing TLS 1.2+ with preferred cipher suites.

    Falls back to system defaults if the preferred cipher string is not
    supported by the platform's OpenSSL build.
    """
    import ssl
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    try:
        ctx.set_ciphers("ECDHE+AESGCM:ECDHE+CHACHA20:DHE+AESGCM:DHE+CHACHA20")
    except ssl.SSLError:
        pass  # platform doesn't support the cipher string — use system defaults
    ctx.load_default_certs()
    return ctx


def _resolve_safe_ip(url: str) -> Optional[str]:
    """Resolve *url*'s hostname and verify it is not a private/reserved address.

    Returns the first resolved IP on success, ``None`` when the host resolves to
    a private range or on any resolution error.  Checks every returned address —
    if any is private the whole batch is rejected (prevents split-horizon DNS
    bypass).

    Set ``PROXION_ALLOW_PRIVATE_RELAY=1`` to permit loopback/private addresses
    for local development.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return None
    if parsed.scheme not in ("http", "https"):
        return None
    host = parsed.hostname  # strips userinfo to prevent credential bypass
    if not host:
        return None

    allow_private = os.environ.get("PROXION_ALLOW_PRIVATE_RELAY") == "1"
    try:
        infos = socket.getaddrinfo(host, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except socket.gaierror:
        return None

    if not infos:
        return None

    first_ip: Optional[str] = None
    for info in infos:
        ip_str = info[4][0]
        try:
            addr = ipaddress.ip_address(ip_str)
            private = (
                addr.is_loopback or addr.is_private or addr.is_link_local
                or addr.is_unspecified or addr.is_reserved
            )
        except ValueError:
            return None
        if private and not allow_private:
            return None
        if first_ip is None:
            first_ip = ip_str

    return first_ip


def _pin_url(url: str, resolved_ip: str) -> tuple[str, dict]:
    """Return (pinned_url, extra_headers) for HTTP IP-pinning.

    For HTTP, rewrites the URL to use the pre-resolved IP and adds a Host header.
    For HTTPS, returns the original URL unchanged (TLS cert validation already
    prevents rebinding).
    """
    parsed = urlparse(url)
    if parsed.scheme == "http":
        port = parsed.port or 80
        path_qs = (parsed.path or "/") + (f"?{parsed.query}" if parsed.query else "")
        return f"http://{resolved_ip}:{port}{path_qs}", {"Host": parsed.hostname}
    return url, {}


def safe_get(
    url: str,
    *,
    timeout: float = _DEFAULT_TIMEOUT,
    max_bytes: int = _DEFAULT_MAX_BYTES,
    headers: Optional[dict] = None,
) -> bytes:
    """GET *url* with SSRF protection and body size limit.

    Raises :exc:`NetworkError` when the host resolves to a blocked IP range,
    on non-2xx responses, when the body exceeds *max_bytes*, or on any other
    transport failure.
    """
    if not _HTTPX_AVAILABLE:
        raise NetworkError("httpx is required for network operations")

    resolved_ip = _resolve_safe_ip(url)
    if resolved_ip is None:
        if _audit_fn:
            try:
                _audit_fn("ssrf_blocked", "warning", url)
            except Exception:
                pass
        raise NetworkError(f"blocked or unresolvable URL: {url!r}")

    pinned_url, extra_headers = _pin_url(url, resolved_ip)
    req_headers = {**(headers or {}), **extra_headers}

    try:
        with httpx.Client(timeout=timeout, follow_redirects=True, verify=_make_tls_context()) as client:
            with client.stream("GET", pinned_url, headers=req_headers) as resp:
                if resp.status_code < 200 or resp.status_code >= 300:
                    raise NetworkError(f"GET {url}: HTTP {resp.status_code}")
                chunks: list[bytes] = []
                received = 0
                for chunk in resp.iter_bytes(chunk_size=65_536):
                    received += len(chunk)
                    if received > max_bytes:
                        raise NetworkError(
                            f"GET {url}: response exceeds {max_bytes // 1024} KB"
                        )
                    chunks.append(chunk)
                return b"".join(chunks)
    except NetworkError:
        raise
    except Exception as exc:
        raise NetworkError(f"GET {url} failed: {exc}") from exc


async def async_safe_get(
    url: str,
    *,
    timeout: float = _DEFAULT_TIMEOUT,
    max_bytes: int = _DEFAULT_MAX_BYTES,
    headers: Optional[dict] = None,
) -> bytes:
    """Async GET with SSRF protection and body size limit.

    Raises :exc:`NetworkError` when blocked, non-2xx, or body exceeds *max_bytes*.
    """
    if not _HTTPX_AVAILABLE:
        raise NetworkError("httpx is required for network operations")

    resolved_ip = _resolve_safe_ip(url)
    if resolved_ip is None:
        if _audit_fn:
            try:
                _audit_fn("ssrf_blocked", "warning", url)
            except Exception:
                pass
        raise NetworkError(f"blocked or unresolvable URL: {url!r}")

    pinned_url, extra_headers = _pin_url(url, resolved_ip)
    req_headers = {**(headers or {}), **extra_headers}

    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, verify=_make_tls_context()) as client:
            async with client.stream("GET", pinned_url, headers=req_headers) as resp:
                if resp.status_code < 200 or resp.status_code >= 300:
                    raise NetworkError(f"GET {url}: HTTP {resp.status_code}")
                chunks: list[bytes] = []
                received = 0
                async for chunk in resp.aiter_bytes(chunk_size=65_536):
                    received += len(chunk)
                    if received > max_bytes:
                        raise NetworkError(
                            f"GET {url}: response exceeds {max_bytes // 1024} KB"
                        )
                    chunks.append(chunk)
                return b"".join(chunks)
    except NetworkError:
        raise
    except Exception as exc:
        raise NetworkError(f"GET {url} failed: {exc}") from exc


async def async_safe_head(
    url: str,
    *,
    timeout: float = _DEFAULT_TIMEOUT,
    headers: Optional[dict] = None,
) -> Optional[dict]:
    """Async HEAD with SSRF protection.

    Returns the response headers dict on success, ``None`` if blocked or failed.
    """
    if not _HTTPX_AVAILABLE:
        return None

    resolved_ip = _resolve_safe_ip(url)
    if resolved_ip is None:
        if _audit_fn:
            try:
                _audit_fn("ssrf_blocked", "warning", url)
            except Exception:
                pass
        return None

    pinned_url, extra_headers = _pin_url(url, resolved_ip)
    req_headers = {**(headers or {}), **extra_headers}

    try:
        async with httpx.AsyncClient(timeout=timeout, verify=_make_tls_context()) as client:
            resp = await client.head(pinned_url, headers=req_headers)
            return dict(resp.headers)
    except Exception:
        return None


def safe_post(
    url: str,
    payload: dict,
    *,
    timeout: float = _DEFAULT_TIMEOUT,
) -> bytes:
    """Synchronous POST *payload* as JSON to *url* with SSRF protection.

    Returns the response body bytes on HTTP 2xx.
    Raises :exc:`NetworkError` if blocked, non-2xx, or transport failure.
    """
    if not _HTTPX_AVAILABLE:
        raise NetworkError("httpx is required for network operations")

    resolved_ip = _resolve_safe_ip(url)
    if resolved_ip is None:
        if _audit_fn:
            try:
                _audit_fn("ssrf_blocked", "warning", url)
            except Exception:
                pass
        raise NetworkError(f"blocked or unresolvable URL: {url!r}")

    pinned_url, extra_headers = _pin_url(url, resolved_ip)
    req_headers = {"Content-Type": "application/json", **extra_headers}

    try:
        with httpx.Client(timeout=timeout, verify=_make_tls_context()) as client:
            resp = client.post(
                pinned_url,
                content=json.dumps(payload).encode(),
                headers=req_headers,
            )
            if resp.status_code < 200 or resp.status_code >= 300:
                raise NetworkError(f"POST {url}: HTTP {resp.status_code}")
            return resp.content
    except NetworkError:
        raise
    except Exception as exc:
        raise NetworkError(f"POST {url} failed: {exc}") from exc


async def async_safe_post_content(
    url: str,
    payload: dict,
    *,
    timeout: float = _DEFAULT_TIMEOUT,
) -> bytes:
    """Async POST *payload* as JSON with SSRF protection; returns response bytes.

    Raises :exc:`NetworkError` if blocked, non-2xx, or transport failure.
    """
    if not _HTTPX_AVAILABLE:
        raise NetworkError("httpx is required for network operations")

    resolved_ip = _resolve_safe_ip(url)
    if resolved_ip is None:
        if _audit_fn:
            try:
                _audit_fn("ssrf_blocked", "warning", url)
            except Exception:
                pass
        raise NetworkError(f"blocked or unresolvable URL: {url!r}")

    pinned_url, extra_headers = _pin_url(url, resolved_ip)
    req_headers = {"Content-Type": "application/json", **extra_headers}

    try:
        async with httpx.AsyncClient(timeout=timeout, verify=_make_tls_context()) as client:
            resp = await client.post(
                pinned_url,
                content=json.dumps(payload).encode(),
                headers=req_headers,
            )
            if resp.status_code < 200 or resp.status_code >= 300:
                raise NetworkError(f"POST {url}: HTTP {resp.status_code}")
            return resp.content
    except NetworkError:
        raise
    except Exception as exc:
        raise NetworkError(f"POST {url} failed: {exc}") from exc


async def async_safe_post(
    url: str,
    payload: dict,
    *,
    timeout: float = _DEFAULT_TIMEOUT,
) -> bool:
    """POST *payload* as JSON to *url* with SSRF protection.

    Returns ``True`` on HTTP 2xx, ``False`` on blocked hosts, non-2xx responses,
    or any transport error.  Never raises.
    """
    if not _HTTPX_AVAILABLE:
        return False

    resolved_ip = _resolve_safe_ip(url)
    if resolved_ip is None:
        if _audit_fn:
            try:
                _audit_fn("ssrf_blocked", "warning", url)
            except Exception:
                pass
        return False

    pinned_url, extra_headers = _pin_url(url, resolved_ip)
    req_headers = {"Content-Type": "application/json", **extra_headers}

    try:
        async with httpx.AsyncClient(timeout=timeout, verify=_make_tls_context()) as client:
            resp = await client.post(
                pinned_url,
                content=json.dumps(payload).encode(),
                headers=req_headers,
            )
            return resp.status_code < 300
    except Exception:
        return False
