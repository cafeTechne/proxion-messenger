"""Solid Access Control Policy (ACP) support alongside WAC.

CSS 7 supports both WAC (``/acl`` resources) and ACP (``/acr`` resources).
ACP is more expressive and is the direction Solid 1.0 is heading.

This module provides:
- :func:`detect_acl_mode` — probe a Pod to find out if it uses WAC or ACP
- :func:`set_acp_policy` — write an ACP Access Control Resource
- :func:`set_acl_auto` — auto-detect mode and call the right setter
"""

from __future__ import annotations

import json
from typing import List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .solid_client import SolidClient


# ---------------------------------------------------------------------------
# Term allowlist and validation
# ---------------------------------------------------------------------------

# Known ACP v1 (JSON-LD) and v3 (Turtle) predicates that are security-critical.
# Any unknown predicate in this namespace is rejected to prevent silent policy
# injection when migrating between term vocabularies.
_KNOWN_ACP_PREDICATES = frozenset({
    # ACP core
    "allow", "deny", "allOf", "anyOf", "noneOf", "agent", "group",
    "accessControl", "memberAccessControl", "policy", "default",
    # Proxion-internal document keys (JSON-LD subject stanzas)
    "owner", "subject",
    # ACP v3 classes
    "AccessControlResource", "Policy", "Matcher",
    # ACL modes (via acl: prefix)
    "Read", "Write", "Control", "Append",
})

# Predicates in the ACP namespace that must always be known (critical policy path)
_CRITICAL_ACP_NAMESPACE = "http://www.w3.org/ns/solid/acp#"
_CRITICAL_ACL_NAMESPACE = "http://www.w3.org/ns/auth/acl#"


def validate_acp_predicates(policy: dict) -> None:
    """Validate that *policy* only uses known ACP predicates.

    Raises
    ------
    ValueError
        If any key in *policy* or nested ``policy``/``owner`` dicts is not in
        the known predicate allowlist.  Only top-level and one level of nesting
        are checked — deep nesting is an error in itself.
    """
    def _check_keys(d: dict, context: str) -> None:
        for key in d:
            if key.startswith("@"):
                continue  # JSON-LD keywords (@context, @type, etc.)
            local = key.split("#")[-1].split("/")[-1]
            if local not in _KNOWN_ACP_PREDICATES:
                raise ValueError(
                    f"Unknown ACP predicate {key!r} in {context}: "
                    f"not in allowlist.  Add to _KNOWN_ACP_PREDICATES if intentional."
                )
            if isinstance(d[key], dict):
                _check_keys(d[key], f"{context}.{key}")

    if not isinstance(policy, dict):
        raise TypeError(f"ACP policy must be a dict, got {type(policy).__name__}")
    _check_keys(policy, "policy")


def detect_acl_mode(pod_client: SolidClient, container_url: str, strict: bool = False) -> str:
    """Return ``"wac"`` or ``"acp"`` based on the Pod's Link header.

    Sends a HEAD request to *container_url* and inspects the ``Link`` header:
    - ``rel="acl"`` → WAC
    - ``rel="acr"`` → ACP

    When *strict* is ``True``, propagates any exception from the HEAD request
    rather than silently defaulting to ``"wac"``.  Use this when a silent
    downgrade to a weaker policy is unacceptable.
    """
    try:
        headers = pod_client.head(container_url)
        link = ""
        if isinstance(headers, dict):
            link = headers.get("Link", headers.get("link", ""))
        if 'rel="acr"' in link or "rel=acr" in link:
            return "acp"
        return "wac"
    except Exception:
        if strict:
            raise
        return "wac"


def set_acp_policy(
    pod_client: SolidClient,
    resource_url: str,
    owner_webid: str,
    subject_webid: str,
    subject_modes: Optional[List[str]] = None,
) -> str:
    """Write an ACP Access Control Resource (ACR) for *resource_url*.

    Generates a JSON-LD ACP policy document and PUTs it to
    ``resource_url + ".acr"``.

    Parameters
    ----------
    pod_client:
        Authenticated client for the Pod.
    resource_url:
        The stash:// or http:// URL of the resource to protect.
    owner_webid:
        WebID of the resource owner (gets full Read/Write/Control access).
    subject_webid:
        WebID of the agent being granted access.
    subject_modes:
        List of ACP mode strings, e.g. ``["Read"]`` or ``["Read", "Write"]``.
        Defaults to ``["Read"]``.

    Returns
    -------
    str
        The ACR URI that was written (``resource_url + ".acr"``).
    """
    if subject_modes is None:
        subject_modes = ["Read"]

    policy = {
        "@context": "http://www.w3.org/ns/solid/acp#",
        "policy": {
            "allow": subject_modes,
            "allOf": [{"agent": subject_webid}],
        },
        "owner": {
            "allow": ["Read", "Write", "Control"],
            "allOf": [{"agent": owner_webid}],
        },
    }

    acr_url = resource_url + ".acr"
    pod_client.put(
        acr_url,
        json.dumps(policy).encode("utf-8"),
        content_type="application/ld+json",
    )
    return acr_url


