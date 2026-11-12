"""Durable owner consent is pending until the requester confirms it."""
import json
from unittest.mock import AsyncMock, patch

import pytest
import websockets

from test_contacts_presence_federation import _client_did, _register, _wait
from test_friend_request_withdraw import _start, _restart, _send_request, _status, _on_loop, _withdraw_body


@pytest.fixture(autouse=True)
def local_gateways(monkeypatch):
    monkeypatch.setenv('PROXION_ALLOW_PRIVATE_RELAY', '1')
    monkeypatch.setenv('PROXION_ALLOW_INSECURE_FEDERATION', '1')
    for name in ('PROXION_CSS_URL', 'PROXION_CSS_EMAIL', 'PROXION_CSS_PASSWORD'):
        monkeypatch.delenv(name, raising=False)


async def prepare(a, b):
    async with websockets.connect(f'ws://127.0.0.1:{a.ws}') as sender, websockets.connect(f'ws://127.0.0.1:{b.ws}') as receiver:
        await _register(sender, _client_did(), 'Alice')
        await _register(receiver, _client_did(), 'Bob')
        iid = (await _send_request(sender, b))['invitation_id']
        assert await _wait(receiver, lambda event: event.get('type') == 'friend_request_received')
    return iid


async def accept_offline(b, iid):
    async with websockets.connect(f'ws://127.0.0.1:{b.ws}') as receiver:
        await _register(receiver, _client_did(), 'Bob')
        await receiver.send(json.dumps({'cmd': 'accept_friend_request', 'invitation_id': iid}))
        event = await _wait(receiver, lambda event: event.get('type') == 'friend_request_acceptance_pending' and event.get('retryable'))
        assert event
        await receiver.send(json.dumps({'cmd': 'list_friend_requests'}))
        snapshot = await _wait(receiver, lambda event: event.get('type') == 'friend_requests')
        assert next(row for row in snapshot['pending'] if row['invitation_id'] == iid)['status'] == 'accepting'
    assert _status(b, iid) == 'accepting'
    assert b.gw._store.list_relationships() == []
    return json.loads(b.gw._store.get_friend_acceptance(iid)['payload_json'])


async def retry_due(g, iid):
    # Advance only the queue's retry deadline; invite/signature clocks stay real.
    with g.gw._store._conn() as conn:
        conn.execute('UPDATE friend_acceptances SET next_attempt=0 WHERE invitation_id=?', (iid,))
    await _on_loop(g, g.gw._relay_retry_pass())


@pytest.mark.asyncio
async def test_acceptance_survives_both_gateway_restarts_and_confirms_one_contact(tmp_path):
    a, b = _start(tmp_path, 'a'), _start(tmp_path, 'b')
    iid = await prepare(a, b); a.handle.stop()
    original = await accept_offline(b, iid)
    b.handle.stop(); a2, b2 = _restart(tmp_path, a), _restart(tmp_path, b)
    assert json.loads(b2.gw._store.get_friend_acceptance(iid)['payload_json'])['certificate'] == original['certificate']
    await retry_due(b2, iid)
    assert _status(a2, iid) == _status(b2, iid) == 'accepted'
    assert len(a2.gw._store.list_relationships()) == len(b2.gw._store.list_relationships()) == 1
    assert b2.gw._store.get_friend_acceptance(iid) is None


@pytest.mark.asyncio
async def test_withdrawal_410_after_restart_adds_nobody(tmp_path):
    a, b = _start(tmp_path, 'a'), _start(tmp_path, 'b')
    iid = await prepare(a, b); a.handle.stop()
    await accept_offline(b, iid)
    # Simulate a withdrawal whose signed notice could not reach the acceptor.
    a.gw._store.mark_invite_status(iid, 'withdrawn')
    b.handle.stop(); a2, b2 = _restart(tmp_path, a), _restart(tmp_path, b)
    await retry_due(b2, iid)
    assert _status(a2, iid) == _status(b2, iid) == 'withdrawn'
    assert a2.gw._store.list_relationships() == b2.gw._store.list_relationships() == []
    assert b2.gw._store.get_friend_acceptance(iid) is None


@pytest.mark.asyncio
async def test_lost_confirmation_retries_exact_certificate_without_duplicate_contacts(tmp_path):
    a, b = _start(tmp_path, 'a'), _start(tmp_path, 'b'); iid = await prepare(a, b)
    original_post = b.gw._post_invite_accept

    async def lost_response(url, payload):
        assert await original_post(url, payload) == 200
        return None

    with patch.object(b.gw, '_post_invite_accept', lost_response):
        original = await accept_offline(b, iid)
    assert _status(a, iid) == 'accepted'
    a.handle.stop(); b.handle.stop(); a2, b2 = _restart(tmp_path, a), _restart(tmp_path, b)
    await retry_due(b2, iid)
    assert _status(b2, iid) == 'accepted'
    assert b2.gw._store.list_relationships()[0]['certificate_id'] == original['certificate']['certificate_id']
    assert len(a2.gw._store.list_relationships()) == len(b2.gw._store.list_relationships()) == 1


