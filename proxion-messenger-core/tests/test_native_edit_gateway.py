"""Caption negotiation, opaque custody, and exact primary receive commits."""
import asyncio
import json
import sqlite3
import pytest
from proxion_messenger_core.local_store import LocalStore
from test_native_media_custody import gateway, payload, socket
from test_native_edits import edit, file_parent


def capable(gw, owner, *, pub='A' * 43, device=None, version=1):
    ws = socket(gw, owner, device=device)
    gw._session_meta[ws].update(native_edit_v=version, native_media_pub=pub)
    return ws


async def drain(gw):
    tasks = list(gw.__dict__.get('_native_media_tasks', {}).values())
    if tasks: await asyncio.gather(*tasks)


def packet():
    sealed = payload(); sealed.pop('file')
    return {'cmd': 'native_edit_send', 'v': 1, 'cert_id': 'thread', 'message_id': 'edit-1', 'file_id': 'file',
        'native_sequence': 1, 'parent_header_digest': LocalStore._native_header_digest(sealed),
        'media_digest': 'B' * 43, 'recipient_pub': 'A' * 43, 'payload': sealed}


@pytest.mark.asyncio
async def test_admission_precedes_ack_and_cannot_spoof_file_author(tmp_path):
    gw = gateway(tmp_path, 'send'); owner, peer = 'did:key:alice', 'did:key:bob'
    sender = capable(gw, owner); parent = file_parent(gw._store, sender=owner, recipient=peer)
    observed = []
    async def observe(wire):
        with sqlite3.connect(str(gw._store.db_path)) as conn:
            observed.append((json.loads(wire), conn.execute("SELECT COUNT(*) FROM native_media WHERE transport='edit'").fetchone()[0]))
    sender.send.side_effect = observe
    foreign = capable(gw, 'did:key:foreign')
    await gw.process_command(foreign, {**packet(), 'from_webid': owner})
    assert gw._store.get_native_media(owner, 'edit-1') is None
    await gw.process_command(sender, {**packet(), 'from_webid': 'did:key:spoof'})
    assert observed[0][1] == 1 and observed[0][0]['native_edit_v'] == 1 and observed[0][0]['status'] == 'pending'
    assert gw._store.get_native_media(owner, 'file') == parent
    await drain(gw); gw._store.close()


@pytest.mark.asyncio
async def test_no_legacy_linked_or_wrong_key_delivery_or_commit(tmp_path):
    gw = gateway(tmp_path, 'delivery'); owner, peer = 'did:key:alice', 'did:key:bob'
    parent = file_parent(gw._store, sender=owner, recipient=peer); row = edit(gw._store, parent)
    legacy, linked, wrong = socket(gw, peer), capable(gw, peer, device='did:key:linked'), capable(gw, peer, pub='B' * 43)
    await gw._deliver_native_media(row)
    for ws in (legacy, linked, wrong): ws.send.assert_not_called()
    ack = {'v': 1, 'queue_id': row['queue_id'], 'from_webid': owner, 'message_id': row['message_id'],
        'envelope_digest': row['envelope_digest'], 'receive_id': 'a' * 64, 'native_edit_v': 1}
    for ws in (legacy, linked, wrong): await gw._handle_native_media_commit(ws, ack)
    assert not gw._store.get_native_media(owner, row['message_id'])['committed']
    receiver = capable(gw, peer); await gw._deliver_native_media(row)
    event = json.loads(receiver.send.call_args.args[0])
    assert event['type'] == 'native_edit' and event['native_commit']['queue_id'] == row['queue_id']
    assert not gw._store.get_native_media(owner, row['message_id'])['committed']
    await gw._handle_native_media_commit(receiver, {k: v for k, v in ack.items() if k != 'native_edit_v'})
    assert not gw._store.get_native_media(owner, row['message_id'])['committed']
    await gw._handle_native_media_commit(receiver, ack, header_only=True)
    header = gw._store.get_native_media(owner, row['message_id'])
    assert header['header_committed'] and not header['committed'] and header['event']
    assert json.loads(receiver.send.call_args.args[0])['native_edit_v'] == 1
    await gw._handle_native_media_commit(receiver, ack)
    assert gw._store.get_native_media(owner, row['message_id'])['committed']
    assert json.loads(receiver.send.call_args.args[0])['native_edit_v'] == 1
    assert gw._store.get_native_media(owner, 'file') == parent
    gw._store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('version', [None, True, 0, 2])
