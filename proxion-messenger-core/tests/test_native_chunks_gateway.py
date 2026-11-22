"""Staged native chunk acknowledgements bind exact durable custody and identity."""
import base64
import json
import sqlite3
import time

import pytest

from proxion_messenger_core.local_store import LocalStore
from proxion_messenger_core.relay import sign_relay_envelope
from proxion_messenger_core._gateway_http import _relay_body_is_file_chunk
from test_native_media_custody import gateway, socket
from test_native_media_parts import begin, manifest, put, CHUNK


def paired(tmp_path):
    source, target = gateway(tmp_path, 'source'), gateway(tmp_path, 'target')
    source_did = source._own_gateway_did()
    target._store.save_relationship({'certificate_id': 'relationship', 'subject': 'ab' * 32,
        'created_at': 0, 'expires_at': 2**31-1}, peer_did=source_did, owner_webid='did:key:bob')
    return source, target


@pytest.mark.asyncio
async def test_socket_upload_acks_only_committed_parts_and_never_accepts_another_senders_file(tmp_path):
    gw = gateway(tmp_path, 'upload'); owner = 'did:key:alice'
    ws = socket(gw, owner); gw._session_meta[ws]['native_chunks_v'] = 1
    gw._store.save_dm_thread('thread', 'did:key:bob', owner_webid=owner)
    data = {'v': 1, 'message_id': 'file', 'cert_id': 'thread', 'media_digest': 'A' * 43, 'manifest': manifest()}
    seen = []
    async def observe(wire):
        frame = json.loads(wire)
        with sqlite3.connect(str(gw._store.db_path)) as external:
            seen.append((frame, external.execute('SELECT COUNT(*) FROM native_media_parts').fetchone()[0]))
    ws.send.side_effect = observe
    await gw.process_command(ws, {'cmd': 'native_media_begin', 'from_webid': 'did:key:spoofed', **data})
    row = gw._store.get_native_media(owner, 'file')
    assert row and not row['custody_complete']
    assert seen[0][0]['type'] == 'native_media_parts' and seen[0][1] == 0
    assert seen[0][0]['missing'] == [0] and not seen[0][0]['custody_complete']
    packet = {'cmd': 'native_media_part', 'v': 1, 'message_id': 'file', 'manifest_digest': row['manifest_digest'],
        'seq': 0, 'data_b64': base64.b64encode(b'opaque ciphertext bytes').decode()}
    foreign = socket(gw, 'did:key:foreign'); gw._session_meta[foreign]['native_chunks_v'] = 1
    await gw.process_command(foreign, packet)
    assert not gw._store.get_native_media(owner, 'file')['custody_complete']
    await gw.process_command(ws, packet)
    assert seen[-1][0]['custody_complete'] and seen[-1][1] == 1
    assert not seen[-1][0]['header_committed'] and not seen[-1][0]['committed']
    await gw._stop_native_media(); gw._store.close()


@pytest.mark.asyncio
async def test_upload_is_explicitly_opted_in_and_storage_safety_gated(tmp_path, monkeypatch):
    gw = gateway(tmp_path, 'gates'); ws = socket(gw, 'did:key:alice')
    gw._store.save_dm_thread('thread', 'did:key:bob', owner_webid='did:key:alice')
    data = {'v': 1, 'message_id': 'file', 'cert_id': 'thread', 'media_digest': 'A' * 43, 'manifest': manifest()}
    for version in (None, True, 2):
        gw._session_meta[ws]['native_chunks_v'] = version
        await gw._handle_native_media_begin(ws, data)
        assert not gw._store.get_native_media('did:key:alice', 'file')
    gw._session_meta[ws]['native_chunks_v'] = 1
    monkeypatch.setenv('PROXION_SAFE_MODE', '1'); await gw._handle_native_media_begin(ws, data)
    assert not gw._store.get_native_media('did:key:alice', 'file')
    monkeypatch.delenv('PROXION_SAFE_MODE')
    linked = socket(gw, 'did:key:alice', device='did:key:linked'); gw._session_meta[linked]['native_chunks_v'] = 1
    await gw._handle_native_media_begin(linked, data)
    assert not gw._store.get_native_media('did:key:alice', 'file')
    gw._store.close()


