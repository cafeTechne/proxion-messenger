"""Tests for Turtle ACL injection prevention across all ACL-setting surfaces."""
import pytest
from unittest.mock import MagicMock, patch

from proxion_messenger_core.solid_client import SolidClient, _assert_safe_webid


# ---------------------------------------------------------------------------
# _assert_safe_webid unit tests
# ---------------------------------------------------------------------------

SAFE_WEBIDS = [
    "https://pod.example.com/alice/profile/card#me",
    "http://localhost:3000/bob/profile/card#me",
    "did:key:z6MkhaXgBZDvotDkL5257faiztiGiC2QtKLGpbnnEGta2doK",
    "https://solidcommunity.net/users/carol#me",
]

MALICIOUS_WEBIDS = [
    # Close angle bracket — breaks Turtle <URI> literal
    'https://evil.example.com/> acl:agent <https://attacker.example.com/card#me',
    # Double quote — Turtle string injection
    'https://evil.example.com/" acl:mode acl:Write',
    # Backslash — Turtle escape injection
    'https://evil.example.com/\\ acl:agent <attacker>',
    # Newline — Turtle statement termination + injection
    "https://evil.example.com/\nacl:agent <https://attacker.example.com/card#me>",
    # Carriage return — same as newline in Turtle
    "https://evil.example.com/\r some:injection <here>",
]


@pytest.mark.parametrize("webid", SAFE_WEBIDS)
def test_assert_safe_webid_accepts_safe(webid):
    _assert_safe_webid(webid)  # Must not raise


@pytest.mark.parametrize("webid", MALICIOUS_WEBIDS)
def test_assert_safe_webid_rejects_malicious(webid):
    with pytest.raises(ValueError, match="unsafe"):
        _assert_safe_webid(webid)


# ---------------------------------------------------------------------------
# SolidClient.set_acl injection tests
# ---------------------------------------------------------------------------

def _mock_client(status=200):
    session = MagicMock()
    resp = MagicMock()
    resp.status_code = status
    session.put.return_value = resp
    resolver = MagicMock()
    resolver.resolve.return_value = "http://localhost:3000/alice/rooms/r1/"
    return SolidClient(resolver=resolver, session=session)


class TestSetAclInjection:
    def test_set_acl_safe_webids_succeed(self):
        client = _mock_client()
        client.set_acl(
            "stash://rooms/r1/",
            "https://pod.example.com/alice/profile/card#me",
            "https://pod.example.com/bob/profile/card#me",
        )

    @pytest.mark.parametrize("bad_webid", MALICIOUS_WEBIDS)
    def test_set_acl_owner_injection_rejected(self, bad_webid):
        client = _mock_client()
        with pytest.raises(ValueError):
            client.set_acl(
                "stash://rooms/r1/",
                bad_webid,
                "https://pod.example.com/bob/profile/card#me",
            )

    @pytest.mark.parametrize("bad_webid", MALICIOUS_WEBIDS)
    def test_set_acl_subject_injection_rejected(self, bad_webid):
        client = _mock_client()
        with pytest.raises(ValueError):
            client.set_acl(
                "stash://rooms/r1/",
                "https://pod.example.com/alice/profile/card#me",
                bad_webid,
            )

    def test_set_acl_no_http_call_on_injection(self):
        client = _mock_client()
        try:
            client.set_acl(
                "stash://rooms/r1/",
                'https://evil.com/">',
                "https://pod.example.com/bob/profile/card#me",
            )
        except ValueError:
            pass
        client._session.put.assert_not_called()


# ---------------------------------------------------------------------------
# SolidClient.set_acl_multi injection tests
# ---------------------------------------------------------------------------

class TestSetAclMultiInjection:
    def test_set_acl_multi_safe_list_succeeds(self):
        client = _mock_client()
        client.set_acl_multi(
            "stash://rooms/r1/",
            "https://pod.example.com/alice/profile/card#me",
            [
                "https://pod.example.com/bob/profile/card#me",
                "https://pod.example.com/carol/profile/card#me",
            ],
        )

    @pytest.mark.parametrize("bad_webid", MALICIOUS_WEBIDS)
    def test_set_acl_multi_owner_injection_rejected(self, bad_webid):
        client = _mock_client()
        with pytest.raises(ValueError):
            client.set_acl_multi(
                "stash://rooms/r1/",
                bad_webid,
                ["https://pod.example.com/bob/profile/card#me"],
            )

    @pytest.mark.parametrize("bad_webid", MALICIOUS_WEBIDS)
    def test_set_acl_multi_subject_injection_rejected(self, bad_webid):
        client = _mock_client()
        with pytest.raises(ValueError):
            client.set_acl_multi(
                "stash://rooms/r1/",
                "https://pod.example.com/alice/profile/card#me",
                [bad_webid],
            )

    def test_set_acl_multi_one_bad_subject_stops_all(self):
        client = _mock_client()
        with pytest.raises(ValueError):
            client.set_acl_multi(
                "stash://rooms/r1/",
                "https://pod.example.com/alice/profile/card#me",
                [
                    "https://pod.example.com/bob/profile/card#me",
                    'https://evil.com/"> bad injection',
                    "https://pod.example.com/carol/profile/card#me",
                ],
            )
        client._session.put.assert_not_called()

    def test_set_acl_multi_empty_subject_list_succeeds(self):
        client = _mock_client()
        client.set_acl_multi(
            "stash://rooms/r1/",
            "https://pod.example.com/alice/profile/card#me",
            [],
        )


# ---------------------------------------------------------------------------
# set_room_acl injection tests (room.py)
# ---------------------------------------------------------------------------

class TestSetRoomAclInjection:
    def _make_room_and_client(self):
        from proxion_messenger_core.room import RoomConfig
        room = RoomConfig(
            room_id="room1",
            name="Test Room",
            owner_webid="https://pod.example.com/alice/profile/card#me",
            pod_url="http://localhost:3000/alice/",
            stash_root="stash://rooms/room1/",
            created_at="2026-01-01T00:00:00Z",
        )
        session = MagicMock()
        resp = MagicMock()
        resp.status_code = 200
        session.put.return_value = resp
        resolver = MagicMock()
        resolver.resolve.side_effect = lambda uri: f"http://localhost:3000/{uri.replace('stash://', '')}"
        client = SolidClient(resolver=resolver, session=session)
        return room, client

    def test_set_room_acl_safe_members_succeeds(self):
        from proxion_messenger_core.room import set_room_acl
        room, client = self._make_room_and_client()
        with patch("proxion_messenger_core.acp.detect_acl_mode", return_value="wac"):
            set_room_acl(
                room,
                client,
                owner_webid="https://pod.example.com/alice/profile/card#me",
                member_webids=["https://pod.example.com/bob/profile/card#me"],
            )

    @pytest.mark.parametrize("bad_webid", MALICIOUS_WEBIDS)
    def test_set_room_acl_owner_injection_rejected(self, bad_webid):
        from proxion_messenger_core.room import set_room_acl
        room, client = self._make_room_and_client()
        with pytest.raises(ValueError):
            set_room_acl(
                room,
                client,
                owner_webid=bad_webid,
                member_webids=["https://pod.example.com/bob/profile/card#me"],
            )

    @pytest.mark.parametrize("bad_webid", MALICIOUS_WEBIDS)
    def test_set_room_acl_member_injection_rejected(self, bad_webid):
        from proxion_messenger_core.room import set_room_acl
        room, client = self._make_room_and_client()
        with pytest.raises(ValueError):
            set_room_acl(
                room,
                client,
                owner_webid="https://pod.example.com/alice/profile/card#me",
                member_webids=[bad_webid],
            )
