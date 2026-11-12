"""Effective ACP grants are a complete membership snapshot, including children."""
import json
from unittest.mock import MagicMock

import httpx
import pytest

from proxion_messenger_core.acp import detect_acl_mode, set_acl_multi_auto
from proxion_messenger_core.solid import SolidResolver
from proxion_messenger_core.solid_client import SolidClient, SolidError

ROOT = "https://pod.example/alice/"
RESOURCE = ROOT + "rooms/test/"
OWNER = ROOT + "profile/card#me"
MEMBERS = ["https://pod.example/bob/#me", "https://pod.example/carol/#me"]


@pytest.mark.parametrize("link", [
    '<.acr>; rel="acl"', '<.acr>; rel="type acl"', '<.acr>; rel=acr',
    '<control>; type="text/turtle"; rel="http://www.w3.org/ns/solid/acp#accessControl"',
])
def test_detection_uses_real_head_transport_and_resolves_stash(link):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, headers={"Link": link})

    with httpx.Client(transport=httpx.MockTransport(handler)) as session:
        client = SolidClient(SolidResolver(ROOT), session=session)
        assert detect_acl_mode(client, "stash://pod/rooms/test/", strict=True) == "acp"
    assert len(calls) == 1 and calls[0].method == "HEAD" and str(calls[0].url) == RESOURCE


@pytest.mark.parametrize("status", [302, 401, 403, 503])
def test_failed_head_does_not_write_a_fallback_policy(status):
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(status))) as session:
        client = SolidClient(SolidResolver(ROOT), session=session)
        with pytest.raises(SolidError):
            set_acl_multi_auto(client, RESOURCE, OWNER, MEMBERS)


@pytest.mark.parametrize("encoding", ["css", "ess"])
@pytest.mark.parametrize("members", [MEMBERS, MEMBERS[:1], []])
def test_all_members_and_empty_membership_are_written_once(encoding, members):
    pod = MagicMock()
    pod._resolve_uri.return_value = RESOURCE
    pod.head.return_value = {"link": '<policy>; rel="http://www.w3.org/ns/solid/acp#accessControl"'}
    assert set_acl_multi_auto(pod, "stash://pod/rooms/test/", OWNER, members, ["Read", "Append"], encoding) == RESOURCE + "policy"
    pod.put.assert_called_once()
    body = pod.put.call_args.args[1].decode()
    assert "stash://" not in body
    assert OWNER in body
    for member in MEMBERS:
        assert (member in body) == (member in members)
    if encoding == "css":
        doc = json.loads(body)
        controls = doc["accessControl"]
        assert len(controls) == len(members) + 1
        assert {control["@id"] for control in controls} == {control["@id"] for control in doc["memberAccessControl"]}
        assert controls[0]["apply"]["allow"] == ["acl:Read", "acl:Write", "acl:Control"]
        for control in controls[1:]:
            assert control["apply"]["allow"] == ["acl:Read", "acl:Append"]
    else:
        assert "acp:memberAccessControl" in body
        assert body.count("a acp:Matcher") == len(members) + 1


def test_off_origin_access_control_link_is_rejected_before_writing():
    pod = MagicMock()
    pod.head.return_value = {"Link": '<https://foreign.example/policy>; rel="acr"'}
    with pytest.raises(ValueError, match="origin"):
        set_acl_multi_auto(pod, RESOURCE, OWNER, MEMBERS)
    pod.put.assert_not_called()
