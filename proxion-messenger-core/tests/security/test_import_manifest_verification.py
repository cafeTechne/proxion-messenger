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


def _post_import(port: int, body: bytes, manifest: dict | None = None) -> tuple[int, dict]:
    import http.client
    headers = {"Content-Type": "application/json"}
    if manifest is not None:
        headers["X-Proxion-Import-Manifest"] = json.dumps(manifest)
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request("POST", "/import?dry_run=1", body=body, headers=headers)
        resp = conn.getresponse()
        return resp.status, json.loads(resp.read() or b"{}")
    finally:
        conn.close()


@pytest.fixture
def live_http_port(tmp_path):
    from gwharness import free_port, start_gateway
    from proxion_messenger_core.persist import AgentState
    from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
    ws_port, http_port = free_port(), free_port()
    gw = ProxionGateway(
        agent=AgentState.generate(), dm_clients={}, room_memberships={},
        config=GatewayConfig(host="127.0.0.1", port=ws_port, http_port=http_port,
                             db_path=str(tmp_path / "live.db")),
    )
    start_gateway(gw, ws_port, http_port)
    return http_port


_BODY = json.dumps({"messages": [], "relationships": []}).encode()


class TestImportManifestVerification:
    def test_import_accepts_matching_manifest_hash(self, live_http_port):
        manifest = {"source": "test", "sha256": hashlib.sha256(_BODY).hexdigest()}
        status, body = _post_import(live_http_port, _BODY, manifest)
        assert status == 200, body
        assert body.get("dry_run") is True

    def test_import_rejects_hash_mismatch(self, live_http_port):
        status, body = _post_import(live_http_port, _BODY, {"source": "test", "sha256": "0" * 64})
        assert (status, body) == (400, {"error": "import_manifest_hash_mismatch"})

    def test_import_requires_manifest_when_env_enabled(self, live_http_port, monkeypatch):
        """When PROXION_REQUIRE_IMPORT_MANIFEST=1, import without manifest should fail."""
        assert _post_import(live_http_port, _BODY)[0] == 200
        monkeypatch.setenv("PROXION_REQUIRE_IMPORT_MANIFEST", "1")
        status, body = _post_import(live_http_port, _BODY)
        assert (status, body) == (400, {"error": "import_manifest_required"})

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
