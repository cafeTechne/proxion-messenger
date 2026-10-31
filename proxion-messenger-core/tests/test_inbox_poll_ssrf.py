"""The LDN inbox poll makes authenticated GETs of URLs taken from push
subscriptions, so it must not be steerable: the pod credentials go only to the
connected pod's origin, a hostname must resolve to a public address, and a
redirect is refused instead of followed (with the credentials) to its target."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

import proxion_messenger_core.network as net
from proxion_messenger_core.gateway import GatewayConfig, ProxionGateway
from proxion_messenger_core.persist import AgentState

POD = "http://alice.pod/"
WEBID = POD + "profile/card#me"
INBOX = POD + "inbox/"
PROFILE = (f"<{WEBID}> <http://www.w3.org/ns/ldp#inbox> <{INBOX}> .\n").encode()
PUBLIC_IP = "93.184.216.34"


def _addrinfo(ip):
    return [(None, None, None, None, (ip, 0))]


@pytest.fixture
def gw(tmp_path):
    g = ProxionGateway(agent=AgentState.generate(), dm_clients={}, room_memberships={},
                       config=GatewayConfig(db_path=str(tmp_path / "poll.db")))
    g._pod_url = POD
    client = MagicMock()
    client._auth_headers = {}
    client._dynamic_headers.side_effect = lambda m, u: {"Authorization": "DPoP pod-token"}
    # An unchecked authenticated read would succeed; the tests assert it never happens.
    client.get.side_effect = lambda url: PROFILE
    g._pod_client = lambda: client
    return g


@pytest.fixture
def transport(monkeypatch):
    """Serve safe_get's requests from a handler and record them."""
    seen = []
    routes = {}

    def handler(request):
        seen.append(request)
        return routes.get(request.headers.get("host", "") + request.url.path,
                          httpx.Response(404))

    real_client = httpx.Client

    def _client(**kw):
        kw["transport"] = httpx.MockTransport(handler)
        return real_client(**kw)

    monkeypatch.setattr(net.httpx, "Client", _client)
    return seen, routes


def test_pod_origin_profile_is_fetched_pinned_with_creds(gw, transport):
    seen, routes = transport
    routes["alice.pod/profile/card"] = httpx.Response(200, content=PROFILE)
    with patch("proxion_messenger_core.network.socket.getaddrinfo", return_value=_addrinfo(PUBLIC_IP)):
        assert gw._discover_inbox_from_profile(WEBID) == INBOX
    assert len(seen) == 1
    assert seen[0].url.host == PUBLIC_IP                    # pinned to the checked IP
    assert seen[0].headers["authorization"] == "DPoP pod-token"


def test_pod_host_resolving_to_private_ip_is_not_fetched(gw, transport):
    seen, _routes = transport
    with patch("proxion_messenger_core.network.socket.getaddrinfo", return_value=_addrinfo("10.0.0.5")):
        assert gw._discover_inbox_from_profile(WEBID) is None
        assert gw._list_inbox_children(INBOX) is None
    assert seen == []
    gw._pod_client().get.assert_not_called()


def test_redirect_is_not_followed_with_creds(gw, transport):
    seen, routes = transport
    routes["alice.pod/profile/card"] = httpx.Response(
        302, headers={"location": "http://evil.example/steal"})
    routes["alice.pod/inbox/"] = httpx.Response(
        307, headers={"location": "http://169.254.169.254/latest/"})
    with patch("proxion_messenger_core.network.socket.getaddrinfo", return_value=_addrinfo(PUBLIC_IP)):
        assert gw._discover_inbox_from_profile(WEBID) is None
        assert gw._list_inbox_children(INBOX) is None
    assert [r.headers.get("host") for r in seen] == ["alice.pod", "alice.pod"]
    gw._pod_client().get.assert_not_called()


def test_foreign_origin_webid_gets_no_request(gw, transport):
    seen, _routes = transport
    with patch("proxion_messenger_core.network.socket.getaddrinfo", return_value=_addrinfo(PUBLIC_IP)):
        assert gw._discover_inbox_from_profile("http://other.example/card#me") is None
        assert gw._discover_inbox_from_profile("http://169.254.169.254/latest#me") is None
    assert seen == []
    gw._pod_client().get.assert_not_called()


def test_no_pod_origin_means_no_request(gw, transport):
    seen, _routes = transport
    gw._pod_url = None
    assert gw._discover_inbox_from_profile(WEBID) is None
    assert seen == []


def test_local_literal_pod_uses_the_pod_client(gw):
    """A pod configured as localhost cannot be DNS-rebound; it is read directly."""
    gw._pod_url = "http://localhost:3000/alice/"
    client = gw._pod_client()
    client.get.side_effect = lambda url: (
        b"<http://localhost:3000/alice/profile/card#me> "
        b"<http://www.w3.org/ns/ldp#inbox> <http://localhost:3000/alice/inbox/> .")
    assert (gw._discover_inbox_from_profile("http://localhost:3000/alice/profile/card#me")
            == "http://localhost:3000/alice/inbox/")
    client.get.assert_called_once_with("http://localhost:3000/alice/profile/card")