async def test_send_is_strictly_opted_in(tmp_path, version):
    gw = gateway(tmp_path, 'version'); owner = 'did:key:alice'; ws = capable(gw, owner, version=version)
    file_parent(gw._store, sender=owner, recipient='did:key:bob')
    await gw.process_command(ws, packet())
    assert not gw._store.get_native_media(owner, 'edit-1')
    gw._store.close()


def paired(tmp_path):
    source, target = gateway(tmp_path, 'source'), gateway(tmp_path, 'target')
    alice, bob = 'did:key:alice', 'did:key:bob'; did = source._own_gateway_did()
    target._store.save_relationship({'certificate_id': 'contact', 'subject': 'ab' * 32,
        'created_at': 0, 'expires_at': 2**31-1}, peer_did=did, owner_webid=bob)
    file_parent(source._store, sender=alice, recipient=target._own_gateway_did())
    file_parent(target._store, sender=did, recipient=bob, incoming=True)
    target._store.save_x25519_pub(bob, 'A' * 43)
    return source, target, alice, bob


@pytest.mark.asyncio
async def test_signed_custody_lost_receipt_restart_and_header_full_progress(tmp_path, monkeypatch):
    source, target, alice, bob = paired(tmp_path)
    parent = source._store.get_native_media(alice, 'file'); row = edit(source._store, parent)
    source._resolve_peer_gateway = lambda _: 'https://peer.example'; lost = True
    async def route(method, url, **kwargs):
        nonlocal lost
        status, body = await target._handle_relay_post(kwargs['body'], client_ip='127.0.0.1')
        if lost: lost = False; raise OSError('lost durable custody reply')
        return int(status.split()[0]), {}, body.encode()
    monkeypatch.setattr('proxion_messenger_core.network.async_safe_request', route)
    await source._deliver_native_media(row)
    incoming = target._store.get_native_media(source._own_gateway_did(), row['message_id'])
    assert incoming and not incoming['committed'] and not incoming['header_committed']
    assert source._store.get_native_media(alice, row['message_id'])['event'] == row['event']
    await drain(target)
    path = str(target._store.db_path); target._store.close(); target._store = LocalStore(path)
    await source._deliver_native_media(row)
    forwarded = source._store.get_native_media(alice, row['message_id'])
    assert forwarded['forwarded'] and not forwarded['committed'] and forwarded['event'] is None
    receiver = capable(target, bob)
    ack = {'v': 1, 'native_edit_v': 1, 'queue_id': incoming['queue_id'], 'from_webid': source._own_gateway_did(),
        'message_id': row['message_id'], 'envelope_digest': incoming['envelope_digest'], 'receive_id': 'a' * 64}
    await target._handle_native_media_commit(receiver, ack, header_only=True)
    source._is_owner_account = lambda *args: True
    retry_ws = capable(source, alice)
    request = {**packet(), 'message_id': row['message_id']}
    await source.process_command(retry_ws, request); await drain(source)
    progress = source._store.get_native_media(alice, row['message_id'])
    assert progress['header_committed'] and not progress['committed']
    await target._handle_native_media_commit(receiver, ack); await source._deliver_native_media(progress)
    assert source._store.get_native_media(alice, row['message_id'])['committed']
    assert source._store.get_native_media(alice, 'file') == parent
    await drain(target); source._store.close(); target._store.close()


