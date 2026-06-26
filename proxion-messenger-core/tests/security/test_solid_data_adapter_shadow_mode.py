"""Tests for solid_client.py shadow mode and cutover stage enforcement."""
import os
import pytest
from unittest.mock import MagicMock, patch

from proxion_messenger_core.solid_client import SolidClient, SolidError
from proxion_messenger_core.solid import SolidResolver


def _make_client(mock_response_content=b"data"):
    resolver = SolidResolver("http://pod.example/alice/")
    mock_session = MagicMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.content = mock_response_content
    mock_session.get.return_value = resp
    return SolidClient(resolver, session=mock_session), mock_session


class TestShadowModeComparesLegacyAndAdapterOutputs:
    def test_dual_read_calls_get_legacy_and_shadow_compare(self):
        """When PROXION_SOLID_DUAL_READ=1, _get_legacy is called and shadow compare runs."""
        client, _ = _make_client(b"hello")
        with patch.dict(os.environ, {"PROXION_SOLID_DUAL_READ": "1", "PROXION_SOLID_CUTOVER_STAGE": "0"}):
            with patch.object(client, "_get_legacy", return_value=b"hello") as mock_get_legacy:
                with patch.object(client, "_shadow_compare") as mock_compare:
                    result = client.get("stash://pod/resource")
        assert result == b"hello"
        mock_get_legacy.assert_called_once()
        mock_compare.assert_called_once_with("GET", "stash://pod/resource", b"hello", None)

    def test_no_dual_read_by_default(self):
        """Without PROXION_SOLID_DUAL_READ, normal get runs without shadow compare."""
        client, mock_session = _make_client(b"normal")
        with patch.dict(os.environ, {"PROXION_SOLID_DUAL_READ": "0", "PROXION_SOLID_CUTOVER_STAGE": "0"}):
            with patch.object(client, "_shadow_compare") as mock_compare:
                result = client.get("stash://pod/resource")
        assert result == b"normal"
        mock_compare.assert_not_called()


class TestDualReadMismatchIncrementsMetricAndLogsEvent:
    def test_shadow_compare_records_mismatch_when_results_differ(self):
        """_shadow_compare increments dual_read_mismatch_count on result difference."""
        from proxion_messenger_core.solid_migration import migration_store
        before = migration_store._dual_read_mismatch_count

        client, _ = _make_client()
        client._shadow_compare("GET", "stash://pod/x", b"legacy", b"adapter_different")

        assert migration_store._dual_read_mismatch_count == before + 1

    def test_shadow_compare_no_mismatch_when_results_match(self):
        """_shadow_compare does not increment count when results are identical."""
        from proxion_messenger_core.solid_migration import migration_store
        before = migration_store._dual_read_mismatch_count

        client, _ = _make_client()
        client._shadow_compare("GET", "stash://pod/x", b"same", b"same")

        assert migration_store._dual_read_mismatch_count == before

    def test_shadow_compare_no_mismatch_when_adapter_none(self):
        """_shadow_compare treats adapter_result=None as 'no adapter data' (no mismatch)."""
        from proxion_messenger_core.solid_migration import migration_store
        before = migration_store._dual_read_mismatch_count

        client, _ = _make_client()
        client._shadow_compare("GET", "stash://pod/x", b"legacy", None)

        assert migration_store._dual_read_mismatch_count == before


class TestCutoverStageBlocksLegacyPathWhenStage3:
    def test_stage0_allows_legacy(self):
        """Stage 0: no error raised, legacy path proceeds."""
        client, _ = _make_client(b"ok")
        with patch.dict(os.environ, {"PROXION_SOLID_CUTOVER_STAGE": "0"}):
            client._check_cutover_stage("GET")  # should not raise

    def test_stage1_allows_legacy(self):
        client, _ = _make_client(b"ok")
        with patch.dict(os.environ, {"PROXION_SOLID_CUTOVER_STAGE": "1"}):
            client._check_cutover_stage("GET")  # should not raise

    def test_stage3_blocks_legacy_without_override(self):
        """Stage 3: SolidError raised unless PROXION_SOLID_EMERGENCY_OVERRIDE=1."""
        client, _ = _make_client(b"ok")
        with patch.dict(os.environ, {
            "PROXION_SOLID_CUTOVER_STAGE": "3",
            "PROXION_SOLID_EMERGENCY_OVERRIDE": "0",
        }):
            with pytest.raises(SolidError, match="blocked at cutover stage 3"):
                client._check_cutover_stage("GET")

    def test_stage3_allows_emergency_override(self):
        """Stage 3 with PROXION_SOLID_EMERGENCY_OVERRIDE=1 does not raise."""
        client, _ = _make_client(b"ok")
        with patch.dict(os.environ, {
            "PROXION_SOLID_CUTOVER_STAGE": "3",
            "PROXION_SOLID_EMERGENCY_OVERRIDE": "1",
        }):
            client._check_cutover_stage("GET")  # should not raise
