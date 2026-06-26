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

Security invariants
-------------------
- Every mutating method except ``expire_stale`` and ``record_offer`` requires
  an *actor_webid* that must be the initiator or responder.
- ``_transition`` reads the current state before writing and enforces the valid
  state machine transitions.
- ``mark_succeeded`` additionally requires that the attempt is in ``accepted``
  state and that a peer endpoint (peer_ip) has been recorded — preventing blind
  direct-mode promotion.
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


class InvalidPunchTransition(Exception):
    """Raised when a state transition violates the hole-punch state machine."""


class HolePunchForbidden(Exception):
    """Raised when an actor is not authorized to mutate a hole-punch attempt."""


def is_valid_punch_transition(current: str, new: str) -> bool:
    """Return True when transitioning from *current* to *new* is allowed."""
    if current in _TERMINAL:
        return False
    return new in _VALID_TRANSITIONS.get(current, set())


class HolePunchCoordinator:
    """Manages hole punch attempts against a :class:`~proxion_messenger_core.local_store.LocalStore`."""

    def __init__(self, store: "LocalStore") -> None:
        self._store = store

    # ------------------------------------------------------------------
    # Authorization check
    # ------------------------------------------------------------------

    def can_attempt_hole_punch(self, actor_webid: str, peer_webid: str) -> bool:
        """Return True if actor is authorized to initiate a hole punch with peer.

        Authorization passes when:
        - no store is available (dev/test mode), OR
        - a wg_peer record exists for peer_webid (overlay keys already exchanged), OR
        - actor and peer share at least one room.
        """
        if not self._store:
            return True
        if self._store.get_wg_peer(peer_webid):
            return True
        try:
            actor_rooms = set(self._store.get_rooms_for_member(actor_webid))
            peer_rooms = set(self._store.get_rooms_for_member(peer_webid))
            if actor_rooms & peer_rooms:
                return True
        except Exception:
            pass
        return False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def initiate(
        self,
        initiator_webid: str,
        responder_webid: str,
        local_ip: str,
        local_port: int,
        attempt_nonce: str = "",
    ) -> str:
        """Create a new pending hole punch attempt and return its *attempt_id*."""
        attempt_id = str(uuid.uuid4())
        self._store.create_hole_punch_attempt(
            attempt_id,
            peer_webid=responder_webid,
            local_ip=local_ip,
            local_port=local_port,
            initiator_webid=initiator_webid,
            responder_webid=responder_webid,
            attempt_nonce=attempt_nonce,
        )
        return attempt_id

    def record_offer(self, attempt_id: str) -> None:
        """Advance the attempt to ``offered`` (local endpoint sent to peer).

        Called by the gateway relay itself — no actor_webid required.
        """
        self._transition(attempt_id, PUNCH_STATE_OFFERED)

    def record_peer_endpoint(
        self,
        attempt_id: str,
        actor_webid: str,
        peer_ip: str,
        peer_port: int,
    ) -> None:
        """Record the peer's external endpoint and advance to ``accepted``.

        Raises HolePunchForbidden if actor is not initiator or responder.
        """
        attempt = self._store.get_hole_punch_attempt_for_actor(attempt_id, actor_webid)
        if attempt is None:
            raise HolePunchForbidden(
                f"actor {actor_webid!r} is not party to attempt {attempt_id!r}"
            )
        self._store.update_hole_punch_attempt(
            attempt_id,
            peer_ip=peer_ip,
            peer_port=peer_port,
        )
        self._transition(attempt_id, PUNCH_STATE_ACCEPTED)

    def mark_succeeded(self, attempt_id: str, actor_webid: str) -> None:
        """Mark the attempt as succeeded and upgrade the transport path to direct.

        Raises HolePunchForbidden if actor is not party to the attempt.
        Raises InvalidPunchTransition if the attempt is not in ``accepted`` state
        or if no peer endpoint has been recorded.
        """
        attempt = self._store.get_hole_punch_attempt_for_actor(attempt_id, actor_webid)
        if attempt is None:
            raise HolePunchForbidden(
                f"actor {actor_webid!r} is not party to attempt {attempt_id!r}"
            )
        if attempt["state"] != PUNCH_STATE_ACCEPTED:
            raise InvalidPunchTransition(
                f"mark_succeeded requires 'accepted' state; current state is {attempt['state']!r}"
            )
        if not attempt.get("peer_ip"):
            raise InvalidPunchTransition(
                "mark_succeeded requires peer endpoint proof (peer_ip must be recorded)"
            )
        self._transition(attempt_id, PUNCH_STATE_SUCCEEDED, completed=True)
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

    def mark_failed(self, attempt_id: str, actor_webid: str) -> None:
        """Mark the attempt as failed (relay path remains active).

        Raises HolePunchForbidden if actor is not party to the attempt.
        """
        attempt = self._store.get_hole_punch_attempt_for_actor(attempt_id, actor_webid)
        if attempt is None:
            raise HolePunchForbidden(
                f"actor {actor_webid!r} is not party to attempt {attempt_id!r}"
            )
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

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _transition(
        self,
        attempt_id: str,
        new_state: str,
        completed: bool = False,
    ) -> None:
        """Write a state transition after validating it against the state machine."""
        current_attempt = self._store.get_hole_punch_attempt(attempt_id)
        current_state = current_attempt["state"] if current_attempt else None
        if current_state is not None and not is_valid_punch_transition(current_state, new_state):
            raise InvalidPunchTransition(
                f"cannot transition from {current_state!r} to {new_state!r}"
            )
        now = time.time()
        kwargs: dict = {"state": new_state, "updated_at": now}
        if completed:
            kwargs["completed_at"] = now
        self._store.update_hole_punch_attempt(attempt_id, **kwargs)
