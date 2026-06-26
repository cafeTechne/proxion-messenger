"""Canonical monotonic message delivery state machine.

States advance in one direction only: queued → sent → delivered → read.
Any transition that moves backwards is rejected.

Usage
-----
    from proxion_messenger_core.delivery_state import is_valid_transition, STATES

    if is_valid_transition(current_state, new_state):
        store.set_message_delivery_state(message_id, receiver_webid, new_state)
"""
from __future__ import annotations

STATES: tuple[str, ...] = ("queued", "sent", "delivered", "read")
_ORDER: dict[str, int] = {s: i for i, s in enumerate(STATES)}


def is_valid_transition(current: str | None, new: str) -> bool:
    """Return True if transitioning from *current* to *new* is a valid monotonic step.

    Parameters
    ----------
    current:
        The current state, or None if no state has been recorded yet.
    new:
        The proposed next state.
    """
    if new not in _ORDER:
        return False
    if current is None:
        return True
    return _ORDER[new] > _ORDER.get(current, -1)
