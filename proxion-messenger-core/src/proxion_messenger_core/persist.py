"""Agent state persistence — save and load identity to/from disk.

An *agent* has two long-lived private keys:

* **identity key** — Ed25519 (signing, PoP, certificate issuance)
* **store key** — X25519 (receiving sealed messages from the Coordination Store)

Both are private and must never leave the device in plaintext.  This module
serialises them as PEM with passphrase-based encryption (AES-256-CBC + SHA-256
key derivation via ``BestAvailableEncryption``), embeds them in a JSON state
file alongside the revocation list and known certificates, and writes the file
atomically.

File format
-----------
The state file is UTF-8 JSON with the following top-level fields::

    {
      "@type": "ProxionAgentState",
      "version": 1,
      "identity_key_pem": "<encrypted PEM string>",
      "store_key_pem": "<encrypted PEM string>",
      "revocation_entries": {
        "<revocation_id_hex>": "<ISO-8601 datetime>"
      },
      "certificates": [
        { "@type": "RelationshipCertificate", ... }
      ]
    }

The PEM strings are the standard output of
``cryptography.hazmat.primitives.serialization.private_bytes`` with
``BestAvailableEncryption(passphrase)`` — interoperable with standard tools.

Atomic writes
-------------
The file is written to ``<path>.tmp`` then renamed to ``<path>``.  This
prevents a partially-written state file from being observed as valid.

Usage
-----
::

    from proxion_messenger_core.persist import AgentState

    # First run — create a fresh agent
    state = AgentState.generate()
    state.save("/path/to/agent.json", b"my-passphrase")

    # Subsequent runs — restore
    state = AgentState.load("/path/to/agent.json", b"my-passphrase")

    # Access keys and state
    token = issue_from_certificate(cert, ..., signing_key=state.signing_key_bytes)
    msgs = store.list_all(mailbox_id_for(state.store_pub_bytes))
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Union

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    BestAvailableEncryption,
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

import time

from .errors import ProxionError
from .federation import Capability, FederationInvite, RelationshipCertificate
from .revocation import RevocationList


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class PersistError(ProxionError):
    """Raised when agent state cannot be saved or loaded."""


# ---------------------------------------------------------------------------
# PendingInvite
# ---------------------------------------------------------------------------

@dataclass
class PendingInvite:
    """A federation invite that has been sent but not yet finalised.

    Attributes
    ----------
    invite:
        The full :class:`~proxion_messenger_core.federation.FederationInvite` that was sent.
    peer_store_pub_hex:
        Hex-encoded 32-byte X25519 public key of the peer's coordination store.
        Used by :func:`~proxion_messenger_core.handshake.finalize_handshake` to seal
        the resulting certificate back to the peer.
    sent_at:
        Unix timestamp when the invite was dispatched.
    """

    invite: FederationInvite
    peer_store_pub_hex: str
    sent_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "invite": self.invite.to_dict(),
            "peer_store_pub_hex": self.peer_store_pub_hex,
            "sent_at": self.sent_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PendingInvite":
        return cls(
            invite=FederationInvite.from_dict(d["invite"]),
            peer_store_pub_hex=d["peer_store_pub_hex"],
            sent_at=d.get("sent_at", 0.0),
        )


# ---------------------------------------------------------------------------
# Internal PEM helpers
# ---------------------------------------------------------------------------

def _ed25519_to_pem(key: Ed25519PrivateKey, passphrase: Optional[bytes]) -> str:
    enc = BestAvailableEncryption(passphrase) if passphrase else NoEncryption()
    return key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, enc).decode("ascii")


def _x25519_to_pem(key: X25519PrivateKey, passphrase: Optional[bytes]) -> str:
    enc = BestAvailableEncryption(passphrase) if passphrase else NoEncryption()
    return key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, enc).decode("ascii")


def _ed25519_from_pem(pem: str, passphrase: Optional[bytes]) -> Ed25519PrivateKey:
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    try:
        key = load_pem_private_key(pem.encode("ascii"), password=passphrase)
    except Exception as exc:
        raise PersistError(f"failed to load Ed25519 identity key: {exc}") from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise PersistError("identity_key_pem is not an Ed25519 private key")
    return key


def _x25519_from_pem(pem: str, passphrase: Optional[bytes]) -> X25519PrivateKey:
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    try:
        key = load_pem_private_key(pem.encode("ascii"), password=passphrase)
    except Exception as exc:
        raise PersistError(f"failed to load X25519 store key: {exc}") from exc
    if not isinstance(key, X25519PrivateKey):
        raise PersistError("store_key_pem is not an X25519 private key")
    return key


# ---------------------------------------------------------------------------
# RevocationList serialisation
# ---------------------------------------------------------------------------

def _rl_to_dict(rl: RevocationList) -> dict:
    """Snapshot the revocation list as ``{revocation_id: iso_datetime}``."""
    with rl._lock:
        return {
            rid: entry.revoked_until.isoformat()
            for rid, entry in rl._entries.items()
        }


def _rl_from_dict(d: dict) -> RevocationList:
    rl = RevocationList()
    for rid, iso in d.items():
        until = datetime.fromisoformat(iso)
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
        rl.revoke_until(rid, until)
    return rl


# ---------------------------------------------------------------------------
# AgentState
# ---------------------------------------------------------------------------

@dataclass
class AgentState:
    """The durable state of a Proxion agent.

    Attributes
    ----------
    identity_key:
        Ed25519 private key used for signing federation invites, certificates,
        and challenge responses (PoP).  Keep secret.
    store_key:
        X25519 private key used to decrypt sealed messages from the
        Coordination Store.  Keep secret.
    revocation_list:
        Local revocation state.  Populated by :func:`~proxion_messenger_core.revoke.receive_revocations`
        and :meth:`~proxion_messenger_core.revocation.RevocationList.revoke`.
    certificates:
        Known :class:`~proxion_messenger_core.federation.RelationshipCertificate` objects
        received through successful handshakes.
    """

    identity_key: Ed25519PrivateKey
    store_key: X25519PrivateKey
    revocation_list: RevocationList = field(default_factory=RevocationList)
    certificates: List[RelationshipCertificate] = field(default_factory=list)
    pending_invites: List[PendingInvite] = field(default_factory=list)
    css_pod_url: Optional[str] = None
    css_webid: Optional[str] = None
    identity_key_version: int = 1
    store_key_version: int = 1
    identity_key_rotated_at: Optional[float] = None
    store_key_rotated_at: Optional[float] = None
    # R16: key lifecycle timestamps
    identity_key_created_at: Optional[float] = None
    store_key_created_at: Optional[float] = None

    # ------------------------------------------------------------------
    # Convenience properties
    # ------------------------------------------------------------------

    @property
    def identity_pub(self) -> Ed25519PublicKey:
        """Ed25519 public key — safe to share."""
        return self.identity_key.public_key()

    @property
    def identity_pub_bytes(self) -> bytes:
        """Raw 32-byte Ed25519 public key."""
        return self.identity_pub.public_bytes(Encoding.Raw, PublicFormat.Raw)

    @property
    def store_pub(self) -> X25519PublicKey:
        """X25519 public key — share with peers so they can seal messages for you."""
        return self.store_key.public_key()

    @property
    def store_pub_bytes(self) -> bytes:
        """Raw 32-byte X25519 public key."""
        return self.store_pub.public_bytes(Encoding.Raw, PublicFormat.Raw)

    @property
    def signing_key_bytes(self) -> bytes:
        """Raw 32-byte Ed25519 private key — for use as ``signing_key`` in token APIs."""
        return self.identity_key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def generate(cls) -> "AgentState":
        """Create a brand-new agent with freshly generated keypairs."""
        now = time.time()
        return cls(
            identity_key=Ed25519PrivateKey.generate(),
            store_key=X25519PrivateKey.generate(),
            identity_key_created_at=now,
            store_key_created_at=now,
        )

    # ------------------------------------------------------------------
    # Invite TTL
    # ------------------------------------------------------------------

    def purge_expired_invites(self, now: Optional[float] = None) -> List["PendingInvite"]:
        """Remove and return pending invites whose ``expires_at`` has passed.

        Parameters
        ----------
        now:
            Unix timestamp to use as "current time".  Defaults to
            ``time.time()``.  Pass an explicit value in tests.

        Returns
        -------
        List[PendingInvite]
            The removed (expired) invites — callers may log them.
        """
        if now is None:
            now = time.time()
        active, expired = [], []
        for pi in self.pending_invites:
            (expired if pi.invite.expires_at < now else active).append(pi)
        self.pending_invites = active
        return expired

    # ------------------------------------------------------------------
    # Key rotation
    # ------------------------------------------------------------------

    def rotate_store_key(self) -> X25519PrivateKey:
        """Replace the X25519 store key with a freshly generated one.

        Returns the **old** private key so the caller can drain the old mailbox
        before discarding it.  Messages sealed to the old public key can still
        be decrypted with the returned key for as long as the caller keeps it.

        After calling this method, call :meth:`save` to persist the new key.

        Returns
        -------
        X25519PrivateKey
            The previous store key (caller is responsible for draining or
            discarding it).

        Example
        -------
        ::

            old_key = state.rotate_store_key()
            # Drain any messages that arrived before rotation
            old_msgs = store.take_all(mailbox_id_for(old_pub_bytes))
            for sm in old_msgs:
                data = open_sealed_json(sm.envelope, old_key)
                # ... process
            state.save(path, passphrase)
        """
        self.store_key_version += 1
        self.store_key_rotated_at = time.time()
        old_key = self.store_key
        self.store_key = X25519PrivateKey.generate()
        return old_key

    def rotate_identity_key(self) -> Ed25519PrivateKey:
        """Replace the Ed25519 identity key with a freshly generated one.

        Returns the **old** private key.  Existing certificates issued against
        the old public key remain valid — their ``issuer``/``subject`` fields
        reference the old pubkey hex, which is fine; certificates are not
        automatically re-issued.

        Peers will need to be notified of the new identity key via a fresh
        federation handshake if they need to verify future signatures.

        After calling this method, call :meth:`save` to persist the new key.

        Returns
        -------
        Ed25519PrivateKey
            The previous identity key.
        """
        self.identity_key_version += 1
        self.identity_key_rotated_at = time.time()
        old_key = self.identity_key
        self.identity_key = Ed25519PrivateKey.generate()
        return old_key

    # ------------------------------------------------------------------
    # Backup / restore
    # ------------------------------------------------------------------

    def export_backup(
        self,
        passphrase: Optional[bytes] = None,
        recipient_pubkey_hex: Optional[str] = None,
    ) -> bytes:
        """Return an encrypted portable backup blob of both private keys.

        Exactly one of passphrase or recipient_pubkey_hex must be provided.

        passphrase mode (default): PEM-encrypted with the given passphrase.
        recipient_key mode (R10): hybrid X25519 ECDH + AES-256-GCM envelope;
            recipient decrypts with their X25519 private key.
        """
        if passphrase is not None and recipient_pubkey_hex is not None:
            raise PersistError("export_backup: passphrase and recipient_pubkey_hex are mutually exclusive")
        if passphrase is None and recipient_pubkey_hex is None:
            raise PersistError("export_backup: one of passphrase or recipient_pubkey_hex is required")

        if recipient_pubkey_hex is not None:
            return self._export_backup_recipient_key(recipient_pubkey_hex)

        # Passphrase mode (original)
        import hashlib as _hl_bak, time as _time_bak
        _identity_pem = _ed25519_to_pem(self.identity_key, passphrase)
        _store_pem = _x25519_to_pem(self.store_key, passphrase)
        _content_bytes = json.dumps(
            {"identity_key_pem": _identity_pem, "store_key_pem": _store_pem}
        ).encode("utf-8")
        _content_sha256 = _hl_bak.sha256(_content_bytes).hexdigest()
        data = {
            "@type": "ProxionBackup",
            "version": 1,
            "backup_mode": "passphrase",
            "manifest": {
                "manifest_version": "1",
                "created_at": _time_bak.time(),
                "content_sha256": _content_sha256,
                "mode": "passphrase",
                "key_versions": {"identity": "Ed25519", "store": "X25519"},
            },
            "identity_key_pem": _identity_pem,
            "store_key_pem": _store_pem,
        }
        return json.dumps(data, indent=2).encode("utf-8")

    def _export_backup_recipient_key(self, recipient_pubkey_hex: str) -> bytes:
        """R10: Encrypt backup using hybrid X25519 ECDH + AES-256-GCM envelope."""
        import base64 as _b64
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.hashes import SHA256
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

        try:
            recipient_pub_bytes = bytes.fromhex(recipient_pubkey_hex)
            recipient_pub = X25519PublicKey.from_public_bytes(recipient_pub_bytes)
        except Exception as exc:
            raise PersistError(f"Invalid recipient_pubkey_hex: {exc}") from exc

        # Generate ephemeral X25519 keypair
        ephemeral_priv = X25519PrivateKey.generate()
        ephemeral_pub = ephemeral_priv.public_key()
        shared_secret = ephemeral_priv.exchange(recipient_pub)

        # Derive AES-256 key via HKDF-SHA256
        ephemeral_pub_bytes = ephemeral_pub.public_bytes(Encoding.Raw, PublicFormat.Raw)
        hkdf = HKDF(algorithm=SHA256(), length=32, salt=None, info=b"ProxionBackupR10")
        aes_key = hkdf.derive(shared_secret)

        # Plaintext: raw DER bytes of both keys (no PEM passphrase)
        identity_der = self.identity_key.private_bytes(Encoding.DER, PrivateFormat.PKCS8, NoEncryption())
        store_der = self.store_key.private_bytes(Encoding.DER, PrivateFormat.PKCS8, NoEncryption())
        plaintext = json.dumps({
            "identity_key_der": _b64.b64encode(identity_der).decode("ascii"),
            "store_key_der": _b64.b64encode(store_der).decode("ascii"),
        }).encode("utf-8")

        nonce = os.urandom(12)
        aesgcm = AESGCM(aes_key)
        ciphertext = aesgcm.encrypt(nonce, plaintext, None)

        import hashlib as _hl_rk, time as _time_rk
        _content_sha256 = _hl_rk.sha256(ciphertext).hexdigest()
        data = {
            "@type": "ProxionBackup",
            "version": 1,
            "backup_mode": "recipient_key",
            "manifest": {
                "manifest_version": "1",
                "created_at": _time_rk.time(),
                "content_sha256": _content_sha256,
                "mode": "recipient_key",
                "key_versions": {"identity": "Ed25519", "store": "X25519"},
            },
            "ephemeral_pub_hex": ephemeral_pub_bytes.hex(),
            "nonce_hex": nonce.hex(),
            "ciphertext_hex": ciphertext.hex(),
        }
        return json.dumps(data, indent=2).encode("utf-8")

    @classmethod
    def import_backup(
        cls,
        data: bytes,
        passphrase: Optional[bytes] = None,
        recipient_privkey: Optional[object] = None,
    ) -> "AgentState":
        """Decrypt and return an AgentState from a backup blob.

        Parameters
        ----------
        data:
            Bytes previously produced by :meth:`export_backup`.
        passphrase:
            The passphrase used when exporting (passphrase mode).
        recipient_privkey:
            X25519PrivateKey used for recipient_key mode (R10).

        Raises
        ------
        PersistError
            If the data is malformed, the type tag is wrong, or decryption fails.
        """
        try:
            obj = json.loads(data.decode("utf-8"))
        except Exception as exc:
            raise PersistError(f"backup is not valid JSON: {exc}") from exc
        if obj.get("@type") != "ProxionBackup":
            raise PersistError("file does not appear to be a ProxionBackup")
        if obj.get("version", 0) != 1:
            raise PersistError(f"unsupported backup version: {obj.get('version')}")

        backup_mode = obj.get("backup_mode", "passphrase")

        if backup_mode == "recipient_key":
            if recipient_privkey is None:
                raise PersistError("recipient_key backup requires recipient_privkey")
            return cls._import_backup_recipient_key(obj, recipient_privkey)

        # Passphrase mode
        if passphrase is None:
            raise PersistError("passphrase backup requires passphrase")
        _EXPECTED_KEYS = {"@type", "version", "backup_mode", "manifest", "identity_key_pem", "store_key_pem",
                          "revocation_entries", "certificates", "pending_invites",
                          "css_pod_url", "css_webid", "agent_id"}
        unknown = set(obj.keys()) - _EXPECTED_KEYS
        if unknown:
            raise PersistError(f"backup contains unknown fields: {sorted(unknown)}")
        # R12: verify manifest content_sha256 before decryption
        if "manifest" in obj:
            import hashlib as _hl_imp
            _expected_sha256 = obj["manifest"].get("content_sha256", "")
            if _expected_sha256:
                _content_bytes = json.dumps(
                    {"identity_key_pem": obj["identity_key_pem"], "store_key_pem": obj["store_key_pem"]}
                ).encode("utf-8")
                _actual_sha256 = _hl_imp.sha256(_content_bytes).hexdigest()
                if _actual_sha256 != _expected_sha256:
                    raise PersistError("backup_integrity_mismatch")
        identity_key = _ed25519_from_pem(obj["identity_key_pem"], passphrase)
        store_key = _x25519_from_pem(obj["store_key_pem"], passphrase)
        return cls(identity_key=identity_key, store_key=store_key)

    @classmethod
    def _import_backup_recipient_key(cls, obj: dict, recipient_privkey) -> "AgentState":
        """R10: Decrypt a recipient_key-mode backup."""
        import base64 as _b64
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PublicKey
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.hashes import SHA256
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

        try:
            ephemeral_pub_bytes = bytes.fromhex(obj["ephemeral_pub_hex"])
            nonce = bytes.fromhex(obj["nonce_hex"])
            ciphertext = bytes.fromhex(obj["ciphertext_hex"])
        except (KeyError, ValueError) as exc:
            raise PersistError(f"malformed recipient_key backup: {exc}") from exc

        # R12: verify manifest content_sha256 (hashes ciphertext)
        if "manifest" in obj:
            import hashlib as _hl_rki
            _expected_sha256 = obj["manifest"].get("content_sha256", "")
            if _expected_sha256:
                _actual_sha256 = _hl_rki.sha256(ciphertext).hexdigest()
                if _actual_sha256 != _expected_sha256:
                    raise PersistError("backup_integrity_mismatch")

        try:
            ephemeral_pub = X25519PublicKey.from_public_bytes(ephemeral_pub_bytes)
            shared_secret = recipient_privkey.exchange(ephemeral_pub)
            hkdf = HKDF(algorithm=SHA256(), length=32, salt=None, info=b"ProxionBackupR10")
            aes_key = hkdf.derive(shared_secret)
            aesgcm = AESGCM(aes_key)
            plaintext = aesgcm.decrypt(nonce, ciphertext, None)
        except Exception as exc:
            raise PersistError(f"recipient_key backup decryption failed: {exc}") from exc

        try:
            inner = json.loads(plaintext.decode("utf-8"))
            identity_der = _b64.b64decode(inner["identity_key_der"])
            store_der = _b64.b64decode(inner["store_key_der"])
        except Exception as exc:
            raise PersistError(f"recipient_key backup inner parse failed: {exc}") from exc

        from cryptography.hazmat.primitives.serialization import load_der_private_key
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey as _Ed
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey as _X
        identity_key = load_der_private_key(identity_der, password=None)
        if not isinstance(identity_key, _Ed):
            raise PersistError("identity key DER is not Ed25519")
        store_key = load_der_private_key(store_der, password=None)
        if not isinstance(store_key, _X):
            raise PersistError("store key DER is not X25519")
        return cls(identity_key=identity_key, store_key=store_key)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(
        self,
        path: Union[str, Path],
        passphrase: Optional[bytes] = None,
        *,
        unlock_mode: str = "passphrase",
        identity_id: Optional[str] = None,
    ) -> None:
        """Serialise and write agent state to *path*.

        The file is written atomically: a ``.tmp`` sibling is written first,
        then renamed over *path*.  Existing data at *path* is not truncated
        until the new write succeeds.

        Parameters
        ----------
        path:
            Destination file path.  Parent directory must exist.
        passphrase:
            Byte string used to encrypt both private keys.  Required when
            ``unlock_mode='passphrase'`` (the default).
        unlock_mode:
            ``'passphrase'`` (default) or ``'keychain'``.  Keychain mode
            generates a random wrap key and stores it in the OS credential
            store; no passphrase is needed on subsequent loads.
        identity_id:
            Unique identifier for the keychain entry (keychain mode only).
            Defaults to the path stem when not provided.

        Raises
        ------
        PersistError
            If serialisation or file I/O fails.
        """
        path = Path(path)
        tmp = path.with_suffix(path.suffix + ".tmp")
        bak = path.with_suffix(path.suffix + ".bak")

        from .key_envelope import encrypt_key_bundle

        if unlock_mode == "keychain":
            from .keychain_store import generate_and_store_wrap_key, is_keychain_available
            if identity_id is None:
                identity_id = path.stem
            if not is_keychain_available():
                raise PersistError("keychain is not available on this system")
            wrap_key = generate_and_store_wrap_key(identity_id)
            state_kdf_base: dict = {
                "scheme": "keychain-aes256gcm-v1",
                "unlock_mode": "keychain",
                "identity_id": identity_id,
            }
        elif unlock_mode == "passphrase":
            if passphrase is None:
                raise PersistError("passphrase is required when unlock_mode='passphrase'")
            import os as _os_pp
            if len(passphrase) < 12 and _os_pp.environ.get("PROXION_ALLOW_WEAK_PASSPHRASE") != "1":
                raise PersistError("passphrase too weak: must be at least 12 characters")
            from .key_envelope import derive_wrap_key_scrypt
            salt = os.urandom(16)
            wrap_key = derive_wrap_key_scrypt(passphrase, salt)
            state_kdf_base = {
                "scheme": "scrypt-aes256gcm-v1",
                "salt_b64": base64.b64encode(salt).decode("ascii"),
                "n": 32768, "r": 8, "p": 1, "dklen": 32,
            }
        else:
            raise PersistError(f"unknown unlock_mode: {unlock_mode!r}")

        # Serialize private keys to PKCS8 DER (unencrypted)
        identity_der = self.identity_key.private_bytes(Encoding.DER, PrivateFormat.PKCS8, NoEncryption())
        store_der = self.store_key.private_bytes(Encoding.DER, PrivateFormat.PKCS8, NoEncryption())
        key_bundle = {
            "identity_key_der": base64.b64encode(identity_der).decode("ascii"),
            "store_key_der": base64.b64encode(store_der).decode("ascii"),
        }

        envelope = encrypt_key_bundle(key_bundle, wrap_key)
        state_kdf = {
            **state_kdf_base,
            "nonce_b64": envelope["nonce_b64"],
            "ciphertext_b64": envelope["ciphertext_b64"],
        }

        data = {
            "@type": "ProxionAgentState",
            "version": 1,
            "state_kdf": state_kdf,
            "revocation_entries": _rl_to_dict(self.revocation_list),
            "certificates": [c.to_dict() for c in self.certificates],
            "pending_invites": [p.to_dict() for p in self.pending_invites],
            "css_pod_url": self.css_pod_url,
            "css_webid": self.css_webid,
            "identity_key_version": self.identity_key_version,
            "store_key_version": self.store_key_version,
            "identity_key_rotated_at": self.identity_key_rotated_at,
            "store_key_rotated_at": self.store_key_rotated_at,
            "identity_key_created_at": self.identity_key_created_at,
            "store_key_created_at": self.store_key_created_at,
        }

        try:
            # 1. Write to temporary file
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            
            # 2. Before replacing, create a backup of current if it exists
            if path.exists():
                import shutil
                try:
                    shutil.copy2(path, bak)
                except Exception:
                    pass # Best effort backup

            # 3. Atomic rename
            tmp.replace(path)
        except OSError as exc:
            raise PersistError(f"failed to write agent state to {path}: {exc}") from exc

    @classmethod
    def load(cls, path: Union[str, Path], passphrase: Optional[bytes] = None) -> "AgentState":
        """Load and decrypt agent state from *path*.

        Parameters
        ----------
        path:
            Path to a state file previously written by :meth:`save`.
        passphrase:
            The passphrase used when the file was saved.

        Returns
        -------
        AgentState
            The restored agent state.

        Raises
        ------
        PersistError
            If the file is missing, malformed, or the passphrase is wrong.
        """
        path = Path(path)
        bak = path.with_suffix(path.suffix + ".bak")
        
        last_exc = None
        files_tried = 0
        for p in [path, bak]:
            if not p.exists():
                continue
            files_tried += 1
            try:
                try:
                    raw = p.read_text(encoding="utf-8")
                except OSError as exc:
                    raise PersistError(f"cannot read agent state from {p}: {exc}") from exc

                try:
                    data = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise PersistError(f"agent state file is not valid JSON: {exc}") from exc
                
                if data.get("@type") != "ProxionAgentState":
                    raise PersistError("file does not appear to be a ProxionAgentState")
                if data.get("version", 0) != 1:
                    raise PersistError(f"unsupported state version: {data.get('version')}")

                # New format: scrypt-aes256gcm-v1 or keychain-aes256gcm-v1 key envelope
                if "state_kdf" in data:
                    kdf = data["state_kdf"]
                    from .key_envelope import decrypt_key_bundle
                    if kdf.get("unlock_mode") == "keychain":
                        from .keychain_store import load_wrap_key
                        _kid = kdf.get("identity_id", Path(p).stem)
                        wrap_key = load_wrap_key(_kid)
                        if wrap_key is None:
                            raise PersistError(f"keychain wrap key not found for identity '{_kid}'")
                    else:
                        if passphrase is None:
                            raise PersistError("passphrase required to load this state file")
                        from .key_envelope import derive_wrap_key_scrypt
                        salt = base64.b64decode(kdf["salt_b64"])
                        wrap_key = derive_wrap_key_scrypt(passphrase, salt)
                    bundle = decrypt_key_bundle(kdf, wrap_key)
                    from cryptography.hazmat.primitives.serialization import load_der_private_key
                    try:
                        identity_key = load_der_private_key(
                            base64.b64decode(bundle["identity_key_der"]), password=None
                        )
                        if not isinstance(identity_key, Ed25519PrivateKey):
                            raise PersistError("identity key is not Ed25519")
                        store_key = load_der_private_key(
                            base64.b64decode(bundle["store_key_der"]), password=None
                        )
                        if not isinstance(store_key, X25519PrivateKey):
                            raise PersistError("store key is not X25519")
                    except PersistError:
                        raise
                    except Exception as exc:
                        raise PersistError("invalid passphrase or corrupted state") from exc
                else:
                    # Legacy PEM format — load as before; next save will upgrade
                    if passphrase is None:
                        raise PersistError("passphrase required to load legacy PEM state file")
                    identity_key = _ed25519_from_pem(data["identity_key_pem"], passphrase)
                    store_key = _x25519_from_pem(data["store_key_pem"], passphrase)

                rl = _rl_from_dict(data.get("revocation_entries", {}))
                certs = [
                    RelationshipCertificate.from_dict(c)
                    for c in data.get("certificates", [])
                ]
                pending = [
                    PendingInvite.from_dict(p)
                    for p in data.get("pending_invites", [])
                ]

                state = cls(
                    identity_key=identity_key,
                    store_key=store_key,
                    revocation_list=rl,
                    certificates=certs,
                    pending_invites=pending,
                    css_pod_url=data.get("css_pod_url"),
                    css_webid=data.get("css_webid"),
                    identity_key_version=data.get("identity_key_version", 1),
                    store_key_version=data.get("store_key_version", 1),
                    identity_key_rotated_at=data.get("identity_key_rotated_at"),
                    store_key_rotated_at=data.get("store_key_rotated_at"),
                    identity_key_created_at=data.get("identity_key_created_at"),
                    store_key_created_at=data.get("store_key_created_at"),
                )
                # R16: backfill key lifecycle timestamps for legacy state files
                if state.identity_key_created_at is None:
                    state.identity_key_created_at = time.time()
                if state.store_key_created_at is None:
                    state.store_key_created_at = time.time()
                return state
            except Exception as exc:
                last_exc = exc
                continue
        
        if last_exc:
            raise last_exc
        if files_tried == 0:
            raise PersistError(f"cannot read agent state from {path}: file not found")
        raise PersistError(f"failed to load agent state from {path} or its backup")