@pytest.mark.asyncio
async def test_part_rate_refusal_names_the_original_upload_without_a_legacy_ack(tmp_path):
    gw = gateway(tmp_path, 'rate'); ws = socket(gw, 'did:key:alice'); gw._session_meta[ws]['native_chunks_v'] = 1
    gw._rate_file_counters[ws] = [gw._FILE_CHUNK_RATE_LIMIT, time.monotonic()]
    await gw.process_command(ws, {'cmd': 'native_media_part', 'v': 1, 'message_id': 'file', 'seq': 7})
    assert json.loads(ws.send.call_args.args[0]) == {'type': 'error', 'code': 'E_RATE', 'message': 'rate_limited',
        'cmd': 'native_media_part', 'message_id': 'file', 'seq': 7}
    gw._store.close()


@pytest.mark.asyncio
async def test_signed_partial_receipt_loss_and_target_restart_resume_only_missing_parts(tmp_path, monkeypatch):
    source, target = paired(tmp_path)
    raw = b'A' * CHUNK + b'B' * CHUNK + b'C' * 17
    row = begin(source._store, raw, recipient=target._own_gateway_did())
    for seq in range(3): assert put(source._store, row, seq, raw[seq * CHUNK:(seq + 1) * CHUNK])
    row = source._store.get_native_media('alice', 'file')
    sent = []; lose = True
    async def route(method, url, **kwargs):
        nonlocal lose
        data = json.loads(kwargs['body']); assert len(kwargs['body']) <= 131072
        status, body = await target._handle_relay_post(kwargs['body'], client_ip='127.0.0.1')
        if data['operation'] == 'part':
            sent.append(data['seq'])
            with sqlite3.connect(str(target._store.db_path)) as external:
                assert external.execute('SELECT COUNT(*) FROM native_media_parts WHERE seq=?', (data['seq'],)).fetchone()[0] == 1
            if lose:
                lose = False; raise OSError('lost durable part receipt')
        return int(status.split()[0]), {}, body.encode()
    monkeypatch.setattr('proxion_messenger_core.network.async_safe_request', route)
    assert await source._relay_native_chunks('https://peer.example', row) is None
    incoming = target._store.get_native_media(source._own_gateway_did(), 'file')
    assert incoming and not incoming['custody_complete'] and sent == [0]
    path = str(target._store.db_path); target._store.close(); target._store = LocalStore(path)
    proof = await source._relay_native_chunks('https://peer.example', row)
    assert proof['custody_complete'] and not proof['header_committed'] and not proof['committed']
    assert sent == [0, 1, 2]
    assert source._store.get_native_media_part('alice', 'file', row['manifest_digest'], 0)
    assert source._store.commit_native_media(target._own_gateway_did(), row['queue_id'], 'alice', 'file', row['envelope_digest'], forwarded=True)
    assert source._store.get_native_media_part('alice', 'file', row['manifest_digest'], 0) is None
    incoming = target._store.get_native_media(source._own_gateway_did(), 'file')
    assert incoming['custody_complete'] and incoming['recipient_pub'] == 'A' * 43
    receiver = socket(target, 'did:key:bob'); target._session_meta[receiver].update(native_chunks_v=1, native_media_pub='B' * 43)
    ack = {'v': 1, 'queue_id': incoming['queue_id'], 'from_webid': source._own_gateway_did(),
        'message_id': 'file', 'envelope_digest': incoming['envelope_digest'], 'receive_id': 'a' * 64}
    await target._handle_native_media_commit(receiver, ack, header_only=True)
    assert not target._store.get_native_media(source._own_gateway_did(), 'file')['header_committed']
    target._session_meta[receiver]['native_media_pub'] = 'A' * 43
    await target._handle_native_media_commit(receiver, ack, header_only=True)
    forwarded = source._store.get_native_media('alice', 'file')
    progress = await source._query_native_receipt('https://peer.example', forwarded)
    assert progress['header_committed'] and not progress['committed']
    await target._handle_native_media_commit(receiver, ack)
    progress = await source._query_native_receipt('https://peer.example', forwarded)
    assert progress['header_committed'] and progress['committed']
    source._store.close(); target._store.close()


