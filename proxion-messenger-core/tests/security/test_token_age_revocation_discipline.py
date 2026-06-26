"""R10: Token age and revocation discipline tests for CssClientCredentials."""
import time
import pytest
from unittest.mock import patch, MagicMock

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from proxion_messenger_core.css_auth import CssClientCredentials


@pytest.fixture
def creds():
    return CssClientCredentials(
        css_base_url="http://css.example.com",
        client_id="test-client",
        client_secret="secret",
        identity_key=Ed25519PrivateKey.generate(),
    )


def test_token_forced_refresh_after_max_age(creds, monkeypatch):
    """Token should be force-refreshed when age exceeds PROXION_MAX_TOKEN_AGE_S."""
    monkeypatch.setenv("PROXION_MAX_TOKEN_AGE_S", "10")
    fetch_calls = []

    def _mock_fetch(scope="pod_rw"):
        fetch_calls.append(scope)
        return ("fresh_token", 3600)

    with patch.object(creds, "fetch_access_token", side_effect=_mock_fetch):
        creds.get_token("pod_rw")  # initial fetch
        assert len(fetch_calls) == 1
        # Simulate token being 11 seconds old
        creds._token_issued_at["pod_rw"] = time.time() - 11
        creds.get_token("pod_rw")  # should force-refresh due to age
        assert len(fetch_calls) == 2


def test_three_consecutive_401s_purge_token_cache(creds):
    """After 3 consecutive 401s, the token cache must be purged."""
    creds._cached_tokens["pod_rw"] = "old_token"
    creds._token_issued_at["pod_rw"] = time.time()
    creds._token_expiries["pod_rw"] = time.time() + 3600

    creds.record_401("pod_rw")
    creds.record_401("pod_rw")
    assert "pod_rw" in creds._cached_tokens  # not yet purged after 2
    creds.record_401("pod_rw")  # 3rd — triggers purge
    assert "pod_rw" not in creds._cached_tokens


def test_credential_anomaly_logged_on_401_streak(creds):
    """Credential anomaly must be recorded on 3-consecutive-401 streak."""
    mock_store = MagicMock()
    creds.record_401("pod_rw", store=mock_store)
    creds.record_401("pod_rw", store=mock_store)
    creds.record_401("pod_rw", store=mock_store)
    mock_store.save_credential_anomaly.assert_called_once()
    call_args = mock_store.save_credential_anomaly.call_args
    assert call_args[1]["anomaly_type"] == "consecutive_401_streak"


def test_successful_fetch_resets_401_streak(creds, monkeypatch):
    """A successful token fetch should reset the 401 streak counter."""
    monkeypatch.setenv("PROXION_MAX_TOKEN_AGE_S", "9999")

    def _mock_fetch(scope="pod_rw"):
        return ("token", 3600)

    with patch.object(creds, "fetch_access_token", side_effect=_mock_fetch):
        creds.record_401("pod_rw")
        creds.record_401("pod_rw")
        creds.get_token("pod_rw")  # successful fetch — resets streak
        assert creds._consecutive_401s.get("pod_rw", 0) == 0


def test_issued_at_tracked_on_fetch(creds, monkeypatch):
    """issued_at should be set when a token is first fetched."""
    monkeypatch.setenv("PROXION_MAX_TOKEN_AGE_S", "9999")

    def _mock_fetch(scope="pod_rw"):
        return ("tok", 3600)

    with patch.object(creds, "fetch_access_token", side_effect=_mock_fetch):
        before = time.time()
        creds.get_token("pod_rw")
        after = time.time()
        issued = creds._token_issued_at.get("pod_rw", 0)
        assert before <= issued <= after


def test_purge_clears_issued_at(creds):
    creds._token_issued_at["pod_rw"] = time.time()
    creds._cached_tokens["pod_rw"] = "tok"
    creds._token_expiries["pod_rw"] = time.time() + 3600
    creds.purge_token_cache()
    assert "pod_rw" not in creds._cached_tokens
    assert "pod_rw" not in creds._token_expiries
