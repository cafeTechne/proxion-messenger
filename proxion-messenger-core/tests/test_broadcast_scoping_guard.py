"""Guard against NEW gateway-wide broadcasts of per-thread content.

`self.broadcast(...)` reaches every session on the gateway. Sending per-thread
content (a message_id / thread_id / cert_id) that way is a leak on a shared or
multi-user gateway — R50 fixed DM edit + reaction, R52 fixed contact_added +
contact_revoked. This test scans the gateway source for any NEW literal
`self.broadcast({...})` whose payload names one of those keys, and fails unless
the call's event "type" is on the reviewed allowlist. It keeps the leak class
from silently regressing.

Indirect broadcasts (e.g. `self.broadcast(self._entry_to_event(...))`) are not
literal dicts and aren't scanned here; the pod-poll message broadcast is tracked
separately (deferred with a live-pod test).
"""
from __future__ import annotations

import glob
import os
import re

import proxion_messenger_core

SENTINEL_KEYS = ("message_id", "thread_id", "cert_id")

# Event "type" values whose literal broadcast payload legitimately names a
# per-thread key. Each entry needs a documented reason.
ALLOWLIST = {
    # Deferred: only fires on the pod-poll path and shares the message_id->thread
    # ->recipients lookup as the pod-poll message broadcast; scoped together in a
    # future round with a live CSS pod test (PLAN_ROUND_51 §E3).
    "link_preview",
}


def _gateway_sources() -> list[str]:
    pkg_dir = os.path.dirname(proxion_messenger_core.__file__)
    files = [os.path.join(pkg_dir, "gateway.py")]
    files += sorted(glob.glob(os.path.join(pkg_dir, "_gateway_*.py")))
    return [f for f in files if os.path.exists(f)]


def _literal_broadcasts(src: str):
    """Yield (line, type_value, body) for each literal self.broadcast({...})."""
    for m in re.finditer(r"self\.broadcast\(\s*\{", src):
        i = src.index("{", m.start())
        depth, j = 0, i
        while j < len(src):
            if src[j] == "{":
                depth += 1
            elif src[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        body = src[i:j + 1]
        line = src[:m.start()].count("\n") + 1
        tm = re.search(r'"type"\s*:\s*"([^"]+)"', body)
        yield line, (tm.group(1) if tm else "?"), body


def test_no_unreviewed_per_thread_broadcast():
    offenders = []
    for path in _gateway_sources():
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        for line, typ, body in _literal_broadcasts(src):
            hits = [k for k in SENTINEL_KEYS if re.search(rf'["\']?{k}["\']?\s*:', body)]
            if hits and typ not in ALLOWLIST:
                offenders.append(f"{os.path.basename(path)}:{line} type={typ} keys={hits}")
    assert not offenders, (
        "New gateway-wide broadcast of per-thread content — scope it to the "
        "thread's participants (see PLAN_ROUND_51 §E3) or, if genuinely global, "
        "add the event type to ALLOWLIST with a reason:\n  " + "\n  ".join(offenders)
    )


def test_guard_would_catch_a_planted_leak():
    """The scanner itself works: a synthetic payload with a sentinel key and an
    un-allowlisted type is detected."""
    planted = 'self.broadcast({\n    "type": "totally_new_leak",\n    "message_id": mid,\n})'
    found = [
        typ for _, typ, body in _literal_broadcasts(planted)
        if any(re.search(rf'["\']?{k}["\']?\s*:', body) for k in SENTINEL_KEYS)
    ]
    assert "totally_new_leak" in found
    assert "totally_new_leak" not in ALLOWLIST
