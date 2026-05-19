"""Signed runtime config verification (Round 9).

Supports optional Ed25519-signed config files.  When PROXION_REQUIRE_SIGNED_CONFIG=1
the gateway will refuse to start if the config signature cannot be verified.

Usage:
    config = load_verified_config(config_path, sig_path, pubkey_hex)
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


class ConfigVerificationError(Exception):
    """Raised when config signature verification fails."""


def load_verified_config(
    config_path: str,
    sig_path: str,
    pubkey_hex: Optional[str] = None,
) -> dict:
    """Load and verify a signed JSON config file.

    Args:
        config_path: Path to the JSON config file.
        sig_path: Path to the detached Ed25519 signature (raw 64 bytes, hex-encoded).
        pubkey_hex: Hex-encoded Ed25519 public key (32 bytes).  If None, reads
                    PROXION_CONFIG_PUBKEY_HEX from environment.

    Returns:
        Parsed config dict.

    Raises:
        ConfigVerificationError: On any signature validation failure.
        FileNotFoundError: If config or sig file is missing.
    """
    if pubkey_hex is None:
        pubkey_hex = os.environ.get("PROXION_CONFIG_PUBKEY_HEX", "")
    if not pubkey_hex:
        raise ConfigVerificationError("No config public key configured (PROXION_CONFIG_PUBKEY_HEX)")

    try:
        pub_bytes = bytes.fromhex(pubkey_hex)
    except ValueError as exc:
        raise ConfigVerificationError(f"Invalid PROXION_CONFIG_PUBKEY_HEX: {exc}") from exc

    try:
        config_bytes = open(config_path, "rb").read()
    except OSError as exc:
        raise FileNotFoundError(f"Config file not found: {config_path}") from exc

    try:
        sig_hex = open(sig_path, "r", encoding="ascii").read().strip()
        sig_bytes = bytes.fromhex(sig_hex)
    except OSError as exc:
        raise FileNotFoundError(f"Config signature file not found: {sig_path}") from exc
    except ValueError as exc:
        raise ConfigVerificationError(f"Invalid signature encoding: {exc}") from exc

    # Verify Ed25519 signature
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        pub_key = Ed25519PublicKey.from_public_bytes(pub_bytes)
        pub_key.verify(sig_bytes, config_bytes)
    except Exception as exc:
        raise ConfigVerificationError(f"Config signature invalid: {exc}") from exc

    try:
        return json.loads(config_bytes)
    except json.JSONDecodeError as exc:
        raise ConfigVerificationError(f"Config is not valid JSON: {exc}") from exc


def check_signed_config_startup(store=None) -> None:
    """Check signed config requirement at gateway startup.

    If PROXION_REQUIRE_SIGNED_CONFIG=1 and no valid config exists, raises
    ConfigVerificationError and optionally persists a security event.

    This is a no-op when the env var is not set.
    """
    if os.environ.get("PROXION_REQUIRE_SIGNED_CONFIG") != "1":
        return

    config_path = os.environ.get("PROXION_CONFIG_FILE", "")
    sig_path = os.environ.get("PROXION_CONFIG_SIG_FILE", "")

    if not config_path or not sig_path:
        _emit_security_event(store, "config_signature_invalid",
                             "PROXION_REQUIRE_SIGNED_CONFIG=1 but no config/sig paths set")
        raise ConfigVerificationError(
            "PROXION_REQUIRE_SIGNED_CONFIG=1 requires PROXION_CONFIG_FILE and PROXION_CONFIG_SIG_FILE"
        )

    try:
        load_verified_config(config_path, sig_path)
        logger.info("Signed config verified: %s", config_path)
    except (ConfigVerificationError, FileNotFoundError) as exc:
        _emit_security_event(store, "config_signature_invalid", str(exc))
        raise


def _emit_security_event(store, event_type: str, detail: str) -> None:
    if store is None:
        return
    try:
        store.save_security_event(event_type, "critical", details=detail)
    except Exception:
        pass
