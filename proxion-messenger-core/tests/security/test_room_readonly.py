"""Tests for read-only room ACL enforcement and invite signature verification."""
import pytest
from unittest.mock import MagicMock, patch

from proxion_messenger_core.room import RoomConfig, set_room_acl
from proxion_messenger_core.solid_client import SolidClient


def _make_room_and_client(read_only: bool = False):
    room = RoomConfig(
        room_id="room-test",
        name="Test Room",
        owner_webid="https://pod.example.com/alice/profile/card#me",
        pod_url="http://localhost:3000/alice/",
        stash_root="stash://rooms/room-test/",
        created_at="2026-01-01T00:00:00Z",
        read_only=read_only,
    )
    session = MagicMock()
    resp = MagicMock()
    resp.status_code = 200
    session.put.return_value = resp
    resolver = MagicMock()
    resolver.resolve.side_effect = lambda uri: f"http://localhost:3000/{uri.replace('stash://', '')}"
    client = SolidClient(resolver=resolver, session=session)
    return room, client


OWNER = "https://pod.example.com/alice/profile/card#me"
BOB = "https://pod.example.com/bob/profile/card#me"
CAROL = "https://pod.example.com/carol/profile/card#me"


# ---------------------------------------------------------------------------
# set_room_acl read-only enforcement (WAC path)
# ---------------------------------------------------------------------------

class TestSetRoomAclReadOnly:
    def test_writable_room_grants_read_write(self):
        room, client = _make_room_and_client(read_only=False)
        with patch("proxion_messenger_core.acp.detect_acl_mode", return_value="wac"):
            set_room_acl(room, client, owner_webid=OWNER, member_webids=[BOB])
        put_call = client._session.put.call_args
        acl_body = put_call[1]["content"].decode() if "content" in put_call[1] else put_call[0][1].decode()
        assert "acl:Write" in acl_body

    def test_read_only_room_does_not_grant_write(self):
        room, client = _make_room_and_client(read_only=True)
        with patch("proxion_messenger_core.acp.detect_acl_mode", return_value="wac"):
            set_room_acl(room, client, owner_webid=OWNER, member_webids=[BOB])
        put_call = client._session.put.call_args
        acl_body = put_call[1]["content"].decode() if "content" in put_call[1] else put_call[0][1].decode()
        # Members stanza must NOT contain Write
        # Owner stanza still has Write/Control — split the body at #members
        members_section = acl_body.split("<#members>")[-1] if "<#members>" in acl_body else acl_body
        assert "acl:Write" not in members_section

    def test_read_only_room_grants_read(self):
        room, client = _make_room_and_client(read_only=True)
        with patch("proxion_messenger_core.acp.detect_acl_mode", return_value="wac"):
            set_room_acl(room, client, owner_webid=OWNER, member_webids=[BOB])
        put_call = client._session.put.call_args
        acl_body = put_call[1]["content"].decode() if "content" in put_call[1] else put_call[0][1].decode()
        assert "acl:Read" in acl_body

    def test_owner_always_gets_write_even_in_read_only(self):
        room, client = _make_room_and_client(read_only=True)
        with patch("proxion_messenger_core.acp.detect_acl_mode", return_value="wac"):
            set_room_acl(room, client, owner_webid=OWNER, member_webids=[BOB])
        put_call = client._session.put.call_args
        acl_body = put_call[1]["content"].decode() if "content" in put_call[1] else put_call[0][1].decode()
        owner_section = acl_body.split("<#owner>")[1].split("<#members>")[0] if "<#owner>" in acl_body else ""
        assert "acl:Write" in owner_section
        assert "acl:Control" in owner_section

    def test_multiple_members_read_only(self):
        room, client = _make_room_and_client(read_only=True)
        with patch("proxion_messenger_core.acp.detect_acl_mode", return_value="wac"):
            set_room_acl(room, client, owner_webid=OWNER, member_webids=[BOB, CAROL])
        put_call = client._session.put.call_args
        acl_body = put_call[1]["content"].decode() if "content" in put_call[1] else put_call[0][1].decode()
        members_section = acl_body.split("<#members>")[-1] if "<#members>" in acl_body else acl_body
        assert BOB in acl_body
        assert CAROL in acl_body
        assert "acl:Write" not in members_section


