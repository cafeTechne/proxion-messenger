"""Link preview generation with SSRF protection."""
from __future__ import annotations

import logging
import re
from typing import Optional, Dict
from urllib.parse import urlparse

from .network import _resolve_safe_ip

logger = logging.getLogger(__name__)


def is_safe_url(url: str) -> bool:
    """Return True only if *url* is safe to fetch (SSRF protection)."""
    return _resolve_safe_ip(url) is not None


async def fetch_link_preview(url: str) -> Optional[Dict[str, str]]:
    """Fetch OpenGraph metadata from a URL.

    Resolves the hostname once and pins HTTP connections to that IP to prevent
    DNS rebinding (TOCTOU). Redirects are followed manually so each hop is
    re-validated before the next request is made.
    """
    resolved_ip = _resolve_safe_ip(url)
    if resolved_ip is None:
        logger.warning("SSRF protection blocked URL: %s", url)
        return None

    try:
        import httpx
    except ImportError:
        logger.error("httpx is required for link previews")
        return None

    try:
        base_headers = {"User-Agent": "Proxion/1.0 (LinkPreview; +https://proxion.chat)"}
        async with httpx.AsyncClient(
            timeout=3.0,
            follow_redirects=False,  # Manual per-hop re-validation below
            limits=httpx.Limits(max_keepalive_connections=5),
        ) as client:
            current_url = url
            current_ip = resolved_ip

            for _hop in range(4):  # allow up to 3 redirects
                parsed = urlparse(current_url)
                # Pin HTTP to the resolved IP (no TLS concern).
                # HTTPS relies on TLS cert verification; a private-IP cert
                # cannot be obtained from a public CA, so rebinding fails there.
                if parsed.scheme == "http":
                    port = parsed.port or 80
                    path_qs = (parsed.path or "/") + (f"?{parsed.query}" if parsed.query else "")
                    fetch_url = f"http://{current_ip}:{port}{path_qs}"
                    req_headers = {**base_headers, "Host": parsed.hostname}
                else:
                    fetch_url = current_url
                    req_headers = base_headers

                _PREVIEW_MAX_BYTES = 512 * 1024  # 512 KB — cap against decompression bombs
                chunks: list[bytes] = []
                total = 0
                async with client.stream("GET", fetch_url, headers=req_headers) as resp:
                    if resp.status_code not in (200, 301, 302, 303, 307, 308):
                        return None
                    if resp.status_code not in (301, 302, 303, 307, 308):
                        async for chunk in resp.aiter_bytes(chunk_size=8192):
                            chunks.append(chunk)
                            total += len(chunk)
                            if total >= _PREVIEW_MAX_BYTES:
                                break

                if resp.status_code not in (301, 302, 303, 307, 308):
                    break

                location = resp.headers.get("location", "")
                if not location:
                    break
                if location.startswith("/"):
                    location = f"{parsed.scheme}://{parsed.hostname}{location}"
                redirect_ip = _resolve_safe_ip(location)
                if redirect_ip is None:
                    logger.warning("SSRF protection blocked redirect to: %s", location)
                    return None
                current_url = location
                current_ip = redirect_ip

            if resp.status_code != 200:
                return None

            html = b"".join(chunks).decode("utf-8", errors="replace")
            preview: Dict[str, str] = {
                "url": url,
                "title": "",
                "description": "",
                "image": "",
            }

            # OpenGraph title
            title_match = re.search(r'<meta property="og:title" content="([^"]+)"', html)
            if title_match:
                preview["title"] = title_match.group(1)
            else:
                title_tag = re.search(r'<title>([^<]+)</title>', html, re.IGNORECASE)
                if title_tag:
                    preview["title"] = title_tag.group(1).strip()

            # OpenGraph description
            desc_match = re.search(r'<meta property="og:description" content="([^"]+)"', html)
            if desc_match:
                preview["description"] = desc_match.group(1)
            else:
                desc_meta = re.search(r'<meta name="description" content="([^"]+)"', html, re.IGNORECASE)
                if desc_meta:
                    preview["description"] = desc_meta.group(1)

            # OpenGraph image
            img_match = re.search(r'<meta property="og:image" content="([^"]+)"', html)
            if img_match:
                preview["image"] = img_match.group(1)

            return preview
    except Exception as e:
        logger.debug("Failed to fetch preview for %s: %s", url, e)
        return None


def extract_urls(text: str) -> list[str]:
    """Extract http/https URLs from text, excluding trailing punctuation."""
    urls = re.findall(r'(https?://[^\s<>"]+)', text)
    return [url.rstrip(".,!?:;") for url in urls]
