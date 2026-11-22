"""Native media custody is durable, exact, bounded and authenticated."""
import base64
import asyncio
import copy
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock

import pytest
from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.local_store import LocalStore
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.relay import verify_relay_envelope


def payload(raw=b'opaque ciphertext bytes'):
    return {'e2e': True, 'e2e_v': 1, 'content': 'Y2lwaGVy', 'nonce': 'A' * 16,
            'msg_num': 0, 'pn': 0, 'ratchet_pub': 'A' * 43,
            'file': {'filename': 'file', 'mime_type': 'application/octet-stream',
                     'size': len(raw), 'data_b64': base64.b64encode(raw).decode()}}


def accept(store, message='m', sender='alice', peer='bob', **kwargs):
    return store.accept_native_media(sender, message, peer, 'thread', 'A' * 43,
                                     kwargs.pop('body', payload()), **kwargs)


@pytest.fixture
def store(tmp_path):
    db = LocalStore(str(tmp_path / 'media.db'))
    yield db
    db.close()


def test_restart_exact_retry_and_conflicting_payload_or_destination(tmp_path):
    path = str(tmp_path / 'restart.db')
    db = LocalStore(path)
    first = accept(db, display_name='Before')
    db.close()
    db = LocalStore(path)
    try:
        assert accept(db, display_name='After') == first
        changed = payload(); changed['msg_num'] = 1
        assert not accept(db, body=changed)
        assert not accept(db, peer='replacement')
        assert db.pending_native_media('bob') == [first]
        assert first['event']['message_id'] == 'm'
        assert first['event']['from_display_name'] == 'Before'
    finally:
        db.close()


def test_concurrent_admission_cap_never_evicts_and_retry_still_fits(store):
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(lambda index: accept(store, str(index)), range(12)))
    assert sum(bool(row) for row in rows) == 10
    before = store.pending_native_media('bob')
    store.MAX_NATIVE_MEDIA_BYTES = 1
    assert accept(store, before[0]['message_id']) == before[0]
    assert not accept(store, 'extra')
    assert store.pending_native_media('bob') == before


def test_commit_is_scoped_idempotent_and_preserves_conflict_evidence(store):
    row = accept(store)
    args = (row['queue_id'], 'alice', 'm', row['envelope_digest'])
    assert not store.commit_native_media('foreign', *args)
    assert not store.commit_native_media('bob', *args[:-1], 'b' * 64)
    assert not store.commit_native_media('bob', True, *args[1:])
    assert store.commit_native_media('bob', *args)
    assert store.commit_native_media('bob', *args)
    assert store.pending_native_media('bob') == []
    retried = accept(store)
    assert retried['committed'] and retried['event'] is None
    assert not accept(store, body=payload(b'changed ciphertext bytes'))
    with store._conn() as conn:
        assert conn.execute('SELECT byte_count,event_json FROM native_media').fetchone()[0] == 0


def test_block_and_device_revocation_remove_bytes_without_claiming_commit(store):
    accept(store)
    accept(store, 'other', sender='other')
    store.set_block('bob', 'alice', True)
    assert not accept(store)
    assert [r['message_id'] for r in store.pending_native_media('bob')] == ['other']
    assert store.get_native_media('alice', 'm')['status'] == 'blocked'
    store.set_block('bob', 'alice', False)
    assert not accept(store)  # old key/id is never silently reauthored
    store.mark_revoked('device:bob', 'bob')
    assert not store.pending_native_media('bob')
    assert not accept(store, 'new')


def test_account_purge_and_reserved_receipt_capacity(store):
    store.MAX_NATIVE_MEDIA_RECEIPTS = 1
    row = accept(store)
    assert not accept(store, 'extra')
    assert store.commit_native_media('bob', row['queue_id'], 'alice', 'm', row['envelope_digest'])
    assert accept(store)['committed']
    assert not accept(store, 'extra')  # conflict receipt must not be evicted
    assert store.delete_account_data('alice')['native_media'] == 1
    assert accept(store, 'fresh')


