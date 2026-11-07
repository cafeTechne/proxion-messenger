"""Exercise delivery and persistence, rather than just scheduler invocation."""
import asyncio
import json
import time
from unittest.mock import AsyncMock, Mock

import pytest

from proxion_messenger_core.gateway import GatewayConfig, ProxionGateway
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core import run_local_handshake
from proxion_messenger_core.federation import Capability
from proxion_messenger_core.store import MemoryStore


def fail_storage(*args, **kwargs):
    raise OSError('disk busy')


@pytest.fixture
def gateway(tmp_path):
    gw = ProxionGateway(agent=AgentState.generate(), dm_clients={}, room_memberships={},
                        config=GatewayConfig(db_path=str(tmp_path / 'scheduler.db')))
    gw._local_rooms['room'] = {'name': 'Room', 'code': 'code', 'members': set(),
                               'history_mode': 'all', 'messages': []}
    gw._store.save_room('room', 'Room', 'code', '', 'all', creator_webid='did:key:alice')
    gw._store.add_room_member('room', 'did:key:alice')
    return gw


def seed(gw, job_id='job', thread='room'):
    job = {'id': job_id, 'thread_id': thread, 'from_webid': 'did:key:alice',
           'content': 'A scheduled message', 'send_at': time.time() - 1,
           'created_at': time.time() - 10}
    gw._store.save_scheduled_message(job)
    return job


def cert_thread(gw):
    peer = AgentState.generate()
    caps = [Capability(can='read', with_='stash://messages/'),
            Capability(can='write', with_='stash://messages/')]
    cert, valid = run_local_handshake(
        alice_identity_priv=gw.agent.identity_key, alice_store_priv=gw.agent.store_key,
        bob_identity_priv=peer.identity_key, bob_store_priv=peer.store_key,
        alice_capabilities=caps, bob_capabilities=caps, store=MemoryStore())
    assert valid
    gw.dm_clients[cert.certificate_id] = (cert, Mock())
    return cert.certificate_id


@pytest.mark.asyncio
async def test_room_storage_failure_retries_and_then_delivers(gateway, monkeypatch):
    job = seed(gateway)
    save = gateway._store.save_message
    monkeypatch.setattr(gateway._store, 'save_message', fail_storage)
    assert not await gateway._deliver_scheduled_message(job)
    assert gateway._store.get_due_scheduled_messages(time.time())
    assert not gateway._system_ws
    monkeypatch.setattr(gateway._store, 'save_message', save)
    assert await gateway._deliver_scheduled_message(job)
    assert not gateway._store.get_due_scheduled_messages(time.time())
    assert len(gateway._store.get_messages('room')) == 1
    assert not gateway._rate_counters


@pytest.mark.asyncio
async def test_acknowledgement_failure_does_not_publish_room_message_twice(gateway, monkeypatch):
    peer = AsyncMock()
    gateway._local_rooms['room']['members'].add(peer)
    job = seed(gateway)
    mark = gateway._store.mark_scheduled_sent
    monkeypatch.setattr(gateway._store, 'mark_scheduled_sent', fail_storage)
    with pytest.raises(OSError):
        await gateway._deliver_scheduled_message(job)
    assert peer.send.await_count == 1
    assert gateway._store.get_due_scheduled_messages(time.time())
    monkeypatch.setattr(gateway._store, 'mark_scheduled_sent', mark)
    assert await gateway._deliver_scheduled_message(job)
    assert peer.send.await_count == 1
    assert len(gateway._store.get_messages('room')) == 1
    assert not gateway._store.get_due_scheduled_messages(time.time())


@pytest.mark.asyncio
async def test_online_sender_receives_own_scheduled_message(gateway):
    sender, peer = AsyncMock(), AsyncMock()
    gateway._client_webids[sender] = 'did:key:alice'
    gateway._client_webids[peer] = 'did:key:bob'
    gateway._local_rooms['room']['members'].update((sender, peer))
    assert await gateway._deliver_scheduled_message(seed(gateway))
    assert json.loads(sender.send.call_args.args[0])['own'] is True
    assert json.loads(peer.send.call_args.args[0])['own'] is False


@pytest.mark.asyncio
async def test_restart_recovers_delivered_room_before_acknowledgement(gateway, monkeypatch):
    job = seed(gateway)
    monkeypatch.setattr(gateway._store, 'mark_scheduled_sent', fail_storage)
    with pytest.raises(OSError):
        await gateway._deliver_scheduled_message(job)
    restarted = ProxionGateway(agent=gateway.agent, dm_clients={}, room_memberships={}, config=gateway.config)
    restarted.process_command = AsyncMock(side_effect=AssertionError('must not resend'))
    assert await restarted._deliver_scheduled_message(job)
    assert len(restarted._store.get_messages('room')) == 1
    assert not restarted._store.get_due_scheduled_messages(time.time())


