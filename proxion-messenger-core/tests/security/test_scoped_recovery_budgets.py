"""Tests for scoped recovery budget enforcement (R15)."""
import tempfile
import time
import pytest

from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    return LocalStore(path)


def _today() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class TestIdentityScopeBudgetEnforced:
    def test_identity_scope_budget_enforced_independent_of_global_budget(self, store):
        day = _today()
        for _ in range(5):
            store.increment_scoped_budget("backup", "identity:alice@example", day)
        assert not store.check_scoped_budget("backup", "identity:alice@example", day, 5)
        assert store.check_scoped_budget("backup", "identity:bob@example", day, 5)

    def test_identity_scope_counts_are_independent_per_user(self, store):
        day = _today()
        store.increment_scoped_budget("restore", "identity:alice", day)
        store.increment_scoped_budget("restore", "identity:alice", day)
        store.increment_scoped_budget("restore", "identity:bob", day)
        assert not store.check_scoped_budget("restore", "identity:alice", day, 2)
        assert store.check_scoped_budget("restore", "identity:bob", day, 2)


class TestIpScopeBudgetEnforced:
    def test_ip_scope_budget_enforced(self, store):
        day = _today()
        limit = 3
        for _ in range(limit):
            store.increment_scoped_budget("backup", "ip:192.0.2.1", day)
        assert not store.check_scoped_budget("backup", "ip:192.0.2.1", day, limit)
        assert store.check_scoped_budget("backup", "ip:192.0.2.2", day, limit)

    def test_global_scope_tracked_separately(self, store):
        day = _today()
        store.increment_scoped_budget("backup", "global", day)
        store.increment_scoped_budget("backup", "global", day)
        assert store.check_scoped_budget("backup", "global", day, 5)
        assert not store.check_scoped_budget("backup", "global", day, 2)


class TestScopeBudgetResetOnNewDay:
    def test_scope_budget_reset_on_new_utc_day(self, store):
        yesterday = "2020-01-01"
        day = _today()
        for _ in range(10):
            store.increment_scoped_budget("restore", "identity:alice", yesterday)
        assert not store.check_scoped_budget("restore", "identity:alice", yesterday, 5)
        assert store.check_scoped_budget("restore", "identity:alice", day, 5)