def test_store_rejects_spoofed_fields_bad_ciphertext_and_oversize(store):
    assert not accept(store, body={**payload(), 'from_webid': 'victim'})
    for raw in (b'', b'x' * 16, b'x' * (512 * 1024 + 1)):
        assert not accept(store, body=payload(raw))
    bad = payload(); bad['file']['size'] += 1
    assert not accept(store, body=bad)
    assert not accept(store, message='../unsafe')
    assert store.pending_native_media('bob') == []


def test_schema_67_upgrade_preserves_fanout(tmp_path):
    path = str(tmp_path / 'upgrade.db')
    old = LocalStore(path)
    assert old.enqueue_device_fanout('existing', 'a', 'b', 'd', {'content': 'cipher'})
    old.close()
    with sqlite3.connect(path) as conn:
        conn.execute('DROP TABLE native_media')
        conn.execute('UPDATE schema_version SET version=67')
    upgraded = LocalStore(path)
    try:
        assert accept(upgraded)
        assert upgraded.pending_device_fanout('b', 'd')[0]['payload']['content'] == 'cipher'
        with upgraded._conn() as conn:
            assert conn.execute('SELECT version FROM schema_version').fetchone()[0] == 68
    finally:
        upgraded.close()


def gateway(tmp_path, name):
    return ProxionGateway(agent=AgentState.generate(), dm_clients={}, room_memberships={},
                          config=GatewayConfig(db_path=str(tmp_path / (name + '.db'))))


def socket(gw, owner, *, modern=True, device=None):
    ws = AsyncMock()
    ws.__hash__ = lambda self: id(self)
    ws.__eq__ = lambda self, other: self is other
    gw.clients.add(ws); gw._client_webids[ws] = owner
    gw._webid_sockets.setdefault(owner, set()).add(ws)
    gw._session_meta[ws] = {'native_media_v': int(modern)}
    if device:
        gw._session_device_did[ws] = device
    return ws


@pytest.mark.asyncio
async def test_accept_before_write_and_no_legacy_or_sibling_drain(tmp_path):
    gw = gateway(tmp_path, 'local')
    alice, bob = 'did:key:alice', 'did:key:bob'
    sender, receiver = socket(gw, alice), socket(gw, bob, modern=False)
    sibling = socket(gw, bob, device='did:key:linked')
    gw._store.save_dm_thread('thread', bob, owner_webid=alice)
    p = payload()
    data = {'native_media_v': 1, 'message_id': 'stable', 'cert_id': 'thread',
            'media_digest': 'A' * 43, 'data_b64': p['file']['data_b64']}
    before_write = []
    async def observe(wire):
        with sqlite3.connect(str(gw._store.db_path)) as external:
            before_write.append(bool(external.execute('SELECT 1 FROM native_media WHERE from_webid=? AND message_id=?', (alice, 'stable')).fetchone()))
    sender.send.side_effect = observe
    await gw.process_command(sender, {**data, 'cmd': 'send_file', 'filename': 'file',
        'mime_type': 'application/octet-stream', 'from_webid': 'did:key:spoofed',
        **{k: v for k, v in p.items() if k != 'file'}})
    assert before_write == [True]
    await asyncio.gather(*list(gw.__dict__.get("_native_media_tasks", {}).values()))
    receiver.send.assert_not_called(); sibling.send.assert_not_called()
    gw._session_meta[receiver]['native_media_v'] = 1
    await gw._handle_native_media_recover(receiver, {})
    await asyncio.gather(*list(gw.__dict__.get("_native_media_tasks", {}).values()))
    event = json.loads(receiver.send.call_args[0][0])
    assert event['message_id'] == 'stable'
    ack = {**event['native_commit'], 'from_webid': alice, 'message_id': 'stable', 'receive_id': 'a' * 64}
    await gw._handle_native_media_commit(sibling, ack)
    assert gw._store.pending_native_media(bob)
    await gw._handle_native_media_commit(receiver, ack)
    assert not gw._store.pending_native_media(bob)
    assert json.loads(receiver.send.call_args[0][0])['type'] == 'native_media_committed'
    await gw._handle_native_media_commit(receiver, ack)
    assert json.loads(receiver.send.call_args[0][0])['type'] == 'native_media_committed'
    gw._store.close()