@pytest.mark.asyncio
async def test_removed_online_member_cannot_deliver_scheduled_message(gateway):
    job = seed(gateway)
    # A lingering live socket is not authority after persistent removal.
    ws = AsyncMock()
    gateway._local_rooms['room']['members'].add(ws)
    gateway._client_webids[ws] = job['from_webid']
    gateway._store.remove_room_member('room', job['from_webid'])
    assert not await gateway._deliver_scheduled_message(job)
    assert gateway._store.get_due_scheduled_messages(time.time())
    assert gateway._store.get_messages('room') == []
    assert set(gateway._client_webids) == {ws}
    assert not gateway._system_ws


@pytest.mark.asyncio
async def test_unavailable_thread_is_retained_visible_and_cancellable(gateway):
    job = seed(gateway, thread='missing')
    assert not await gateway._deliver_scheduled_message(job)
    assert gateway._store.get_due_scheduled_messages(time.time())
    assert gateway._store.get_scheduled_messages_for_user(job['from_webid'])[0]['id'] == job['id']
    assert gateway._store.cancel_scheduled_message(job['id'], job['from_webid'])
    assert not gateway._store.get_due_scheduled_messages(time.time())


@pytest.mark.asyncio
async def test_one_exception_does_not_block_other_due_jobs(gateway, monkeypatch):
    bad = seed(gateway, 'bad')
    good = seed(gateway, 'good')
    deliver = gateway._deliver_scheduled_message

    async def fail_one(job):
        if job['id'] == bad['id']:
            raise RuntimeError('transient failure')
        return await deliver(job)

    calls = 0

    async def tick(_):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise asyncio.CancelledError

    monkeypatch.setattr(gateway, '_deliver_scheduled_message', fail_one)
    monkeypatch.setattr(asyncio, 'sleep', tick)
    with pytest.raises(asyncio.CancelledError):
        await gateway._scheduler_loop()
    assert [row['id'] for row in gateway._store.get_due_scheduled_messages(time.time())] == [bad['id']]
    assert len(gateway._store.get_messages(good['thread_id'])) == 1


@pytest.mark.asyncio
async def test_local_dm_keeps_canonical_thread_and_stable_id_on_retry(gateway, monkeypatch):
    gateway._store.save_dm_thread('conversation', 'did:key:bob', 'Bob', owner_webid='did:key:alice')
    job = seed(gateway, thread='conversation')
    mark = gateway._store.mark_scheduled_sent
    monkeypatch.setattr(gateway._store, 'mark_scheduled_sent', fail_storage)
    with pytest.raises(OSError):
        await gateway._deliver_scheduled_message(job)
    before = gateway._store.get_messages('conversation')
    assert len(before) == 1
    monkeypatch.setattr(gateway._store, 'mark_scheduled_sent', mark)
    assert await gateway._deliver_scheduled_message(job)
    assert gateway._store.get_messages('conversation') == before
    assert not gateway._store.get_due_scheduled_messages(time.time())


@pytest.mark.asyncio
async def test_cancellation_during_delivery_keeps_job_and_cleans_identity(gateway, monkeypatch):
    job = seed(gateway)
    monkeypatch.setattr(gateway, 'process_command', AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        await gateway._deliver_scheduled_message(job)
    assert gateway._store.get_due_scheduled_messages(time.time())
    assert not gateway._client_webids
    assert not gateway._system_ws


@pytest.mark.asyncio
async def test_cert_dm_retry_uses_same_signed_message_id(gateway, monkeypatch):
    job = seed(gateway, thread=cert_thread(gateway))
    send = Mock()
    monkeypatch.setattr('proxion_messenger_core.messaging.send', send)
    mark = gateway._store.mark_scheduled_sent
    monkeypatch.setattr(gateway._store, 'mark_scheduled_sent', fail_storage)
    with pytest.raises(OSError):
        await gateway._deliver_scheduled_message(job)
    first = send.call_args.args[0]
    assert first.verify(gateway.agent.identity_pub_bytes)
    monkeypatch.setattr(gateway._store, 'mark_scheduled_sent', mark)
    assert await gateway._deliver_scheduled_message(job)
    second = send.call_args.args[0]
    assert second.verify(gateway.agent.identity_pub_bytes)
    assert first.message_id == second.message_id
    assert not gateway._store.get_due_scheduled_messages(time.time())


@pytest.mark.asyncio
async def test_cert_dm_is_acknowledged_only_after_durable_outbox_handoff(gateway, monkeypatch):
    job = seed(gateway, thread=cert_thread(gateway))
    monkeypatch.setattr('proxion_messenger_core.messaging.send', Mock(side_effect=OSError('pod offline')))
    enqueue = gateway.outbox.enqueue
    monkeypatch.setattr(gateway.outbox, 'enqueue', Mock(side_effect=OSError('disk busy')))
    assert not await gateway._deliver_scheduled_message(job)
    assert gateway._store.get_due_scheduled_messages(time.time())
    monkeypatch.setattr(gateway.outbox, 'enqueue', enqueue)
    assert await gateway._deliver_scheduled_message(job)
    queued = gateway.outbox.get_items()
    assert len(queued) == 1
    assert queued[0].target_cert_id == job['thread_id']
    assert queued[0].message.verify(gateway.agent.identity_pub_bytes)
    assert not gateway._store.get_due_scheduled_messages(time.time())
