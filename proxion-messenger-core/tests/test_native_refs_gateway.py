"""Typed reference custody never drains through legacy fanout or readers."""
import asyncio
import copy
import json

import pytest

from proxion_messenger_core.local_store import LocalStore
from test_native_media_custody import gateway, payload, socket
from test_native_edits import file_parent


def capable(gw, owner, *, device=None, version=1, pub='A' * 43):
    ws = socket(gw, owner, device=device)
    gw._session_meta[ws].update(dm_commit_v=1, native_ref_v=version, native_media_pub=pub)
    gw._store.save_device_e2e_key(owner, device or owner, pub)
    if not device: gw._store.save_x25519_pub(owner, pub)
    return ws


def envelope(*, actor='did:key:alice', peer='did:key:bob', pub='A' * 43):
    body = payload(); body.pop('file')
    return {**body, 'native_ref_v': 1, 'recipient_pub': pub, 'from_device_id': actor,
        'thread_id': 'thread', 'thread_peer': peer, 'x25519_pub': 'A' * 43}


async def drain(gw):
    tasks = list(gw.__dict__.get('_native_media_tasks', {}).values())
    if tasks: await asyncio.gather(*tasks)


@pytest.mark.asyncio
async def test_legacy_or_wrong_key_session_cannot_drain_a_typed_reference(tmp_path):
    gw = gateway(tmp_path, 'typed-device'); owner, actor = 'did:key:bob', 'did:key:alice'
    legacy = capable(gw, owner, version=0)
    body = envelope()
    assert await gw._native_ref_admit(owner, owner, actor, 'reply', body) is False
    legacy.send.assert_not_called()
    row = gw._store.pending_device_fanout(owner, owner)[0]
    ack = {'v': 1, 'native_ref_v': 1, 'queue_id': row['queue_id'], 'from_webid': actor,
        'message_id': 'reply', 'envelope_digest': row['envelope_digest'], 'receive_id': 'a' * 64}
    await gw._handle_dm_receive_commit(legacy, ack)
    await gw._handle_dm_receive_recover(legacy, {})
    legacy.send.assert_not_called(); assert gw._store.pending_device_fanout(owner, owner)
    gw._session_meta[legacy].update(native_ref_v=1, native_media_pub='B' * 43, dm_commit_replay_at=0)
    await gw._handle_dm_receive_recover(legacy, {}); await gw._handle_dm_receive_commit(legacy, ack)
    assert gw._store.pending_device_fanout(owner, owner)
    gw._session_meta[legacy].update(native_media_pub='A' * 43, dm_commit_replay_at=0)
    await gw._handle_dm_receive_recover(legacy, {})
    assert json.loads(legacy.send.call_args.args[0])['payload']['native_ref_v'] == 1
    await gw._handle_dm_receive_commit(legacy, {k: v for k, v in ack.items() if k != 'native_ref_v'})
    assert gw._store.pending_device_fanout(owner, owner)
    await gw._handle_dm_receive_commit(legacy, ack)
    assert gw._store.device_fanout_committed(owner, owner, actor, 'reply', body)
    assert json.loads(legacy.send.call_args.args[0])['native_ref_v'] == 1
    assert await gw._native_ref_admit(owner, owner, actor, 'reply', body) is True
    gw._store.close()