@pytest.mark.asyncio
async def test_cross_gateway_custody_requires_signed_exact_request(tmp_path, monkeypatch):
    source, target = gateway(tmp_path, 'source'), gateway(tmp_path, 'target')
    source_did, target_did = source._own_gateway_did(), target._own_gateway_did()
    target._store.save_relationship({'certificate_id': 'contact', 'subject': 'ab' * 32,
                                    'created_at': 0, 'expires_at': 2**31-1}, peer_did=source_did, owner_webid='did:key:bob')
    row = accept(source._store, peer=target_did)
    responses = []
    async def route(method, url, **kwargs):
        status, body = await target._handle_relay_post(kwargs['body'], client_ip='127.0.0.1')
        responses.append(json.loads(body))
        return int(status.split()[0]), {}, body.encode()
    monkeypatch.setattr('proxion_messenger_core.network.async_safe_request', route)
    assert await source._relay_native_media('https://peer.example', row)
    assert verify_relay_envelope(responses[-1])
    assert target._store.pending_native_media('did:key:bob')[0]['incoming']
    assert await source._relay_native_media('https://peer.example', row)  # lost response: exact payload reused
    assert len(target._store.pending_native_media('did:key:bob')) == 1
    async def legacy(*args, **kwargs):
        return 200, {}, b'{"status":"received"}'
    monkeypatch.setattr('proxion_messenger_core.network.async_safe_request', legacy)
    assert not await source._relay_native_media('https://legacy.example', row)
    changed = copy.deepcopy(row); changed['event']['content'] = 'Y2hhbmdlZA'
    monkeypatch.setattr('proxion_messenger_core.network.async_safe_request', route)
    assert not await source._relay_native_media('https://peer.example', changed)
    source._store.close(); target._store.close()


@pytest.mark.asyncio
async def test_relay_limit_and_unsigned_or_foreign_proofs_never_release_source(tmp_path, monkeypatch):
    from proxion_messenger_core.relay import sign_relay_envelope
    source, peer, stranger = gateway(tmp_path, 'source'), gateway(tmp_path, 'peer'), gateway(tmp_path, 'stranger')
    row = accept(source._store, peer=peer._own_gateway_did())
    calls = []
    async def forged(method, url, **kwargs):
        request = json.loads(kwargs['body'])
        calls.append(request)
        import hashlib
        proof = {'v': 1, 'content_type': 'native_media_custody', 'from_webid': peer._own_gateway_did(),
                 'to_webid': source._own_gateway_did(), 'message_id': row['message_id'],
                 'media_digest': row['media_digest'], 'request_digest': hashlib.sha256(kwargs['body']).hexdigest(),
                 'relay_sig_did': stranger._own_gateway_did(), 'relay_ts': request['relay_ts'], 'relay_nonce': 'proof'}
        proof['signature'] = sign_relay_envelope(stranger.agent.identity_key, proof)
        return 200, {}, json.dumps(proof).encode()
    monkeypatch.setattr('proxion_messenger_core.network.async_safe_request', forged)
    assert not await source._relay_native_media('https://peer.example', row)
    oversized = copy.deepcopy(row); oversized['event']['file'] = payload(b'x' * (128 * 1024))['file']
    assert not await source._relay_native_media('https://peer.example', oversized)
    assert len(calls) == 1  # existing relay body cap applied before network I/O
    assert source._store.pending_native_media('alice', outgoing=True) == [row]
    source._store.close(); peer._store.close(); stranger._store.close()


