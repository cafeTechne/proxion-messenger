"""Closed-app scan progress survives a gateway/store reopen."""
import json
from unittest.mock import patch

import pytest

from test_pod_bridge import gw, ROOT, WEBID, PEER, FakePod, be_consumer, run, _listing, make_ws, register, fetch  # noqa: F401
from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig

DID = 'did:key:zReceiver'


def restart(g):
    fresh = ProxionGateway(agent=g.agent, dm_clients={}, room_memberships={},
                          config=GatewayConfig(db_path=g._store.db_path, host='127.0.0.1'))
    fresh._pod_webid, fresh._pod_url = WEBID, ROOT
    fresh._pod = FakePod(); fresh._pod.routes = dict(g._pod.routes)
    fresh.own_pod_clients[WEBID] = (None, fresh._pod)
    return fresh


def baseline(g):
    be_consumer(g, DID)
    for box in ('dm', 'call'):
        g._pod.routes[('GET', ROOT + f'proxion/{box}-inbox/')] = (200, {}, _listing())
    run(g._pb_closed_app_check())
    return ROOT + 'proxion/call-inbox/'


def add_call(g, box):
    g._pod.routes[('GET', box)] = (200, {}, _listing('call'))
    g._pod.routes[('GET', box + 'call')] = (200, {}, json.dumps({'type': 'voice_invite', 'from_webid': PEER}).encode())


def test_arrival_while_gateway_is_down_is_not_rebaselined(gw):
    box = baseline(gw); add_call(gw, box)
    fresh = restart(gw)
    with patch.object(fresh, '_pb_push', return_value=True) as push:
        run(fresh._pb_closed_app_check())
        push.assert_called_once_with(DID, 'missed_call')
    again = restart(fresh)
    with patch.object(again, '_pb_push') as push:
        run(again._pb_closed_app_check()); push.assert_not_called()


@pytest.mark.parametrize('reason', ['read_failure', 'throttled'])
def test_deferred_call_survives_restart(gw, reason):
    box = baseline(gw); add_call(gw, box)
    good = gw._pod.routes[('GET', box + 'call')]
    if reason == 'read_failure':
        gw._pod.routes[('GET', box + 'call')] = (503, {}, b'')
    with patch.object(gw, '_pb_push', return_value=False):
        run(gw._pb_closed_app_check())
    fresh = restart(gw); fresh._pod.routes[('GET', box + 'call')] = good
    with patch.object(fresh, '_pb_push', return_value=True) as push:
        run(fresh._pb_closed_app_check()); push.assert_called_once_with(DID, 'missed_call')


def test_unexamined_call_past_batch_limit_survives_restart(gw):
    box = baseline(gw)
    names = [f'ice-{i}' for i in range(20)] + ['call']
    gw._pod.routes[('GET', box)] = (200, {}, _listing(*names))
    for name in names:
        gw._pod.routes[('GET', box + name)] = (200, {}, json.dumps({'type': 'voice_invite' if name == 'call' else 'ice_candidate', 'from_webid': PEER}).encode())
    with patch.object(gw, '_pb_push') as push:
        run(gw._pb_closed_app_check()); push.assert_not_called()
    fresh = restart(gw)
    with patch.object(fresh, '_pb_push', return_value=True) as push:
        run(fresh._pb_closed_app_check()); push.assert_called_once_with(DID, 'missed_call')


def test_owner_identity_for_device_signer_survives_restart(gw):
    ws, account = make_ws('did:key:zAccount'); register(gw, ws, account)
    gw._session_device_did[ws] = DID
    signer_url = ROOT + 'proxion/identity/signer.json'
    gw._pod.routes[('PUT', signer_url)] = (201, {}, b'')
    assert fetch(gw, ws, 'PUT', signer_url, json.dumps({'version': 1, 'signer': DID, 'account_did': None}))['status'] == 201
    be_consumer(gw, DID)
    gw._webid_sockets.clear()
    box = baseline(gw); add_call(gw, box)
    fresh = restart(gw)
    with patch.object(fresh, '_pb_push', return_value=True) as push:
        run(fresh._pb_closed_app_check()); push.assert_called_once_with(account, 'missed_call')


def test_receiver_change_does_not_inherit_another_receivers_checkpoint(gw):
    box = baseline(gw); add_call(gw, box)
    be_consumer(gw, 'did:key:zReplacement')
    with patch.object(gw, '_pb_push') as push:
        run(gw._pb_closed_app_check()); push.assert_not_called()
    be_consumer(gw, DID)
    with patch.object(gw, '_pb_push', return_value=True) as push:
        run(gw._pb_closed_app_check()); push.assert_called_once_with(DID, 'missed_call')


def test_checkpoint_failure_does_not_kill_future_scans(gw):
    baseline(gw)
    with patch.object(gw._store, 'save_pod_push_scan', side_effect=OSError('disk full')):
        run(gw._pb_closed_app_check())
    add_call(gw, ROOT + 'proxion/call-inbox/')
    with patch.object(gw, '_pb_push', return_value=True) as push:
        run(gw._pb_closed_app_check()); push.assert_called_once()


def test_another_device_on_same_account_does_not_count_as_receiver_online(gw):
    box = baseline(gw)
    account = 'did:key:zAccount'
    gw._store.save_pod_push_receiver(WEBID, ROOT, DID, account)
    # Keep the same scan partition while changing from a direct signer to a
    # delegated account receiver; its first offline observation is a baseline.
    gw._pb()['consumer_identity'] = account
    run(gw._pb_closed_app_check())
    ws, _ = make_ws(account); register(gw, ws, account)
    gw._session_device_did[ws] = 'did:key:zOtherDevice'
    add_call(gw, box)
    with patch.object(gw, '_pb_push', return_value=True) as push:
        run(gw._pb_closed_app_check()); push.assert_called_once_with(account, 'missed_call')
    gw._session_device_did[ws] = DID
    assert gw._pb_consumer_online()


def test_scan_and_receiver_records_are_scoped_to_pod_device_and_account(gw):
    store = gw._store
    store.save_pod_push_receiver(WEBID, ROOT, DID, 'account')
    store.save_pod_push_scan(WEBID, ROOT, DID, 'account', ROOT + 'proxion/call-inbox/', {'old'})
    assert store.get_pod_push_receiver(WEBID, ROOT + 'other/', DID) is None
    for context in [(WEBID, ROOT, 'different-device', 'account'),
                    (WEBID, ROOT, DID, 'different-account'),
                    ('https://other/profile/card#me', ROOT, DID, 'account')]:
        assert store.load_pod_push_scan(*context, ROOT + 'proxion/call-inbox/') is None


@pytest.mark.parametrize('fails', [False, True])
def test_scan_stops_if_pod_changes_during_listing(gw, fails):
    baseline(gw)
    calls = []
    original = gw._pb_request_own
    async def switch(method, url, *args):
        calls.append(url)
        if url == ROOT + 'proxion/dm-inbox/':
            gw._pod_webid, gw._pod_url = PEER, 'https://other.example/bob/'
            if fails:
                raise OSError('switched')
            return 200, {}, _listing('old-message')
        return await original(method, url, *args)
    with patch.object(gw, '_pb_request_own', switch), patch.object(gw, '_pb_push') as push:
        run(gw._pb_closed_app_check()); push.assert_not_called()
    assert ROOT + 'proxion/call-inbox/' not in calls
    assert ROOT + 'proxion/dm-inbox/old-message' not in calls
