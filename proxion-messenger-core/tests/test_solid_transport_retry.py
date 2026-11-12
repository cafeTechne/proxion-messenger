"""Bounded transport retries never replay unprotected mutations."""
from unittest.mock import Mock

import httpx
import pytest

from proxion_messenger_core.solid import SolidResolver
from proxion_messenger_core.solid_client import SolidClient, SolidError

URL = 'https://pod.example/alice/rooms/r/room.json'


def client(handler):
    c = SolidClient(SolidResolver('https://pod.example/alice/'), session=httpx.Client(transport=httpx.MockTransport(handler)))
    c._dynamic_headers = Mock(side_effect=lambda *a: {'DPoP': 'proof-' + str(c._dynamic_headers.call_count)})
    return c


@pytest.mark.parametrize('method,headers', [('GET', {}), ('HEAD', {}), ('PUT', {'If-Match': '"v1"'}), ('PUT', {'If-None-Match': '*'})])
def test_one_transport_retry_has_fresh_proof_and_preserves_preconditions(method, headers):
    seen = []
    def handle(request):
        seen.append(request)
        if len(seen) == 1:
            raise httpx.RemoteProtocolError('connection closed')
        return httpx.Response(200, content=b'{}')
    c = client(handle)
    assert c.request(method, URL, b'{}', headers)[0] == 200
    assert len(seen) == 2 and seen[0].headers['DPoP'] != seen[1].headers['DPoP']
    for key, value in headers.items():
        assert seen[0].headers[key] == seen[1].headers[key] == value


@pytest.mark.parametrize('method,headers', [('POST', {}), ('PATCH', {}), ('PUT', {}), ('PUT', {'If-Match': 'W/"weak"'}), ('PUT', {'If-Match': '*'})])
def test_unprotected_mutations_are_not_replayed(method, headers):
    calls = []
    def handle(request):
        calls.append(request)
        raise httpx.RemoteProtocolError('ambiguous delivery')
    with pytest.raises(SolidError):
        client(handle).request(method, URL, b'{}', headers)
    assert len(calls) == 1


def test_transport_retry_is_bounded():
    handle = Mock(side_effect=httpx.RemoteProtocolError('offline'))
    with pytest.raises(SolidError):
        client(handle).request('GET', URL)
    assert handle.call_count == 2


def test_conditional_retry_returns_conflict_if_the_first_write_already_committed():
    calls = []
    def handle(request):
        calls.append(request)
        if len(calls) == 1:
            raise httpx.RemoteProtocolError('write committed but reply lost')
        return httpx.Response(412)
    assert client(handle).request('PUT', URL, b'{}', {'If-Match': '"v1"'})[0] == 412
    assert len(calls) == 2


def test_transport_retry_and_auth_refresh_have_separate_bounded_budgets():
    calls = []
    def handle(request):
        calls.append(request)
        if len(calls) == 1:
            raise httpx.RemoteProtocolError('stale connection')
        return httpx.Response(401 if len(calls) == 2 else 200)
    c = client(handle); c._refresh_auth = Mock()
    assert c.request('GET', URL)[0] == 200
    assert len(calls) == 3 and c._refresh_auth.call_count == 1


def test_response_size_failure_is_not_retried():
    handle = Mock(return_value=httpx.Response(200, content=b'oversized'))
    with pytest.raises(SolidError):
        client(handle).request('GET', URL, max_bytes=1)
    assert handle.call_count == 1
