"""R17: Platform keychain adapter for secure wrap-key storage.

Stores the AgentState wrap key in the OS keychain (Windows Credential Manager,
macOS Keychain, Linux libsecret) via the ``keyring`` package (optional dependency).
Falls back gracefully when keyring is unavailable.

Usage::

    from proxion_messenger_core.keychain_store import store_wrap_key, load_wrap_key

    # On first run: generate a random wrap key and store it in the keychain.
    key = os.urandom(32)
    store_wrap_key("alice-identity-id", key)

    # On subsequent runs: load from keychain (no passphrase prompt).
    key = load_wrap_key("alice-identity-id")
    if key is None:
        # Not found — first run or keychain was cleared.
        ...
"""

from __future__ import annotations

import base64
import os

SERVICE_NAME = "ProxionMessenger"


class KeychainError(Exception):
    """Raised when a keychain operation fails non-gracefully."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _import_keyring():
    """Return the keyring module, or raise ImportError if unavailable."""
    import keyring  # noqa: PLC0415  (lazy import — optional dependency)
    return keyring


def _b64enc(b: bytes) -> str:
    return base64.b64encode(b).decode()


def _b64dec(s: str) -> bytes:
    return base64.b64decode(s)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def is_keychain_available() -> bool:
    """Return True if the ``keyring`` package is installed and has a usable backend.

    This performs a lightweight probe (no actual credential read/write).
    """
    try:
        kr = _import_keyring()
        backend = kr.get_keyring()
        # The ``keyring.core.fail.Fail`` backend signals "no usable backend".
        backend_type = type(backend).__name__
        if backend_type in {"Fail", "FailKeyring"}:
            return False
        return True
    except Exception:
        return False


def store_wrap_key(identity_id: str, key_bytes: bytes) -> None:
    """Store *key_bytes* (32 bytes) in the OS keychain under *identity_id*.

    Parameters
    ----------
    identity_id:
        Unique string identifying the identity (e.g. a WebID or hash thereof).
        Used as the ``username`` field in the keychain entry.
    key_bytes:
        Raw 32-byte wrap key to store.

    Raises
    ------
    KeychainError
        If the keyring package is unavailable or the store operation fails.
    """
    try:
        kr = _import_keyring()
    except ImportError as exc:
        raise KeychainError(
            "keyring package not installed — install it with: pip install keyring"
        ) from exc

    encoded = _b64enc(key_bytes)
    try:
        kr.set_password(SERVICE_NAME, identity_id, encoded)
    except Exception as exc:
        raise KeychainError(f"Failed to store wrap key for {identity_id!r}: {exc}") from exc


def load_wrap_key(identity_id: str) -> bytes | None:
    """Return the stored wrap key bytes, or *None* if not found.

    Parameters
    ----------
    identity_id:
        The same identifier used when calling ``store_wrap_key``.

    Returns
    -------
    bytes | None
        32-byte wrap key, or ``None`` if no entry exists.

    Raises
    ------
    KeychainError
        If the keyring package is unavailable or a backend error occurs
        (as opposed to a simple "not found" result).
    """
    try:
        kr = _import_keyring()
    except ImportError as exc:
        raise KeychainError(
            "keyring package not installed — install it with: pip install keyring"
        ) from exc

    try:
        value = kr.get_password(SERVICE_NAME, identity_id)
    except Exception as exc:
        raise KeychainError(f"Failed to load wrap key for {identity_id!r}: {exc}") from exc

    if value is None:
        return None
    return _b64dec(value)


def delete_wrap_key(identity_id: str) -> None:
    """Remove the wrap key for *identity_id* from the keychain.

    No-op if the entry does not exist.

    Raises
    ------
    KeychainError
        If the keyring package is unavailable or a backend error occurs.
    """
    try:
        kr = _import_keyring()
    except ImportError as exc:
        raise KeychainError(
            "keyring package not installed — install it with: pip install keyring"
        ) from exc

    try:
        kr.delete_password(SERVICE_NAME, identity_id)
    except Exception as exc:
        # Treat "not found" as a no-op; distinguish by message heuristic since
        # keyring raises different exception types per backend.
        exc_str = str(exc).lower()
        if any(phrase in exc_str for phrase in ("not found", "no item", "does not exist", "no such")):
            return
        raise KeychainError(
            f"Failed to delete wrap key for {identity_id!r}: {exc}"
        ) from exc


def generate_and_store_wrap_key(identity_id: str) -> bytes:
    """Generate a cryptographically random 32-byte wrap key, store it, and return it.

    Convenience function for first-run initialisation.

    Parameters
    ----------
    identity_id:
        Unique identifier for the identity whose wrap key is being created.

    Returns
    -------
    bytes
        The newly generated 32-byte wrap key.

    Raises
    ------
    KeychainError
        If storing the key fails.
    """
    key = os.urandom(32)
    store_wrap_key(identity_id, key)
    return key
