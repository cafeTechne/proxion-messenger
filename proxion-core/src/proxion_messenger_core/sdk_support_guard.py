"""Runtime SDK support enforcement guard (Round 14).

Validates that the Solid/Inrupt SDK packages declared in web/package.json
are present and not forbidden.  Called at gateway startup when
PROXION_REQUIRE_SUPPORTED_SOLID_SDK_RUNTIME=1.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_POLICY_VERSION = "1"

REQUIRED_PACKAGES = [
    "@inrupt/solid-client",
    "@inrupt/solid-client-authn-node",
    "@inrupt/solid-client-authn-browser",
    "@inrupt/solid-client-notifications",
]

CONDITIONAL_PACKAGES = {
    "@inrupt/solid-client-access-grants": "PROXION_ENABLE_ACCESS_GRANTS",
}

FORBIDDEN_PACKAGES = frozenset({
    "solid-auth-client",
    "solid-auth-fetcher",
})


def _find_package_json() -> Optional[Path]:
    here = Path(__file__).resolve().parent
    candidates = [
        here.parents[4] / "web" / "package.json",
        here.parents[3] / "web" / "package.json",
        here.parents[2] / "web" / "package.json",
    ]
    env_dir = os.environ.get("PROXION_WEB_DIR", "")
    if env_dir:
        candidates.insert(0, Path(env_dir) / "package.json")
    for c in candidates:
        if c.exists():
            return c
    return None


def check_sdk_support(pkg_json_path: Optional[str] = None) -> dict:
    """Check installed Solid SDK packages against policy.

    Returns dict with: ok, unsupported_packages, missing_packages, policy_version.
    """
    missing: list[str] = []
    unsupported: list[str] = []

    path = Path(pkg_json_path) if pkg_json_path else _find_package_json()
    if path is None or not path.exists():
        return {
            "ok": False,
            "unsupported_packages": [],
            "missing_packages": list(REQUIRED_PACKAGES),
            "policy_version": _POLICY_VERSION,
            "error": "package.json not found",
        }

    try:
        pkg = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "ok": False,
            "unsupported_packages": [],
            "missing_packages": list(REQUIRED_PACKAGES),
            "policy_version": _POLICY_VERSION,
            "error": f"package.json parse error: {exc}",
        }

    all_declared: dict = {
        **pkg.get("dependencies", {}),
        **pkg.get("devDependencies", {}),
    }

    for pkg_name in REQUIRED_PACKAGES:
        if pkg_name not in all_declared:
            missing.append(pkg_name)
        elif not all_declared[pkg_name]:
            unsupported.append(pkg_name)

    for pkg_name, env_key in CONDITIONAL_PACKAGES.items():
        if os.environ.get(env_key) == "1" and pkg_name not in all_declared:
            missing.append(pkg_name)

    for pkg_name in FORBIDDEN_PACKAGES:
        if pkg_name in all_declared:
            unsupported.append(pkg_name)

    return {
        "ok": not missing and not unsupported,
        "unsupported_packages": unsupported,
        "missing_packages": missing,
        "policy_version": _POLICY_VERSION,
    }


def enforce_sdk_support_guard(store=None) -> None:
    """Enforce SDK support gate at startup.

    No-op unless PROXION_REQUIRE_SUPPORTED_SOLID_SDK_RUNTIME=1.
    PROXION_ALLOW_UNSUPPORTED_SDK_UNTIL=<unix_ts> provides a temporary
    emergency bypass that emits a critical security event.

    Raises RuntimeError if guard fails and no valid bypass is present.
    """
    if os.environ.get("PROXION_REQUIRE_SUPPORTED_SOLID_SDK_RUNTIME") != "1":
        return

    result = check_sdk_support()
    if result["ok"]:
        logger.info("SDK support guard passed (policy_version=%s)", result["policy_version"])
        return

    bypass_str = os.environ.get("PROXION_ALLOW_UNSUPPORTED_SDK_UNTIL", "")
    if bypass_str:
        try:
            bypass_until = float(bypass_str)
            if time.time() < bypass_until:
                _emit_bypass_event(store, result, bypass_until)
                logger.critical(
                    "SDK support guard BYPASSED until %s — unsupported=%s missing=%s",
                    bypass_until,
                    result["unsupported_packages"],
                    result["missing_packages"],
                )
                return
        except ValueError:
            pass

    raise RuntimeError(
        f"SDK support guard failed (policy_version={result['policy_version']}): "
        f"missing={result['missing_packages']} "
        f"unsupported={result['unsupported_packages']}. "
        "Set PROXION_ALLOW_UNSUPPORTED_SDK_UNTIL=<unix_ts> for a temporary emergency bypass."
    )


def _emit_bypass_event(store, result: dict, bypass_until: float) -> None:
    if store is None:
        return
    try:
        store.save_security_event(
            "sdk_support_guard_bypassed",
            "critical",
            details=json.dumps({
                "missing": result["missing_packages"],
                "unsupported": result["unsupported_packages"],
                "bypass_until": bypass_until,
                "policy_version": result["policy_version"],
            }),
        )
    except Exception:
        pass
