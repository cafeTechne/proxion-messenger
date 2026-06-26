"""Transport selection policy for Proxion federation paths.

Decides which transport to use for a peer based on stored WireGuard overlay
state. No live connections are made here — decisions are purely based on the
most recently recorded peer state in LocalStore.

Path modes
----------
- ``direct``  — WireGuard direct UDP, recent handshake confirmed.
- ``relay``   — HTTP relay path via the existing relay.py protocol.
- ``none``    — No known path; caller should prompt for address exchange.

Sealed-sender requirement
-------------------------
When the relay path is active, the message MUST be sealed (e2e_v=3) so the
relay server cannot observe the sender's identity or message content.
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from .local_store import LocalStore

HANDSHAKE_STALE_SECONDS: int = 180


def select_transport(
    store: "LocalStore", peer_webid: str
) -> Literal["direct", "relay", "none"]:
    """Return the preferred transport for *peer_webid* based on stored state.

    Logic
    -----
    1. If no peer record exists → ``"none"``.
    2. If path_mode is ``"direct"`` AND last_handshake_at is within
       HANDSHAKE_STALE_SECONDS → ``"direct"``.
    3. If path_mode is ``"direct"`` but handshake is stale, or path_mode is
       ``"relay"`` → ``"relay"``.
    4. Otherwise → ``"none"``.
    """
    peer = store.get_wg_peer(peer_webid)
    if not peer:
        return "none"
    if peer["path_mode"] == "direct":
        handshake_at = peer.get("last_handshake_at") or 0.0
        age = time.time() - handshake_at
        if age <= HANDSHAKE_STALE_SECONDS:
            return "direct"
        return "relay"
    if peer["path_mode"] == "relay":
        return "relay"
    return "none"


def requires_sealed_sender(store: "LocalStore", peer_webid: str) -> bool:
    """Return True when the relay path is active for *peer_webid*.

    Callers should enforce e2e_v=3 (sealed sender) before relaying when this
    returns True.
    """
    return select_transport(store, peer_webid) == "relay"


def record_transport_event(
    store: "LocalStore",
    peer_webid: str,
    old_mode: str | None,
    new_mode: str,
    reason: str = "",
) -> None:
    """Log a transport mode transition to wg_connectivity_events."""
    store.log_wg_connectivity_event(peer_webid, old_mode, new_mode, reason)
