"""
File transfer design for Proxion Messenger.

Two tiers, chosen automatically by file size:

TIER 1 — Pod upload (files up to ~50 MB)
-----------------------------------------
1. Sender uploads the encrypted file as an LDP resource to their Solid pod.
   URL: {pod_base}/files/{file_id}
   The file is encrypted with a random 256-bit AES-GCM key before upload,
   so the pod server cannot read the content.

2. Sender writes a regular DM message containing a JSON payload:
   {
     "type": "file",
     "file_id": "<uuid>",
     "filename": "photo.jpg",
     "mime_type": "image/jpeg",
     "size_bytes": 1234567,
     "pod_url": "https://alice.pod.example/files/<file_id>",
     "key_b64": "<base64-encoded AES key>",    # E2E: only recipient can decrypt
     "iv_b64":  "<base64-encoded IV>"
   }
   This message is itself encrypted by the existing E2E messaging layer,
   so the pod_url and key are never visible to the pod server or any relay.

3. Recipient's gateway delivers the message; browser fetches and decrypts
   the file directly from the sender's pod.

4. ACL: The sender grants the recipient read access on the file resource
   (via ACP/WAC using the recipient's WebID from the RelationshipCertificate).
   After the recipient acknowledges receipt, the sender can optionally revoke
   access or leave it for history.

TIER 2 — WebRTC data channel (files above ~50 MB or when no pod is available)
------------------------------------------------------------------------------
1. Sender's browser opens a WebRTC data channel on the existing call connection
   (or establishes a new one using the same STUN/TURN ICE infrastructure).

2. File is chunked into 64 KB blocks, each encrypted with AES-GCM.
   The encryption key is exchanged in a DM message (same as Tier 1 step 2)
   before the data channel opens.

3. Transfer uses a simple stop-and-wait protocol per chunk with sequence numbers:
   - Sender:    CHUNK <seq> <encrypted_bytes>
   - Receiver:  ACK <seq>
   - On timeout after 5s: retransmit (max 3 attempts, then ABORT)

4. Progress events are emitted to the UI via the gateway WebSocket:
   { "type": "file_progress", "file_id": "...", "bytes_sent": N, "total": N }

5. When the data channel is already open (active call), the file transfer
   can run concurrently with voice/video. When opened solely for file transfer,
   it is closed after completion.

NAT traversal: identical to voice — STUN hole punching first, TURN relay fallback.
No gateway involvement in the data path once the data channel is open.

Signaling messages (WebSocket gateway → pod inbox for cross-gateway):
  { "type": "file_offer",  "file_id": "...", "filename": "...", "size_bytes": N,
    "key_b64": "...", "iv_b64": "...", "session_id": "..." }
  { "type": "file_accept", "file_id": "...", "session_id": "..." }
  { "type": "file_reject", "file_id": "...", "reason": "..." }

Privacy properties
------------------
- Pod server sees: encrypted ciphertext only (Tier 1) or nothing (Tier 2 P2P).
- Gateway sees: signaling envelope (file metadata in encrypted DM payload).
- TURN server sees: encrypted ciphertext only (relay mode).
- End-to-end encryption key never leaves the E2E message channel.
"""

from __future__ import annotations

import base64
import os
import uuid
from dataclasses import dataclass, field
from typing import Optional


TIER1_MAX_BYTES = 50 * 1024 * 1024   # 50 MB
CHUNK_SIZE = 64 * 1024               # 64 KB chunks for WebRTC data channel


@dataclass
class FileOffer:
    """Metadata exchanged in the DM channel before a file transfer begins."""
    file_id: str
    filename: str
    mime_type: str
    size_bytes: int
    # Tier 1 fields (pod upload)
    pod_url: Optional[str] = None
    # Encryption key material (always present — used by both tiers)
    key_b64: str = field(default_factory=lambda: base64.b64encode(os.urandom(32)).decode())
    iv_b64: str = field(default_factory=lambda: base64.b64encode(os.urandom(12)).decode())
    # Tier 2 fields (WebRTC data channel)
    session_id: Optional[str] = None

    @classmethod
    def new(cls, filename: str, mime_type: str, size_bytes: int) -> "FileOffer":
        return cls(
            file_id=uuid.uuid4().hex,
            filename=filename,
            mime_type=mime_type,
            size_bytes=size_bytes,
        )

    def tier(self) -> int:
        return 1 if self.size_bytes <= TIER1_MAX_BYTES else 2

    def to_message_payload(self) -> dict:
        payload: dict = {
            "type": "file_offer" if self.tier() == 2 else "file",
            "file_id": self.file_id,
            "filename": self.filename,
            "mime_type": self.mime_type,
            "size_bytes": self.size_bytes,
            "key_b64": self.key_b64,
            "iv_b64": self.iv_b64,
        }
        if self.pod_url:
            payload["pod_url"] = self.pod_url
        if self.session_id:
            payload["session_id"] = self.session_id
        return payload

    @classmethod
    def from_message_payload(cls, payload: dict) -> "FileOffer":
        return cls(
            file_id=payload["file_id"],
            filename=payload["filename"],
            mime_type=payload.get("mime_type", "application/octet-stream"),
            size_bytes=payload["size_bytes"],
            pod_url=payload.get("pod_url"),
            key_b64=payload["key_b64"],
            iv_b64=payload["iv_b64"],
            session_id=payload.get("session_id"),
        )


def encrypt_file(plaintext: bytes, key_b64: str, iv_b64: str) -> bytes:
    """AES-256-GCM encrypt. Returns ciphertext + 16-byte auth tag."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key = base64.b64decode(key_b64)
    iv = base64.b64decode(iv_b64)
    return AESGCM(key).encrypt(iv, plaintext, None)


def decrypt_file(ciphertext: bytes, key_b64: str, iv_b64: str) -> bytes:
    """AES-256-GCM decrypt. Raises InvalidTag on tampering."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key = base64.b64decode(key_b64)
    iv = base64.b64decode(iv_b64)
    return AESGCM(key).decrypt(iv, ciphertext, None)


def chunk_count(size_bytes: int) -> int:
    """Number of chunks needed for a Tier-2 transfer."""
    return (size_bytes + CHUNK_SIZE - 1) // CHUNK_SIZE
