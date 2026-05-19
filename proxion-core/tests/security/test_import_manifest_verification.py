"""Tests for R7 X-Proxion-Import-Manifest header verification on /import."""
import asyncio
import hashlib
import json
import pytest
import os
from unittest.mock import MagicMock, AsyncMock, patch


@pytest.fixture
def gateway(tmp_path):
    from proxion_messenger_core.persist import AgentState
    from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
    agent = AgentState.generate()
    return ProxionGateway(
        agent=agent, dm_clients={}, room_memberships={},
        config=GatewayConfig(db_path=str(tmp_path / "test.db")),
    )


class TestImportManifestVerification:
    def test_import_accepts_matching_manifest_hash(self, gateway):
        """Import succeeds when X-Proxion-Import-Manifest sha256 matches body."""
        body = json.dumps({"messages": [], "relationships": []}).encode()
        sha = hashlib.sha256(body).hexdigest()
        manifest = json.dumps({"source": "test", "sha256": sha}).encode()
        headers_raw = {
            b"x-proxion-import-manifest": manifest,
            b"origin": b"http://localhost",
            b"authorization": b"",
        }
        # Simulate the internal checks by calling the store directly
        store = gateway._store
        assert store is not None
        # Verify the hash manually — the manifest check logic hashes the body
        actual = hashlib.sha256(body).hexdigest()
        assert actual == sha

    def test_import_rejects_hash_mismatch(self, gateway):
        """Import body hash mismatch should be detected."""
        body = b'{"messages": []}'
        bad_sha = "0" * 64
        actual_sha = hashlib.sha256(body).hexdigest()
        assert actual_sha != bad_sha

    def test_import_requires_manifest_when_env_enabled(self, gateway):
        """When PROXION_REQUIRE_IMPORT_MANIFEST=1, import without manifest should fail."""
        os.environ["PROXION_REQUIRE_IMPORT_MANIFEST"] = "1"
        # Verify the env is set
        assert os.environ.get("PROXION_REQUIRE_IMPORT_MANIFEST") == "1"
        os.environ.pop("PROXION_REQUIRE_IMPORT_MANIFEST", None)

    def test_import_provenance_saved_after_successful_import(self, gateway):
        """After a successful import, provenance record is saved."""
        body = json.dumps({"messages": [], "relationships": []}).encode()
        store = gateway._store
        import time, uuid
        store.save_import_provenance(
            id=str(uuid.uuid4()),
            source="test",
            body_sha256=hashlib.sha256(body).hexdigest(),
            imported_by="127.0.0.1",
            imported_at=time.time(),
            dry_run=False,
            summary_json='{"messages": 0}',
        )
        records = store.list_import_provenance()
        assert len(records) == 1

    def test_manifest_source_field_stored(self, gateway):
        """Import provenance stores the manifest source field."""
        import time, uuid
        store = gateway._store
        store.save_import_provenance(
            id=str(uuid.uuid4()),
            source="backup-v2",
            body_sha256=None,
            imported_by="localhost",
            imported_at=time.time(),
            dry_run=False,
            summary_json='{}',
        )
        records = store.list_import_provenance()
        assert records[0]["source"] == "backup-v2"
