"""Idempotency envelope helpers for replay-safe command handling.

Mutating WebSocket commands (send_dm, send_room, key-upload/distribution) may
be replayed during reconnect storms or offline catch-up.  Including an ``op_id``
in the command payload lets the gateway detect duplicates and return the prior
result without re-executing the side effect.

Usage (client)
--------------
    env = make_op_envelope("send_dm", actor_webid="alice@example.org")
    ws.send(json.dumps({"type": "send_dm", **env, ...rest...}))

Usage (gateway)
---------------
    if is_duplicate_operation(store, data.get("op_id")):
        prior = store.get_operation_result(data["op_id"])
        await ws.send(json.dumps({"type": "ack", "cached": True, **prior}))
        return
    # ... execute command ...
    store.record_operation_result(data["op_id"], ...)
"""
from __future__ import annotations

import time
import uuid


def make_op_envelope(
    op_type: str,
    actor_webid: str,
    actor_device_id: str | None = None,
) -> dict:
    """Return an idempotency envelope dict to include in a mutating command."""
    return {
        "op_id": str(uuid.uuid4()),
        "op_type": op_type,
        "actor_webid": actor_webid,
        "actor_device_id": actor_device_id,
        "created_at": time.time(),
    }


def is_duplicate_operation(store, op_id: str | None) -> bool:
    """Return True if op_id has already been recorded in the store."""
    if not op_id:
        return False
    return store.get_operation_result(op_id) is not None