@pytest.mark.asyncio
async def test_policy_change_after_acceptance_prevents_stale_delivery(tmp_path):
    gw = gateway(tmp_path, 'policy')
    sender, receiver = 'did:key:alice', 'did:key:bob'
    ws = socket(gw, receiver)
    row = accept(gw._store, sender=sender, peer=receiver)
    gw._store.set_block(receiver, sender, True)
    await gw._deliver_native_media(row)  # caller retained a stale pre-block copy
    ws.send.assert_not_called()
    assert gw._store.get_native_media(sender, 'm')['status'] == 'blocked'
    gw._store.close()


@pytest.mark.asyncio
async def test_unknown_version_cannot_fall_back_to_legacy_echo(tmp_path):
    gw = gateway(tmp_path, 'version')
    ws = socket(gw, 'did:key:alice')
    for version in (True, 2, None):
        p = payload()
        await gw._handle_send_file(ws, {'cert_id': 'thread', 'native_media_v': version,
            'filename': 'file', 'mime_type': 'application/octet-stream',
            'data_b64': p['file']['data_b64'], **{k: v for k, v in p.items() if k != 'file'}})
        assert json.loads(ws.send.call_args[0][0])['message'] == 'native_media_unsupported_version'
    assert gw._store.pending_native_media('did:key:alice', outgoing=True) == []
    gw._store.close()


def test_later_block_does_not_erase_a_previous_commit_receipt(store):
    row = accept(store)
    args = (row['queue_id'], 'alice', 'm', row['envelope_digest'])
    assert store.commit_native_media('bob', *args)
    store.set_block('bob', 'alice', True)
    assert store.get_native_media('alice', 'm')['status'] == 'committed'
    assert store.commit_native_media('bob', *args)


@pytest.mark.asyncio
async def test_current_browser_registration_does_not_activate_staged_protocol(tmp_path):
    gw = gateway(tmp_path, 'legacy-default')
    ws = socket(gw, gw._own_gateway_did(), modern=False)
    ws.remote_address = ('127.0.0.1', 1234)
    await gw._handle_register(ws, {'did': gw._own_gateway_did(), 'dm_commit_v': 1})
    frames = [json.loads(call.args[0]) for call in ws.send.call_args_list]
    registered = next(frame for frame in frames if frame.get('type') == 'registered')
    assert 'native_media_v' not in registered
    assert not gw._native_capable(ws)
    ws.send.reset_mock()
    await gw._handle_native_media_recover(ws, {})
    ws.send.assert_not_called()
    gw._store.close()


@pytest.mark.asyncio
async def test_storage_failure_emits_no_acceptance_or_delivery(tmp_path, monkeypatch):
    gw = gateway(tmp_path, 'quota')
    owner, peer = 'did:key:alice', 'did:key:bob'
    source, target = socket(gw, owner), socket(gw, peer)
    gw._store.save_dm_thread('thread', peer, owner_webid=owner)
    def fail(*args, **kwargs):
        raise sqlite3.OperationalError('disk full')
    monkeypatch.setattr(gw._store, 'accept_native_media', fail)
    p = payload()
    with pytest.raises(sqlite3.OperationalError):
        await gw._handle_native_inline(source, {'cert_id': 'thread', 'message_id': 'm',
            'media_digest': 'A' * 43, 'data_b64': p['file']['data_b64']},
            {k: v for k, v in p.items() if k != 'file'}, b'opaque ciphertext bytes')
    source.send.assert_not_called(); target.send.assert_not_called()
    gw._store.close()


