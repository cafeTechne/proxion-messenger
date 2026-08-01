"""R77 — Solid inbox webhook → Web Push relay.

The gateway issues a stateless HMAC token encoding a user's WebID; when CSS POSTs
the inbox webhook, the gateway recovers the WebID and sends a content-free push.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState

WEBID = "https://alice.pod/profile/card#me"


@pytest.fixture
def agent():
    a = AgentState.generate()
    a.webid = WEBID
    return a


@pytest.fixture
def gw(agent, tmp_path):
    cfg = GatewayConfig(db_path=str(tmp_path / "wh.db"))
    g = ProxionGateway(agent=agent, dm_clients={}, room_memberships={}, config=cfg)
    # VAPID subject is env-only by default; set it so pushes are attempted.
    g._vapid_subject = "mailto:admin@example.com"
    return g


def test_token_round_trips(gw):
    tok = gw._inbox_webhook_token(WEBID)
    assert tok and "." in tok
    assert gw._verify_inbox_webhook_token(tok) == WEBID


def test_tampered_token_rejected(gw):
    tok = gw._inbox_webhook_token(WEBID)
    # Flip the last character of the signature.
    bad = tok[:-1] + ("A" if tok[-1] != "A" else "B")
    assert gw._verify_inbox_webhook_token(bad) is None
    assert gw._verify_inbox_webhook_token("garbage") is None
    assert gw._verify_inbox_webhook_token("") is None


def test_token_is_bound_to_webid(gw):
    """A token for one WebID never validates for another."""
    tok = gw._inbox_webhook_token(WEBID)
    other = gw._inbox_webhook_token("https://mallory.pod/profile/card#me")
    assert tok != other
    assert gw._verify_inbox_webhook_token(tok) == WEBID


def test_valid_token_pushes_content_free(gw):
    gw._store.save_push_subscription("s1", WEBID, "https://push.example/ep", "p256", "auth")
    with patch("proxion_messenger_core.webpush.send_web_push") as sp:
        sp.return_value = True
        assert gw._deliver_inbox_webhook(gw._inbox_webhook_token(WEBID)) is True
    assert sp.call_count == 1
    payload = sp.call_args.kwargs["payload"]
    assert payload["type"] == "invite"
    # Privacy-preserving: never a sender or message content.
    assert "content" not in payload and not payload.get("thread_id")


def test_invalid_token_pushes_nothing(gw):
    gw._store.save_push_subscription("s1", WEBID, "https://push.example/ep", "p256", "auth")
    with patch("proxion_messenger_core.webpush.send_web_push") as sp:
        assert gw._deliver_inbox_webhook("not-a-valid-token") is False
    sp.assert_not_called()


def test_no_subscription_pushes_nothing(gw):
    with patch("proxion_messenger_core.webpush.send_web_push") as sp:
        assert gw._deliver_inbox_webhook(gw._inbox_webhook_token(WEBID)) is False
    sp.assert_not_called()


@pytest.mark.asyncio
async def test_get_inbox_webhook_command_returns_token(gw):
    ws = MagicMock()
    sent = []
    async def _send(m): sent.append(m)
    ws.send = _send
    gw._client_webids[ws] = WEBID
    await gw._handle_get_inbox_webhook(ws, {})
    msg = json.loads(sent[-1])
    assert msg["type"] == "inbox_webhook"
    assert msg["path"] == "/solid-webhook/"
    assert gw._verify_inbox_webhook_token(msg["token"]) == WEBID
