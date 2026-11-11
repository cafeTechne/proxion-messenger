"""Pure policy for the gateway's pod bridge (desktop <-> browser-build interop).

The browser build finds a contact's pod from their WebID alone: a CSS-style WebID
``{root}profile/card#me`` gives the storage root ``{root}``, anything else falls
back to the WebID's origin (``web/webdm.js`` peerPodRootFromWebId). Peers deliver
DMs to that derived root, so the desktop app can only take part when the pod its
gateway is connected to IS that root. Pods whose storage lives elsewhere (split
WebID and storage) are refused with a reason instead of silently missing drops.
"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urljoin, urlsplit


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


# ── Request policy ───────────────────────────────────────────────────────────
#
# The bridge only ever touches the handful of resources the browser build's
# drop-box protocol uses. Everything else is refused before any I/O.

MAX_REQUEST_BODY = 64 * 1024
MAX_SMALL_RESPONSE = 64 * 1024
MAX_LISTING_RESPONSE = 512 * 1024

BOXES = ("dm", "call", "join")
# Boxes that carry messages to one consuming device (property: one consumer).
CONSUMER_BOXES = frozenset(BOXES)

_DROP_NAME = r"[A-Za-z0-9][A-Za-z0-9._-]{0,200}"
_ACL_SUFFIX = r"\.(?:acl|acr)"
# Room ids as the clients mint them (pod.js SAFE_ID_RE), and a Long Chat day path.
_ROOM_ID = r"[A-Za-z0-9_-]{1,128}"
_DAY = r"\d{4}/\d{2}/\d{2}"
_DM_ASSET_ID = r"[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}"
_DM_ASSET = rf"asset-{_DM_ASSET_ID}-(?:[0-9]|[1-5][0-9]|6[0-4])\.json"

# (regex over the path relative to our pod root, allowed methods, kind, max response)
_OWN_RULES = [
    (r"proxion/dm-assets/", {"HEAD", "PUT"}, "dm_assets", MAX_SMALL_RESPONSE),
    (rf"proxion/dm-assets/{_ACL_SUFFIX}", {"GET", "HEAD", "PUT"}, "dm_asset_acl", MAX_SMALL_RESPONSE),
    (rf"proxion/dm-assets/{_DM_ASSET}", {"GET", "HEAD", "PUT", "DELETE"}, "dm_asset", MAX_SMALL_RESPONSE),
    (rf"proxion/({'|'.join(BOXES)})-inbox/", {"GET", "HEAD", "PUT", "POST"}, "box", MAX_LISTING_RESPONSE),
    (rf"proxion/({'|'.join(BOXES)})-inbox/{_DROP_NAME}", {"GET", "HEAD", "DELETE"}, "drop", MAX_SMALL_RESPONSE),
    (rf"proxion/({'|'.join(BOXES)})-inbox/{_ACL_SUFFIX}", {"GET", "HEAD", "PUT"}, "acl", MAX_LISTING_RESPONSE),
    (r"proxion/identity/(signer|x25519-pub)\.json", {"GET", "HEAD", "PUT"}, "identity", MAX_SMALL_RESPONSE),
    (rf"proxion/identity/(signer|x25519-pub)\.json{_ACL_SUFFIX}", {"GET", "HEAD", "PUT"}, "acl", MAX_LISTING_RESPONSE),
    (r"proxion/presence\.json", {"GET", "HEAD", "PUT"}, "presence", MAX_SMALL_RESPONSE),
    (rf"proxion/presence\.json{_ACL_SUFFIX}", {"GET", "HEAD", "PUT"}, "acl", MAX_LISTING_RESPONSE),
    (r"proxion/join-requests/[0-9a-f]{64}\.jsonld", {"GET", "HEAD", "PUT", "DELETE"}, "marker", MAX_SMALL_RESPONSE),
    (r"proxion/joined-rooms\.json(?:ld)?", {"GET", "HEAD", "PUT"}, "joined", MAX_LISTING_RESPONSE),
    # Pod rooms the owner hosts (a Long Chat that members append to). The
    # descriptor and its owner-only ACL live under rooms/, the chat under
    # proxion/rooms/. The public type index is how the room is listed again.
    # The owner can also recover descriptors whose type-index write failed.
    (r"rooms/", {"GET", "HEAD"}, "owned_rooms", MAX_LISTING_RESPONSE),
    (rf"rooms/({_ROOM_ID})/room\.json", {"GET", "HEAD", "PUT", "DELETE"}, "descriptor", MAX_SMALL_RESPONSE),
    (rf"rooms/({_ROOM_ID})/", {"HEAD"}, "room_meta", MAX_SMALL_RESPONSE),
    (rf"rooms/({_ROOM_ID})/{_ACL_SUFFIX}", {"GET", "HEAD", "PUT"}, "acl", MAX_LISTING_RESPONSE),
    (rf"proxion/rooms/({_ROOM_ID})/", {"HEAD", "POST"}, "room_container", MAX_SMALL_RESPONSE),
    (rf"proxion/rooms/({_ROOM_ID})/asset-[A-Za-z0-9_-]{{1,160}}\.json", {"GET", "HEAD"}, "room_asset", MAX_SMALL_RESPONSE),
    (rf"proxion/rooms/({_ROOM_ID})/{_ACL_SUFFIX}", {"GET", "HEAD", "PUT"}, "acl", MAX_LISTING_RESPONSE),
    (rf"proxion/rooms/({_ROOM_ID})/index\.ttl", {"GET", "HEAD", "PUT"}, "room_index", MAX_SMALL_RESPONSE),
    (rf"proxion/rooms/({_ROOM_ID})/{_DAY}/chat\.ttl", {"GET", "HEAD", "PATCH"}, "room_day", MAX_LISTING_RESPONSE),
    (rf"proxion/rooms/({_ROOM_ID})/pins\.json", {"GET", "HEAD", "PUT"}, "room_pins", MAX_SMALL_RESPONSE),
    (rf"proxion/rooms/({_ROOM_ID})/members\.jsonld", {"GET", "HEAD", "PUT"}, "room_members", MAX_SMALL_RESPONSE),
    (r"settings/publicTypeIndex\.ttl", {"GET", "HEAD", "PUT", "PATCH"}, "typeindex", MAX_LISTING_RESPONSE),
    (rf"settings/publicTypeIndex\.ttl{_ACL_SUFFIX}", {"GET", "HEAD", "PUT"}, "acl", MAX_LISTING_RESPONSE),
    (r"profile/card", {"GET", "HEAD", "PATCH"}, "own_profile", MAX_LISTING_RESPONSE),
]
_OWN_RULES = [(re.compile(p + r"\Z"), m, k, cap) for p, m, k, cap in _OWN_RULES]

# A room on someone else's pod that we joined: authenticated (DPoP) reads and
# appends, only under a container the client registered after an approved join
# (pod_room_register), on Long Chat files, the read-only signed pin index, and
# immutable room attachment chunks.
# Relative to the container.
_ROOM_RULES = [
    (re.compile(r"\Z"), {"POST"}, "room_container", MAX_SMALL_RESPONSE),
    (re.compile(r"asset-[A-Za-z0-9_-]{1,160}\.json\Z"), {"GET", "HEAD"}, "room_asset", MAX_SMALL_RESPONSE),
    (re.compile(r"index\.ttl\Z"), {"GET", "HEAD"}, "room_index", MAX_SMALL_RESPONSE),
    (re.compile(rf"{_DAY}/chat\.ttl\Z"), {"GET", "HEAD", "PATCH"}, "room_day", MAX_LISTING_RESPONSE),
    (re.compile(r"pins\.json\Z"), {"GET", "HEAD"}, "room_pins", MAX_SMALL_RESPONSE),
    (re.compile(r"members\.jsonld\Z"), {"GET", "HEAD"}, "room_members", MAX_SMALL_RESPONSE),
]
MAX_JOINED_ROOMS = 200

# Foreign pods: unauthenticated, and only the public pieces of the protocol.
# Matched against the END of the path, so any pod root works.
_FOREIGN_RULES = [
    (re.compile(rf"/proxion/dm-assets/{_DM_ASSET}\Z"), {"GET", "HEAD"}, "dm_asset", MAX_SMALL_RESPONSE),
    (re.compile(r"/proxion/identity/(signer|x25519-pub)\.json\Z"), {"GET", "HEAD"}, "identity", MAX_SMALL_RESPONSE),
    (re.compile(r"/proxion/presence\.json\Z"), {"GET", "HEAD"}, "presence", MAX_SMALL_RESPONSE),
    (re.compile(rf"/proxion/({'|'.join(BOXES)})-inbox/\Z"), {"POST"}, "box", MAX_SMALL_RESPONSE),
    (re.compile(r"/profile/card\Z"), {"GET", "HEAD"}, "profile", MAX_LISTING_RESPONSE),
]

_CONTENT_TYPES = frozenset({"application/json", "application/ld+json", "text/turtle",
                            "text/n3", "application/sparql-update"})
_CONTAINER_LINK = '<http://www.w3.org/ns/ldp#BasicContainer>; rel="type"'


class Decision:
    __slots__ = ("allow", "reason", "own", "kind", "box", "max_bytes", "url", "auth", "room")

    def __init__(self, allow, reason="", own=False, kind="", box="", max_bytes=0, url="",
                 auth=False, room=""):
        self.allow, self.reason, self.own, self.kind = allow, reason, own, kind
        self.box, self.max_bytes, self.url = box, max_bytes, url
        # auth: a foreign request made WITH our credentials (a joined room).
        # room: the room id a room path names.
        self.auth, self.room = auth, room

    def __repr__(self):   # pragma: no cover - debugging aid
        return f"Decision({self.allow}, {self.reason!r}, own={self.own}, kind={self.kind!r})"


def _deny(reason: str) -> Decision:
    return Decision(False, reason)


def _clean_url(url) -> Optional[tuple]:
    """Split *url* when it is a plain absolute http(s) URL with no query,
    fragment, credentials, encoded characters or dot segments; else None."""
    if not isinstance(url, str) or not url or len(url) > 2048:
        return None
    if any(c in url for c in ("?", "#", "%", "\\", " ", "\t", "\r", "\n", "@")):
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname or not parts.path.startswith("/"):
        return None
    segs = parts.path.split("/")
    if any(s in (".", "..") for s in segs) or "//" in parts.path:
        return None
    return parts


def classify(method: str, url: str, pod_root: Optional[str], rooms=None) -> Decision:
    """Decide whether the bridge may make *method* on *url*.

    Under our own pod root the request is made with the pod credentials, on the
    allowlisted protocol paths only. Under a joined room's registered container
    (*rooms*: container URL -> room id) it is made with the credentials too, but
    GET/HEAD/PATCH on Long Chat files and GET/HEAD on the pin index. Anywhere else it is made
    unauthenticated, on the public protocol paths only (peer identity files,
    presence, profile card, and POSTs into peer drop boxes)."""
    method = (method or "").upper()
    parts = _clean_url(url)
    if parts is None:
        return _deny("bad_url")
    root = pod_root if (pod_root and pod_root.endswith("/")) else ((pod_root or "") + "/" if pod_root else "")
    if root and url.startswith(root) and _clean_url(root) is not None:
        rel = url[len(root):]
        for rx, methods, kind, cap in _OWN_RULES:
            m = rx.match(rel)
            if not m:
                continue
            if method not in methods:
                return _deny("method_not_allowed")
            box = m.group(1) if kind in ("box", "drop") else ""
            room = m.group(1) if kind.startswith("room") or kind == "descriptor" else ""
            return Decision(True, own=True, kind=kind, box=box, max_bytes=cap, url=url, room=room)
        return _deny("path_not_allowed")
    for container, room_id in (rooms or {}).items():
        if not url.startswith(container) or _clean_url(container) is None:
            continue
        rel = url[len(container):]
        for rx, methods, kind, cap in _ROOM_RULES:
            if not rx.match(rel):
                continue
            if method not in methods:
                return _deny("method_not_allowed")
            return Decision(True, own=False, kind=kind, max_bytes=cap, url=url, auth=True, room=room_id)
        return _deny("path_not_allowed")
    for rx, methods, kind, cap in _FOREIGN_RULES:
        m = rx.search(parts.path)
        if not m:
            continue
        if method not in methods:
            return _deny("method_not_allowed")
        box = m.group(1) if kind == "box" else ""
        return Decision(True, own=False, kind=kind, box=box, max_bytes=cap, url=url)
    return _deny("path_not_allowed")


def filter_request_headers(headers, decision: Decision, method: str) -> dict:
    """The request headers the bridge forwards: a content type from a short list,
    Accept, the LDP container Link (own box creation only) and conditional
    headers. Never Authorization, DPoP, Cookie or anything else the client sent."""
    out: dict = {}
    if not isinstance(headers, dict):
        return out
    for k, v in headers.items():
        if not isinstance(k, str) or not isinstance(v, str) or len(v) > 512:
            continue
        lk = k.lower()
        if lk == "content-type":
            base = v.split(";", 1)[0].strip().lower()
            if base in _CONTENT_TYPES:
                out["Content-Type"] = base
        elif lk == "slug":
            if decision.kind == "room_container" and method.upper() == "POST" and re.fullmatch(r"asset-[A-Za-z0-9_-]{1,160}\.json", v):
                out["Slug"] = v
        elif lk == "accept":
            out["Accept"] = v[:200]
        elif lk == "link":
            if decision.own and decision.kind in ("box", "dm_assets") and method.upper() == "PUT" and v == _CONTAINER_LINK:
                out["Link"] = v
        elif lk in ("if-match", "if-none-match"):
            out[k.title()] = v
    return out


def is_dm_peer_webid(webid) -> bool:
    """Mirror of isDmPeerWebId in web/webdm.js: https, or http on loopback; a
    profile-looking path or a fragment; no query; not a gateway invite link."""
    if not isinstance(webid, str) or not webid or len(webid) > 2048:
        return False
    if any(c in webid for c in '<>"{}|\\^` \t\r\n'):
        return False
    try:
        parts = urlsplit(webid)
    except ValueError:
        return False
    if parts.query or re.match(r"^/i/[^/]*/?$", parts.path or ""):
        return False
    if not (len(parts.fragment) > 0 or re.search(r"/(profile|card)(/|$)", parts.path or "", re.I)):
        return False
    if parts.scheme == "https" and parts.hostname:
        return True
    return parts.scheme == "http" and parts.hostname in ("localhost", "127.0.0.1", "::1")


_ROOM_ID_RE = re.compile(_ROOM_ID + r"\Z")


def joined_room_container(owner_webid, room_id) -> Optional[str]:
    """The Long Chat container of *room_id* on *owner_webid*'s pod, the only
    container a joined room may be registered under (chatRootUrl in
    web/longchat.js over the WebID-derived root), or None."""
    if not isinstance(room_id, str) or not _ROOM_ID_RE.match(room_id) or not is_dm_peer_webid(owner_webid):
        return None
    root = pod_root_from_webid(owner_webid)
    if not root or _clean_url(root) is None:
        return None
    return f"{root}proxion/rooms/{room_id}/"


def pending_join_path(room_id: str, owner_webid: str) -> str:
    """Where the joiner's client records a join it asked for (podio.js
    _pendingJoinPath), relative to the joiner's pod root."""
    import hashlib
    digest = hashlib.sha256(f"{room_id}\n{owner_webid}".encode("utf-8")).hexdigest()
    return f"proxion/join-requests/{digest}.jsonld"


