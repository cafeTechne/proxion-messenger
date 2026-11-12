"""Retire only provider-confirmed expired subscription snapshots."""
import json
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from test_push_subscription_pod_sync import gateway  # noqa: F401
from proxion_messenger_core import webpush
from proxion_messenger_core.local_store import LocalStore


@pytest.mark.parametrize('status,expired', [(201, False), (404, True), (410, True), (401, False), (403, False), (429, False), (503, False)])
@pytest.mark.parametrize('exception', [False, True])
def test_provider_expiry_classification(monkeypatch, status, expired, exception):
    response = SimpleNamespace(status_code=status)
    error = RuntimeError('provider failure'); error.response = response
    sender = Mock(side_effect=error) if exception else Mock(return_value=response)
    monkeypatch.setitem(sys.modules, 'pywebpush', SimpleNamespace(webpush=sender, WebPushException=RuntimeError))
    monkeypatch.setattr(webpush, 'is_safe_push_endpoint', lambda endpoint: True)
    retire = Mock()
    ok = webpush.send_web_push({'endpoint': 'https://push.example/one'}, {}, 'pem', 'mailto:test@example.com', on_expired=retire)
    assert ok is (status == 201 and not exception)
    assert retire.called is expired


def test_expiry_survives_restart_and_preserves_replacement_keys(tmp_path):
    path = str(tmp_path / 'expiry.db'); store = LocalStore(path)
    store.save_push_subscription('one', 'owner', 'https://push.example/one', 'old-key', 'old-auth')
    snapshot = store.get_push_subscriptions('owner')[0]
    store.save_push_subscription('one', 'owner', snapshot['endpoint'], 'new-key', 'new-auth')
    store.expire_push_subscription(snapshot)
    assert store.get_push_subscriptions('owner')[0]['p256dh_b64'] == 'new-key'
    reopened = LocalStore(path)
    reopened.save_push_subscription('stale-pod-id', 'owner', snapshot['endpoint'], 'old-key', 'old-auth')
    assert [s['subscription_id'] for s in reopened.get_push_subscriptions('owner')] == ['one']
    assert reopened.push_subscription_expired('owner', snapshot['endpoint'], 'old-key', 'old-auth')
    assert not reopened.push_subscription_expired('other-owner', snapshot['endpoint'], 'old-key', 'old-auth')


@pytest.mark.asyncio
async def test_fanout_removes_only_expired_subscription(gateway):
    for sid in ('expired', 'temporary'):
        gateway._store.save_push_subscription(sid, 'owner', 'https://push.example/' + sid, 'p', 'a')
    def send(**kwargs):
        if kwargs['subscription']['endpoint'].endswith('/expired'):
            kwargs['on_expired']()
        return False
    with patch.object(webpush, 'send_web_push', send):
        await gateway._push_offline_fanout(gateway._store.get_push_subscriptions('owner'), {}, 'pem', 'mailto:test@example.com')
    assert [s['subscription_id'] for s in gateway._store.get_push_subscriptions('owner')] == ['temporary']


@pytest.mark.asyncio
async def test_reconnect_requests_browser_renewal_for_confirmed_expiry(gateway):
    endpoint = 'https://push.example/one'
    gateway._store.save_push_subscription('one', 'owner', endpoint, 'p', 'a')
    gateway._store.expire_push_subscription(gateway._store.get_push_subscriptions('owner')[0])
    ws = Mock(); ws.send = AsyncMock(); gateway._client_webids[ws] = 'owner'
    with patch.object(webpush, 'is_safe_push_endpoint', return_value=True):
        await gateway._handle_subscribe_push(ws, {'endpoint': endpoint, 'p256dh_b64': 'p', 'auth_b64': 'a'})
    assert json.loads(ws.send.call_args.args[0])['type'] == 'push_subscription_expired'
    assert gateway._store.get_push_subscriptions('owner') == []


@pytest.mark.asyncio
async def test_pod_backup_cannot_restore_expired_subscription(gateway):
    record = {'subscription_id': 'one', 'owner_webid': 'owner', 'endpoint': 'https://push.example/one', 'p256dh_b64': 'p', 'auth_b64': 'a'}
    gateway._store.save_push_subscription('one', 'owner', record['endpoint'], 'p', 'a')
    gateway._store.expire_push_subscription(record)
    client = Mock(); client.list.return_value = ['stash://pod/push/one.json']; client.get.return_value = json.dumps(record).encode()
    with patch.object(gateway, '_pod_client', return_value=client), patch.object(webpush, 'is_safe_push_endpoint', return_value=True):
        await gateway._restore_push_subscriptions_from_pod()
    assert gateway._store.get_push_subscriptions('owner') == []


@pytest.mark.parametrize('path', ['call', 'invite'])
def test_other_notification_paths_retire_expired_subscriptions(gateway, path):
    gateway._store.save_push_subscription('one', 'owner', 'https://push.example/one', 'p', 'a')
    gateway._vapid_private_pem, gateway._vapid_subject = 'pem', 'mailto:test@example.com'
    def send(**kwargs):
        kwargs['on_expired']()
        return False
    with patch.object(webpush, 'send_web_push', send), patch.object(webpush, 'is_safe_push_endpoint', return_value=True):
        if path == 'call':
            gateway._push_missed_call('owner')
        else:
            gateway._send_inbox_push('owner')
    assert gateway._store.get_push_subscriptions('owner') == []


def test_expiry_retires_the_same_token_reregistered_under_a_new_id(tmp_path):
    store = LocalStore(str(tmp_path / 'token.db'))
    store.save_push_subscription('old-id', 'owner', 'https://push.example/one', 'p', 'a')
    snapshot = store.get_push_subscriptions('owner')[0]
    store.save_push_subscription('new-id', 'owner', snapshot['endpoint'], 'p', 'a')
    store.expire_push_subscription(snapshot)
    assert store.get_push_subscriptions('owner') == []