@pytest.mark.asyncio
async def test_signed_preflight_requires_original_key_and_capable_primary(tmp_path, monkeypatch):
    source, target, alice, bob = paired(tmp_path); sender = capable(source, alice)
    source._resolve_peer_gateway = lambda _: 'https://peer.example'; source._is_owner_account = lambda *args: True
    async def route(method, url, **kwargs):
        status, body = await target._handle_relay_post(kwargs['body'], client_ip='127.0.0.1')
        return int(status.split()[0]), {}, body.encode()
    monkeypatch.setattr('proxion_messenger_core.network.async_safe_request', route)
    query = {'cmd': 'native_edit_capability', 'v': 1, 'cert_id': 'thread', 'request_id': 'request', 'expected_pub': 'A' * 43, 'file_id': 'file'}
    await source.process_command(sender, query); await drain(source)
    assert not json.loads(sender.send.call_args.args[0])['supported']
    receiver = capable(target, bob)
    await source.process_command(sender, query); await drain(source)
    result = json.loads(sender.send.call_args.args[0])
    assert result['supported'] and result['native_sequence'] == 1
    assert result['parent_header_digest'] == source._store.get_native_media(alice, 'file')['header_digest']
    target._session_meta[receiver]['native_media_pub'] = 'B' * 43
    await source.process_command(sender, query); await drain(source)
    assert not json.loads(sender.send.call_args.args[0])['supported']
    target._session_meta[receiver]['native_media_pub'] = 'A' * 43
    await source.process_command(sender, {**query, 'file_id': 'missing'}); await drain(source)
    assert not json.loads(sender.send.call_args.args[0])['supported']
    await source.process_command(sender, {**query, 'expected_pub': 'B' * 43}); await drain(source)
    assert not json.loads(sender.send.call_args.args[0])['supported']
    source._store.close(); target._store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('version', [None, True, 1, 2])
async def test_auth_challenge_preserves_only_strict_edit_opt_in(tmp_path, monkeypatch, version):
    import base64
    from proxion_messenger_core.didkey import pub_key_to_did
    from proxion_messenger_core.persist import AgentState
    monkeypatch.setenv('PROXION_REQUIRE_AUTH', '1')
    gw = gateway(tmp_path, 'auth'); client = AgentState.generate(); owner = pub_key_to_did(client.identity_pub_bytes)
    ws = socket(gw, owner, modern=False); ws.remote_address = ('127.0.0.1', 1234)
    await gw._handle_register(ws, {'did': owner, 'native_media_v': 1, 'native_edit_v': version, 'x25519_pub': 'A' * 43})
    nonce = gw._pending_auth[ws]['nonce']
    sig = base64.urlsafe_b64encode(client.identity_key.sign(nonce.encode())).rstrip(b'=').decode()
    await gw._handle_auth_response(ws, {'signature': sig})
    registered = next(json.loads(call.args[0]) for call in ws.send.call_args_list
                      if json.loads(call.args[0]).get('type') == 'registered')
    enabled = type(version) is int and version == 1
    assert gw._native_edits_capable(ws) is enabled
    assert registered.get('native_edit_v') == (1 if enabled else None)
    gw._store.close()


@pytest.mark.asyncio
async def test_signed_send_refuses_forged_author_changed_parent_and_changed_retry(tmp_path, monkeypatch):
    source, target, alice, bob = paired(tmp_path)
    parent = source._store.get_native_media(alice, 'file'); row = edit(source._store, parent)
    source._resolve_peer_gateway = lambda _: 'https://peer.example'
    from proxion_messenger_core.relay import sign_relay_envelope
    mode = 'forged'
    async def route(method, url, **kwargs):
        data = json.loads(kwargs['body'])
        if mode == 'forged': data['from_webid'] = 'did:key:victim'
        if mode == 'parent':
            data['parent_header_digest'] = 'f' * 64
            data['signature'] = sign_relay_envelope(source.agent.identity_key, data)
        if mode == 'retry':
            data['payload']['msg_num'] += 1
            data['header_digest'] = target._store._native_header_digest(data['payload'])
            data['signature'] = sign_relay_envelope(source.agent.identity_key, data)
        status, body = await target._handle_relay_post(json.dumps(data).encode(), client_ip='127.0.0.1')
        return int(status.split()[0]), {}, body.encode()
    monkeypatch.setattr('proxion_messenger_core.network.async_safe_request', route)
    for mode in ('forged', 'parent'):
        await source._deliver_native_media(row)
        assert not target._store.get_native_media(source._own_gateway_did(), row['message_id'])
        assert source._store.get_native_media(alice, row['message_id'])['event']
    mode = 'valid'; await source._deliver_native_media(row)
    original = target._store.get_native_media(source._own_gateway_did(), row['message_id'])
    assert original
    mode = 'retry'; await source._deliver_native_edit(row)
    assert target._store.get_native_media(source._own_gateway_did(), row['message_id']) == original
    await drain(target); source._store.close(); target._store.close()