@pytest.mark.asyncio
async def test_full_ack_progress_is_independent_per_device_and_legacy_ingress_cannot_bypass_gate(tmp_path):
    gw = gateway(tmp_path, 'fanout'); actor, peer, linked = 'did:key:alice', 'did:key:bob', 'did:key:linked'
    sender, receiver = capable(gw, actor), capable(gw, peer)
    old_linked = capable(gw, actor, device=linked, version=0)
    gw._store.save_dm_thread('thread', peer, owner_webid=actor)
    body = envelope()
    entries = [{'to_webid': peer, 'to_device_id': peer, 'payload': body},
        {'to_webid': actor, 'to_device_id': linked, 'payload': body}]
    data = {'cmd': 'native_ref_send', 'native_ref_v': 1, 'cert_id': 'thread', 'message_id': 'reply', 'fanout': entries}
    await gw.process_command(sender, data); await drain(gw)
    assert json.loads(sender.send.call_args.args[0])['delivered'] == []
    old_linked.send.assert_not_called()
    assert gw._store.pending_device_fanout(actor, linked)
    row = gw._store.pending_device_fanout(peer, peer)[0]
    await gw._handle_dm_receive_commit(receiver, {'v': 1, 'native_ref_v': 1, 'queue_id': row['queue_id'],
        'from_webid': actor, 'message_id': 'reply', 'envelope_digest': row['envelope_digest'], 'receive_id': 'a' * 64})
    await gw.process_command(sender, data); await drain(gw)
    assert json.loads(sender.send.call_args.args[0])['delivered'] == [{'to_webid': peer, 'to_device_id': peer}]
    receiver.send.reset_mock(); sender.send.reset_mock()
    await gw.process_command(sender, {**data, 'cmd': 'send_dm_fanout', 'message_id': 'bypass'})
    assert not any(row['message_id'] == 'bypass' for row in gw._store.pending_device_fanout(peer, peer))
    receiver.send.assert_not_called()
    gw._store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['foreign recipient', 'foreign actor', 'wrong thread', 'wrong pub', 'extra field', 'missing version'])
async def test_invalid_frames_do_not_allocate_custody(tmp_path, change):
    gw = gateway(tmp_path, 'invalid'); actor, peer = 'did:key:alice', 'did:key:bob'
    ws = capable(gw, actor); capable(gw, peer)
    gw._store.save_dm_thread('thread', peer, owner_webid=actor)
    body = envelope(); entry = {'to_webid': peer, 'to_device_id': peer, 'payload': body}
    data = {'native_ref_v': 1, 'cert_id': 'thread', 'message_id': 'reply', 'fanout': [entry]}
    if change == 'foreign recipient': entry['to_webid'] = 'did:key:foreign'
    elif change == 'foreign actor': body['from_device_id'] = 'did:key:foreign'
    elif change == 'wrong thread': body['thread_id'] = 'other'
    elif change == 'wrong pub': body['recipient_pub'] = 'B' * 43
    elif change == 'extra field': body['reply_to_id'] = 'native:did:key:alice:file'
    else: data['native_ref_v'] = True
    await gw._handle_native_ref_send(ws, data); await drain(gw)
    assert not gw._store.pending_device_fanout(peer, peer)
    gw._store.close()


def paired(tmp_path):
    source, target = gateway(tmp_path, 'source'), gateway(tmp_path, 'target')
    actor, owner = 'did:key:alice', 'did:key:bob'; peer = target._own_gateway_did(); sender = source._own_gateway_did()
    target._store.save_relationship({'certificate_id': 'contact', 'subject': 'ab' * 32,
        'created_at': 0, 'expires_at': 2**31-1}, peer_did=sender, owner_webid=owner)
    source._store.save_dm_thread('thread', peer, owner_webid=actor)
    return source, target, actor, owner, sender, peer


@pytest.mark.asyncio
async def test_signed_remote_custody_restart_loss_and_eventual_client_commit(tmp_path, monkeypatch):
    source, target, actor, owner, sender_did, peer = paired(tmp_path)
    ws, old = capable(source, actor), capable(target, owner, version=0)
    source._resolve_peer_gateway = lambda _: 'https://peer.example'; source._is_owner_account = lambda *args: True
    lost = True
    async def route(method, url, **kwargs):
        nonlocal lost
        status, body = await target._handle_relay_post(kwargs['body'], client_ip='127.0.0.1')
        if lost: lost = False; raise OSError('lost custody response')
        return int(status.split()[0]), {}, body.encode()
    monkeypatch.setattr('proxion_messenger_core.network.async_safe_request', route)
    body = envelope(peer=peer); data = {'native_ref_v': 1, 'cert_id': 'thread', 'message_id': 'reply',
        'fanout': [{'to_webid': peer, 'to_device_id': owner, 'payload': body}]}
    await source._handle_native_ref_send(ws, data); await drain(source)
    assert json.loads(ws.send.call_args.args[0])['delivered'] == []; old.send.assert_not_called()
    before = target._store.pending_device_fanout(owner, owner)[0]
    path = str(target._store.db_path); target._store.close(); target._store = LocalStore(path)
    await source._handle_native_ref_send(ws, data); await drain(source)
    assert target._store.pending_device_fanout(owner, owner)[0] == before
    target._session_meta[old]['native_ref_v'] = 1
    await target._handle_dm_receive_recover(old, {})
    ack = {'v': 1, 'native_ref_v': 1, 'queue_id': before['queue_id'], 'from_webid': sender_did,
        'message_id': 'reply', 'envelope_digest': before['envelope_digest'], 'receive_id': 'a' * 64}
    await target._handle_dm_receive_commit(old, ack)
    await source._handle_native_ref_send(ws, data); await drain(source)
    assert json.loads(ws.send.call_args.args[0])['delivered'] == [{'to_webid': peer, 'to_device_id': owner}]
    changed = copy.deepcopy(data); changed['fanout'][0]['payload']['msg_num'] += 1
    await source._handle_native_ref_send(ws, changed); await drain(source)
    assert json.loads(ws.send.call_args.args[0])['delivered'] == []
    source._store.close(); target._store.close()


