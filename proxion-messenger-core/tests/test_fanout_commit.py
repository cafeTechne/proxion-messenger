"""Authenticated commit is a stronger boundary than a socket write."""
import json
from unittest.mock import AsyncMock
import pytest
from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.local_store import LocalStore


def socket():
    ws = AsyncMock()
    ws.__hash__ = lambda self: id(self)
    ws.__eq__ = lambda self, other: self is other
    return ws


def test_commit_receipt_survives_restart_and_rejects_conflicting_retries(tmp_path):
    path = str(tmp_path / 'commit.db')
    store = LocalStore(path)
    payload = {'e2e': True, 'content': 'cipher', 'nonce': 'n'}
    assert store.enqueue_device_fanout('m', 'alice', 'bob', 'device', payload)
    row = store.pending_device_fanout('bob', 'device')[0]
    args = (row['queue_id'], 'alice', 'm', row['envelope_digest'])
    assert not store.acknowledge_device_fanout('foreign', 'device', *args)
    assert not store.acknowledge_device_fanout('bob', 'sibling', *args)
    assert not store.acknowledge_device_fanout('bob', 'device', *args[:-1], '0' * 64)
    assert store.acknowledge_device_fanout('bob', 'device', *args)
    assert store.acknowledge_device_fanout('bob', 'device', *args)  # harmless lost/replayed ack
    store.close()
    store = LocalStore(path)
    try:
        assert not store.pending_device_fanout('bob', 'device')
        assert store.enqueue_device_fanout('m', 'alice', 'bob', 'device', payload)
        assert not store.enqueue_device_fanout('m', 'alice', 'bob', 'device', {**payload, 'content': 'changed'})
        assert store.device_fanout_committed('bob', 'device', 'alice', 'm', payload)
        store.delete_account_data('bob')
        assert not store.device_fanout_committed('bob', 'device', 'alice', 'm', payload)
    finally:
        store.close()


def test_receipt_capacity_is_reserved_before_acceptance(tmp_path):
    store = LocalStore(str(tmp_path / 'capacity.db'))
    try:
        store.MAX_FANOUT_COMMIT_RECEIPTS = 1
        assert store.enqueue_device_fanout('m', 'alice', 'bob', 'd', {'content': 'a'})
        assert not store.enqueue_device_fanout('next', 'alice', 'bob', 'd', {'content': 'b'})
        row = store.pending_device_fanout('bob', 'd')[0]
        assert store.acknowledge_device_fanout('bob', 'd', row['queue_id'], 'alice', 'm', row['envelope_digest'])
        assert store.enqueue_device_fanout('m', 'alice', 'bob', 'd', {'content': 'a'})
        assert not store.enqueue_device_fanout('next', 'alice', 'bob', 'd', {'content': 'b'})
    finally:
        store.close()


@pytest.mark.asyncio
async def test_live_write_lost_commit_replay_and_foreign_ack(tmp_path):
    gw = ProxionGateway(agent=AgentState.generate(), dm_clients={}, room_memberships={},
                        config=GatewayConfig(db_path=str(tmp_path / 'gateway.db')))
    sender, target, sibling = socket(), socket(), socket()
    for ws, owner, device in [(sender, 'alice', 'alice'), (target, 'bob', 'device'), (sibling, 'bob', 'sibling')]:
        gw.clients.add(ws)
        gw._client_webids[ws] = owner
        gw._webid_sockets.setdefault(owner, set()).add(ws)
        gw._session_device_did[ws] = device
        gw._session_meta[ws] = {'dm_commit_v': 1}
    payload = {'e2e': True, 'content': 'cipher', 'nonce': 'n'}
    queued_before_write = []
    async def observe_write(wire):
        queued_before_write.append(bool(gw._store.pending_device_fanout('bob', 'device')))
    target.send.side_effect = observe_write
    await gw._handle_send_dm_fanout(sender, {'message_id': 'm', 'fanout': [
        {'to_webid': 'bob', 'to_device_id': 'device', 'payload': payload}]})
    assert queued_before_write == [True]
    first = json.loads(target.send.call_args[0][0])
    assert first['receive_commit']['v'] == 1
    assert len(gw._store.pending_device_fanout('bob', 'device')) == 1
    await gw._handle_dm_receive_recover(target, {})
    assert json.loads(target.send.call_args[0][0]) == first
    ack = {**first['receive_commit'], 'from_webid': 'alice', 'message_id': 'm'}
    await gw._handle_dm_receive_commit(sibling, ack)
    assert len(gw._store.pending_device_fanout('bob', 'device')) == 1
    await gw._handle_dm_receive_commit(target, {**ack, 'message_id': 'wrong'})
    assert len(gw._store.pending_device_fanout('bob', 'device')) == 1
    await gw._handle_dm_receive_commit(target, ack)
    assert not gw._store.pending_device_fanout('bob', 'device')
    target.send.reset_mock()
    await gw._handle_send_dm_fanout(sender, {'message_id': 'm', 'fanout': [
        {'to_webid': 'bob', 'to_device_id': 'device', 'payload': payload}]})
    target.send.assert_not_called()
    assert json.loads(sender.send.call_args[0][0])['delivered']
    gw._store.close()


