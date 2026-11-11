"""Stored-file review opens only owner directory reads and legacy metadata."""
import pytest

from proxion_messenger_core import podbridge
from test_pod_bridge import ROOT, PEER_ROOT, fetch, gw, make_ws, register  # noqa: F401


@pytest.mark.parametrize("path", [
    "proxion/rooms/", "proxion/rooms/retained/",
    "proxion/rooms/retained/files/", "proxion/rooms/retained/files/message/",
])
@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_own_inventory_directories_are_bounded(path, method):
    decision = podbridge.classify(method, ROOT + path, ROOT)
    assert decision.allow and decision.own
    assert decision.max_bytes == podbridge.MAX_LISTING_RESPONSE


@pytest.mark.parametrize("path", [
    "proxion/rooms/retained/files/message/old.txt",
    "proxion/rooms/retained/files/message/.hidden",
    "proxion/rooms/retained/files/note.webm",
])
def test_legacy_file_access_is_metadata_only(path):
    assert podbridge.classify("HEAD", ROOT + path, ROOT).allow
    for method in ("GET", "PUT", "POST", "PATCH", "DELETE"):
        assert not podbridge.classify(method, ROOT + path, ROOT).allow
    assert not podbridge.classify("HEAD", PEER_ROOT + path, ROOT).allow


@pytest.mark.parametrize("path", [
    "proxion/rooms/retained/files/message/secret.acl",
    "proxion/rooms/retained/files/message/secret.ACR",
    "proxion/rooms/retained/files/message/%2e%2e",
    "proxion/rooms/retained/files/message/../private",
    "proxion/rooms/retained/files/message/file.txt?x=1",
    "proxion/rooms/retained/files/message/file.txt#fragment",
    "proxion/rooms/retained/files/message/deep/file.txt",
    "proxion/rooms/retained/files/random.txt",
])
def test_metadata_does_not_open_arbitrary_paths(path):
    assert not podbridge.classify("HEAD", ROOT + path, ROOT).allow


@pytest.mark.parametrize("path", [
    "proxion/rooms/", "proxion/rooms/retained/files/",
    "proxion/rooms/retained/files/message/",
])
def test_inventory_cannot_mutate_or_browse_foreign_directories(path):
    for method in ("PUT", "POST", "PATCH", "DELETE"):
        assert not podbridge.classify(method, ROOT + path, ROOT).allow
    for method in ("GET", "HEAD"):
        assert not podbridge.classify(method, PEER_ROOT + path, ROOT).allow


def test_gateway_serves_metadata_without_file_body_and_enforces_owner(gw):
    ws, did = make_ws()
    register(gw, ws, did)
    url = ROOT + "proxion/rooms/retained/files/message/old.txt"
    gw._pod.routes[("HEAD", url)] = (200, {"content-length": "123"}, b"")
    reply = fetch(gw, ws, "HEAD", url)
    assert reply["status"] == 200 and reply["headers"]["content-length"] == "123"
    assert not reply.get("body")
    assert fetch(gw, ws, "GET", url)["status"] == 403
    gw._force_auth = True
    remote, remote_did = make_ws(ip="203.0.113.9")
    register(gw, remote, remote_did, ip="203.0.113.9")
    assert fetch(gw, remote, "HEAD", url)["error"] == "not_owner"
    assert len(gw._pod.calls) == 1