@pytest.mark.asyncio
async def test_relationship_owner_change_cannot_retarget_an_incoming_file(tmp_path):
    gw = gateway(tmp_path, 'rebound')
    sender, original, replacement = 'did:key:peer', 'did:key:original', 'did:key:replacement'
    contact = {'certificate_id': 'contact', 'subject': 'ab' * 32, 'created_at': 0, 'expires_at': 2**31-1}
    gw._store.save_relationship(contact, peer_did=sender, owner_webid=original)
    row = gw._store.accept_native_media(sender, 'm', original, sender, 'A' * 43, payload(), incoming=True)
    old_ws, new_ws = socket(gw, original), socket(gw, replacement)
    gw._store.save_relationship(contact, peer_did=sender, owner_webid=replacement)
    await gw._deliver_native_media(row)
    old_ws.send.assert_not_called(); new_ws.send.assert_not_called()
    assert gw._store.pending_native_media(original)[0]['to_webid'] == original
    assert not gw._store.pending_native_media(replacement)
    gw._store.close()


@pytest.mark.asyncio
async def test_safe_mode_corruption_and_memory_store_cannot_issue_custody(tmp_path, monkeypatch):
    gw = gateway(tmp_path, 'unavailable')
    data = {'from_webid': 'did:key:peer', 'to_webid': gw._own_gateway_did(),
            'message_id': 'm', 'media_digest': 'A' * 43, 'payload': payload()}
    monkeypatch.setenv('PROXION_SAFE_MODE', '1')
    assert (await gw._handle_native_inline_relay(data))[0].startswith('503')
    monkeypatch.delenv('PROXION_SAFE_MODE')
    gw._store._integrity_ok = False
    assert (await gw._handle_native_inline_relay(data))[0].startswith('503')
    gw._store.close()
    gw._store = LocalStore(':memory:')
    assert (await gw._handle_native_inline_relay(data))[0].startswith('503')
    gw._store.close()


@pytest.mark.asyncio
async def test_delivery_attempts_are_globally_bounded_without_releasing_rows(tmp_path, monkeypatch):
    gw = gateway(tmp_path, 'bounded')
    waiting = asyncio.Event()
    async def stalled(row):
        await waiting.wait()
    monkeypatch.setattr(gw, '_deliver_native_media', stalled)
    for i in range(17):
        row = accept(gw._store, str(i), sender='sender-' + str(i))
        gw._schedule_native_media(row)
    assert len(gw._native_media_tasks) == 16
    assert len(gw._store.pending_native_media('bob')) == 10  # read batch bound, not eviction
    with gw._store._conn() as conn:
        assert conn.execute('SELECT COUNT(*) FROM native_media WHERE event_json IS NOT NULL').fetchone()[0] == 17
    waiting.set(); await asyncio.gather(*list(gw._native_media_tasks.values()))
    gw._store.close()


@pytest.mark.asyncio
async def test_shutdown_cancels_attempts_before_closing_custody_store(tmp_path, monkeypatch):
    gw = gateway(tmp_path, 'shutdown')
    row = accept(gw._store)
    started, waiting = asyncio.Event(), asyncio.Event()
    async def stalled(row):
        started.set(); await waiting.wait()
    monkeypatch.setattr(gw, '_deliver_native_media', stalled)
    gw._schedule_native_media(row); await started.wait()
    await gw._stop_native_media()
    assert not gw._native_media_tasks
    assert gw._store.pending_native_media('bob') == [row]
    gw._schedule_native_media(row)
    assert not gw._native_media_tasks
    gw._store.close()


def test_peer_custody_is_not_a_client_commit_and_cannot_downgrade_one(store):
    row = accept(store)
    args = (row['queue_id'], 'alice', 'm', row['envelope_digest'])
    assert store.commit_native_media('bob', *args, forwarded=True)
    handed_off = store.get_native_media('alice', 'm')
    assert handed_off['forwarded'] and not handed_off['committed']
    assert store.commit_native_media('bob', *args)
    assert store.get_native_media('alice', 'm')['committed']
    assert store.commit_native_media('bob', *args, forwarded=True)
    assert store.get_native_media('alice', 'm')['committed']
