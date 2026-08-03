"""R82 W2 — missed-call Web Push for an offline callee."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState

CALLEE = "https://bob.pod/profile/card#me"
CALLER = "https://alice.pod/profile/card#me"


@pytest.fixture
def gw(tmp_path):
    a = AgentState.generate()
    a.webid = "https://gw.pod/profile/card#me"
    g = ProxionGateway(agent=a, dm_clients={}, room_memberships={},
                       config=GatewayConfig(db_path=str(tmp_path / "mc.db")))
    g._vapid_subject = "mailto:admin@example.com"
    return g


def test_missed_call_pushes_content_free(gw):
    gw._store.save_push_subscription("s1", CALLEE, "https://push/ep", "p", "a")
    with patch("proxion_messenger_core.webpush.send_web_push") as sp:
        sp.return_value = True
        assert gw._push_missed_call(CALLEE, CALLER) is True
    payload = sp.call_args.kwargs["payload"]
    assert payload["type"] == "missed_call"
    assert "content" not in payload and not payload.get("thread_id")   # privacy


def test_missed_call_honors_mute(gw):
    gw._store.save_push_subscription("s1", CALLEE, "https://push/ep", "p", "a")
    with patch.object(gw._store, "is_thread_muted", return_value=True):
        with patch("proxion_messenger_core.webpush.send_web_push") as sp:
            assert gw._push_missed_call(CALLEE, CALLER) is False
        sp.assert_not_called()


def test_missed_call_no_subscription(gw):
    with patch("proxion_messenger_core.webpush.send_web_push") as sp:
        assert gw._push_missed_call(CALLEE, CALLER) is False
    sp.assert_not_called()
