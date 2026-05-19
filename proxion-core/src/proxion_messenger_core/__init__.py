"""proxion-messenger-core — Python library and gateway server for Proxion Messenger."""

__version__ = "0.1.0"

from .attenuation import derive_token
from .context import Caveat, RequestContext
from .errors import AttenuationError, CipherError, ProxionError, TokenError, ValidationError, CssAccountExistsError
from .crypto import Cipher
from .pop import PopProof, fingerprint, fingerprint_from_key, make_challenge, sign_challenge, verify_pop
from .sealed import (
    SealedEnvelope, mailbox_id_for, seal, seal_json, open_sealed, open_sealed_json,
)
from .store import MemoryStore, StoreConfig, StoredMessage, StoreError, QuotaExceededError
from .solid_store import SolidStore
from .store_client import RemoteStore
from .store_sqlite import SqliteStore
from .persist import AgentState, PendingInvite, PersistError
from .readstate import ReadState
from .room_store import RoomStore
from .handshake import (
    HandshakeError,
    create_invite, send_invite,
    receive_invites, accept_invite,
    receive_acceptances, finalize_handshake, send_certificate, receive_certificates,
    run_local_handshake, run_bidirectional_handshake,
)
from .tokens import Token, issue_token, token_canonical_bytes, verify_integrity
from .revocation import (
    RevocationList,
    token_revocation_id,
    certificate_revocation_id,
)
from .certtoken import (
    CertTokenError,
    issue_from_certificate,
    check_token_within_cert,
    revoke_tokens_for_certificate,
    revoke_tokens_via_ledger,
    delegate_cert,
    renew_cert,
    revoke_cert_and_tokens,
)
from .revoke import (
    RevocationNotice,
    RevocationError,
    create_token_revocation,
    create_certificate_revocation,
    broadcast_revocation,
    receive_revocations,
    revoke_and_broadcast,
)
from .solid import SolidResolver, SolidResolverError, permission_to_solid_url
from .solid_client import SolidClient, SolidError
from .solid_auth import AuthenticatedSolidClient
from .validator import ALLOW, Decision, validate_request
from .messaging import (
    Message,
    compose,
    send,
    receive,
    thread_path,
    message_path,
    narrow_to_thread,
    make_pod_receipt_writer,
    renew_thread_token,
    delete_message,
    thread_info,
    compose_and_send,
    edit_message,
    apply_edits,
)
from .msgcrypto import derive_message_key, encrypt_message, decrypt_message, is_encrypted
from .identity import IdentityCard, publish_identity, fetch_identity, upload_avatar, get_avatar
from .solid_auth import AuthenticatedSolidClient, set_thread_read_acl
from .dpop import make_dpop_proof
from .css_auth import CssClientCredentials, DpopSolidClient
from .css_setup import CssAccountManager, build_dpop_client
from .room import RoomConfig, RoomMembership, create_room, invite_to_room, join_room, send_to_room, read_room, set_room_acl, delete_room_message, remove_room_member, get_room_members, update_room_metadata
from .files import FileAttachment, send_file, receive_files, download_file
from .presence import PresenceDoc, set_presence, get_presence
from .inbox import InboxEntry, poll_inbox, watch_inbox
from .voice import VoiceInvite, signal_voice_invite, receive_voice_invites, VoiceChannelState, join_voice_channel, leave_voice_channel, get_voice_channel_state, VoiceAnswer, IceCandidate, signal_voice_answer, signal_ice_candidate
from .search import SearchResult, search_messages
from .device import DeviceLink, create_device_link, accept_device_link
from .gateway import GatewayConfig, run_gateway
from .notifications import subscribe_to_resource, watch_stash_uri
from .discovery import fetch_peer_discovery
from .css_setup import CssAccountManager, build_dpop_client, publish_proxion_discovery
from .reactions import Reaction, add_reaction, remove_reaction, get_reactions
from .replies import get_replies, build_thread_view, get_thread, flatten_thread
from .outbox import Outbox, OutboxItem, OutboxRecord, enqueue, list_due, mark_success, mark_failed
from .mirror import mirror_room_to_pod, get_mirror_messages

