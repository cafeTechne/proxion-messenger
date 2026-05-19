"""Tests for Solid SDK migration error observability."""
import json
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from proxion_messenger_core.solid_migration import (
    migration_store, MigrationErrorStore,
    SOLID_AUTH_FAILED, SOLID_NOT_FOUND, SOLID_FORBIDDEN,
    MODE_LEGACY, MODE_BRIDGE,
)


@pytest.fixture(autouse=True)
def fresh_store():
    """Provide a fresh MigrationErrorStore for each test."""
    return MigrationErrorStore()


class TestGetSolidMigrationErrorsOwnerOnly:
    @pytest.mark.asyncio
    async def test_owner_receives_migration_errors(self):
        """Owner DID receives the migration error snapshot."""
        from proxion_messenger_core._gateway_misc import MiscHandlerMixin

        gateway = MagicMock(spec=MiscHandlerMixin)
        gateway._client_webids = {}
        gateway._store = None
        gateway.agent = MagicMock()
        gateway.agent.identity_pub_bytes = b"\x00" * 32

        owner_did = "did:key:owner"
        ws = MagicMock()
        sent = []

        async def fake_send(msg):
            sent.append(json.loads(msg))

        ws.send = fake_send
        gateway._client_webids[ws] = owner_did

        with patch("proxion_messenger_core.didkey.pub_key_to_did", return_value=owner_did):
            await MiscHandlerMixin._handle_get_solid_migration_errors(gateway, ws, {})

        assert sent, "Expected at least one message"
        msg = sent[0]
        assert msg["type"] == "solid_migration_errors"

    @pytest.mark.asyncio
    async def test_non_owner_receives_forbidden(self):
        """Non-owner DID receives E_FORBIDDEN error."""
        from proxion_messenger_core._gateway_misc import MiscHandlerMixin

        gateway = MagicMock(spec=MiscHandlerMixin)
        gateway._client_webids = {}
        gateway.agent = MagicMock()
        gateway.agent.identity_pub_bytes = b"\x00" * 32

        ws = MagicMock()
        sent = []

        async def fake_send(msg):
            sent.append(json.loads(msg))

        ws.send = fake_send
        gateway._client_webids[ws] = "did:key:intruder"

        with patch("proxion_messenger_core.didkey.pub_key_to_did", return_value="did:key:owner"):
            await MiscHandlerMixin._handle_get_solid_migration_errors(gateway, ws, {})

        assert sent[0]["code"] == "E_FORBIDDEN"


class TestErrorGroupingByNormalizedCode:
    def test_record_groups_by_code_and_mode(self):
        store = MigrationErrorStore()
        store.record(SOLID_AUTH_FAILED, MODE_LEGACY)
        store.record(SOLID_AUTH_FAILED, MODE_LEGACY)
        store.record(SOLID_NOT_FOUND, MODE_BRIDGE)
        snap = store.snapshot()
        assert snap["by_code"][SOLID_AUTH_FAILED][MODE_LEGACY] == 2
        assert snap["by_code"][SOLID_NOT_FOUND][MODE_BRIDGE] == 1

    def test_all_known_codes_are_importable(self):
        from proxion_messenger_core.solid_migration import (
            SOLID_AUTH_REQUIRED, SOLID_AUTH_FAILED, SOLID_FORBIDDEN,
            SOLID_NOT_FOUND, SOLID_CONFLICT, SOLID_PRECONDITION_FAILED,
            SOLID_NETWORK_UNAVAILABLE, SOLID_NOT_SUPPORTED,
        )
        for code in (
            SOLID_AUTH_REQUIRED, SOLID_AUTH_FAILED, SOLID_FORBIDDEN,
            SOLID_NOT_FOUND, SOLID_CONFLICT, SOLID_PRECONDITION_FAILED,
            SOLID_NETWORK_UNAVAILABLE, SOLID_NOT_SUPPORTED,
        ):
            assert isinstance(code, str) and code.startswith("SOLID_")


class TestModeBreakdownPresentInErrorMetrics:
    def test_snapshot_includes_auth_mode_fields(self):
        store = MigrationErrorStore()
        store.set_auth_mode(MODE_BRIDGE)
        store.record_auth_fallback(SOLID_AUTH_FAILED)
        snap = store.snapshot()
        assert snap["auth_mode_active"] == MODE_BRIDGE
        assert snap["auth_mode_fallback_count"] == 1
        assert snap["auth_mode_last_failure_code"] == SOLID_AUTH_FAILED

    def test_snapshot_includes_cutover_stage(self):
        import os
        with patch.dict(os.environ, {"PROXION_SOLID_CUTOVER_STAGE": "2"}):
            store = MigrationErrorStore()
            snap = store.snapshot()
        assert snap["cutover_stage"] == 2

    def test_snapshot_includes_dual_read_mismatch_count(self):
        store = MigrationErrorStore()
        store.record_dual_read_mismatch()
        store.record_dual_read_mismatch()
        snap = store.snapshot()
        assert snap["dual_read_mismatch_count"] == 2