@pytest.mark.asyncio
async def test_ref_capability_distinguishes_both_original_authors_and_requires_current_primary(tmp_path, monkeypatch):
    source, target, actor, owner, sender_did, peer = paired(tmp_path)
    ws, recipient = capable(source, actor), capable(target, owner)
    source._resolve_peer_gateway = lambda _: 'https://peer.example'; source._is_owner_account = lambda *args: True
    file_parent(source._store, sender=actor, recipient=peer)
    file_parent(target._store, sender=sender_did, recipient=owner, incoming=True)
    file_parent(source._store, sender=peer, recipient=actor, incoming=True)
    file_parent(target._store, sender=owner, recipient=sender_did)
    async def route(method, url, **kwargs):
        status, body = await target._handle_relay_post(kwargs['body'], client_ip='127.0.0.1')
        return int(status.split()[0]), {}, body.encode()
    monkeypatch.setattr('proxion_messenger_core.network.async_safe_request', route)
    query = {'native_ref_v': 1, 'cert_id': 'thread', 'request_id': 'query', 'expected_pub': 'A' * 43, 'file_id': 'file'}
    for author in ('actor', 'peer'):
        await source._handle_native_ref_capability(ws, {**query, 'author': author}); await drain(source)
        result = json.loads(ws.send.call_args.args[0]); assert result['supported'] and result['primary_device_id'] == owner
    target._session_meta[recipient]['native_ref_v'] = 0
    await source._handle_native_ref_capability(ws, {**query, 'author': 'peer'}); await drain(source)
    assert not json.loads(ws.send.call_args.args[0])['supported']
    source._store.close(); target._store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('version', [None, True, 1, 2])
@pytest.mark.parametrize('linked', [False, True])
async def test_authenticated_registration_keeps_strict_reference_support_for_primary_and_linked(tmp_path, monkeypatch, version, linked):
    import base64
    from proxion_messenger_core.device_cert import issue_device_cert
    from proxion_messenger_core.didkey import pub_key_to_did
    from proxion_messenger_core.persist import AgentState
    monkeypatch.setenv('PROXION_REQUIRE_AUTH', '1')
    gw = gateway(tmp_path, 'register-ref')
    account, client = AgentState.generate(), AgentState.generate()
    if not linked: client = account
    owner, device = pub_key_to_did(account.identity_pub_bytes), pub_key_to_did(client.identity_pub_bytes)
    ws = socket(gw, owner); ws.remote_address = ('127.0.0.1', 1234)
    packet = {'did': device, 'dm_commit_v': 1, 'native_ref_v': version, 'native_action_v': version, 'x25519_pub': 'A' * 43}
    if linked: packet['delegation_cert'] = issue_device_cert(account.identity_key, device)
    await gw._handle_register(ws, packet)
    nonce = gw._pending_auth[ws]['nonce']
    sig = base64.urlsafe_b64encode(client.identity_key.sign(nonce.encode())).rstrip(b'=').decode()
    await gw._handle_auth_response(ws, {'signature': sig})
    enabled = type(version) is int and version == 1
    registered = next(json.loads(call.args[0]) for call in ws.send.call_args_list if json.loads(call.args[0]).get('type') == 'registered')
    assert gw._native_ref_capable(ws) is enabled
    assert registered.get('native_ref_v') == (1 if enabled else None)
    assert gw._native_action_capable(ws) is enabled
    assert registered.get('native_action_v') == (1 if enabled else None)
    assert gw._native_ref_device(owner, device, 'A' * 43)
    gw._store.close()


