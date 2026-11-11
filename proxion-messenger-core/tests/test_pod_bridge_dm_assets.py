"""Ciphertext-only DM assets: scoped immutable writes and unauthenticated reads."""
from __future__ import annotations

import base64
import json
from unittest.mock import AsyncMock, patch

import pytest

from test_pod_bridge import (  # noqa: F401 (fixture)
    ROOT, WEBID, PEER_ROOT, fetch, gw, make_ws, register,
)
from proxion_messenger_core import podbridge as pbp

ASSET_ID = "018f7291-4cc9-4acf-bdbc-7483881ca971"
CONTAINER = ROOT + "proxion/dm-assets/"
ASSET = CONTAINER + f"asset-{ASSET_ID}-0.json"
CHUNK = {"v": 1, "id": ASSET_ID, "index": 0, "data": base64.b64encode(b"ciphertext").decode()}
HEADERS = {"Content-Type": "application/json", "If-None-Match": "*"}


def test_asset_policy_is_tightly_scoped():
    foreign = PEER_ROOT + "proxion/dm-assets/"
    for method in ("GET", "HEAD", "PUT", "DELETE"):
        assert pbp.classify(method, ASSET, ROOT).allow
    for method in ("GET", "HEAD"):
        decision = pbp.classify(method, foreign + ASSET.rsplit("/", 1)[1], ROOT)
        assert decision.allow and not decision.own and not decision.auth
        assert decision.max_bytes == 65536
    for base in (CONTAINER, foreign):
        for suffix in ("asset-secret-0.json", f"asset-{ASSET_ID}-65.json", f"asset-{ASSET_ID}-00.json",
                       f"asset-{ASSET_ID}-0.json/child", f"asset-{ASSET_ID}-0.json?token=x", "../private.json"):
            assert not pbp.classify("GET", base + suffix, ROOT).allow
        for method in ("PATCH", "POST"):
            assert not pbp.classify(method, base + ASSET.rsplit("/", 1)[1], ROOT).allow
    assert not pbp.classify("DELETE", foreign + ASSET.rsplit("/", 1)[1], ROOT).allow
    for method in ("PUT", "DELETE", "POST", "GET"):
        assert not pbp.classify(method, foreign, ROOT).allow
        assert not pbp.classify(method, foreign + ".acl", ROOT).allow
    assert not pbp.classify("GET", CONTAINER, ROOT).allow
    assert pbp.classify("GET", CONTAINER + f"asset-{ASSET_ID}-64.json", ROOT).allow


def test_owner_creates_and_reads_an_immutable_chunk(gw):
    ws, did = make_ws()
    register(gw, ws, did)
    gw._pod.routes[("PUT", ASSET)] = (201, {}, b"")
    assert fetch(gw, ws, "PUT", ASSET, json.dumps(CHUNK), HEADERS)["status"] == 201
    assert gw._pod.calls[-1][3] == HEADERS
    assert fetch(gw, ws, "PUT", ASSET, json.dumps(CHUNK), {"Content-Type": "application/json"})["error"] == "immutable_asset"
    assert fetch(gw, ws, "PUT", ASSET, json.dumps(CHUNK), {**HEADERS, "If-None-Match": '"old"'})["error"] == "immutable_asset"
    gw._pod.routes[("GET", ASSET)] = (200, {}, json.dumps(CHUNK).encode())
    assert fetch(gw, ws, "GET", ASSET)["status"] == 200


@pytest.mark.parametrize("changes", [
    {"v": True}, {"v": 2}, {"id": "../escape"}, {"index": True}, {"index": 1},
    {"index": -1}, {"index": 65}, {"data": "!bad"}, {"data": "eB=="},
    {"data": base64.b64encode(b"x" * 32769).decode()}, {"name": "leaked-name.txt"},
    {"key": "never-store-keys"},
])
def test_invalid_or_metadata_bearing_chunks_are_refused_before_io(gw, changes):
    ws, did = make_ws()
    register(gw, ws, did)
    assert fetch(gw, ws, "PUT", ASSET, json.dumps({**CHUNK, **changes}), HEADERS)["error"] == "bad_body"
    assert gw._pod.calls == []


