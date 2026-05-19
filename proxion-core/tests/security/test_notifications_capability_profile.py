"""Tests for notification capability profiling and fallback persistence (Round 14)."""
import tempfile
import pytest

from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    return LocalStore(path)


class TestCapabilityProfileSavedAfterProbe:
    def test_capability_profile_saved_after_probe(self, store):
        store.save_pod_capability_profile(
            pod_origin="https://pod.example",
            notifications_supported=True,
            channel_types=["WebSocketChannel2023"],
            auth_requirements=["DPoP"],
            verification_source="runtime_probe",
        )
        profile = store.get_pod_capability_profile("https://pod.example")
        assert profile is not None
        assert profile["notifications_supported"] == 1
        assert profile["verification_source"] == "runtime_probe"
        assert "WebSocketChannel2023" in profile["channel_types"]

    def test_profile_upserts_on_re_probe(self, store):
        store.save_pod_capability_profile(
            pod_origin="https://pod.example",
            notifications_supported=False,
            channel_types=[],
            auth_requirements=[],
        )
        store.save_pod_capability_profile(
            pod_origin="https://pod.example",
            notifications_supported=True,
            channel_types=["WebSocketChannel2023"],
            auth_requirements=["DPoP"],
        )
        profile = store.get_pod_capability_profile("https://pod.example")
        assert profile["notifications_supported"] == 1

    def test_missing_origin_returns_none(self, store):
        assert store.get_pod_capability_profile("https://unknown.example") is None


class TestFallbackReasonCodesPersisted:
    def test_fallback_reason_codes_persisted(self, store):
        codes = [
            "notifs_capability_absent",
            "notifs_protocol_unsupported",
            "notifs_auth_failed",
            "notifs_transport_failed",
            "notifs_payload_invalid",
        ]
        for code in codes:
            store.record_notification_fallback("https://pod.example", code)

        events = store.get_notification_fallback_events("https://pod.example")
        recorded_codes = [e["reason_code"] for e in events]
        for code in codes:
            assert code in recorded_codes

    def test_fallback_events_filtered_by_origin(self, store):
        store.record_notification_fallback("https://alice.example", "notifs_transport_failed")
        store.record_notification_fallback("https://bob.example", "notifs_auth_failed")

        alice_events = store.get_notification_fallback_events("https://alice.example")
        assert all(e["pod_origin"] == "https://alice.example" for e in alice_events)
        assert len(alice_events) == 1


class TestInvalidNotificationPayloadQuarantined:
    def test_invalid_notification_payload_quarantined(self, store):
        """Malformed payloads must be recorded (persisted) rather than silently dropped."""
        store.record_notification_fallback(
            pod_origin="https://pod.example",
            reason_code="notifs_payload_invalid",
            detail='raw: "not-json-object"',
        )
        events = store.get_notification_fallback_events("https://pod.example")
        assert any(e["reason_code"] == "notifs_payload_invalid" for e in events)
