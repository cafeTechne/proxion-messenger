"""Tests for connectivity_diagnostics structured output."""
import pytest

from proxion_messenger_core.local_store import LocalStore
from proxion_messenger_core.wg_overlay import WgOverlayManager, generate_wg_keypair
from proxion_messenger_core.connectivity_diagnostics import get_connectivity_status, format_next_steps


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


def test_diagnostics_returns_structured_status(store):
    manager = WgOverlayManager(store)
    manager.ensure_local_identity()

    status = get_connectivity_status(store)
    assert isinstance(status, dict)
    assert "overlay_identity" in status
    assert "peer_count" in status
    assert "label" in status
    assert "next_steps" in status
    assert status["overlay_identity"] is not None


def test_diagnostics_reports_peer_counts_by_mode(store):
    manager = WgOverlayManager(store)
    manager.ensure_local_identity()

    _, pub1 = generate_wg_keypair()
    _, pub2 = generate_wg_keypair()
    manager.upsert_peer("did:web:a.example", pub1, None, "10.0.0.1/32", "direct")
    manager.upsert_peer("did:web:b.example", pub2, None, "10.0.0.2/32", "relay")

    status = get_connectivity_status(store)
    assert status["peer_count"] == 2
    assert status["direct_peers"] == 1
    assert status["relay_peers"] == 1


def test_diagnostics_next_steps_when_identity_missing(store):
    status = get_connectivity_status(store)
    assert status["overlay_identity"] is None
    steps = format_next_steps(status)
    assert len(steps) >= 1
    assert any("overlay" in s.lower() or "easy federation" in s.lower() for s in steps)
