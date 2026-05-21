"""Connectivity diagnostics for Proxion overlay network.

Returns structured status dicts with plain-language labels and actionable next
steps.  No live network probes are performed inside this module — callers inject
externally-measured reachability results via ``extra_checks``.

Usage
-----
    from proxion_messenger_core.connectivity_diagnostics import get_connectivity_status
    status = get_connectivity_status(store)
    # status["label"] → "Private direct connection" / "Private relayed connection" / ...
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .local_store import LocalStore

_LABELS = {
    "all_direct": "Private direct connection",
    "some_relay": "Private relayed connection",
    "no_peers": "No peers configured",
    "no_identity": "Overlay not set up",
    "degraded": "Needs attention",
}


def get_connectivity_status(
    store: "LocalStore",
    extra_checks: dict[str, Any] | None = None,
) -> dict:
    """Return structured connectivity diagnostics from DB state.

    Parameters
    ----------
    store:
        LocalStore instance to query.
    extra_checks:
        Optional dict of externally-measured results, e.g.
        ``{"udp_reachable": True, "relay_reachable": False}``.

    Returns
    -------
    dict with keys:
        overlay_identity (dict | None)
        peer_count (int)
        direct_peers (int)
        relay_peers (int)
        unknown_peers (int)
        label (str) — plain-language status
        next_steps (list[str]) — actionable guidance
        extra_checks (dict) — echo of input or {}
    """
    extra = extra_checks or {}
    identity = store.get_wg_local_identity()
    direct = store.get_wg_peers_by_mode("direct")
    relay = store.get_wg_peers_by_mode("relay")
    unknown = store.get_wg_peers_by_mode("unknown")
    peer_count = len(direct) + len(relay) + len(unknown)

    next_steps: list[str] = []

    if identity is None:
        label = _LABELS["no_identity"]
        next_steps.append("Enable Easy Federation in Settings to set up your overlay identity.")
    elif peer_count == 0:
        label = _LABELS["no_peers"]
        next_steps.append("Share your Connect ID with a contact to begin.")
    elif len(relay) > 0 and len(direct) == 0:
        label = _LABELS["some_relay"]
        next_steps.append(
            "All connections are relayed. Direct connections improve privacy — "
            "check that your WireGuard port is reachable."
        )
    elif len(relay) > 0:
        label = _LABELS["some_relay"]
        next_steps.append(
            f"{len(relay)} peer(s) are using relayed connections. "
            "Direct connections are preferred for best privacy."
        )
    else:
        label = _LABELS["all_direct"]

    if extra.get("relay_reachable") is False:
        label = _LABELS["degraded"]
        next_steps.append("Relay server is not reachable. Check your internet connection.")

    return {
        "overlay_identity": {"pubkey_b64": identity["pubkey_b64"]} if identity else None,
        "peer_count": peer_count,
        "direct_peers": len(direct),
        "relay_peers": len(relay),
        "unknown_peers": len(unknown),
        "label": label,
        "next_steps": next_steps,
        "extra_checks": extra,
    }


def format_next_steps(status: dict) -> list[str]:
    """Return the ``next_steps`` list from a status dict (convenience wrapper)."""
    return status.get("next_steps", [])
