"""Capability-token-enforcing Solid Pod client.

:class:`AuthenticatedSolidClient` wraps :class:`~proxion_messenger_core.solid_client.SolidClient`
with local token validation, checking that a capability token permits each
operation before forwarding the request.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Optional, TYPE_CHECKING

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .context import RequestContext
from .pop import sign_challenge
from .solid_client import SolidClient
from .tokens import Token
from .validator import validate_request

if TYPE_CHECKING:
    from .federation import RelationshipCertificate


class AuthenticatedSolidClient:
    """Capability-token-enforcing wrapper around :class:`SolidClient`.

    Validates that operations are covered by token permissions before
    forwarding to the underlying :class:`SolidClient`.

    Parameters
    ----------
    solid_client:
        The underlying :class:`SolidClient` to delegate requests to.
    token:
        The capability :class:`~proxion_messenger_core.tokens.Token` to enforce.
    identity_key:
        The holder's Ed25519 private key for Proof-of-Possession.
    signing_key:
        The validator's HMAC signing key (bytes) used to verify the token.
    aud:
        The intended audience for the token (default: "").
    """

    def __init__(
        self,
        solid_client: SolidClient,
        token: Token,
        identity_key: Ed25519PrivateKey,
        signing_key: bytes,
        aud: str = "",
        cert: Optional["RelationshipCertificate"] = None,
        now: Optional[datetime] = None,
    ) -> None:
        # J-008 fix: derive aud from cert when provided, rather than requiring
        # callers to remember to pass aud=cert.issuer explicitly.
        if cert is not None:
            aud = cert.issuer
        self._client = solid_client
        self._token = token
        self._identity_key = identity_key
        self._signing_key = signing_key
        self._aud = aud
        self._now = now

    def get(self, stash_uri: str) -> bytes:
        """Fetch a resource, enforcing token permissions.

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
        PermissionError
            If the token does not permit reading this resource.
        SolidError
            On HTTP or resolution errors.
        """
        self._check_allowed("read", stash_uri)
        return self._client.get(stash_uri)

    def put(
        self,
        stash_uri: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> None:
        """Write a resource, enforcing token permissions.

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
        PermissionError
            If the token does not permit writing to this resource.
        SolidError
            On HTTP or resolution errors.
        """
        self._check_allowed("write", stash_uri)
        self._client.put(stash_uri, data, content_type)

    def delete(self, stash_uri: str) -> None:
        """Delete a resource, enforcing token permissions.

        Parameters
        ----------
        stash_uri:
            A ``stash://`` URI for the resource to delete.

        Raises
        ------
        PermissionError
            If the token does not permit writing (deletion requires write).
        SolidError
            On HTTP or resolution errors.
        """
        self._check_allowed("write", stash_uri)
        self._client.delete(stash_uri)

    def list(self, stash_uri: str) -> list[str]:
        """List a container, enforcing read permission on the container URI."""
        self._check_allowed("read", stash_uri)
        members = self._client.list(stash_uri)
        if not stash_uri.startswith("stash://"):
            return members
        rest = stash_uri[len("stash://"):]
        slash = rest.find("/")
        if slash == -1:
            return members
        owner = rest[:slash]
        prefix = f"stash://{owner}/"
        container_base = stash_uri.rstrip("/") + "/"
        normalized: list[str] = []
        for uri in members:
            if not isinstance(uri, str):
                normalized.append(uri)
                continue
            if not uri.startswith("stash://") and not uri.startswith("http"):
                # Relative path from list() — expand to full stash:// URI
                normalized.append(container_base + uri)
            elif uri.startswith("stash://") and not uri.startswith(prefix):
                urest = uri[len("stash://"):]
                uslash = urest.find("/")
                if uslash != -1:
                    normalized.append(prefix + urest[uslash + 1:])
                    continue
                normalized.append(uri)
            else:
                normalized.append(uri)
        return normalized

    def _check_allowed(self, action: str, resource: str) -> None:
        """Check that the token permits the given action on the resource.

        Parameters
        ----------
        action:
            The action (e.g., "read" or "write").
        resource:
            The resource URI (e.g., "stash://alice/data/file.txt").

        Raises
        ------
        PermissionError
            If the token does not permit this action on this resource.
        """
        # Build request context
        nonce = secrets.token_hex(16)
        ctx = RequestContext(
            action=action,
            resource=resource,
            aud=self._aud,
            device_nonce=nonce,
            now=self._now if self._now is not None else datetime.now(timezone.utc),
        )

        # Create Proof-of-Possession
        proof = sign_challenge(self._identity_key, self._token.token_id, nonce)

        # Validate the token
        decision = validate_request(self._token, ctx, proof, self._signing_key)

        if not decision.allowed:
            reason = decision.reason or "unknown reason"
            raise PermissionError(
                f"token denied: {reason} (action={action}, resource={resource})"
            )


def set_thread_read_acl(
    pod_client: "SolidClient",
    cert: "RelationshipCertificate",
    owner_webid: str,
    subject_webid: str,
) -> str:
    """Write a WAC ACL granting the cert subject read access on the thread container.

    Writes a real W3C WAC Turtle document to ``<container>.acl`` with two
    authorization stanzas: the owner retains Read/Write/Control, the subject
    gets Read only. Both stanzas include ``acl:default`` so the grant applies
    recursively to all resources in the container.

    Parameters
    ----------
    pod_client:
        A :class:`~proxion_messenger_core.solid_client.SolidClient` (or DpopSolidClient)
        authenticated as the container owner.
    cert:
        The :class:`~proxion_messenger_core.federation.RelationshipCertificate` whose
        thread container to protect.  The container path is derived from
        ``cert.certificate_id``.
    owner_webid:
        WebID URL of the Pod owner
        (e.g. ``http://localhost:3001/alice/profile/card#me``).
    subject_webid:
        WebID URL of the cert subject
        (e.g. ``http://localhost:3002/bob/profile/card#me``).

    Returns
    -------
    str
        The stash:// ACL path written (``thread_path(cert_id).rstrip("/") + ".acl"``).
    """
    from .messaging import thread_path
    from .acp import set_acl_auto
    container = thread_path(cert.certificate_id)
    set_acl_auto(pod_client, container, owner_webid, subject_webid, subject_modes=["Read"])
    return container.rstrip("/") + "/.acl"