@pytest.mark.asyncio
async def test_signed_withdrawal_cancels_pending_acceptance_and_stale_retry(tmp_path):
    a, b = _start(tmp_path, 'a'), _start(tmp_path, 'b'); iid = await prepare(a, b)
    a.handle.stop(); await accept_offline(b, iid)
    queued = b.gw._store.get_friend_acceptance(iid)
    body = json.dumps(_withdraw_body(a.agent, iid, a.did)).encode()
    result = await _on_loop(b, b.gw._handle_invite_withdraw_post(body))
    assert result[0].startswith('200') and _status(b, iid) == 'withdrawn'
    with patch.object(b.gw, '_post_invite_accept', AsyncMock(return_value=200)) as post:
        await _on_loop(b, b.gw._attempt_friend_acceptance(queued))
        post.assert_not_called()
    assert b.gw._store.list_relationships() == []


@pytest.mark.asyncio
async def test_acceptance_storage_failure_keeps_request_and_does_not_send(tmp_path):
    a, b = _start(tmp_path, 'a'), _start(tmp_path, 'b'); iid = await prepare(a, b)
    with patch.object(b.gw._store, 'queue_friend_acceptance', side_effect=OSError('disk full')), \
            patch.object(b.gw, '_post_invite_accept', AsyncMock()) as post:
        async with websockets.connect(f'ws://127.0.0.1:{b.ws}') as receiver:
            await _register(receiver, _client_did(), 'Bob')
            await receiver.send(json.dumps({'cmd': 'accept_friend_request', 'invitation_id': iid}))
            event = await _wait(receiver, lambda event: event.get('type') == 'error')
            assert event['message'] == 'friend_acceptance_store_failed'
        post.assert_not_called()
    assert _status(b, iid) == 'pending' and b.gw._store.list_relationships() == []


@pytest.mark.asyncio
async def test_local_completion_failure_retains_confirmed_certificate_for_retry(tmp_path):
    a, b = _start(tmp_path, 'a'), _start(tmp_path, 'b'); iid = await prepare(a, b)
    # Fail after save_relationship has run inside the completion transaction.
    # The contact insert must roll back along with the invitation transition.
    with patch.object(b.gw._store, 'mark_invite_status', side_effect=OSError('disk full')):
        original = await accept_offline(b, iid)
    assert _status(a, iid) == 'accepted'
    await retry_due(b, iid)
    assert _status(b, iid) == 'accepted'
    assert b.gw._store.list_relationships()[0]['certificate_id'] == original['certificate']['certificate_id']


@pytest.mark.asyncio
async def test_exact_confirmed_retry_is_read_only_and_cannot_readd_a_removed_contact(tmp_path):
    from test_invite_accept_replay import _remove
    a, b = _start(tmp_path, 'a'), _start(tmp_path, 'b'); iid = await prepare(a, b)
    original_post = b.gw._post_invite_accept
    captured = []

    async def lost_response(url, payload):
        captured.append(payload)
        assert await original_post(url, payload) == 200
        return None

    with patch.object(b.gw, '_post_invite_accept', lost_response):
        await accept_offline(b, iid)
    payload = {**captured[0], 'from_display_name': 'replacement', 'from_e2e_key': 'malicious-key'}
    status, _ = await _on_loop(a, a.gw._handle_invite_accept_post(json.dumps(payload).encode()))
    assert status.startswith('200')
    assert a.gw._store.get_display_name(b.did) != 'replacement'
    assert a.gw._store.get_peer_browser_e2e_key(b.did) != 'malicious-key'
    await _on_loop(a, _remove(a.gw, b.did))
    await retry_due(b, iid)
    assert b.gw._store.list_relationships() == []
    assert _status(b, iid) == 'declined'


@pytest.mark.asyncio
async def test_blocking_peer_invalidates_queued_acceptance_before_network(tmp_path):
    a, b = _start(tmp_path, 'a'), _start(tmp_path, 'b'); iid = await prepare(a, b)
    a.handle.stop(); original = await accept_offline(b, iid)
    b.gw._store.set_block(original['owner'], a.did, True)
    with patch.object(b.gw, '_post_invite_accept', AsyncMock()) as post:
        await retry_due(b, iid)
        post.assert_not_called()
    assert _status(b, iid) == 'declined' and b.gw._store.list_relationships() == []


@pytest.mark.asyncio
async def test_different_gateway_identity_cannot_reuse_queued_owner_consent(tmp_path):
    from proxion_messenger_core.persist import AgentState
    a, b = _start(tmp_path, 'a'), _start(tmp_path, 'b'); iid = await prepare(a, b)
    a.handle.stop(); await accept_offline(b, iid); b.handle.stop()
    replacement = _start(tmp_path, b.name, agent=AgentState.generate(), ports=b.ports)
    with patch.object(replacement.gw, '_post_invite_accept', AsyncMock()) as post:
        await retry_due(replacement, iid)
        post.assert_not_called()
    assert replacement.gw._store.list_relationships() == []
    assert _status(replacement, iid) == 'declined'


@pytest.mark.asyncio
async def test_ignore_during_pending_acceptance_keeps_the_durable_request(tmp_path):
    a, b = _start(tmp_path, 'a'), _start(tmp_path, 'b'); iid = await prepare(a, b)
    a.handle.stop(); await accept_offline(b, iid)
    async with websockets.connect(f'ws://127.0.0.1:{b.ws}') as receiver:
        await _register(receiver, _client_did(), 'Bob')
        await receiver.send(json.dumps({'cmd': 'decline_friend_request', 'invitation_id': iid}))
        event = await _wait(receiver, lambda event: event.get('type') == 'error')
        assert event['message'] == 'acceptance_in_progress' and event['invitation_id'] == iid
    assert _status(b, iid) == 'accepting'
    assert b.gw._store.get_friend_acceptance(iid) is not None