def set_acp_v3_policy(
    pod_client: "SolidClient",
    resource_url: str,
    owner_webid: str,
    subject_webid: str,
    subject_modes: Optional[List[str]] = None,
) -> str:
    """Write an ACP v3 Access Control Resource in Turtle for Inrupt ESS.

    Generates a Turtle-encoded ACR using ``acp:AccessControlResource``,
    ``acp:Policy``, and ``acp:Matcher`` compatible with ESS ACP v3.

    Parameters
    ----------
    pod_client:
        Authenticated client for the Pod.
    resource_url:
        The URL of the resource to protect.
    owner_webid:
        WebID of the resource owner (gets Read/Write/Control).
    subject_webid:
        WebID of the agent being granted access.
    subject_modes:
        List of ACP mode strings, e.g. ``["Read"]``.  Defaults to ``["Read"]``.

    Returns
    -------
    str
        The ACR URI that was written (``resource_url + ".acr"``).
    """
    from .solid_client import _assert_safe_webid
    _assert_safe_webid(owner_webid)
    _assert_safe_webid(subject_webid)

    if subject_modes is None:
        subject_modes = ["Read"]

    modes_str = " ".join(f"acl:{m}" for m in subject_modes)
    turtle = (
        "@prefix acp: <http://www.w3.org/ns/solid/acp#> .\n"
        "@prefix acl: <http://www.w3.org/ns/auth/acl#> .\n\n"
        "<> a acp:AccessControlResource ;\n"
        "    acp:policy <#owner-policy>, <#subject-policy> .\n\n"
        "<#owner-policy> a acp:Policy ;\n"
        "    acp:allow acl:Read, acl:Write, acl:Control ;\n"
        "    acp:allOf <#owner-matcher> .\n\n"
        f"<#owner-matcher> a acp:Matcher ;\n"
        f"    acp:agent <{owner_webid}> .\n\n"
        "<#subject-policy> a acp:Policy ;\n"
        f"    acp:allow {modes_str} ;\n"
        "    acp:allOf <#subject-matcher> .\n\n"
        f"<#subject-matcher> a acp:Matcher ;\n"
        f"    acp:agent <{subject_webid}> .\n"
    )
    acr_url = resource_url + ".acr"
    pod_client.put(acr_url, turtle.encode("utf-8"), content_type="text/turtle")
    return acr_url


def set_acl_auto(
    pod_client: "SolidClient",
    stash_uri: str,
    owner_webid: str,
    subject_webid: str,
    subject_modes: Optional[List[str]] = None,
    pod_type: str = "css",
) -> str:
    """Auto-detect WAC vs ACP and call the appropriate setter.

    For WAC pods calls ``pod_client.set_acl()``.
    For ACP pods on CSS calls :func:`set_acp_policy` (JSON-LD).
    For ACP pods on ESS calls :func:`set_acp_v3_policy` (Turtle).

    Pass ``pod_type="ess"`` when the server is known to be Inrupt ESS.

    Returns the ACL/ACR resource URI that was written.
    """
    mode = detect_acl_mode(pod_client, stash_uri)
    if mode == "acp":
        if pod_type == "ess":
            return set_acp_v3_policy(pod_client, stash_uri, owner_webid, subject_webid, subject_modes)
        return set_acp_policy(pod_client, stash_uri, owner_webid, subject_webid, subject_modes)

    # WAC path
    modes = subject_modes or ["Read"]
    pod_client.set_acl(stash_uri, owner_webid, subject_webid, modes)
    return stash_uri + ".acl"


def set_acl_multi_auto(
    pod_client: "SolidClient",
    stash_uri: str,
    owner_webid: str,
    subject_webids: List[str],
    subject_modes: Optional[List[str]] = None,
    pod_type: str = "css",
) -> str:
    """Like set_acl_auto but grants access to multiple subjects in one document.

    For WAC pods writes a single Turtle document with one stanza per subject.
    For ACP pods writes one policy document per subject.
    For ESS pods uses ACP v3 Turtle format instead of JSON-LD.

    Returns the URI of the last ACL/ACR resource written.

    Detection uses *strict* mode: if the HEAD probe fails, this function raises
    rather than silently falling back to WAC.  A silent WAC fallback on an ACP
    pod would write a policy document the server ignores, leaving the resource
    unprotected.
    """
    mode = detect_acl_mode(pod_client, stash_uri, strict=True)
    if mode == "acp":
        last = stash_uri + ".acr"
        for webid in subject_webids:
            if pod_type == "ess":
                last = set_acp_v3_policy(pod_client, stash_uri, owner_webid, webid, subject_modes)
            else:
                last = set_acp_policy(pod_client, stash_uri, owner_webid, webid, subject_modes)
        return last

    # WAC path — single document, all subjects
    pod_client.set_acl_multi(stash_uri, owner_webid, subject_webids, subject_modes)
    return stash_uri + ".acl"


async def detect_pod_type(pod_url: str) -> str:
    """Detect the type of Solid server hosting a Pod.

    Makes a HEAD request to the pod URL and inspects Server, X-Powered-By,
    Link, and WWW-Authenticate headers to reliably distinguish between CSS
    and ESS.

    Parameters
    ----------
    pod_url : str
        The base URL of the pod (e.g., https://alice.pod.inrupt.com).

    Returns
    -------
    str
        One of ``"css"``, ``"ess"``, or ``"unknown"``.
    """
    from .network import async_safe_head

    resp_headers = await async_safe_head(pod_url)
    if resp_headers is None:
        return "unknown"

    server = resp_headers.get("server", "").lower()
    xpb = resp_headers.get("x-powered-by", "").lower()
    link = resp_headers.get("link", "").lower()
    www_auth = resp_headers.get("www-authenticate", "").lower()

    # ESS signals: Inrupt branding in server/x-powered-by or WWW-Authenticate
    if "inrupt" in server or "enterprise-solid-server" in server:
        return "ess"
    if "ess" in xpb or "inrupt" in xpb:
        return "ess"
    if "inrupt" in www_auth or "ess" in www_auth:
        return "ess"

    # CSS signals (server header uses spaces: "Community Solid Server/7.x")
    if "community-solid-server" in server or "community solid server" in server or "css" in xpb:
        return "css"

    # ACP Link header without a CSS signal — ESS is the primary ACP-v3 server
    if 'rel="acr"' in link or "rel=acr" in link:
        return "ess"

    return "unknown"