@pytest.mark.asyncio
async def test_register_drain_retains_legacy_typed_rows_until_upgrade(tmp_path, monkeypatch):
    monkeypatch.setenv('PROXION_REQUIRE_AUTH', '0')
    gw = gateway(tmp_path, 'register-replay'); owner, actor = 'did:key:bob', 'did:key:alice'
    ws = capable(gw, owner, version=0)
    assert gw._store.enqueue_device_fanout('reply', actor, owner, owner, envelope())
    await gw._handle_register(ws, {'did': owner, 'dm_commit_v': 1, 'x25519_pub': 'A' * 43})
    assert not any(json.loads(call.args[0]).get('type') == 'dm_fanout' for call in ws.send.call_args_list)
    assert gw._store.pending_device_fanout(owner, owner)
    ws.send.reset_mock()
    await gw._handle_register(ws, {'did': owner, 'dm_commit_v': 1, 'native_ref_v': 1, 'x25519_pub': 'A' * 43})
    frames = [json.loads(call.args[0]) for call in ws.send.call_args_list if json.loads(call.args[0]).get('type') == 'dm_fanout']
    assert len(frames) == 1 and frames[0]['payload']['native_ref_v'] == 1
    assert gw._store.pending_device_fanout(owner, owner)  # socket write is not a history commit
    gw._store.close()


@pytest.mark.asyncio
async def test_native_action_requires_distinct_device_capability_and_consumption_proof(tmp_path):
    gw = gateway(tmp_path, 'action-device'); owner, actor = 'did:key:bob', 'did:key:alice'
    ws = capable(gw, owner); body = {**envelope(), 'native_action_v': 1}
    assert gw._native_ref_payload(body)
    assert await gw._native_ref_admit(owner, owner, actor, 'delete', body) is False
    ws.send.assert_not_called()
    row = gw._store.pending_device_fanout(owner, owner)[0]
    ack = {'v': 1, 'native_ref_v': 1, 'queue_id': row['queue_id'], 'from_webid': actor,
        'message_id': 'delete', 'envelope_digest': row['envelope_digest'], 'receive_id': 'b' * 64}
    await gw._handle_dm_receive_commit(ws, {**ack, 'native_action_v': 1})
    assert gw._store.pending_device_fanout(owner, owner)
    gw._session_meta[ws].update(native_action_v=1, dm_commit_replay_at=0)
    await gw._handle_dm_receive_recover(ws, {})
    assert json.loads(ws.send.call_args.args[0])['payload']['native_action_v'] == 1
    await gw._handle_dm_receive_commit(ws, ack)
    assert gw._store.pending_device_fanout(owner, owner)
    await gw._handle_dm_receive_commit(ws, {**ack, 'native_action_v': 1})
    assert gw._store.device_fanout_committed(owner, owner, actor, 'delete', body)
    assert json.loads(ws.send.call_args.args[0])['native_action_v'] == 1


@pytest.mark.parametrize('version,enabled', [(1, True), (True, False), (0, False), ('1', False), (2, False)])
@pytest.mark.asyncio
async def test_native_action_capability_is_strict_and_does_not_enable_a_legacy_reference_reader(tmp_path, version, enabled):
    gw = gateway(tmp_path, 'action-version'); owner = 'did:key:bob'
    ws = capable(gw, owner); gw._session_meta[ws]['native_action_v'] = version
    assert gw._native_action_capable(ws) is enabled
    assert gw._native_ref_payload({**envelope(), 'native_action_v': version}) is enabled
    gw._session_meta[ws]['native_ref_v'] = 0
    assert gw._native_action_capable(ws) is False