@pytest.mark.asyncio
async def test_declared_manifest_conflict_and_wrong_owner_never_reserve_target_bytes(tmp_path, monkeypatch):
    source, target = paired(tmp_path)
    row = begin(source._store, recipient=target._own_gateway_did())
    request = {'content_type': 'native_media_chunks', 'native_chunks_v': 1, 'operation': 'begin', 'message_id': 'file',
        'media_digest': row['media_digest'], 'header_digest': row['header_digest'], 'manifest_digest': 'f' * 64,
        'manifest': manifest()}
    async def route(method, url, **kwargs):
        status, body = await target._handle_relay_post(kwargs['body'], client_ip='127.0.0.1')
        return int(status.split()[0]), {}, body.encode()
    monkeypatch.setattr('proxion_messenger_core.network.async_safe_request', route)
    assert await source._native_exchange('https://peer.example', target._own_gateway_did(), request, 'native_media_chunk_receipt') is None
    assert not target._store.get_native_media(source._own_gateway_did(), 'file')
    request['manifest_digest'] = row['manifest_digest']; request['after'] = True
    assert await source._native_exchange('https://peer.example', target._own_gateway_did(), request, 'native_media_chunk_receipt') is None
    assert not target._store.get_native_media(source._own_gateway_did(), 'file')
    request.pop('after')
    proof = await source._native_exchange('https://peer.example', target._own_gateway_did(), request, 'native_media_chunk_receipt')
    assert proof and not proof['custody_complete']
    target._store.save_relationship({'certificate_id': 'relationship', 'subject': 'ab' * 32,
        'created_at': 0, 'expires_at': 2**31-1}, peer_did=source._own_gateway_did(), owner_webid='did:key:other')
    assert await source._native_exchange('https://peer.example', target._own_gateway_did(), request, 'native_media_chunk_receipt') is None
    assert target._store.get_native_media(source._own_gateway_did(), 'file')['to_webid'] == 'did:key:bob'
    source._store.close(); target._store.close()


@pytest.mark.parametrize('bad', [None, {}, {'missing': [{}]}, {'missing': [True]}, {'missing': [0, 0]}])
def test_invalid_proof_shapes_cannot_release_custody(tmp_path, bad):
    gw = gateway(tmp_path, 'proof')
    row = begin(gw._store)
    assert not gw._native_chunk_proof_valid(bad, row)
    if isinstance(bad, dict) and 'missing' in bad:
        assert not gw._native_chunk_proof_valid({'custody_complete': False, 'header_committed': False,
            'committed': False, 'recipient_pub': 'A' * 43, **bad}, row)
    gw._store.close()


def test_chunk_rate_bucket_applies_only_to_actual_part_operations():
    assert _relay_body_is_file_chunk(json.dumps({'content_type': 'native_media_chunks', 'operation': 'part'}).encode())
    assert not _relay_body_is_file_chunk(json.dumps({'content_type': 'native_media_chunks', 'operation': 'begin'}).encode())
    assert not _relay_body_is_file_chunk(json.dumps({'content_type': 'native_inline', 'operation': 'part'}).encode())


@pytest.mark.asyncio
async def test_foreign_signatures_and_unsigned_legacy_replies_cannot_release_parts(tmp_path, monkeypatch):
    source, target = paired(tmp_path); foreign = gateway(tmp_path, 'foreign')
    raw = b'opaque ciphertext bytes'; row = begin(source._store, raw, recipient=target._own_gateway_did()); assert put(source._store, row, 0, raw)
    row = source._store.get_native_media('alice', 'file')
    async def legacy(*args, **kwargs): return 200, {}, b'{"status":"received"}'
    monkeypatch.setattr('proxion_messenger_core.network.async_safe_request', legacy)
    assert await source._relay_native_chunks('https://peer.example', row) is None
    async def wrong_signer(*args, **kwargs):
        data = json.loads(kwargs['body'])
        status, body = target._native_response(data, 'native_media_chunk_receipt', custody_complete=True,
            header_committed=False, committed=False, missing=[], recipient_pub='A' * 43)
        proof = json.loads(body); proof['relay_sig_did'] = foreign._own_gateway_did()
        proof['signature'] = sign_relay_envelope(foreign.agent.identity_key, proof)
        return 200, {}, json.dumps(proof).encode()
    monkeypatch.setattr('proxion_messenger_core.network.async_safe_request', wrong_signer)
    assert await source._relay_native_chunks('https://peer.example', row) is None
    assert source._store.get_native_media_part('alice', 'file', row['manifest_digest'], 0)['data'] == raw
    source._store.close(); target._store.close(); foreign._store.close()


@pytest.mark.asyncio
async def test_unrelated_self_signed_stranger_cannot_grow_native_byte_accounting(tmp_path, monkeypatch):
    stranger, target = gateway(tmp_path, 'stranger'), gateway(tmp_path, 'victim')
    async def route(method, url, **kwargs):
        status, body = await target._handle_relay_post(kwargs['body'], client_ip='127.0.0.1')
        return int(status.split()[0]), {}, body.encode()
    monkeypatch.setattr('proxion_messenger_core.network.async_safe_request', route)
    request = {'content_type': 'native_media_chunks', 'native_chunks_v': 1, 'operation': 'part',
        'message_id': 'file', 'media_digest': 'A' * 43, 'header_digest': 'a' * 64, 'manifest_digest': 'b' * 64,
        'seq': 0, 'data_b64': base64.b64encode(b'X' * CHUNK).decode()}
    assert await stranger._native_exchange('https://peer.example', target._own_gateway_did(), request, 'native_media_chunk_receipt') is None
    assert not target.__dict__.get('_file_relay_bytes')
    assert target._store.pending_native_media(target._own_gateway_did()) == []
    stranger._store.close(); target._store.close()


