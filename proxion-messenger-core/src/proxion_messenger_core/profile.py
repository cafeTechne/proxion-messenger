"""WebID profile management — fetch and update profile metadata from WebID documents.

A WebID profile is an RDF Turtle document that contains metadata about an agent,
such as their display name (foaf:name), avatar URL (foaf:img), and other public
information.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
import re


# Control characters that are illegal raw inside a Turtle literal (0x00-0x1f, 0x7f),
# minus tab/newline/carriage-return which are turned into two-character escapes first.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _escape_turtle_literal(value: str) -> str:
    """Escape a string for safe inclusion inside a ``"..."`` Turtle literal.

    Backslash, double-quote, newline, carriage-return and tab become their
    two-character escapes; any remaining control character is stripped. This
    prevents a name/bio from terminating the literal and injecting triples.
    """
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return _CONTROL_CHARS.sub("", escaped)


@dataclass
class WebIdProfile:
    """A WebID profile document with metadata."""
    
    webid: str
    name: Optional[str] = None
    avatar_url: Optional[str] = None
    bio: Optional[str] = None
    pod_url: Optional[str] = None


async def get_profile(webid: str) -> WebIdProfile:
    """Fetch and parse a WebID profile from its RDF document.
    
    Retrieves the WebID URL with Accept: text/turtle and scans the Turtle
    response for profile metadata (foaf:name, foaf:img, solid:storageUrl, etc.).
    
    Parameters
    ----------
    webid : str
        WebID URL to fetch (e.g., "https://alice.example/profile#me").
    
    Returns
    -------
    WebIdProfile
        Profile object with available metadata. Returns a minimal profile
        (webid only) if the fetch fails or no metadata is found.
    """
    profile = WebIdProfile(webid=webid)
    
    try:
        from .network import async_safe_get, NetworkError
        raw = await async_safe_get(webid, headers={"Accept": "text/turtle"}, timeout=5.0)
        body = raw.decode("utf-8", errors="replace")

        # Extract foaf:name (quoted string)
        name_match = re.search(r'foaf:name\s+"([^"]+)"', body)
        if name_match:
            profile.name = name_match.group(1)

        # Extract foaf:img (URL in angle brackets)
        img_match = re.search(r'foaf:img\s+<([^>]+)>', body)
        if img_match:
            profile.avatar_url = img_match.group(1)

        # Extract solid:storageUrl or solid:storage (pod URL)
        storage_match = re.search(r'solid:(?:storage|storageUrl)\s+<([^>]+)>', body)
        if storage_match:
            profile.pod_url = storage_match.group(1)

        # Extract foaf:bio if present
        bio_match = re.search(r'foaf:bio\s+"([^"]+)"', body)
        if bio_match:
            profile.bio = bio_match.group(1)

    except Exception:
        # Non-200, timeout, or other error — return minimal profile
        pass
    
    return profile


async def update_profile(
    client,
    webid: str,
    name: Optional[str] = None,
    avatar_url: Optional[str] = None,
    bio: Optional[str] = None,
) -> None:
    """Update a WebID profile document.
    
    Builds a minimal Turtle RDF document from the provided fields and PUTs
    it to the WebID URL via the provided client.
    
    Parameters
    ----------
    client : httpx.AsyncClient or similar
        HTTP client for making the PUT request.
    webid : str
        WebID URL to update.
    name : Optional[str]
        Display name to set (foaf:name).
    avatar_url : Optional[str]
        Avatar image URL to set (foaf:img).
    bio : Optional[str]
        Short bio to set (foaf:bio).
    
    Raises
    ------
    ValueError
        If ``webid`` or ``avatar_url`` contains characters unsafe for Turtle IRIs.
    httpx.HTTPStatusError
        If the PUT request returns a non-2xx status code.
    """
    # Validate the IRIs and escape the string literals before interpolation so a
    # hostile name/bio/webid/avatar_url cannot inject triples into the profile.
    from .solid_client import _assert_safe_webid

    _assert_safe_webid(webid)
    if avatar_url:
        _assert_safe_webid(avatar_url)

    # Build Turtle document from non-None fields
    turtle_lines = [
        "@prefix foaf: <http://xmlns.com/foaf/0.1/> .",
        "@prefix solid: <http://www.w3.org/ns/solid/terms#> .",
        "",
        f"<{webid}> a foaf:Person ;",
    ]

    fields = []
    if name:
        fields.append(f'  foaf:name "{_escape_turtle_literal(name)}" ;')
    if avatar_url:
        fields.append(f'  foaf:img <{avatar_url}> ;')
    if bio:
        fields.append(f'  foaf:bio "{_escape_turtle_literal(bio)}" ;')
    
    if fields:
        turtle_lines.extend(fields[:-1])
        turtle_lines.append(fields[-1].rstrip(" ;") + " .")
    else:
        turtle_lines[-1] = turtle_lines[-1].rstrip(" ;") + " ."
    
    turtle = "\n".join(turtle_lines)
    
    resp = await client.put(
        webid,
        content=turtle,
        headers={"Content-Type": "text/turtle"},
    )
    resp.raise_for_status()
