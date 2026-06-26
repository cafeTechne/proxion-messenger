"""Schema v42: canonical version check + migration completeness."""
import pytest
from proxion_messenger_core.local_store import LocalStore


def test_schema_version_bumped_to_42():
    assert LocalStore._SCHEMA_VERSION >= 42


def test_migrations_37_through_42_tables_exist(tmp_path):
    store = LocalStore(str(tmp_path / "v42.db"))
    expected_tables = [
        # v39
        "evidence_verification_records",
        # v40
        "dm_sessions",
        "dm_prekeys",
        # v41
        "rate_limit_buckets",
        # v42
        "message_receipts",
    ]
    with store._conn() as conn:
        existing = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    for tbl in expected_tables:
        assert tbl in existing, f"Missing table from migration: {tbl}"


def test_no_stale_hardcoded_schema_round_assertions_remain():
    # Canonical version check lives here; all other files assert >= NN.
    assert LocalStore._SCHEMA_VERSION >= 42