@pytest.mark.asyncio
async def test_native_action_preflight_does_not_claim_support_from_a_reply_only_primary(tmp_path):
    gw = gateway(tmp_path, 'action-query'); owner, peer = 'did:key:alice', 'did:key:bob'
    source = capable(gw, owner); target = capable(gw, peer)
    gw._session_meta[source]['native_action_v'] = 1
    gw._store.save_dm_thread('thread', peer, owner_webid=owner)
    file_parent(gw._store, sender=owner, recipient=peer)
    query = {'native_ref_v': 1, 'native_action_v': 1, 'cert_id': 'thread', 'request_id': 'query',
        'expected_pub': 'A' * 43, 'file_id': 'file', 'author': 'actor'}
    await gw._handle_native_ref_capability(source, query); await drain(gw)
    result = json.loads(source.send.call_args.args[0]); assert result['supported'] is False and result['native_action_v'] == 1
    gw._session_meta[target]['native_action_v'] = 1
    await gw._handle_native_ref_capability(source, query); await drain(gw)
    assert json.loads(source.send.call_args.args[0])['supported'] is True


@pytest.mark.asyncio
async def test_native_action_capability_and_custody_cross_signed_relay(tmp_path, monkeypatch):
    source, target, actor, owner, sender_did, peer = paired(tmp_path)
    ws, recipient = capable(source, actor), capable(target, owner)
    source._session_meta[ws]['native_action_v'] = 1
    source._resolve_peer_gateway = lambda _: 'https://peer.example'; source._is_owner_account = lambda *args: True
    file_parent(source._store, sender=actor, recipient=peer)
    file_parent(target._store, sender=sender_did, recipient=owner, incoming=True)
    async def route(method, url, **kwargs):
        status, body = await target._handle_relay_post(kwargs['body'], client_ip='127.0.0.1')
        return int(status.split()[0]), {}, body.encode()
    monkeypatch.setattr('proxion_messenger_core.network.async_safe_request', route)
    query = {'native_ref_v': 1, 'native_action_v': 1, 'cert_id': 'thread', 'request_id': 'query',
        'expected_pub': 'A' * 43, 'file_id': 'file', 'author': 'actor'}
    await source._handle_native_ref_capability(ws, query); await drain(source)
    assert json.loads(ws.send.call_args.args[0])['supported'] is False
    target._session_meta[recipient]['native_action_v'] = 1
    await source._handle_native_ref_capability(ws, query); await drain(source)
    result = json.loads(ws.send.call_args.args[0]); assert result['supported'] is True and result['native_action_v'] == 1
    body = {**envelope(actor=actor, peer=peer), 'native_action_v': 1}
    data = {'native_ref_v': 1, 'native_action_v': 1, 'cert_id': 'thread', 'message_id': 'delete',
        'fanout': [{'to_webid': peer, 'to_device_id': owner, 'payload': body}]}
    await source._handle_native_ref_send(ws, data); await drain(source)
    assert json.loads(ws.send.call_args.args[0])['delivered'] == []
    row = target._store.pending_device_fanout(owner, owner)[0]
    assert row['payload']['native_action_v'] == 1
    await target._handle_dm_receive_commit(recipient, {'v': 1, 'native_ref_v': 1, 'native_action_v': 1, 'queue_id': row['queue_id'],
        'from_webid': sender_did, 'message_id': 'delete', 'envelope_digest': row['envelope_digest'], 'receive_id': 'c' * 64})
    await source._handle_native_ref_send(ws, data); await drain(source)
    proof = json.loads(ws.send.call_args.args[0])
    assert proof['native_action_v'] == 1 and proof['delivered'] == [{'to_webid': peer, 'to_device_id': owner}]


@pytest.mark.asyncio
async def test_native_action_marker_alone_cannot_bypass_versioned_ingress(tmp_path):
    gw = gateway(tmp_path, 'action-bypass'); actor, peer = 'did:key:alice', 'did:key:bob'
    source, recipient = capable(gw, actor), capable(gw, peer)
    body = {**envelope(), 'native_action_v': 1}; body.pop('native_ref_v')
    await gw._handle_send_dm_fanout(source, {'message_id': 'bypass', 'fanout': [
        {'to_webid': peer, 'to_device_id': peer, 'payload': body}]})
    assert gw._store.pending_device_fanout(peer, peer) == []
    recipient.send.assert_not_called()
    assert await gw._deliver_commit_fanout([recipient], 'bypass', actor, peer, body) is False
