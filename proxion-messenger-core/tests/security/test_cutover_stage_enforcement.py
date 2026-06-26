"""Tests for PROXION_SOLID_CUTOVER_STAGE enforcement."""
import os
import pytest
from unittest.mock import MagicMock, patch

from proxion_messenger_core.solid_client import SolidClient, SolidError
from proxion_messenger_core.solid import SolidResolver


@pytest.fixture
def client():
    resolver = SolidResolver("http://pod.example/alice/")
    session = MagicMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.content = b"ok"
    session.get.return_value = resp
    return SolidClient(resolver, session=session)


class TestStage0AllowsLegacyFallback:
    def test_stage0_get_does_not_raise(self, client):
        with patch.dict(os.environ, {"PROXION_SOLID_CUTOVER_STAGE": "0"}):
            result = client.get("stash://pod/resource")
        assert result == b"ok"

    def test_stage0_put_does_not_raise(self, client):
        session = client._session
        put_resp = MagicMock()
        put_resp.status_code = 201
        session.put.return_value = put_resp
        with patch.dict(os.environ, {"PROXION_SOLID_CUTOVER_STAGE": "0"}):
            client.put("stash://pod/resource", b"data")

    def test_stage1_also_allows_legacy(self, client):
        with patch.dict(os.environ, {"PROXION_SOLID_CUTOVER_STAGE": "1"}):
            result = client.get("stash://pod/resource")
        assert result == b"ok"


class TestStage2RequiresAdapterForEnabledSurfaces:
    def test_stage2_logs_warning_but_does_not_raise(self, client):
        """Stage 2 allows legacy but does not raise."""
        with patch.dict(os.environ, {"PROXION_SOLID_CUTOVER_STAGE": "2"}):
            result = client.get("stash://pod/resource")
        assert result == b"ok"

    def test_stage2_check_cutover_does_not_raise(self, client):
        with patch.dict(os.environ, {"PROXION_SOLID_CUTOVER_STAGE": "2"}):
            client._check_cutover_stage("GET")  # should not raise


class TestStage3BlocksLegacyWithoutEmergencyOverride:
    def test_stage3_blocks_get(self, client):
        with patch.dict(os.environ, {
            "PROXION_SOLID_CUTOVER_STAGE": "3",
            "PROXION_SOLID_EMERGENCY_OVERRIDE": "0",
        }):
            with pytest.raises(SolidError, match="blocked at cutover stage 3"):
                client.get("stash://pod/resource")

    def test_stage3_blocks_put(self, client):
        with patch.dict(os.environ, {
            "PROXION_SOLID_CUTOVER_STAGE": "3",
            "PROXION_SOLID_EMERGENCY_OVERRIDE": "0",
        }):
            with pytest.raises(SolidError, match="blocked at cutover stage 3"):
                client.put("stash://pod/resource", b"data")

    def test_stage3_emergency_override_allows_legacy(self, client):
        """PROXION_SOLID_EMERGENCY_OVERRIDE=1 bypasses stage-3 block."""
        with patch.dict(os.environ, {
            "PROXION_SOLID_CUTOVER_STAGE": "3",
            "PROXION_SOLID_EMERGENCY_OVERRIDE": "1",
        }):
            result = client.get("stash://pod/resource")
        assert result == b"ok"

    def test_stage3_invalid_value_treated_as_0(self, client):
        """An invalid PROXION_SOLID_CUTOVER_STAGE value defaults to stage 0."""
        with patch.dict(os.environ, {"PROXION_SOLID_CUTOVER_STAGE": "not_a_number"}):
            result = client.get("stash://pod/resource")
        assert result == b"ok"
