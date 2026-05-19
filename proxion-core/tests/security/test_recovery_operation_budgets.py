"""R11: Recovery operation budget enforcement tests."""
import pytest

from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


def test_restore_budget_enforced_per_day(store):
    day = "2099-01-01"
    # Consume all 3 slots
    for _ in range(3):
        store.increment_operation_budget("restore", day_key=day)
    assert store.check_operation_budget("restore", limit=3, day_key=day) is False


def test_import_budget_enforced_per_day(store):
    day = "2099-01-02"
    for _ in range(10):
        store.increment_operation_budget("import", day_key=day)
    assert store.check_operation_budget("import", limit=10, day_key=day) is False


def test_backup_export_budget_enforced_per_day(store):
    day = "2099-01-03"
    for _ in range(20):
        store.increment_operation_budget("backup_export", day_key=day)
    assert store.check_operation_budget("backup_export", limit=20, day_key=day) is False


def test_budget_resets_on_new_utc_day(store):
    """Budget for yesterday must not count toward today."""
    yesterday = "2000-01-01"
    today = "2000-01-02"
    for _ in range(3):
        store.increment_operation_budget("restore", day_key=yesterday)
    # Today is a new day — budget counter is fresh
    assert store.check_operation_budget("restore", limit=3, day_key=today) is True


def test_budget_within_limit_returns_true(store):
    day = "2099-01-04"
    store.increment_operation_budget("restore", day_key=day)
    store.increment_operation_budget("restore", day_key=day)
    assert store.check_operation_budget("restore", limit=3, day_key=day) is True


def test_increment_returns_current_count(store):
    day = "2099-01-05"
    count1 = store.increment_operation_budget("restore", day_key=day)
    count2 = store.increment_operation_budget("restore", day_key=day)
    assert count1 == 1
    assert count2 == 2