@pytest.mark.asyncio
async def test_policy_change_and_recovery_do_not_downgrade_an_action_to_file(tmp_path, monkeypatch):
    gw = gateway(tmp_path, 'policy'); owner, peer = 'did:key:alice', 'did:key:bob'
    ws = capable(gw, owner); parent = file_parent(gw._store, sender=owner, recipient=peer)
    row = edit(gw._store, parent)
    await gw._handle_native_media_recover(ws, {'message_ids': [row['message_id']]})
    assert json.loads(ws.send.call_args.args[0])['native_edit_v'] == 1
    gw._session_meta[ws]['native_replay_at'] = 0
    await gw._handle_native_media_recover(ws, {'message_ids': ['missing-edit'], 'native_edit_v': 1})
    assert json.loads(ws.send.call_args.args[0]) == {'type': 'native_media_status', 'v': 1, 'message_id': 'missing-edit',
        'status': 'missing', 'native_edit_v': 1}
    gw._store.set_block(peer, owner, True)
    await gw._deliver_native_media(row)
    assert gw._store.get_native_media(owner, row['message_id'])['status'] == 'blocked'
    ws.send.reset_mock(); monkeypatch.setenv('PROXION_SAFE_MODE', '1')
    await gw._handle_native_edit_send(ws, packet())
    ws.send.assert_not_called()
    await drain(gw); gw._store.close()


@pytest.mark.asyncio
async def test_native_preflight_resolves_owned_fanout_only_contact_without_writing_a_thread(tmp_path):
    gw = gateway(tmp_path, 'fanout-contact'); alice, bob = 'did:key:alice', 'did:key:bob'
    caller, receiver = capable(gw, alice), capable(gw, bob)
    cert = {'certificate_id': 'contact-only', 'subject': 'ab' * 32, 'created_at': 0, 'expires_at': 2**31-1}
    gw._store.save_relationship(cert, peer_did=bob, owner_webid=alice)
    gw._store.save_x25519_pub(bob, 'A' * 43)
    assert not gw._store.get_dm_threads(owner_webid=alice)
    query = {'request_id': 'probe', 'cert_id': 'contact-only', 'expected_pub': 'A' * 43}
    await gw._handle_native_media_capability(caller, query); await drain(gw)
    assert json.loads(caller.send.call_args.args[0])['supported']
    assert not gw._store.get_dm_threads(owner_webid=alice)
    foreign = capable(gw, 'did:key:foreign')
    await gw._handle_native_media_capability(foreign, query)
    foreign.send.assert_not_called()
    body = payload(); sealed = {k: v for k, v in body.items() if k != 'file'}
    await gw._handle_native_inline(caller, {'cert_id': 'contact-only', 'message_id': 'new-file', 'media_digest': 'B' * 43,
        'data_b64': body['file']['data_b64']}, sealed, b'opaque ciphertext bytes')
    await drain(gw)
    assert gw._store.get_dm_threads(owner_webid=alice)[0]['thread_id'] == 'contact-only'
    assert gw._store.get_native_media(alice, 'new-file')
    gw._store.close()


@pytest.mark.parametrize('policy', ['foreign', 'ownerless', 'revoked', 'expired'])
def test_native_contact_resolution_rejects_unowned_or_dead_certificates(tmp_path, policy):
    gw = gateway(tmp_path, 'invalid-contact'); owner = 'did:key:alice'
    cert = {'certificate_id': 'contact', 'subject': 'ab' * 32, 'created_at': 0, 'expires_at': 1 if policy == 'expired' else 2**31-1}
    gw._store.save_relationship(cert, peer_did='did:key:bob', owner_webid='' if policy == 'ownerless' else 'did:key:foreign' if policy == 'foreign' else owner)
    if policy == 'revoked': gw._store.mark_revoked('contact', owner)
    assert gw._native_dm_thread(owner, 'contact') is None
    assert not gw._store.get_dm_threads(owner_webid=owner)
    gw._store.close()