@pytest.mark.asyncio
async def test_registration_preserves_commit_negotiation_and_legacy_downgrade(tmp_path):
    gw = ProxionGateway(agent=AgentState.generate(), dm_clients={}, room_memberships={},
                        config=GatewayConfig(db_path=str(tmp_path / 'register.db')))
    ws = socket()
    ws.remote_address = ('127.0.0.1', 1234)
    gw.clients.add(ws)
    identity = gw._own_gateway_did()
    assert gw._store.enqueue_device_fanout('pending', 'peer', identity, identity, {'content': 'cipher'})
    await gw._handle_register(ws, {'did': identity, 'dm_commit_v': 1})
    assert gw._fanout_commit_capable(ws)
    assert len(gw._store.pending_device_fanout(identity, identity)) == 1
    frames = [json.loads(call.args[0]) for call in ws.send.call_args_list]
    assert any(frame.get('receive_commit', {}).get('v') == 1 for frame in frames)
    await gw._handle_register(ws, {'did': identity})
    assert not gw._fanout_commit_capable(ws)
    assert not gw._store.pending_device_fanout(identity, identity)
    gw._store.close()


def test_schema_66_upgrade_keeps_pending_ciphertext(tmp_path):
    import sqlite3
    path = str(tmp_path / 'upgrade.db')
    old = LocalStore(path)
    assert old.enqueue_device_fanout('m', 'alice', 'bob', 'device', {'content': 'original'})
    before = old.pending_device_fanout('bob', 'device')[0]
    old.close()
    with sqlite3.connect(path) as conn:
        conn.execute('DROP TABLE committed_device_fanout')
        conn.execute('UPDATE schema_version SET version=66')
    upgraded = LocalStore(path)
    try:
        assert upgraded.pending_device_fanout('bob', 'device') == [before]
        assert upgraded.acknowledge_device_fanout('bob', 'device', before['queue_id'], 'alice', 'm', before['envelope_digest'])
    finally:
        upgraded.close()


@pytest.mark.asyncio
async def test_authenticated_register_keeps_commit_capability(tmp_path, monkeypatch):
    import base64
    from proxion_messenger_core.didkey import pub_key_to_did
    monkeypatch.setenv('PROXION_REQUIRE_AUTH', '1')
    gw = ProxionGateway(agent=AgentState.generate(), dm_clients={}, room_memberships={},
                        config=GatewayConfig(db_path=str(tmp_path / 'auth.db')))
    client = AgentState.generate()
    ws = socket()
    ws.remote_address = ('127.0.0.1', 1234)
    gw.clients.add(ws)
    identity = pub_key_to_did(client.identity_pub_bytes)
    await gw._handle_register(ws, {'did': identity, 'dm_commit_v': 1})
    nonce = gw._pending_auth[ws]['nonce']
    signature = base64.urlsafe_b64encode(client.identity_key.sign(nonce.encode())).rstrip(b'=').decode()
    await gw._handle_auth_response(ws, {'signature': signature})
    assert gw._client_webids.get(ws) == identity
    assert gw._fanout_commit_capable(ws)
    gw._store.close()
