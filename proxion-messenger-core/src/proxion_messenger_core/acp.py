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
    "apply", "resource",
    # Proxion-internal document keys (JSON-LD subject stanzas)
    "owner", "subject",
    # ACP v3 classes
    "AccessControl", "AccessControlResource", "Policy", "Matcher",
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


def _resource_url(pod_client, uri: str) -> str:
    resolver = getattr(pod_client, "_resolve_uri", None)
    resolved = resolver(uri) if callable(resolver) else uri
    return resolved if isinstance(resolved, str) else uri


def _access_control(pod_client, resource_url: str) -> tuple[str, str]:
    """Discover the advertised ACL/ACR, including CSS's rel=acl .acr links."""
    import re
    from urllib.parse import urljoin, urlsplit

    headers = pod_client.head(resource_url)
    link = headers.get("link", headers.get("Link", "")) if isinstance(headers, dict) else ""
    candidates = []
    for match in re.finditer(r'<([^>]*)>([^,]*)', link if isinstance(link, str) else ""):
        uri, params = match.groups()
        relation = re.search(r'\brel\s*=\s*(?:"([^"]*)"|([^;\s]+))', params, re.I)
        if not relation:
            continue
        relations = (relation.group(1) or relation.group(2)).split()
        explicit = "http://www.w3.org/ns/solid/acp#accessControl" in relations or "acr" in relations
        if not explicit and "acl" not in relations:
            continue
        # Real clients resolve stash:// before discovery. Retain conventional
        # locations for injected clients that implement only head/put.
        if resource_url.startswith("stash://") and not urlsplit(uri).scheme:
            url = urljoin(resource_url.replace("stash://", "https://", 1), uri).replace("https://", "stash://", 1)
        else:
            url = urljoin(resource_url, uri)
        parsed, base = urlsplit(url), urlsplit(resource_url)
        if (parsed.scheme, parsed.netloc) != (base.scheme, base.netloc) or parsed.username or parsed.password:
            raise ValueError("Access-control link leaves the resource origin")
        model = "acp" if explicit or parsed.path.endswith(".acr") else "wac"
        candidates.append((explicit, url, model))
    if candidates:
        _, url, model = max(candidates, key=lambda item: item[0])
        return url, model
    return resource_url + ".acl", "wac"


def detect_acl_mode(pod_client: SolidClient, container_url: str, strict: bool = False) -> str:
    """Detect ACP from its relation or .acr target; optionally propagate failures."""
    try:
        return _access_control(pod_client, _resource_url(pod_client, container_url))[1]
    except Exception:
        if strict:
            raise
        return "wac"


def set_acp_multi_policy(pod_client: "SolidClient", resource_url: str, owner_webid: str,
                         subject_webids: List[str], subject_modes: Optional[List[str]] = None,
                         *, turtle: bool = False) -> str:
    """Replace one complete ACR with all members and inherited child access.

    Empty membership actively writes owner-only access, revoking old grants.
    Both encodings use the resolved HTTP resource and discovered ACR location.
    """
    from .solid_client import _assert_safe_webid
    resource = _resource_url(pod_client, resource_url)
    subjects = list(dict.fromkeys(subject_webids))
    for identity in [resource, owner_webid, *subjects]:
        _assert_safe_webid(identity)
    modes = ["Read"] if subject_modes is None else subject_modes
    if any(mode not in {"Read", "Write", "Append", "Control"} for mode in modes):
        raise ValueError("Unsupported access-control mode")
    controls = [("owner", owner_webid, ["Read", "Write", "Control"])]
    if modes:
        controls += [("subject" if len(subjects) == 1 else f"subject-{i}", webid, modes)
                     for i, webid in enumerate(subjects) if webid != owner_webid]
    discovered, model = _access_control(pod_client, resource)
    acr_url = discovered if model == "acp" else resource + ".acr"
    if turtle:
        refs = ", ".join(f"<#{name}-ac>" for name, _, _ in controls)
        body = (
            "@prefix acp: <http://www.w3.org/ns/solid/acp#> .\n"
            "@prefix acl: <http://www.w3.org/ns/auth/acl#> .\n\n"
            f"<> a acp:AccessControlResource ; acp:resource <{resource}> ;\n"
            f"    acp:accessControl {refs} ;\n    acp:memberAccessControl {refs} .\n"
        )
        for name, agent, grants in controls:
            body += (f"<#{name}-ac> a acp:AccessControl ; acp:apply <#{name}-policy> .\n"
                     f"<#{name}-policy> a acp:Policy ; acp:allow {', '.join('acl:' + m for m in grants)} ; acp:anyOf <#{name}-matcher> .\n"
                     f"<#{name}-matcher> a acp:Matcher ; acp:agent <{agent}> .\n")
        content_type = "text/turtle"
    else:
        policy = {
            "@context": {"@vocab": "http://www.w3.org/ns/solid/acp#",
                         "acp": "http://www.w3.org/ns/solid/acp#", "acl": "http://www.w3.org/ns/auth/acl#",
                         "allow": {"@id": "acp:allow", "@type": "@id"}},
            "@type": "acp:AccessControlResource", "resource": {"@id": resource},
            "accessControl": [{"@id": f"#{name}-ac", "@type": "acp:AccessControl",
                               "apply": {"@type": "acp:Policy", "allow": ["acl:" + mode for mode in grants],
                                         "anyOf": {"@type": "acp:Matcher", "agent": {"@id": agent}}}}
                              for name, agent, grants in controls],
            "memberAccessControl": [{"@id": f"#{name}-ac"} for name, _, _ in controls],
        }
        body = json.dumps(policy)
        content_type = "application/ld+json"
    pod_client.put(acr_url, body.encode("utf-8"), content_type=content_type)
    return acr_url


def set_acp_policy(pod_client: "SolidClient", resource_url: str, owner_webid: str,
                   subject_webid: str, subject_modes: Optional[List[str]] = None) -> str:
    """Write an ACP JSON-LD grant for one subject and inherited children."""
    return set_acp_multi_policy(pod_client, resource_url, owner_webid, [subject_webid], subject_modes)


def set_acp_v3_policy(pod_client: "SolidClient", resource_url: str, owner_webid: str,
                      subject_webid: str, subject_modes: Optional[List[str]] = None) -> str:
    """Write the same ACP policy graph as Turtle for ESS-compatible servers."""
    return set_acp_multi_policy(pod_client, resource_url, owner_webid, [subject_webid], subject_modes, turtle=True)


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
    mode = detect_acl_mode(pod_client, stash_uri, strict=True)
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
    For ACP pods writes one complete policy document for all subjects.
    For ESS pods uses ACP v3 Turtle format instead of JSON-LD.

    Returns the URI of the ACL/ACR resource written.

    Detection uses *strict* mode: if the HEAD probe fails, this function raises
    rather than silently falling back to WAC.  A silent WAC fallback on an ACP
    pod would write a policy document the server ignores, leaving the resource
    unprotected.
    """
    mode = detect_acl_mode(pod_client, stash_uri, strict=True)
    if mode == "acp":
        return set_acp_multi_policy(pod_client, stash_uri, owner_webid, subject_webids,
                                    subject_modes, turtle=pod_type == "ess")

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