from .linkpreview import fetch_link_preview, extract_urls as lp_extract_urls
from .pins import PinnedMessage, pin_message, unpin_message, get_pinned_messages
from .export import export_thread_to_json, export_thread_to_markdown, export_room_to_json, export_room_to_markdown
from .acp import detect_acl_mode, set_acl_auto, set_acp_policy
from .didkey import pub_key_to_did, did_to_pub_key, agent_did
from .room import list_public_rooms, search_rooms
from .receipts import ReadReceipt, mark_message_read, get_read_receipts, has_been_read
from .oidc import OidcConfig, fetch_oidc_config, webid_to_issuer, dynamic_register
from .peerdb import PeerRecord, register_peer, get_peer, list_peers, remove_peer, touch_peer
from .profile import WebIdProfile, get_profile, update_profile
from .invites import InviteRecord, create_invite, get_invite, use_invite, revoke_invite, list_invites
from .search import SearchResult, search_messages, search_thread, search_all_threads
from .notifications import NotificationRecord, notify, get_notifications, mark_notification_read, clear_notifications

__all__ = [
    "ALLOW",
    "AttenuationError",
    "Caveat",
    "Cipher",
    "CipherError",
    "PopProof",
    "fingerprint",
    "fingerprint_from_key",
    "make_challenge",
    "sign_challenge",
    "verify_pop",
    # sealed envelope
    "SealedEnvelope",
    "mailbox_id_for",
    "seal",
    "seal_json",
    "open_sealed",
    "open_sealed_json",
    # agent persistence
    "AgentState",
    "PendingInvite",
    "PersistError",
    # coordination store
    "MemoryStore",
    "SolidStore",
    "RemoteStore",
    "SqliteStore",
    "StoreConfig",
    "StoredMessage",
    "StoreError",
    "QuotaExceededError",
    # federation handshake
    "HandshakeError",
    "create_invite",
    "send_invite",
    "receive_invites",
    "accept_invite",
    "receive_acceptances",
    "finalize_handshake",
    "send_certificate",
    "receive_certificates",
    "run_local_handshake",
    "run_bidirectional_handshake",
    "Decision",
    "ProxionError",
    "RequestContext",
    "Token",
    "TokenError",
    # certificate-bounded token issuance
    "CertTokenError",
    "issue_from_certificate",
    "check_token_within_cert",
    "revoke_tokens_for_certificate",
    "revoke_tokens_via_ledger",
    "delegate_cert",
    "renew_cert",
    "revoke_cert_and_tokens",
    "RevocationList",
    "token_revocation_id",
    "certificate_revocation_id",
    # revocation propagation
    "RevocationNotice",
    "RevocationError",
    "create_token_revocation",
    "create_certificate_revocation",
    "broadcast_revocation",
    "receive_revocations",
    "revoke_and_broadcast",
    # Solid Protocol adapter
    "SolidResolver",
    "SolidResolverError",
    "permission_to_solid_url",
    "SolidClient",
    "SolidError",
    "AuthenticatedSolidClient",
    # messaging
    "Message",
    "compose",
    "send",
    "receive",
    "thread_path",
    "message_path",
    "narrow_to_thread",
    "make_pod_receipt_writer",
    "renew_thread_token",
    "delete_message",
    "thread_info",
    "compose_and_send",
    "set_thread_read_acl",
    # CSS / DPoP integration
    "make_dpop_proof",
    "CssClientCredentials",
    "DpopSolidClient",
    "CssAccountManager",
    "build_dpop_client",
    "CssAccountExistsError",
    # Chat rooms
    "RoomConfig",
    "RoomMembership",
    "create_room",
    "invite_to_room",
    "join_room",
    "send_to_room",
    "read_room",
    "set_room_acl",
    # File sharing
    "FileAttachment",
    "send_file",
    "receive_files",
    "download_file",
    # Presence
    "PresenceDoc",
    "set_presence",
    "get_presence",
    # Unified inbox
    "InboxEntry",
    "poll_inbox",
    "watch_inbox",
    # Voice signaling
    "VoiceInvite",
    "signal_voice_invite",
    "receive_voice_invites",
    # Validators and other
    "ValidationError",
    "derive_token",
    "issue_token",
    "token_canonical_bytes",
    "validate_request",
    "verify_integrity",
    # Round 18 — encryption, persistence, identity
    "RoomStore",
    "ReadState",
    "derive_message_key",
    "encrypt_message",
    "decrypt_message",
    "is_encrypted",
    "IdentityCard",
    "publish_identity",
    "fetch_identity",
    "CssAccountExistsError",
    # Round 19 — Gateway, Notifications, Voice Presence, Avatars
    "GatewayConfig",
    "run_gateway",
    "VoiceChannelState",
    "join_voice_channel",
    "leave_voice_channel",
    "get_voice_channel_state",
    "upload_avatar",
    "get_avatar",
    "subscribe_to_resource",
    "watch_stash_uri",
    # Round 20 — Device linking, Search, WebRTC Signaling
    "DeviceLink",
    "create_device_link",
    "accept_device_link",
    "export_device_invite",
    "import_device_invite",
    "save_device_links",
    "load_device_links",
    "search_messages",
    "SearchResult",
    "VoiceAnswer",
    "IceCandidate",
    "signal_voice_answer",
    "signal_ice_candidate",
    # Round 21 — Room Management, Discovery
    "delete_room_message",
    "remove_room_member",
    "get_room_members",
    "fetch_peer_discovery",
    "publish_proxion_discovery",
    # Round 22 — Reactions, Threading, Outbox, Link Preview
    "Reaction",
    "add_reaction",
    "remove_reaction",
    "get_reactions",
    "get_replies",
    "build_thread_view",
    "Outbox",
    "OutboxItem",
    "fetch_link_preview",
    # Round 24 — Editing, Pinning, Export, ACP, Presence/Room extensions
    "edit_message",
    "apply_edits",
    "PinnedMessage",
    "pin_message",
    "unpin_message",
    "get_pinned_messages",
    "export_thread_to_json",
    "export_thread_to_markdown",
    "export_room_to_json",
    "export_room_to_markdown",
    "detect_acl_mode",
    "set_acl_auto",
    "set_acp_policy",
    "update_room_metadata",
    # Round 25 — DID identity, Room discovery, Read receipts
    "pub_key_to_did",
    "did_to_pub_key",
    "agent_did",
    "list_public_rooms",
    "search_rooms",
    "ReadReceipt",
    "mark_message_read",
    "get_read_receipts",
    "has_been_read",
    # Round 26 — OIDC, Outbox Retry, Threading, Peers
    "OidcConfig",
    "fetch_oidc_config",
    "webid_to_issuer",
    "dynamic_register",
    "OutboxRecord",
    "enqueue",
    "list_due",
    "mark_success",
    "mark_failed",
    "get_thread",
    "flatten_thread",
    "PeerRecord",
    "register_peer",
    "get_peer",
    "list_peers",
    "remove_peer",
    "touch_peer",
    # Round 27 — WebID Profiles, Room Invites, Search, Notifications
    "WebIdProfile",
    "get_profile",
    "update_profile",
    "InviteRecord",
    "create_invite",
    "get_invite",
    "use_invite",
    "revoke_invite",
    "list_invites",
    "search_thread",
    "search_all_threads",
    "NotificationRecord",
    "notify",
    "get_notifications",
    "mark_notification_read",
    "clear_notifications",
]
