"""Pure policy for the gateway's pod bridge (desktop <-> browser-build interop).

The browser build finds a contact's pod from their WebID alone: a CSS-style WebID
``{root}profile/card#me`` gives the storage root ``{root}``, anything else falls
back to the WebID's origin (``web/webdm.js`` peerPodRootFromWebId). Peers deliver
DMs to that derived root, so the desktop app can only take part when the pod its
gateway is connected to IS that root. Pods whose storage lives elsewhere (split
WebID and storage) are refused with a reason instead of silently missing drops.
"""
from __future__ import annotations

from typing import Optional
from urllib.parse import urlsplit


def pod_root_from_webid(webid: str) -> Optional[str]:
    """The storage root a browser-build peer derives from *webid*, or None.
    Mirrors peerPodRootFromWebId in web/webdm.js exactly."""
    if not isinstance(webid, str) or not webid:
        return None
    no_frag = webid.split("#", 1)[0]
    i = no_frag.find("/profile/")
    if i > 0:
        return no_frag[: i + 1]
    try:
        parts = urlsplit(webid)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}/"


def _norm_root(url: str) -> str:
    return url if url.endswith("/") else url + "/"


def check_pod_root(webid: Optional[str], pod_url: Optional[str]) -> tuple[bool, str]:
    """Whether the connected pod can carry browser-build DMs for *webid*.

    Returns ``(ok, reason)``; reason is a stable code the client maps to text:
    ``no_pod`` (nothing connected), ``root_mismatch`` (peers would deliver to a
    different place than the pod the gateway reads), or ``""`` when ok."""
    if not webid or not pod_url:
        return False, "no_pod"
    derived = pod_root_from_webid(webid)
    if not derived:
        return False, "root_mismatch"
    try:
        a, b = urlsplit(derived), urlsplit(_norm_root(pod_url))
    except ValueError:
        return False, "root_mismatch"
    same = (a.scheme.lower() == b.scheme.lower() and a.netloc.lower() == b.netloc.lower()
            and a.path == b.path)
    return (True, "") if same else (False, "root_mismatch")
