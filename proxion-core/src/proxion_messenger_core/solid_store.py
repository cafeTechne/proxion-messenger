"""Solid Pod backed implementation of the Coordination Store interface.

Implements the MemoryStore interface using a Solid Pod as the backend,
storing sealed envelopes as JSON files in LDP containers.
"""

from __future__ import annotations

import json
import secrets
import time
from typing import List, Optional, Set

from .sealed import SealedEnvelope
from .solid_client import SolidClient, SolidError
from .store import StoredMessage


class SolidStore:
    """Coordination Store backed by a Solid Pod.

    Stores sealed envelopes in pod-accessible containers, mapping each mailbox
    to a directory under ``stash://handshake/{mailbox_id}/``.

    Parameters
    ----------
    client:
        A :class:`~proxion_messenger_core.solid_client.SolidClient` configured with
        credentials for the pod.
    """

    def __init__(self, client: SolidClient) -> None:
        self._client = client

    def _mailbox_path(self, mailbox_id: str) -> str:
        """Return the container path for a mailbox."""
        return f"stash://handshake/{mailbox_id}/"

    def _message_path(self, mailbox_id: str, message_id: str) -> str:
        """Return the resource path for a message."""
        return f"stash://handshake/{mailbox_id}/{message_id}.json"

    def put(
        self, mailbox_id: str, envelope: SealedEnvelope, ttl_seconds: Optional[int] = None
    ) -> str:
        """Write sealed envelope to pod. Returns generated message_id.

        Parameters
        ----------
        mailbox_id:
            The recipient's opaque mailbox address.
        envelope:
            The sealed message.
        ttl_seconds:
            Optional per-message TTL in seconds.

        Returns
        -------
        str
            The assigned ``message_id``.
        """
        message_id = secrets.token_urlsafe(16)
        posted_at = time.time()

        data = {
            "message_id": message_id,
            "envelope": envelope.to_dict(),
            "posted_at": posted_at,
        }

        path = self._message_path(mailbox_id, message_id)
        json_bytes = json.dumps(data).encode("utf-8")
        self._client.put(path, json_bytes, content_type="application/json")

        return message_id

    def list_all(self, mailbox_id: str) -> List[StoredMessage]:
        """Return all messages without removing them.

        Silently skips files that fail to parse.

        Parameters
        ----------
        mailbox_id:
            The target mailbox address.

        Returns
        -------
        list[StoredMessage]
            Oldest-first list of stored messages (may be empty).
        """
        mailbox_path = self._mailbox_path(mailbox_id)

        # Get list of files in the mailbox container
        try:
            members = self._client.list(mailbox_path)
        except SolidError as e:
            # On 404, return empty list
            if e.status_code == 404:
                return []
            raise

        messages: List[StoredMessage] = []

        # Fetch and deserialize each *.json file
        for member_uri in members:
            # Only process .json files
            if not member_uri.endswith(".json"):
                continue

            try:
                json_bytes = self._client.get(member_uri)
                data = json.loads(json_bytes.decode("utf-8"))

                # Reconstruct StoredMessage
                message_id = data["message_id"]
                envelope = SealedEnvelope.from_dict(data["envelope"])
                posted_at = data["posted_at"]

                messages.append(
                    StoredMessage(
                        message_id=message_id, envelope=envelope, posted_at=posted_at
                    )
                )
            except Exception:
                # Silently skip files that fail to parse
                continue

        return messages

    def take_by_ids(
        self, mailbox_id: str, message_ids: Set[str]
    ) -> List[StoredMessage]:
        """Remove and return only the named messages.

        Parameters
        ----------
        mailbox_id:
            The target mailbox address.
        message_ids:
            A set of ``message_id`` strings to remove.

        Returns
        -------
        list[StoredMessage]
            The messages that were removed (same order as stored).
        """
        # Get all messages first
        all_messages = self.list_all(mailbox_id)

        # Identify which ones to remove
        removed = [m for m in all_messages if m.message_id in message_ids]

        # Delete each matching message from pod
        for msg in removed:
            path = self._message_path(mailbox_id, msg.message_id)
            try:
                self._client.delete(path)
            except SolidError:
                # If deletion fails, log but continue
                pass

        return removed

    def take_all(self, mailbox_id: str) -> List[StoredMessage]:
        """Remove and return all messages from mailbox.

        Parameters
        ----------
        mailbox_id:
            The target mailbox address.

        Returns
        -------
        list[StoredMessage]
            Oldest-first list of all messages that were in the mailbox.
        """
        # Get all messages
        all_messages = self.list_all(mailbox_id)

        # Delete each message from pod
        for msg in all_messages:
            path = self._message_path(mailbox_id, msg.message_id)
            try:
                self._client.delete(path)
            except SolidError:
                # If deletion fails, log but continue
                pass

        return all_messages
