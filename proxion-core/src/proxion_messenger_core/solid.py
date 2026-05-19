"""Solid Protocol URI adapter for Proxion capability tokens.

Maps the abstract ``stash://`` URI scheme used in Proxion capability tokens to
concrete Solid LDP (Linked Data Platform) HTTP URLs, and provides a lightweight
helper for constructing Authorization headers compatible with Solid servers.

URI scheme
----------
``stash://<owner>/<path>``

Mapping rules:
* ``stash://alice/shared/photos/`` → ``{pod_base_url}/shared/photos/``
* The ``<owner>`` segment is used only as a namespace — it does not appear
  in the resolved URL (the pod_base_url already scopes to the owner's Pod).
* Trailing slashes are preserved.

Example::

    resolver = SolidResolver(pod_base_url="https://alice.solidcommunity.net/")
    url = resolver.resolve("stash://alice/shared/photos/file.jpg")
    # → "https://alice.solidcommunity.net/shared/photos/file.jpg"
"""

from __future__ import annotations

from urllib.parse import urljoin
from typing import Optional

from .errors import ProxionError


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class SolidResolverError(ProxionError):
    """Raised when a stash:// URI cannot be resolved."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class SolidResolver:
    """Resolves ``stash://`` URIs to Solid LDP HTTP URLs.

    Parameters
    ----------
    pod_base_url:
        The root URL of the owner's Solid Pod, e.g.
        ``https://alice.solidcommunity.net/``.  Must end with ``/``.
    """

    def __init__(self, pod_base_url: str) -> None:
        if not pod_base_url.endswith("/"):
            pod_base_url = pod_base_url + "/"
        self._base = pod_base_url

    @property
    def pod_base_url(self) -> str:
        """Return the pod base URL (guaranteed to end with /)."""
        return self._base

    def resolve(self, stash_uri: str) -> str:
        """Resolve a ``stash://`` URI to an absolute HTTP URL.

        Parameters
        ----------
        stash_uri:
            A capability resource URI of the form ``stash://<owner>/<path>``.

        Returns
        -------
        str
            The absolute HTTP URL for the resource on the Solid Pod.

        Raises
        ------
        SolidResolverError
            If *stash_uri* is not a valid ``stash://`` URI.
        """
        if not stash_uri.startswith("stash://"):
            raise SolidResolverError(f"not a stash:// URI: {stash_uri!r}")

        without_scheme = stash_uri[len("stash://"):]

        # Strip the owner segment (up to first /)
        slash = without_scheme.find("/")
        if slash == -1:
            # stash://owner with no path → resolve to pod root
            return self._base

        path = without_scheme[slash + 1:]  # everything after owner/
        return urljoin(self._base, path) if path else self._base

    def resolve_back(self, http_url: str, owner: str = "pod") -> str:
        """Invert resolve(): convert an absolute HTTP URL back to a stash:// URI.

        Parameters
        ----------
        http_url:
            An absolute HTTP URL that was produced by this resolver, e.g.
            ``https://alice.solidcommunity.net/shared/photos/file.jpg``.
        owner:
            The owner segment to embed in the returned stash:// URI.
            Defaults to ``"pod"``. Pass the same owner used when calling
            ``resolve()`` to get a round-trippable URI.

        Returns
        -------
        str
            A ``stash://`` URI, e.g. ``stash://pod/shared/photos/file.jpg``.

        Raises
        ------
        SolidResolverError
            If ``http_url`` does not start with this resolver's pod_base_url.
        """
        if not http_url.startswith(self._base):
            raise SolidResolverError(
                f"{http_url!r} is not under pod base {self._base!r}"
            )
        path = http_url[len(self._base):]
        return f"stash://{owner}/{path}"

    def covers(self, permission_resource: str, request_resource: str) -> bool:
        """Return True if *permission_resource* covers *request_resource*.

        This mirrors the hierarchical prefix check in
        :func:`~proxion_messenger_core.validator.validate_request` but operates on
        resolved HTTP URLs.

        Parameters
        ----------
        permission_resource:
            The resource field from a token permission, e.g.
            ``stash://alice/shared/``.
        request_resource:
            The resource being requested, e.g.
            ``stash://alice/shared/photos/img.jpg``.

        Returns
        -------
        bool
            ``True`` if *permission_resource* is an exact match or a prefix
            (with trailing /) of *request_resource*.
        """
        if permission_resource == request_resource:
            return True
        if permission_resource == "/":
            return True
        if permission_resource.endswith("/") and request_resource.startswith(
            permission_resource
        ):
            return True
        return False


def permission_to_solid_url(permission_resource: str, resolver: SolidResolver) -> str:
    """Resolve a permission resource string to a Solid URL.

    Handles both ``stash://`` URIs and plain paths (passed through the
    resolver's base URL).

    Parameters
    ----------
    permission_resource:
        Either a ``stash://`` URI or a plain path (e.g. ``/data/file.txt``).
    resolver:
        A :class:`SolidResolver` configured with the owner's Pod base URL.

    Returns
    -------
    str
        The absolute Solid LDP HTTP URL.
    """
    if permission_resource.startswith("stash://"):
        return resolver.resolve(permission_resource)
    # Plain path — append to pod base
    return urljoin(resolver.pod_base_url, permission_resource.lstrip("/"))
