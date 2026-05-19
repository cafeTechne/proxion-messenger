"""Pod-backed room message storage.

Writes room messages and metadata as JSON files in an LDP container hierarchy
on the user's Solid pod.  SQLite remains the local read-cache; this class is
the durable, sovereign source of truth.

Pod path layout:
  stash://pod/rooms/                                     – all rooms container
  stash://pod/rooms/{room_id}/room.json                  – room metadata
  stash://pod/rooms/{room_id}/messages/                  – messages container
  stash://pod/rooms/{room_id}/messages/{msg_id}.json     – one message
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from .acp import set_acl_multi_auto
from .solid_client import SolidClient, SolidError

_VALID_WEBID_PREFIXES = ("http://", "https://", "did:")
_MAX_ROOM_MEMBERS_ACL = 500


def _sanitize_member_webids(member_webids: Optional[list], owner_webid: str) -> list:
    """Return a deduplicated, validated list of member WebIDs safe for ACL writing.

    - Strips the owner (already granted separately)
    - Filters blank strings
    - Requires each WebID to start with http://, https://, or did:
    - Rejects WebIDs containing Turtle-unsafe characters (>, ", \\, newlines)
    - Caps the list at *_MAX_ROOM_MEMBERS_ACL* to bound ACL document size
    """
    _unsafe_chars = frozenset('>"\\' + "\n\r")
    seen: set = set()
    result: list = []
    for w in (member_webids or []):
        if not w or w == owner_webid or w in seen:
            continue
        if not any(w.startswith(p) for p in _VALID_WEBID_PREFIXES):
            logger.warning("Skipping invalid WebID in room ACL (bad prefix): %.80r", w)
            continue
        if _unsafe_chars.intersection(w):
            logger.warning("Skipping WebID with unsafe chars in room ACL: %.80r", w)
            continue
        seen.add(w)
        result.append(w)
        if len(result) >= _MAX_ROOM_MEMBERS_ACL:
            logger.warning("Room member ACL list truncated at %d entries", _MAX_ROOM_MEMBERS_ACL)
            break
    return result

logger = logging.getLogger(__name__)


class PodRoomStore:
    """Reads and writes local room data to a Solid Pod.

    Parameters
    ----------
    client:
        An authenticated :class:`~proxion_messenger_core.solid_client.SolidClient`
        (typically a :class:`~proxion_messenger_core.css_auth.DpopSolidClient`).
    """

    def __init__(self, client: SolidClient) -> None:
        self._client = client

    # ------------------------------------------------------------------
    # URI helpers
    # ------------------------------------------------------------------

    def _room_meta_uri(self, room_id: str) -> str:
        return f"stash://pod/rooms/{room_id}/room.json"

    def _messages_container_uri(self, room_id: str) -> str:
        return f"stash://pod/rooms/{room_id}/messages/"

    def _message_uri(self, room_id: str, message_id: str) -> str:
        return f"stash://pod/rooms/{room_id}/messages/{message_id}.json"

    def _rooms_container_uri(self) -> str:
        return "stash://pod/rooms/"

    # ------------------------------------------------------------------
    # Room container setup
    # ------------------------------------------------------------------

    def ensure_room_container(
        self,
        room_id: str,
        owner_webid: str = "",
        member_webids: Optional[list] = None,
    ) -> None:
        """Ensure the room container and messages sub-container exist on the pod.

        Uses create-only container PUT semantics (If-None-Match: *) so this is
        idempotent. HTTP 412 means the container already exists.

        When *owner_webid* is provided the room container ACL is written so
        that only the owner (Read/Write/Control) and the listed *member_webids*
        (Read) can access it. This prevents public pod configurations from
        leaking room messages.
        """
        rooms_uri = self._rooms_container_uri()
        room_uri = f"stash://pod/rooms/{room_id}/"
        messages_uri = self._messages_container_uri(room_id)
        self._put_container_create_only(rooms_uri)
        self._put_container_create_only(room_uri)
        self._put_container_create_only(messages_uri)

        if owner_webid:
            members = _sanitize_member_webids(member_webids, owner_webid)
            try:
                set_acl_multi_auto(
                    self._client,
                    room_uri,
                    owner_webid,
                    members,
                    subject_modes=["Read"],
                )
            except Exception as exc:
                logger.warning("Failed to set room ACL for %s: %s", room_id, exc)

    def _put_container_create_only(self, stash_uri: str) -> None:
        """Create a BasicContainer with If-None-Match: *.

        Raises on unexpected errors. HTTP 412 is treated as success because
        the target already exists.
        """
        try:
            url = self._client._resolver.resolve(stash_uri)
            headers = {
                "If-None-Match": "*",
                "Content-Type": "text/turtle",
                "Link": '<http://www.w3.org/ns/ldp#BasicContainer>; rel="type"',
                **self._client._auth_headers,
                **self._client._dynamic_headers("PUT", url),
            }
            response = self._client._session.put(url, content=b"", headers=headers)
            if response.status_code == 412:
                return
            if response.status_code < 200 or response.status_code >= 300:
                raise SolidError(
                    f"PUT container {stash_uri}: HTTP {response.status_code}",
                    status_code=response.status_code,
                )
        except SolidError:
            raise
        except Exception as exc:
            raise SolidError(f"PUT container {stash_uri} failed: {exc}") from exc

    # ------------------------------------------------------------------
    # Room metadata
    # ------------------------------------------------------------------

    def write_room_meta(self, room_id: str, meta: dict) -> None:
        """PUT room.json with room metadata.  Also implicitly creates the container."""
        uri = self._room_meta_uri(room_id)
        self._client.put(uri, json.dumps(meta).encode(), content_type="application/json")

    def read_room_meta(self, room_id: str) -> Optional[dict]:
        """GET room.json, returning the parsed dict or None on 404."""
        uri = self._room_meta_uri(room_id)
        try:
            data = self._client.get(uri)
            return json.loads(data.decode("utf-8"))
        except SolidError as exc:
            if exc.status_code == 404:
                return None
            raise

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    def write_message(self, room_id: str, message: dict) -> None:
        """PUT a message as JSON at messages/{message_id}.json."""
        message_id = message.get("message_id")
        if not message_id:
            raise ValueError("message dict must contain 'message_id'")
        uri = self._message_uri(room_id, message_id)
        self._client.put(uri, json.dumps(message).encode(), content_type="application/json")

    def read_messages(
        self,
        room_id: str,
        after_timestamp: Optional[str] = None,
        limit: int = 200,
    ) -> list[dict]:
        """LIST the messages container, GET each file, and return sorted messages.

        Returns an empty list if the room container does not yet exist on the pod.
        Files that fail to parse are silently skipped.

        Parameters
        ----------
        room_id:
            The room to read messages for.
        after_timestamp:
            If provided, only return messages where ``timestamp > after_timestamp``.
        limit:
            Maximum number of messages to return (newest if truncated).
        """
        container = self._messages_container_uri(room_id)
        try:
            members = self._client.list(container)
        except SolidError as exc:
            if exc.status_code == 404:
                return []
            raise

        messages: list[dict] = []
        for uri in members:
            if not uri.endswith(".json"):
                continue
            try:
                raw = self._client.get(uri)
                msg = json.loads(raw.decode("utf-8"))
                if not isinstance(msg, dict):
                    continue
                messages.append(msg)
            except Exception:
                continue

        # Sort by timestamp (ISO strings sort lexicographically)
        messages.sort(key=lambda m: m.get("timestamp", ""))

        if after_timestamp:
            messages = [m for m in messages if m.get("timestamp", "") > after_timestamp]

        # Return the most recent `limit` messages
        if len(messages) > limit:
            messages = messages[-limit:]

        return messages

    def delete_message(self, room_id: str, message_id: str) -> None:
        """DELETE a message file.  Swallows 404 (already gone)."""
        uri = self._message_uri(room_id, message_id)
        try:
            self._client.delete(uri)
        except SolidError as exc:
            if exc.status_code != 404:
                raise

    def update_message(
        self, room_id: str, message_id: str, new_content: str, edited_at: str
    ) -> None:
        """Read-modify-write a message file to update content and edited_at."""
        uri = self._message_uri(room_id, message_id)
        try:
            raw = self._client.get(uri)
            msg = json.loads(raw.decode("utf-8"))
        except SolidError as exc:
            if exc.status_code == 404:
                return
            raise
        msg["content"] = new_content
        msg["edited_at"] = edited_at
        self._client.put(uri, json.dumps(msg).encode(), content_type="application/json")

    # ------------------------------------------------------------------
    # Room discovery
    # ------------------------------------------------------------------

    def list_room_ids(self) -> list[str]:
        """LIST stash://pod/rooms/ and extract room_id path segments.

        Returns an empty list if the rooms container does not exist yet.
        """
        container = self._rooms_container_uri()
        try:
            members = self._client.list(container)
        except SolidError as exc:
            if exc.status_code == 404:
                return []
            raise

        room_ids: list[str] = []
        for uri in members:
            # Each member looks like stash://pod/rooms/{room_id}/ or the HTTP equivalent
            # Strip trailing slash, take the last path segment
            stripped = uri.rstrip("/")
            segment = stripped.split("/")[-1]
            if segment and segment != "rooms":
                room_ids.append(segment)
        return room_ids
