"""User identity card for Proxion profiles."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .solid_client import SolidClient


IDENTITY_PATH = "stash://profile/identity.json"


@dataclass
class IdentityCard:
    """A user's identity card published on their Solid Pod.
    
    Parameters
    ----------
    display_name : str
        Human-readable display name for the user.
    avatar_url : str, optional
        URL to an image on the Pod, or None.
    bio : str, optional
        Short bio or status text.
    proxion_version : str
        Version of Proxion software this card was created with.
    did : str, optional
        W3C DID key for this agent (did:key:z6Mk...), auto-populated if available.
    """
    display_name: str
    avatar_url: Optional[str] = None
    bio: Optional[str] = None
    proxion_version: str = "0.1.0"
    did: Optional[str] = None


def upload_avatar(pod_client: SolidClient, image_bytes: bytes, mime_type: str) -> str:
    """Upload an avatar image to the Pod and return its stash:// URI.
    
    Extension is derived from mime_type (e.g. image/png -> .png).
    """
    ext = "png"
    if "jpeg" in mime_type: ext = "jpg"
    elif "gif" in mime_type: ext = "gif"
    elif "webp" in mime_type: ext = "webp"
    
    path = f"stash://profile/avatar.{ext}"
    pod_client.put(path, image_bytes, content_type=mime_type)
    return path


def get_avatar(pod_client: SolidClient, stash_uri: str) -> bytes:
    """Download avatar image bytes from the Pod."""
    return pod_client.get(stash_uri)


def publish_identity(pod_client: SolidClient, card: IdentityCard) -> None:
    # Auto-populate did if not already set and if we have pub_key_bytes
    if card.did is None and hasattr(card, 'pub_key_bytes') and card.pub_key_bytes:
        from .didkey import pub_key_to_did
        card.did = pub_key_to_did(card.pub_key_bytes)
    
    data = {
        "display_name": card.display_name,
        "avatar_url": card.avatar_url,
        "bio": card.bio,
        "proxion_version": card.proxion_version,
        "did": card.did,
    }
    pod_client.put(IDENTITY_PATH, json.dumps(data).encode("utf-8"))


def fetch_identity(
    pod_client: SolidClient,
    stash_uri: str = IDENTITY_PATH,
) -> IdentityCard:
    # ... existing implementation ...
    from .solid_client import SolidError
    
    try:
        raw = pod_client.get(stash_uri)
        data = json.loads(raw.decode("utf-8"))
        
        return IdentityCard(
            display_name=data.get("display_name", "Unknown"),
            avatar_url=data.get("avatar_url"),
            bio=data.get("bio"),
            proxion_version=data.get("proxion_version", "0.1.0"),
            did=data.get("did"),
        )
    except (SolidError, json.JSONDecodeError, KeyError, UnicodeDecodeError):
        # Return graceful default on error
        return IdentityCard(
            display_name="Unknown",
            avatar_url=None,
            bio=None,
            proxion_version="0.1.0",
            did=None,
        )

