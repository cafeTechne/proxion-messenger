"""Runtime supply-chain integrity verification (Round 10).

Verifies SHA-256 hashes of critical runtime modules against an optional signed manifest.
Expose verify_runtime_integrity(strict) -> dict for use at gateway startup.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


class IntegrityError(Exception):
    """Raised when runtime integrity check fails in strict mode."""


def _hash_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_manifest(manifest_path: str, sig_path: str, pubkey_hex: Optional[str] = None) -> dict:
    if pubkey_hex is None:
        pubkey_hex = os.environ.get("PROXION_RUNTIME_PUBKEY_HEX", "")
    if not pubkey_hex:
        raise IntegrityError("No runtime manifest public key (PROXION_RUNTIME_PUBKEY_HEX)")

    try:
        pub_bytes = bytes.fromhex(pubkey_hex)
    except ValueError as exc:
        raise IntegrityError(f"Invalid PROXION_RUNTIME_PUBKEY_HEX: {exc}") from exc

    try:
        manifest_bytes = open(manifest_path, "rb").read()
    except OSError as exc:
        raise FileNotFoundError(f"Manifest file not found: {manifest_path}") from exc

    try:
        sig_hex = open(sig_path, "r", encoding="ascii").read().strip()
        sig_bytes = bytes.fromhex(sig_hex)
    except OSError as exc:
        raise FileNotFoundError(f"Manifest signature not found: {sig_path}") from exc
    except ValueError as exc:
        raise IntegrityError(f"Invalid signature encoding: {exc}") from exc

    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        pub_key = Ed25519PublicKey.from_public_bytes(pub_bytes)
        pub_key.verify(sig_bytes, manifest_bytes)
    except Exception as exc:
        raise IntegrityError(f"Manifest signature invalid: {exc}") from exc

    try:
        return json.loads(manifest_bytes)
    except json.JSONDecodeError as exc:
        raise IntegrityError(f"Manifest is not valid JSON: {exc}") from exc


def verify_runtime_integrity(strict: bool = False) -> dict:
    """Verify SHA-256 hashes of critical runtime modules against a signed manifest.

    If PROXION_MANIFEST_FILE and PROXION_MANIFEST_SIG_FILE are set, loads and verifies
    the manifest signature, then checks each listed file's hash. Without a manifest,
    runs as a trivial pass (just probes the gateway module hash for telemetry).

    Returns:
        dict with keys: passed (bool), errors (list[str]), checked (int),
                        manifest_verified (bool), gateway_hash (str, optional).

    Raises:
        IntegrityError: if strict=True and any check fails.
    """
    manifest_path = os.environ.get("PROXION_MANIFEST_FILE", "")
    sig_path = os.environ.get("PROXION_MANIFEST_SIG_FILE", "")

    result: dict = {
        "passed": True,
        "errors": [],
        "checked": 0,
        "manifest_verified": False,
    }

    if not manifest_path or not sig_path:
        # No manifest configured — trivial pass, probe own hash for telemetry
        try:
            import proxion_messenger_core.gateway as _gw_mod
            import inspect as _inspect
            _gw_path = _inspect.getfile(_gw_mod)
            result["gateway_hash"] = _hash_file(_gw_path)
            result["checked"] = 1
        except Exception as exc:
            result["errors"].append(f"hash_probe_failed: {exc}")
        return result

    try:
        manifest = _load_manifest(manifest_path, sig_path)
        result["manifest_verified"] = True
    except (IntegrityError, FileNotFoundError) as exc:
        result["passed"] = False
        result["errors"].append(f"manifest_load_failed: {exc}")
        if strict:
            raise IntegrityError(str(exc)) from exc
        return result

    file_hashes = manifest.get("files", {})
    for path, expected_hash in file_hashes.items():
        result["checked"] += 1
        try:
            actual = _hash_file(path)
            if actual != expected_hash:
                err = f"hash_mismatch: {path} expected={expected_hash[:16]} actual={actual[:16]}"
                result["errors"].append(err)
                result["passed"] = False
                logger.warning("Runtime integrity failure: %s", err)
        except FileNotFoundError:
            err = f"file_missing: {path}"
            result["errors"].append(err)
            result["passed"] = False

    if not result["passed"] and strict:
        raise IntegrityError(f"Runtime integrity check failed: {result['errors']}")

    return result


def check_runtime_integrity_startup(store=None) -> None:
    """Check runtime integrity at gateway startup.

    No-op when PROXION_REQUIRE_RUNTIME_INTEGRITY is not set to '1'.
    Emits runtime_integrity_failed security event and raises IntegrityError on failure.
    """
    if os.environ.get("PROXION_REQUIRE_RUNTIME_INTEGRITY") != "1":
        return

    try:
        result = verify_runtime_integrity(strict=True)
        logger.info("Runtime integrity check passed: %d files checked", result.get("checked", 0))
    except IntegrityError as exc:
        detail = str(exc)
        logger.error("Runtime integrity check failed: %s", detail)
        if store is not None:
            try:
                store.save_security_event("runtime_integrity_failed", "critical", details=detail)
            except Exception:
                pass
        raise