@pytest.mark.asyncio
async def test_download_offer_and_parts_require_complete_custody_and_original_recipient(tmp_path):
    gw = gateway(tmp_path, 'download')
    raw = b'A' * CHUNK + b'B' * 17
    row = begin(gw._store, raw, sender='did:key:alice', recipient='did:key:bob')
    receiver = socket(gw, 'did:key:bob')
    gw._session_meta[receiver].update(native_chunks_v=1, native_media_pub='A' * 43)
    await gw._deliver_native_chunk_media(row)
    receiver.send.assert_not_called()
    for seq in range(2):
        assert put(gw._store, row, seq, raw[seq * CHUNK:(seq + 1) * CHUNK])
    row = gw._store.get_native_media('did:key:alice', 'file')
    await gw._deliver_native_chunk_media(row)
    offer = json.loads(receiver.send.call_args.args[0])
    assert offer['type'] == 'native_media_offer' and offer['manifest_digest'] == row['manifest_digest']
    packet = {'v': 1, 'from_webid': 'did:key:alice', 'message_id': 'file',
              'queue_id': row['queue_id'], 'envelope_digest': row['envelope_digest'],
              'manifest_digest': row['manifest_digest'], 'sequences': [0, 1]}
    receiver.send.reset_mock()
    for change in ({'queue_id': True}, {'envelope_digest': 'f' * 64}, {'manifest_digest': 'f' * 64},
                   {'sequences': [True]}, {'sequences': [0, 0]}, {'sequences': [2]}, {'sequences': []}):
        await gw._handle_native_media_fetch(receiver, {**packet, **change})
    receiver.send.assert_not_called()
    foreign = socket(gw, 'did:key:foreign')
    gw._session_meta[foreign].update(native_chunks_v=1, native_media_pub='A' * 43)
    await gw._handle_native_media_fetch(foreign, packet)
    foreign.send.assert_not_called()
    gw._session_meta[receiver]['native_media_pub'] = 'B' * 43
    await gw._handle_native_media_fetch(receiver, packet)
    receiver.send.assert_not_called()
    gw._session_meta[receiver]['native_media_pub'] = 'A' * 43
    await gw._handle_native_media_fetch(receiver, packet)
    frames = [json.loads(call.args[0]) for call in receiver.send.call_args_list]
    assert [frame['seq'] for frame in frames] == [0, 1]
    assert b''.join(base64.b64decode(frame['data_b64']) for frame in frames) == raw
    retained = gw._store.get_native_media('did:key:alice', 'file')
    assert not retained['committed'] and not retained['header_committed'] and retained['event']
    assert gw._store.get_native_media_part('did:key:alice', 'file', row['manifest_digest'], 0)
    gw._store.close()


@pytest.mark.asyncio
async def test_download_window_stops_if_recipient_blocks_sender_between_parts(tmp_path):
    gw = gateway(tmp_path, 'download-block')
    raw = b'A' * CHUNK + b'B' * 17
    row = begin(gw._store, raw, sender='did:key:alice', recipient='did:key:bob')
    for seq in range(2):
        assert put(gw._store, row, seq, raw[seq * CHUNK:(seq + 1) * CHUNK])
    receiver = socket(gw, 'did:key:bob')
    gw._session_meta[receiver].update(native_chunks_v=1, native_media_pub='A' * 43)
    async def block_after_first(_wire):
        gw._store.set_block('did:key:bob', 'did:key:alice', True)
    receiver.send.side_effect = block_after_first
    await gw._handle_native_media_fetch(receiver, {'v': 1, 'from_webid': 'did:key:alice', 'message_id': 'file',
        'queue_id': row['queue_id'], 'envelope_digest': row['envelope_digest'],
        'manifest_digest': row['manifest_digest'], 'sequences': [0, 1]})
    assert receiver.send.call_count == 1
    retained = gw._store.get_native_media('did:key:alice', 'file')
    assert retained['status'] == 'blocked' and not retained['committed']
    gw._store.close()
