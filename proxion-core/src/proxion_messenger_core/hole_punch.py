"""UDP hole-punch coordination state machine.

Peers exchange their STUN-discovered external endpoints through the existing
sealed DM channel (no third-party directory). This module tracks the attempt
lifecycle in LocalStore and decides when to promote the path to ``direct``.

State machine
-------------
  pending → offered → accepted → succeeded
                   ↘             ↘
                    failed        failed
                    expired       expired

Both ``succeeded`` and ``failed`` are terminal states.
``expired`` is set by :meth:`HolePunchCoordinator.expire_stale` for attempts
that have been stuck in a non-terminal state longer than *timeout_seconds*.
"""
from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .local_store import LocalStore

PUNCH_STATE_PENDING = "pending"
PUNCH_STATE_OFFERED = "offered"
PUNCH_STATE_ACCEPTED = "accepted"
PUNCH_STATE_SUCCEEDED = "succeeded"
PUNCH_STATE_FAILED = "failed"
PUNCH_STATE_EXPIRED = "expired"

STUN_SESSION_TTL_SECONDS: int = 300
HOLE_PUNCH_TIMEOUT_SECONDS: int = 30

_TERMINAL = {PUNCH_STATE_SUCCEEDED, PUNCH_STATE_FAILED, PUNCH_STATE_EXPIRED}

_VALID_TRANSITIONS: dict[str, set[str]] = {
    PUNCH_STATE_PENDING: {PUNCH_STATE_OFFERED, PUNCH_STATE_FAILED, PUNCH_STATE_EXPIRED},
    PUNCH_STATE_OFFERED: {PUNCH_STATE_ACCEPTED, PUNCH_STATE_FAILED, PUNCH_STATE_EXPIRED},
    PUNCH_STATE_ACCEPTED: {PUNCH_STATE_SUCCEEDED, PUNCH_STATE_FAILED, PUNCH_STATE_EXPIRED},
}


def is_valid_punch_transition(current: str, new: str) -> bool:
    """Return True when transitioning from *current* to *new* is allowed."""
    if current in _TERMINAL:
        return False
    return new in _VALID_TRANSITIONS.get(current, set())


class HolePunchCoordinator:
    """Manages hole punch attempts against a :class:`~proxion_messenger_core.local_store.LocalStore`."""

    def __init__(self, store: "LocalStore") -> None:
        self._store = store

    def initiate(
        self,
        peer_webid: str,
        local_ip: str,
        local_port: int,
    ) -> str:
        """Create a new pending hole punch attempt and return its *attempt_id*."""
        attempt_id = str(uuid.uuid4())
        self._store.create_hole_punch_attempt(attempt_id, peer_webid, local_ip, local_port)
        return attempt_id

    def record_offer(self, attempt_id: str) -> None:
        """Advance the attempt to ``offered`` (local endpoint sent to peer)."""
        self._transition(attempt_id, PUNCH_STATE_OFFERED)

    def record_peer_endpoint(
        self,
        attempt_id: str,
        peer_ip: str,
        peer_port: int,
    ) -> None:
        """Record the peer's external endpoint and advance to ``accepted``."""
        self._store.update_hole_punch_attempt(
            attempt_id,
            peer_ip=peer_ip,
            peer_port=peer_port,
        )
        self._transition(attempt_id, PUNCH_STATE_ACCEPTED)

    def mark_succeeded(self, attempt_id: str) -> None:
        """Mark the attempt as succeeded and upgrade the transport path to direct."""
        attempt = self._store.get_hole_punch_attempt(attempt_id)
        self._transition(attempt_id, PUNCH_STATE_SUCCEEDED, completed=True)
        if attempt:
            from .transport_policy import record_transport_event
            record_transport_event(
                self._store,
                attempt["peer_webid"],
                old_mode="relay",
                new_mode="direct",
                reason="hole_punch_succeeded",
            )
            self._store.update_wg_peer_path_mode(
                attempt["peer_webid"],
                "direct",
                last_handshake_at=time.time(),
            )

    def mark_failed(self, attempt_id: str) -> None:
        """Mark the attempt as failed (relay path remains active)."""
        self._transition(attempt_id, PUNCH_STATE_FAILED, completed=True)

    def expire_stale(
        self,
        timeout_seconds: int = HOLE_PUNCH_TIMEOUT_SECONDS,
    ) -> int:
        """Mark attempts stuck in non-terminal states past *timeout_seconds* as expired.

        Returns the number of attempts expired.
        """
        return self._store.expire_stale_hole_punch_attempts(timeout_seconds)

    def get_attempt(self, attempt_id: str) -> dict | None:
        return self._store.get_hole_punch_attempt(attempt_id)

    def _transition(
        self,
        attempt_id: str,
        new_state: str,
        completed: bool = False,
    ) -> None:
        now = time.time()
        kwargs: dict = {"state": new_state, "updated_at": now}
        if completed:
            kwargs["completed_at"] = now
        self._store.update_hole_punch_attempt(attempt_id, **kwargs)
