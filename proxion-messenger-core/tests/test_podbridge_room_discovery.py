"""Room recovery may list the owner's descriptors through the pod bridge."""
import json

import pytest

from proxion_messenger_core import podbridge
from test_pod_bridge import ROOT, fetch, gw, make_ws, register  # noqa: F401


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_own_room_listing_is_bounded_and_read_only(method):
    decision = podbridge.classify(method, ROOT + "rooms/", ROOT)
    assert decision.allow and decision.own
    assert decision.kind == "owned_rooms"
    assert decision.max_bytes == podbridge.MAX_LISTING_RESPONSE


@pytest.mark.parametrize("method,url", [
    ("PUT", ROOT + "rooms/"),
    ("POST", ROOT + "rooms/"),
    ("PATCH", ROOT + "rooms/"),
    ("DELETE", ROOT + "rooms/"),
    ("GET", ROOT + "rooms"),
    ("GET", ROOT + "rooms/?query=all"),
    ("GET", ROOT + "rooms/../private/"),
    ("GET", ROOT + "rooms/%2e%2e/private/"),
    ("GET", ROOT + "private/"),
    ("GET", ROOT + "rooms/unrelated/"),
    ("GET", "https://other.example/bob/rooms/"),
])
def test_discovery_does_not_open_mutations_or_other_pod_paths(method, url):
    assert not podbridge.classify(method, url, ROOT).allow


@pytest.mark.parametrize("extension", ["json", "jsonld"])
@pytest.mark.parametrize("method", ["GET", "HEAD", "PUT"])
def test_joined_room_records_accept_canonical_json_and_legacy_path(extension, method):
    decision = podbridge.classify(method, ROOT + "proxion/joined-rooms." + extension, ROOT)
    assert decision.allow and decision.own and decision.kind == "joined"
    assert decision.max_bytes == podbridge.MAX_LISTING_RESPONSE


@pytest.mark.parametrize("method,url", [
    ("DELETE", ROOT + "proxion/joined-rooms.json"),
    ("GET", ROOT + "proxion/joined-rooms.jsonx"),
    ("GET", "https://other.example/bob/proxion/joined-rooms.json"),
])
def test_joined_room_migration_does_not_open_other_paths(method, url):
    assert not podbridge.classify(method, url, ROOT).allow


def test_owner_can_fetch_descriptor_listing_through_gateway(gw):
    ws, did = make_ws()
    register(gw, ws, did)
    url = ROOT + "rooms/"
    listing = [{"@id": url, "http://www.w3.org/ns/ldp#contains": [{"@id": url + "saved-room/"}]}]
    gw._pod.routes[("GET", url)] = (200, {"content-type": "application/ld+json"}, json.dumps(listing).encode())
    reply = fetch(gw, ws, "GET", url, headers={"Accept": "application/ld+json"})
    assert reply["status"] == 200 and json.loads(reply["body"]) == listing
    assert gw._pod.calls[-1][:2] == ("GET", url)
    assert gw._pod.calls[-1][3] == {"Accept": "application/ld+json"}


def test_non_owner_still_cannot_list_descriptors(gw):
    gw._force_auth = True
    ws, did = make_ws(ip="203.0.113.9")
    register(gw, ws, did, ip="203.0.113.9")
    reply = fetch(gw, ws, "GET", ROOT + "rooms/")
    assert reply["status"] == 403 and reply["error"] == "not_owner"
    assert gw._pod.calls == []
