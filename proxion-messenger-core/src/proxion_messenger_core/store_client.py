"""HTTP client for the Proxion Coordination Store.

:class:`RemoteStore` exposes the same interface as
:class:`~proxion_messenger_core.store.MemoryStore` but forwards every call to a remote
store server over HTTP.  Application code — handshake, revocation propagation,
certtoken issuance — uses this transparently; swapping ``MemoryStore()`` for
``RemoteStore(url)`` is the only change needed to federate across machines.

Usage
-----
::

    from proxion_messenger_core.store_client import RemoteStore

    store = RemoteStore("http://helsinki:8765")

    # Now use exactly like MemoryStore:
    store.put(mailbox_id, envelope)
    msgs = store.list_all(mailbox_id)
    store.take_by_ids(mailbox_id, {msg.message_id for msg in msgs})

Authentication / TLS
--------------------
For a trusted LAN or WireGuard tunnel, plain HTTP is acceptable.  For
internet-facing deployments put nginx/Caddy in front and use HTTPS.
The client passes any extra *httpx* kwargs (e.g. ``verify=``, ``headers=``)
through to the underlying ``httpx.Client``.
"""

from __future__ import annotations

import time
from typing import List, Optional, Set

from .sealed import SealedEnvelope
from .store import StoredMessage

try:
    import httpx
    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False


# ---------------------------------------------------------------------------
# Wire helpers
# ---------------------------------------------------------------------------

def _deserialise_message(d: dict) -> StoredMessage:
    return StoredMessage(
        message_id=d["message_id"],
        envelope=SealedEnvelope.from_dict(d["envelope"]),
        posted_at=float(d["posted_at"]),
    )


# ---------------------------------------------------------------------------
# RemoteStore
# ---------------------------------------------------------------------------

class RemoteStore:
    """HTTP client that mirrors the :class:`~proxion_messenger_core.store.MemoryStore` interface.

    Parameters
    ----------
    base_url:
        Root URL of the coordination store server, e.g. ``"http://localhost:8765"``.
    timeout:
        Request timeout in seconds (default: 10).
    **kwargs:
        Extra arguments forwarded to :class:`httpx.Client` (e.g. ``verify``,
        ``headers``, ``auth``).
    """

    def __init__(
        self,
        base_url: str,
        timeout: float = 10.0,
        auth_token: Optional[str] = None,
        **kwargs,
    ) -> None:
        if not _HTTPX_AVAILABLE:
            raise ImportError(
                "httpx is required to use RemoteStore. "
                "Install it with: pip install 'proxion-messenger-core[client]'"
            )
        self._base = base_url.rstrip("/")
        headers = kwargs.pop("headers", {})
        if auth_token:
            headers["Authorization"] = f"Bearer {auth_token}"
        self._client = httpx.Client(timeout=timeout, headers=headers, **kwargs)

    def _url(self, mailbox_id: str, suffix: str = "") -> str:
        return f"{self._base}/mailbox/{mailbox_id}{suffix}"

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def put(self, mailbox_id: str, envelope: SealedEnvelope, ttl_seconds: Optional[int] = None) -> str:
        """Post a sealed envelope to a remote mailbox.

        Parameters
        ----------
        mailbox_id:
            The recipient's mailbox ID.
        envelope:
            The sealed message.
        ttl_seconds:
            Optional per-message TTL in seconds.

        Returns
        -------
        str
            The assigned ``message_id``.

        Raises
        ------
        httpx.HTTPStatusError
            On 4xx/5xx responses (e.g. 429 quota exceeded).
        """
        body = {"envelope": envelope.to_dict()}
        if ttl_seconds is not None:
            body["ttl_seconds"] = ttl_seconds
        resp = self._client.post(
            self._url(mailbox_id),
            json=body,
        )
        resp.raise_for_status()
        return resp.json()["message_id"]

    # ------------------------------------------------------------------
    # Read / drain
    # ------------------------------------------------------------------

    def list_all(self, mailbox_id: str) -> List[StoredMessage]:
        """Return all pending messages without removing them."""
        resp = self._client.get(self._url(mailbox_id))
        resp.raise_for_status()
        return [_deserialise_message(m) for m in resp.json()["messages"]]

    def take_all(self, mailbox_id: str) -> List[StoredMessage]:
        """Retrieve and remove all messages from a mailbox."""
        resp = self._client.delete(self._url(mailbox_id))
        resp.raise_for_status()
        return [_deserialise_message(m) for m in resp.json()["messages"]]

    def take_by_ids(self, mailbox_id: str, message_ids: Set[str]) -> List[StoredMessage]:
        """Remove and return messages whose ID is in *message_ids*."""
        resp = self._client.post(
            self._url(mailbox_id, "/take"),
            json={"ids": list(message_ids)},
        )
        resp.raise_for_status()
        return [_deserialise_message(m) for m in resp.json()["messages"]]

    def peek(self, mailbox_id: str) -> dict:
        """Return metadata summary without touching messages."""
        resp = self._client.get(self._url(mailbox_id, "/peek"))
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Context manager support
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        self._client.close()

    def __enter__(self) -> "RemoteStore":
        return self

    def __exit__(self, *_) -> None:
        self.close()


class LocalStoreAdapter:
    """Wraps a MemoryStore (or any in-process store) behind the RemoteStore interface.

    Useful in tests that need to simulate cross-agent store access without an HTTP server.
    """

    def __init__(self, store) -> None:
        self._store = store

    def put(self, mailbox_id: str, envelope, ttl_seconds: Optional[int] = None) -> str:
        return self._store.put(mailbox_id, envelope, ttl_seconds=ttl_seconds)

    def list_all(self, mailbox_id: str) -> list:
        return self._store.list_all(mailbox_id)

    def take_all(self, mailbox_id: str) -> list:
        return self._store.take_all(mailbox_id)

    def take_by_ids(self, mailbox_id: str, message_ids: set) -> list:
        return self._store.take_by_ids(mailbox_id, message_ids)

    def peek(self, mailbox_id: str) -> dict:
        return self._store.peek(mailbox_id)
