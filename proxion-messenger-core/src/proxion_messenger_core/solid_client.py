"""Solid Pod HTTP client — CSS-oriented DPoP transport.

Auth model
----------
This module provides low-level REST operations over ``stash://`` URIs.
When used with :class:`~proxion_messenger_core.css_auth.DpopSolidClient`, requests are
authenticated with CSS client-credentials + DPoP.

NSS incompatibility
-------------------
NSS providers (for example solidcommunity.net) generally rely on browser OIDC
authorization flows, not server-side client-credentials. For NSS pods, browser
I/O should use ``web/auth.js`` + ``web/pod.js`` (``solidSession.fetch``).

Security
--------
- Tokens are sent via Authorization headers only
- Secrets/keys are expected to remain in server memory
- No token values are persisted or logged by this module
"""

from __future__ import annotations

from typing import Optional
from urllib.parse import urljoin

from .errors import ProxionError
from .solid import SolidResolver, SolidResolverError

try:
    import httpx
    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class SolidError(ProxionError):
    """Raised on HTTP or resolution errors from a Solid Pod."""

    def __init__(self, message: str, status_code: Optional[int] = None) -> None:
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def _assert_safe_webid(webid: str) -> None:
    """Raise ValueError if *webid* contains characters that would break Turtle serialization."""
    _unsafe = frozenset('>"\\' + "\n\r")
    if _unsafe.intersection(webid):
        raise ValueError(f"WebID {webid!r} contains characters unsafe for Turtle serialization")


# ---------------------------------------------------------------------------
# SolidClient
# ---------------------------------------------------------------------------

