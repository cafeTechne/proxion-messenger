"""The pod poll loop must start polling soon after the first client connects.

It used to sleep 30 minutes whenever no client was connected, so a browser
that opened just after gateway startup saw no pod messages for half an hour.
"""
import asyncio
from unittest.mock import MagicMock

import pytest

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState


@pytest.mark.asyncio
async def test_poll_starts_when_first_client_connects(tmp_path):
    gw = ProxionGateway(agent=AgentState.generate(), dm_clients={}, room_memberships={},
                        config=GatewayConfig(host="127.0.0.1", db_path=str(tmp_path / "g.db")))
    gw.config.poll_interval = 0.1
    polls = []

    async def fake_poll():
        polls.append(1)

    gw.do_poll = fake_poll
    task = asyncio.create_task(gw.poll_loop())
    try:
        await asyncio.sleep(0.3)
        assert polls == []  # nobody connected: no pod traffic
        gw.clients.add(MagicMock())
        for _ in range(30):
            if polls:
                break
            await asyncio.sleep(0.1)
        assert polls, "no pod poll within 3 s of a client connecting"
    finally:
        gw._stop_event.set()
        await asyncio.wait_for(task, timeout=5)
