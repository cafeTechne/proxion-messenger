"""File sharing over federated Solid Pods."""

from __future__ import annotations

import os
import mimetypes
import json
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .federation import RelationshipCertificate
    from .solid_client import SolidClient
    from .persist import AgentState


@dataclass
class FileAttachment:
    """A shared file attachment.
    
    Parameters
    ----------
    filename : str
        Name of the file.
    mime_type : str
        MIME type (e.g., "image/png", "application/pdf").
    size : int
        File size in bytes.
    stash_uri : str
        Where the raw file lives (stash:// URI).
    message_stash_uri : str
        stash:// URI of the metadata message.
    """
    filename: str
    mime_type: str
    size: int
    stash_uri: str
    message_stash_uri: str


def _guess_mime_type(filename: str) -> str:
    """Guess MIME type from filename extension.
    
    Falls back to application/octet-stream if unknown.
    """
    mime_type, _ = mimetypes.guess_type(filename)
    return mime_type or "application/octet-stream"


def send_file(
    cert: RelationshipCertificate,
    pod_client: SolidClient,
    local_path: str,
    mime_type: Optional[str] = None,
) -> FileAttachment:
    """Send a file to a peer via federated messaging.
    
    1. Reads raw file bytes
    2. PUTs to stash://files/{cert_id}/{filename}
    3. Composes a Message with file metadata
    4. Sends metadata message via messaging.send()
    
    Parameters
    ----------
    cert : RelationshipCertificate
        The relationship certificate for the peer.
    pod_client : SolidClient
        Authenticated client for the sender's Pod.
    local_path : str
        Path to the file to send.
    mime_type : str, optional
        MIME type override. If not provided, guessed from extension.
    
    Returns
    -------
    FileAttachment
        Metadata about the sent file.
    
    Raises
    ------
    FileNotFoundError
        If local_path does not exist.
    """
    from .messaging import compose, send
    
    filename = os.path.basename(local_path)
    if not mime_type:
        mime_type = _guess_mime_type(filename)
        
    if not (mime_type.startswith(("image/", "video/", "audio/")) or mime_type == "application/pdf"):
        raise ValueError(f"Unsupported MIME type: {mime_type}")

    file_size = os.path.getsize(local_path)
    if file_size > 10 * 1024 * 1024:
        raise ValueError("File size exceeds 10MB limit")
    
    # Read file
    with open(local_path, "rb") as f:
        raw_bytes = f.read()
    
    size = len(raw_bytes)
    
    # Upload to pod
    stash_uri = f"stash://files/{cert.certificate_id}/{filename}"
    pod_client.put(stash_uri, raw_bytes)
    
    # Compose metadata message
    payload = {
        "type": "file",
        "filename": filename,
        "mime_type": mime_type,
        "size": size,
        "stash_uri": stash_uri,
    }
    
    msg = compose(
        identity_key=pod_client._identity_key if hasattr(pod_client, "_identity_key") else None,
        cert=cert,
        content=json.dumps(payload),
    )
    send(msg, pod_client)
    
    attachment = FileAttachment(
        filename=filename,
        mime_type=mime_type,
        size=size,
        stash_uri=stash_uri,
        message_stash_uri=msg.message_id if hasattr(msg, "message_id") else "",
    )
    
    return attachment


def receive_files(
    cert: RelationshipCertificate,
    pod_client: SolidClient,
    holder_state: AgentState,
    signing_key: bytes,
) -> list[FileAttachment]:
    """Receive file attachments from a peer.
    
    Calls messaging.receive() and filters for messages with type="file".
    
    Parameters
    ----------
    cert : RelationshipCertificate
        The relationship certificate for the peer.
    pod_client : SolidClient
        Authenticated client for reading messages.
    holder_state : AgentState
        The receiver's agent state (for cert validation).
    signing_key : bytes
        The sender's HMAC signing key (for message validation).
    
    Returns
    -------
    list[FileAttachment]
        List of received file attachments (does NOT download raw bytes).
    """
    from .messaging import receive
    
    messages = receive(cert, pod_client, holder_state, signing_key)
    
    attachments = []
    for msg in messages:
        try:
            payload = json.loads(msg.content)
            if payload.get("type") == "file":
                size = payload.get("size", 0)
                mime = payload.get("mime_type", "")
                
                if size > 10 * 1024 * 1024:
                    continue
                if not (mime.startswith(("image/", "video/", "audio/")) or mime == "application/pdf"):
                    continue
                    
                attachment = FileAttachment(
                    filename=payload["filename"],
                    mime_type=mime,
                    size=size,
                    stash_uri=payload["stash_uri"],
                    message_stash_uri=msg.message_id if hasattr(msg, "message_id") else "",
                )
                attachments.append(attachment)
        except (json.JSONDecodeError, KeyError, TypeError):
            # Skip malformed messages
            pass
    
    return attachments


def download_file(
    attachment: FileAttachment,
    pod_client: SolidClient,
    dest_path: str,
) -> None:
    """Download a file attachment to a local path.
    
    GETs the raw file from attachment.stash_uri and writes to dest_path.
    
    Parameters
    ----------
    attachment : FileAttachment
        The attachment to download.
    pod_client : SolidClient
        Authenticated client for reading from the pod.
    dest_path : str
        Local path where the file will be saved.
    """
    raw_bytes = pod_client.get(attachment.stash_uri)
    
    with open(dest_path, "wb") as f:
        f.write(raw_bytes)
