"""R78 (L2) — outbound inbox poll: gateway reads granted inboxes and pushes on new
notifications, so closed-app push works even when the gateway is not reachable."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState

WEBID = "https://alice.pod/profile/card#me"
INBOX = "https://alice.pod/inbox/"

PROFILE_TTL = f"""@prefix ldp: <http://www.w3.org/ns/ldp#>.
<{WEBID}> ldp:inbox <{INBOX}> .
""".encode()

def _inbox_ttl(children):
    body = "@prefix ldp: <http://www.w3.org/ns/ldp#>.\n<" + INBOX + "> a ldp:Container"
    if children:
        body += "; ldp:contains " + ", ".join(f"<{c}>" for c in children)
    return (body + " .\n").encode()


@pytest.fixture
def agent():
    a = AgentState.generate()
    a.webid = WEBID
    return a


@pytest.fixture
def gw(agent, tmp_path):
    cfg = GatewayConfig(db_path=str(tmp_path / "poll.db"))
    g = ProxionGateway(agent=agent, dm_clients={}, room_memberships={}, config=cfg)
    g._vapid_subject = "mailto:admin@example.com"
    return g


def _wire_client(gw, profile=PROFILE_TTL, inbox_bodies=None):
    """Point _pod_client at a fake client returning our TTL by URL."""
    state = {"bodies": inbox_bodies or []}
    client = MagicMock()

    def _get(url):
        if url == WEBID.split("#")[0] or url == WEBID:
            return profile
        if url == INBOX:
            return state["bodies"].pop(0) if state["bodies"] else _inbox_ttl([])
        raise RuntimeError("unexpected GET " + url)

    client.get.side_effect = _get
    gw._pod_client = lambda: client
    return client


def test_discover_inbox_from_profile(gw):
    _wire_client(gw)
    assert gw._discover_inbox_from_profile(WEBID) == INBOX


def test_cross_origin_inbox_rejected_ssrf(gw):
    """R80 A3: a profile advertising an off-origin ldp:inbox must not be fetched."""
    evil = b"@prefix ldp: <http://www.w3.org/ns/ldp#>.\n<%s> ldp:inbox <http://169.254.169.254/latest/> .\n" % WEBID.encode()
    client = MagicMock()
    client.get.side_effect = lambda url: evil if url in (WEBID, WEBID.split("#")[0]) else _inbox_ttl([])
    gw._pod_client = lambda: client
    assert gw._discover_inbox_from_profile(WEBID) is None


def test_list_inbox_children_parses_contains(gw):
    _wire_client(gw, inbox_bodies=[_inbox_ttl([INBOX + "n1", INBOX + "n2"])])
    kids = gw._list_inbox_children(INBOX)
    assert kids == {INBOX + "n1", INBOX + "n2"}


def test_first_poll_seeds_without_pushing(gw):
    gw._store.save_push_subscription("s1", WEBID, "https://push/ep", "p", "a")
    _wire_client(gw, inbox_bodies=[_inbox_ttl([INBOX + "n1"])])
    sent = []
    gw._send_inbox_push = lambda w: (sent.append(w) or True)
    assert gw._poll_inboxes_once() == 0        # pre-existing invite: seeded, not pushed
    assert sent == []
    assert gw._inbox_seen[INBOX] == {INBOX + "n1"}


def test_new_notification_pushes_once(gw):
    gw._store.save_push_subscription("s1", WEBID, "https://push/ep", "p", "a")
    # First poll seeds {n1}; second poll sees {n1, n2} -> pushes for n2.
    _wire_client(gw, inbox_bodies=[_inbox_ttl([INBOX + "n1"]), _inbox_ttl([INBOX + "n1", INBOX + "n2"])])
    sent = []
    gw._send_inbox_push = lambda w: (sent.append(w) or True)
    gw._poll_inboxes_once()
    assert gw._poll_inboxes_once() == 1
    assert sent == [WEBID]


def test_seen_set_tracks_current_children_not_union(gw):
    """R80 A3: the seen-set must not accumulate dismissed notifications forever."""
    gw._store.save_push_subscription("s1", WEBID, "https://push/ep", "p", "a")
    _wire_client(gw, inbox_bodies=[
        _inbox_ttl([INBOX + "n1", INBOX + "n2"]),   # seed
        _inbox_ttl([INBOX + "n1"]),                 # n2 dismissed
    ])
    gw._send_inbox_push = lambda w: True
    gw._poll_inboxes_once()                          # seed {n1,n2}
    gw._poll_inboxes_once()                          # n2 gone
    assert gw._inbox_seen[INBOX] == {INBOX + "n1"}   # bounded to current, not union


def test_unreadable_inbox_is_skipped(gw):
    """No grant → listing returns None → no crash, no push, no seed."""
    gw._store.save_push_subscription("s1", WEBID, "https://push/ep", "p", "a")
    client = MagicMock()
    client.get.side_effect = lambda url: PROFILE_TTL if url in (WEBID, WEBID.split("#")[0]) else (_ for _ in ()).throw(RuntimeError("403"))
    gw._pod_client = lambda: client
    sent = []
    gw._send_inbox_push = lambda w: (sent.append(w) or True)
    assert gw._poll_inboxes_once() == 0
    assert sent == []
    assert INBOX not in gw._inbox_seen


def test_send_inbox_push_rate_limited(gw):
    """A second push to the same WebID within the window is suppressed (dedups the
    webhook + poll paths)."""
    gw._store.save_push_subscription("s1", WEBID, "https://push/ep", "p", "a")
    import proxion_messenger_core.webpush as wp
    calls = []
    orig = wp.send_web_push
    wp.send_web_push = lambda **k: (calls.append(1) or True)
    try:
        assert gw._send_inbox_push(WEBID) is True
        assert gw._send_inbox_push(WEBID) is False   # within window
    finally:
        wp.send_web_push = orig
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_get_gateway_webid_command(gw):
    gw._pod_webid = WEBID
    ws = MagicMock()
    sent = []
    async def _send(m): sent.append(m)
    ws.send = _send
    await gw._handle_get_gateway_webid(ws, {})
    import json
    assert json.loads(sent[-1]) == {"type": "gateway_webid", "webid": WEBID}
