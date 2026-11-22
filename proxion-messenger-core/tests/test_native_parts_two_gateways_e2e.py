"""Native opaque parts cross two real HTTP gateways under existing frame limits.

This verifies gateway custody, not browser opt-in or full file/history delivery.
"""
import hashlib

import pytest

from gwharness import free_port, start_gateway
from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from test_native_media_parts import begin, put, CHUNK


def launch(tmp_path, name):
    ws_port, http_port = free_port(), free_port()
    config = GatewayConfig(host='127.0.0.1', port=ws_port, http_port=http_port,
        public_url=f'ws://127.0.0.1:{ws_port}', http_public_url=f'http://127.0.0.1:{http_port}',
        db_path=str(tmp_path / (name + '.db')))
    gw = ProxionGateway(AgentState.generate(), {}, {}, config)
    return gw, start_gateway(gw, ws_port, http_port)


@pytest.mark.asyncio
@pytest.mark.parametrize('size', [2 * 1024 * 1024, 25 * 1024 * 1024])
async def test_native_custody_parts_cross_real_gateways_without_using_the_general_relay_budget(tmp_path, monkeypatch, size):
    monkeypatch.setenv('PROXION_REQUIRE_AUTH', '0')
    monkeypatch.setenv('PROXION_ALLOW_PRIVATE_RELAY', '1')
    monkeypatch.setenv('PROXION_ALLOW_INSECURE_FEDERATION', '1')
    source, source_handle = launch(tmp_path, 'source')
    target, target_handle = launch(tmp_path, 'target')
    try:
        source_did, target_did = source._own_gateway_did(), target._own_gateway_did()
        target._store.save_relationship({'certificate_id': 'contact', 'subject': 'ab' * 32,
            'created_at': 0, 'expires_at': 2**31-1}, peer_did=source_did, owner_webid=target_did)
        raw = b''.join(bytes([seq % 251]) * min(CHUNK, size - seq * CHUNK) for seq in range((size + CHUNK - 1) // CHUNK))
        row = begin(source._store, raw, recipient=target_did)
        for seq in range(row['chunk_count']):
            assert put(source._store, row, seq, raw[seq * CHUNK:(seq + 1) * CHUNK])
        row = source._store.get_native_media('alice', 'file')
        proof = await source._relay_native_chunks(f'http://127.0.0.1:{target_handle.http_port}', row)
        assert proof and proof['custody_complete'] and not proof['header_committed'] and not proof['committed']
        received = target._store.get_native_media(source_did, 'file')
        assert received['custody_complete'] and received['total_bytes'] == size
        full = hashlib.sha256()
        for seq in range(row['chunk_count']):
            part = target._store.get_native_media_part(source_did, 'file', row['manifest_digest'], seq)
            full.update(part['data'])
        assert full.hexdigest() == row['cipher_digest']
        # The manifest uses the ordinary bucket; parts use the existing bounded
        # file bucket instead of exhausting the 60/min DM relay budget.
        assert len(target._relay_rate_limiter['127.0.0.1']) == 1
        assert len(target._relay_rate_limiter['127.0.0.1|file']) == row['chunk_count']
        assert source._store.commit_native_media(target_did, row['queue_id'], 'alice', 'file', row['envelope_digest'], forwarded=True)
        assert source._store.get_native_media_part('alice', 'file', row['manifest_digest'], 0) is None
    finally:
        source_handle.stop(); target_handle.stop()
