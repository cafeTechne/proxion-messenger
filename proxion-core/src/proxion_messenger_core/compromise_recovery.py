"""R12: Orchestrated key compromise recovery workflow with durable checkpoints."""
from __future__ import annotations

import secrets
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .local_store import LocalStore

STAGES = [
    "prepare",
    "rotate_identity",
    "rotate_store",
    "revoke_old_trust",
    "notify_peers",
    "finalize",
]

_STATUS_ACTIVE = "active"
_STATUS_COMPLETED = "completed"
_STATUS_ABORTED = "aborted"


def start_compromise_recovery(store: "LocalStore", reason: str, initiated_by: str) -> str:
    """Create a new recovery session with all stages at 'pending'. Return session_id."""
    session_id = secrets.token_hex(16)
    now = time.time()
    store.create_compromise_recovery_session(
        session_id=session_id,
        reason=reason,
        initiated_by=initiated_by,
        steps=STAGES,
    )
    return session_id


def resume_compromise_recovery(store: "LocalStore", session_id: str) -> dict:
    """Resume a paused recovery session. Returns current session status."""
    session = store.get_compromise_recovery_session(session_id)
    if not session:
        return {"error": "session_not_found"}
    if session.get("status") != _STATUS_ACTIVE:
        return {"error": "session_not_active", "status": session.get("status")}
    return {
        "session_id": session_id,
        "status": _STATUS_ACTIVE,
        "stages": STAGES,
        "reason": session.get("reason"),
        "initiated_by": session.get("initiated_by"),
    }


def abort_compromise_recovery(store: "LocalStore", session_id: str) -> bool:
    """Mark a recovery session as aborted. Idempotent. Returns True on success."""
    session = store.get_compromise_recovery_session(session_id)
    if not session:
        return False
    if session.get("status") == _STATUS_ABORTED:
        return True
    store.set_compromise_recovery_status(session_id, _STATUS_ABORTED)
    return True