class SolidClient:
    """HTTP client for Solid LDP (Linked Data Platform) resources.

    Resolves ``stash://`` URIs to concrete HTTP URLs using a :class:`SolidResolver`
    and provides GET, PUT, DELETE, and LIST operations.

    Parameters
    ----------
    resolver:
        A :class:`~proxion_messenger_core.solid.SolidResolver` configured with the Solid
        Pod's base URL.
    session:
        Optional :class:`httpx.Client` to use for HTTP requests. If None, a new
        client is created internally and closed on object deletion.
    """

    def __init__(
        self,
        resolver: SolidResolver,
        session: Optional[object] = None,
        stash_owner: str = "pod",
        auth_headers: Optional[dict] = None,
    ) -> None:
        self._resolver = resolver
        self._session = session
        self._stash_owner = stash_owner
        self._auth_headers: dict = auth_headers or {}
        self._owns_session = session is None

        if self._owns_session:
            if not _HTTPX_AVAILABLE:
                raise ImportError("httpx is required for SolidClient")
            self._session = httpx.Client()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def __del__(self):
        self.close()

    def close(self) -> None:
        """Close the underlying session if owned by this client."""
        if self._owns_session and self._session is not None:
            self._session.close()

    def _dynamic_headers(self, method: str, url: str) -> dict:
        """Override in subclasses to inject per-request dynamic headers."""
        return {"User-Agent": "Proxion/1.0"}

    def _refresh_auth(self, response=None) -> None:
        """Attempt to refresh authentication headers. Override in subclasses."""
        pass

    def _resolve_uri(self, uri: str) -> str:
        """Resolve a stash:// URI to HTTP, or pass through an absolute HTTP URI."""
        if uri.startswith("http://") or uri.startswith("https://"):
            return uri
        try:
            return self._resolver.resolve(uri)
        except SolidResolverError as exc:
            raise SolidError(f"failed to resolve {uri!r}: {exc}") from exc

    def _check_cutover_stage(self, operation: str) -> None:
        """Enforce PROXION_SOLID_CUTOVER_STAGE policy for legacy path usage.

        Stage 0/1: log at DEBUG (shadow/canary — legacy allowed).
        Stage 2:   log WARNING (broad — legacy secondary, JS adapter primary).
        Stage 3:   raise SolidError unless PROXION_SOLID_EMERGENCY_OVERRIDE=1.
        """
        import os as _os_cs
        import logging as _log_cs
        try:
            stage = int(_os_cs.environ.get("PROXION_SOLID_CUTOVER_STAGE", "0"))
        except (ValueError, TypeError):
            stage = 0
        if stage <= 1:
            return
        _log = _log_cs.getLogger(__name__)
        if stage == 2:
            _log.warning("solid_client legacy path used during stage-2 cutover: %s", operation)
            try:
                from .solid_migration import migration_store, SOLID_AUTH_FAILED
                migration_store.record(SOLID_AUTH_FAILED, "legacy")
            except Exception:
                pass
        elif stage >= 3:
            if _os_cs.environ.get("PROXION_SOLID_EMERGENCY_OVERRIDE", "0") == "1":
                _log.warning("solid_client legacy path used under emergency override: %s", operation)
                return
            raise SolidError(
                f"Legacy solid_client path blocked at cutover stage 3: {operation}",
                status_code=None,
            )

    def _get_legacy(self, url: str, stash_uri: str) -> bytes:
        """Internal GET using the legacy HTTP path (no adapter)."""
        try:
            for _attempt in range(2):
                response = self._session.get(url, headers={**self._auth_headers, **self._dynamic_headers("GET", url)})
                if response.status_code == 401 and _attempt == 0:
                    self._refresh_auth(response)
                    continue
                if response.status_code < 200 or response.status_code >= 300:
                    raise SolidError(f"GET {stash_uri}: HTTP {response.status_code}", status_code=response.status_code)
                return response.content
        except SolidError:
            raise
        except Exception as exc:
            raise SolidError(f"GET {stash_uri} failed: {exc}") from exc

    def _shadow_compare(self, operation: str, uri: str, legacy_result: object, adapter_result: object) -> None:
        """Compare legacy and adapter results; record mismatch metric if they differ."""
        if legacy_result != adapter_result and adapter_result is not None:
            import logging as _log_sc
            _log_sc.getLogger(__name__).warning(
                "solid_client dual-read mismatch on %s %s", operation, uri
            )
            try:
                from .solid_migration import migration_store
                migration_store.record_dual_read_mismatch()
            except Exception:
                pass

    def get(self, stash_uri: str) -> bytes:
        """Fetch a resource from the Solid Pod.

        Parameters
        ----------
        stash_uri:
            A ``stash://`` URI for the resource.

        Returns
        -------
        bytes
            The raw response body.

        Raises
        ------
        SolidError
            On resolution error or non-2xx HTTP status.
        """
        self._check_cutover_stage("GET")
        url = self._resolve_uri(stash_uri)

        # Dual-read shadow mode: compare legacy output with a stub adapter response
        import os as _os_dr
        if _os_dr.environ.get("PROXION_SOLID_DUAL_READ", "0") == "1":
            result = self._get_legacy(url, stash_uri)
            self._shadow_compare("GET", stash_uri, result, None)
            return result

        return self._get_legacy(url, stash_uri)

    def put(
        self,
        stash_uri: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> None:
        """Write a resource to the Solid Pod.

        Parameters
        ----------
        stash_uri:
            A ``stash://`` URI for the resource.
        data:
            The raw bytes to write.
        content_type:
            The Content-Type header (default: "application/octet-stream").

        Raises
        ------
        SolidError
            On resolution error or non-2xx HTTP status.
        """
        self._check_cutover_stage("PUT")
        url = self._resolve_uri(stash_uri)

        try:
            for _attempt in range(2):
                response = self._session.put(
                    url,
                    content=data,
                    headers={"Content-Type": content_type, **self._auth_headers, **self._dynamic_headers("PUT", url)},
                )
                if response.status_code == 401 and _attempt == 0:
                    self._refresh_auth(response)
                    continue
                if response.status_code < 200 or response.status_code >= 300:
                    raise SolidError(
                        f"PUT {stash_uri}: HTTP {response.status_code}",
                        status_code=response.status_code,
                    )
                return
        except SolidError:
            raise
        except Exception as exc:
            raise SolidError(f"PUT {stash_uri} failed: {exc}") from exc

    def delete(self, stash_uri: str) -> None:
        """Delete a resource from the Solid Pod.

        Parameters
        ----------
        stash_uri:
            A ``stash://`` URI for the resource to delete.

        Raises
        ------
        SolidError
            On resolution error or non-2xx HTTP status.
        """
        url = self._resolve_uri(stash_uri)

        try:
            for _attempt in range(2):
                response = self._session.delete(
                    url, headers={**self._auth_headers, **self._dynamic_headers("DELETE", url)}
                )
                if response.status_code == 401 and _attempt == 0:
                    self._refresh_auth(response)
                    continue
                if response.status_code < 200 or response.status_code >= 300:
                    raise SolidError(
                        f"DELETE {stash_uri}: HTTP {response.status_code}",
                        status_code=response.status_code,
                    )
                return
        except SolidError:
            raise
        except Exception as exc:
            raise SolidError(f"DELETE {stash_uri} failed: {exc}") from exc

    def list(self, stash_uri: str) -> list[str]:
        """List member URIs in an LDP BasicContainer.

        Parameters
        ----------
        stash_uri:
            A ``stash://`` URI for the container (must end with /).

        Returns
        -------
        list[str]
            List of absolute member URIs.

        Raises
        ------
        SolidError
            If the URI doesn't end with /, on resolution error, or on non-2xx
            HTTP status.
        """
        if not stash_uri.endswith("/"):
            raise SolidError(f"list() requires a directory URI ending with /: {stash_uri!r}")

        url = self._resolve_uri(stash_uri)

        try:
            _MAX_BODY = 524_288  # 512 KB — enforced by streaming, not by Content-Length
            import re as _re
            # Retry once on 401 after refreshing auth — like get/put/delete. Without
            # this an expired access token silently breaks pod room/message listing
            # on long-running gateways. DPoP headers are recomputed each attempt.
            body = None
            for _attempt in range(2):
                req_headers = {
                    "Accept": "text/turtle",
                    **self._auth_headers,
                    **self._dynamic_headers("GET", url),
                }
                # Use streaming so a malicious Pod cannot exhaust RAM before the
                # size check fires.  httpx buffers nothing until we call iter_bytes().
                with self._session.stream("GET", url, headers=req_headers) as response:
                    if response.status_code == 401 and _attempt == 0:
                        response.read()  # drain before the connection is reused
                        self._refresh_auth(response)
                        continue
                    if response.status_code < 200 or response.status_code >= 300:
                        raise SolidError(
                            f"LIST {stash_uri}: HTTP {response.status_code}",
                            status_code=response.status_code,
                        )
                    chunks: list[bytes] = []
                    received = 0
                    for chunk in response.iter_bytes(chunk_size=65_536):
                        received += len(chunk)
                        if received > _MAX_BODY:
                            raise SolidError(
                                f"LIST {stash_uri}: response too large (>{_MAX_BODY // 1024} KB)"
                            )
                        chunks.append(chunk)
                    body = b"".join(chunks).decode("utf-8", errors="replace")
                    break
            if body is None:
                raise SolidError(f"LIST {stash_uri}: unauthorized after refresh", status_code=401)

            # Parse Turtle-like RDF to extract ldp:contains member URIs. CSS (and
            # most Solid servers) serialize a container's members as ONE
            # comma-separated predicate-object list — possibly across lines —
            # e.g. `<c> ... ; ldp:contains <m1>, <m2>, <m3> .`. A per-line parser
            # that grabs only the last <...> on the ldp:contains line therefore
            # returns just the final member. Instead, capture each ldp:contains
            # clause (prefixed or full-IRI form) up to the next `.`/`;` and pull
            # EVERY member URI from it.
            members = []
            # Capture the run of `<...>` member tokens that follow each ldp:contains
            # predicate — they're a comma-separated list (`<m1.json>, <m2.json>.`),
            # so terminating on the first `.` would stop inside a `.json` filename.
            for clause in _re.findall(
                r"(?:ldp:contains|ldp#contains>)\s*((?:<[^>]+>\s*,?\s*)+)", body, _re.IGNORECASE
            ):
                for http_uri in _re.findall(r"<([^>]+)>", clause):
                    try:
                        members.append(
                            self._resolver.resolve_back(http_uri, self._stash_owner)
                        )
                    except SolidResolverError:
                        members.append(http_uri)

            return members

        except SolidError:
            raise
        except Exception as exc:
            raise SolidError(f"LIST {stash_uri} failed: {exc}") from exc

    def set_acl(
        self,
        stash_uri: str,
        owner_webid: str,
        subject_webid: str,
        subject_modes: Optional[list[str]] = None,
    ) -> None:
        """Set Web Access Control (WAC) permissions for a resource.

        Creates a two-stanza WAC document granting Read/Write/Control to the owner
        and configurable modes to the subject.

        Parameters
        ----------
        stash_uri:
            A ``stash://`` URI for the resource.
        owner_webid:
            WebID of the resource owner (granted Read, Write, Control).
        subject_webid:
            WebID of the agent to grant access.
        subject_modes:
            List of access modes for subject (default: ["Read"]).
            Example: ["Read", "Write"].

        Raises
        ------
        SolidError
            On resolution error or non-2xx HTTP status.
        """
        try:
            container_url = self._resolver.resolve(stash_uri)
        except SolidResolverError as exc:
            raise SolidError(f"failed to resolve {stash_uri!r}: {exc}") from exc

        _assert_safe_webid(owner_webid)
        _assert_safe_webid(subject_webid)

        acl_url = container_url + ".acl"
        modes = subject_modes or ["Read"]

        # Build subject modes Turtle string
        subject_modes_str = ", ".join(f"acl:{m}" for m in modes)

        # Two-stanza WAC Turtle template
        turtle_content = f"""@prefix acl: <http://www.w3.org/ns/auth/acl#>.

<#owner>
    a acl:Authorization;
    acl:agent <{owner_webid}>;
    acl:accessTo <{container_url}>;
    acl:default <{container_url}>;
    acl:mode acl:Read, acl:Write, acl:Control.

<#subject>
    a acl:Authorization;
    acl:agent <{subject_webid}>;
    acl:accessTo <{container_url}>;
    acl:default <{container_url}>;
    acl:mode {subject_modes_str}.
"""

        try:
            response = self._session.put(
                acl_url,
                content=turtle_content.encode("utf-8"),
                headers={"Content-Type": "text/turtle", **self._auth_headers, **self._dynamic_headers("PUT", acl_url)},
            )
            if response.status_code < 200 or response.status_code >= 300:
                raise SolidError(
                    f"set_acl {stash_uri}: HTTP {response.status_code}",
                    status_code=response.status_code,
                )
        except SolidError:
            raise
        except Exception as exc:
            raise SolidError(f"set_acl {stash_uri} failed: {exc}") from exc

    def set_acl_multi(
        self,
        stash_uri: str,
        owner_webid: str,
        subject_webids: list,
        subject_modes: Optional[list] = None,
    ) -> None:
        """Write a WAC ACL granting owner full control and each subject the given modes.

        Produces a single Turtle document with one stanza per subject so the
        room container ACL stays in sync with the current member list.

        Validates every WebID for Turtle-safety before interpolation to prevent
        ACL document injection.
        """
        try:
            container_url = self._resolver.resolve(stash_uri)
        except Exception as exc:
            raise SolidError(f"failed to resolve {stash_uri!r}: {exc}") from exc

        acl_url = container_url + ".acl"
        modes = subject_modes or ["Read"]
        subject_modes_str = ", ".join(f"acl:{m}" for m in modes)

        _assert_safe_webid(owner_webid)
        for webid in subject_webids:
            _assert_safe_webid(webid)

        stanzas = [
            f"""<#owner>
    a acl:Authorization;
    acl:agent <{owner_webid}>;
    acl:accessTo <{container_url}>;
    acl:default <{container_url}>;
    acl:mode acl:Read, acl:Write, acl:Control."""
        ]
        for idx, webid in enumerate(subject_webids):
            stanzas.append(
                f"""<#member{idx}>
    a acl:Authorization;
    acl:agent <{webid}>;
    acl:accessTo <{container_url}>;
    acl:default <{container_url}>;
    acl:mode {subject_modes_str}."""
            )

        turtle_content = "@prefix acl: <http://www.w3.org/ns/auth/acl#>.\n\n" + "\n\n".join(stanzas) + "\n"

        try:
            response = self._session.put(
                acl_url,
                content=turtle_content.encode("utf-8"),
                headers={"Content-Type": "text/turtle", **self._auth_headers, **self._dynamic_headers("PUT", acl_url)},
            )
            if response.status_code < 200 or response.status_code >= 300:
                raise SolidError(
                    f"set_acl_multi {stash_uri}: HTTP {response.status_code}",
                    status_code=response.status_code,
                )
        except SolidError:
            raise
        except Exception as exc:
            raise SolidError(f"set_acl_multi {stash_uri} failed: {exc}") from exc