# ---------------------------------------------------------------------------
# set_room_acl ACP path read-only enforcement
# ---------------------------------------------------------------------------

class TestSetRoomAclReadOnlyACP:
    def test_acp_read_only_passes_read_only_modes(self):
        room, client = _make_room_and_client(read_only=True)
        with patch("proxion_messenger_core.acp.detect_acl_mode", return_value="acp"), \
             patch("proxion_messenger_core.acp.set_acp_policy", return_value="stash://rooms/room-test/.acr") as mock_acp:
            set_room_acl(room, client, owner_webid=OWNER, member_webids=[BOB])
        mock_acp.assert_called_once()
        _, _, _, _, modes = mock_acp.call_args[0]
        assert modes == ["Read"]
        assert "Write" not in modes

    def test_acp_writable_passes_read_write_modes(self):
        room, client = _make_room_and_client(read_only=False)
        with patch("proxion_messenger_core.acp.detect_acl_mode", return_value="acp"), \
             patch("proxion_messenger_core.acp.set_acp_policy", return_value="stash://rooms/room-test/.acr") as mock_acp:
            set_room_acl(room, client, owner_webid=OWNER, member_webids=[BOB])
        mock_acp.assert_called_once()
        _, _, _, _, modes = mock_acp.call_args[0]
        assert "Read" in modes
        assert "Write" in modes


# ---------------------------------------------------------------------------
# join_room invite signature verification
# ---------------------------------------------------------------------------

class TestJoinRoomInviteVerification:
    def _make_signed_invite_json(self):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from proxion_messenger_core.didkey import pub_key_to_did
        from proxion_messenger_core.federation import FederationInvite, Capability
        import json

        priv = Ed25519PrivateKey.generate()
        pub_bytes = priv.public_key().public_bytes_raw()
        did = pub_key_to_did(pub_bytes)

        cap = Capability(can="read", with_="stash://rooms/r1/")
        invite = FederationInvite(
            issuer={"public_key": pub_bytes.hex(), "did": did},
            endpoint_hints=[],
            capabilities=[cap],
        )
        invite.sign(priv)
        return json.dumps(invite.to_dict()), priv

    def test_unsigned_invite_accepted(self):
        from proxion_messenger_core.federation import FederationInvite, Capability
        from proxion_messenger_core.room import join_room
        import json

        cap = Capability(can="read", with_="stash://rooms/r1/")
        invite = FederationInvite(
            issuer={"public_key": "a" * 64},
            endpoint_hints=[],
            capabilities=[cap],
        )
        invite.signature = None  # explicitly unsigned
        invite_json = json.dumps(invite.to_dict())

        mock_agent = MagicMock()
        mock_agent.identity_key = MagicMock()
        mock_agent.store_pub_bytes = b"\x00" * 32
        mock_store = MagicMock()

        with patch("proxion_messenger_core.handshake.accept_invite"), \
             patch("proxion_messenger_core.handshake.receive_certificates") as mock_recv:
            mock_cert = MagicMock()
            mock_cert.certificate_id = "cert-xyz"
            mock_recv.return_value = [(mock_cert, True)]
            # Should not raise
            join_room(invite_json, mock_agent, "https://bob.example.com/profile/card#me", mock_store)

    def test_signed_invite_with_wrong_key_rejected(self):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from proxion_messenger_core.federation import FederationInvite, Capability
        from proxion_messenger_core.room import join_room
        import json

        priv_a = Ed25519PrivateKey.generate()
        priv_b = Ed25519PrivateKey.generate()
        pub_b_hex = priv_b.public_key().public_bytes_raw().hex()

        cap = Capability(can="read", with_="stash://rooms/r1/")
        invite = FederationInvite(
            issuer={"public_key": pub_b_hex},  # claims to be key B
            endpoint_hints=[],
            capabilities=[cap],
        )
        invite.sign(priv_a)  # but signed with key A → mismatch
        invite_json = json.dumps(invite.to_dict())

        mock_agent = MagicMock()
        mock_store = MagicMock()

        with pytest.raises(ValueError, match="signature verification failed"):
            join_room(invite_json, mock_agent, mock_store, "https://bob.example.com/profile/card#me")