def test_foreign_asset_read_strips_credentials_and_stays_under_ssrf_and_size_controls(gw):
    ws, did = make_ws()
    register(gw, ws, did)
    url = PEER_ROOT + "proxion/dm-assets/" + ASSET.rsplit("/", 1)[1]
    request = AsyncMock(return_value=(200, {"content-type": "application/json"}, json.dumps(CHUNK).encode()))
    with patch("proxion_messenger_core.network.async_safe_request", request):
        result = fetch(gw, ws, "GET", url, headers={"Authorization": "secret", "DPoP": "secret", "Cookie": "secret", "Accept": "application/json"})
    assert result["status"] == 200
    assert request.call_args.kwargs["headers"] == {"Accept": "application/json"}
    assert request.call_args.kwargs["max_bytes"] == 65536
    assert gw._pod.calls == []


def test_container_creation_is_empty_and_conditional(gw):
    ws, did = make_ws()
    register(gw, ws, did)
    gw._pod.routes[("PUT", CONTAINER)] = (201, {}, b"")
    headers = {"Content-Type": "text/turtle", "Link": '<http://www.w3.org/ns/ldp#BasicContainer>; rel="type"', "If-None-Match": "*"}
    assert fetch(gw, ws, "PUT", CONTAINER, "", headers)["status"] == 201
    assert gw._pod.calls[-1][3] == headers
    assert fetch(gw, ws, "PUT", CONTAINER, "{}", headers)["error"] == "bad_body"
    assert fetch(gw, ws, "PUT", CONTAINER, "", {})["error"] == "immutable_asset"


@pytest.mark.parametrize("model,suffix", [("wac", ".acl"), ("acp", ".acr"), ("acp", ".acl")])
def test_asset_acl_allows_only_exact_owner_write_public_read_grants(gw, model, suffix):
    ws, did = make_ws()
    register(gw, ws, did)
    url = CONTAINER + suffix
    acl = pbp.dm_asset_acl_turtle(CONTAINER, WEBID, model)
    gw._pod.routes[("PUT", url)] = (201, {}, b"")
    assert fetch(gw, ws, "PUT", url, acl, {"Content-Type": "text/turtle"})["status"] == 201
    public_write = acl.replace("acl:Read;", "acl:Read, acl:Write;") if model == "acp" else acl.replace("acl:Read.", "acl:Read, acl:Write.")
    for wrong in (acl.replace(WEBID, "https://attacker.example/#me"), public_write, acl + "#extra"):
        assert fetch(gw, ws, "PUT", url, wrong)["error"] == "bad_body"


def test_non_owner_cannot_read_or_write_assets(gw):
    ws, did = make_ws(ip="203.0.113.9")
    register(gw, ws, did, ip="203.0.113.9")
    gw._force_auth = True
    assert fetch(gw, ws, "GET", ASSET)["error"] == "not_owner"
    assert fetch(gw, ws, "PUT", ASSET, json.dumps(CHUNK), HEADERS)["error"] == "not_owner"
    assert gw._pod.calls == []


def test_owner_asset_delete_requires_one_strong_etag_and_stays_scoped(gw):
    ws, did = make_ws()
    register(gw, ws, did)
    for value in (None, "*", 'W/"weak"', '"one", "two"', '"bad\r\nheader"'):
        assert fetch(gw, ws, "DELETE", ASSET, headers={"If-Match": value} if value is not None else {})["error"] == "asset_version_required"
    assert gw._pod.calls == []
    gw._pod.routes[("DELETE", ASSET)] = (204, {}, b"")
    assert fetch(gw, ws, "DELETE", ASSET, headers={"If-Match": '"version"'})["status"] == 204
    assert gw._pod.calls[-1][3] == {"If-Match": '"version"'}
    for url in (CONTAINER, CONTAINER + '.acl', ROOT + 'proxion/rooms/room1/asset-other-0.json',
                PEER_ROOT + 'proxion/dm-assets/' + ASSET.rsplit('/', 1)[1]):
        assert fetch(gw, ws, "DELETE", url, headers={"If-Match": '"version"'})["status"] == 403