def inbox_acl_turtle(inbox_url: str, owner_webid: str) -> str:
    """The WAC ACL for one of our drop boxes: owner full control, public Append.
    Mirrors buildInboxAcl in web/podio.js."""
    return (
        "@prefix acl: <http://www.w3.org/ns/auth/acl#>.\n@prefix foaf: <http://xmlns.com/foaf/0.1/>.\n"
        f"<#owner> a acl:Authorization; acl:agent <{owner_webid}>; acl:accessTo <{inbox_url}>; "
        f"acl:default <{inbox_url}>; acl:mode acl:Read, acl:Write, acl:Control.\n"
        f"<#public> a acl:Authorization; acl:agentClass foaf:Agent; acl:accessTo <{inbox_url}>; acl:mode acl:Append.\n"
    )


def dm_asset_acl_turtle(container: str, owner: str, model: str = "wac") -> str:
    """Ciphertext-only asset ACL: owner writes, public reads, inherited by chunks.
    Mirrors dmAssetAcl in web/dm-attachments.js; only these grants may be written
    through the bridge. The file key and metadata never belong in this store."""
    if model == "acp":
        return (
            "@prefix acp: <http://www.w3.org/ns/solid/acp#>.\n@prefix acl: <http://www.w3.org/ns/auth/acl#>.\n"
            f"<> a acp:AccessControlResource; acp:resource <{container}>; acp:accessControl <#owner-ac>, <#public-ac>; acp:memberAccessControl <#owner-ac>, <#public-ac>.\n"
            "<#owner-ac> a acp:AccessControl; acp:apply <#owner-policy>.\n"
            "<#owner-policy> a acp:Policy; acp:allow acl:Read, acl:Write, acl:Control; acp:anyOf <#owner-matcher>.\n"
            f"<#owner-matcher> a acp:Matcher; acp:agent <{owner}>.\n"
            "<#public-ac> a acp:AccessControl; acp:apply <#public-policy>.\n"
            "<#public-policy> a acp:Policy; acp:allow acl:Read; acp:anyOf <#public-matcher>.\n"
            "<#public-matcher> a acp:Matcher; acp:agent acp:PublicAgent.\n"
        )
    return (
        "@prefix acl: <http://www.w3.org/ns/auth/acl#>.\n@prefix foaf: <http://xmlns.com/foaf/0.1/>.\n"
        f"<#owner> a acl:Authorization; acl:agent <{owner}>; acl:accessTo <{container}>; acl:default <{container}>; acl:mode acl:Read, acl:Write, acl:Control.\n"
        f"<#public> a acl:Authorization; acl:agentClass foaf:Agent; acl:accessTo <{container}>; acl:default <{container}>; acl:mode acl:Read.\n"
    )


def container_members(body: bytes, base: str) -> list:
    """Member URLs of an LDP container's Turtle listing (ldp:contains), resolved
    against *base*. Same clause parse as SolidClient.list."""
    text = (body or b"").decode("utf-8", "replace")
    out = []
    for clause in re.findall(r"(?:ldp:contains|ldp#contains>)\s*((?:<[^>]+>\s*,?\s*)+)", text, re.IGNORECASE):
        for u in re.findall(r"<([^>]+)>", clause):
            out.append(urljoin(base, u))
    return out
