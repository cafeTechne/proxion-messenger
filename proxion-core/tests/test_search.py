import pytest
from datetime import datetime, timezone
from proxion_messenger_core.search import search_messages, SearchResult
from proxion_messenger_core.inbox import InboxEntry

# Mock Message class for search tests
class MockMessage:
    def __init__(self, message_id, content, from_pub_hex, timestamp):
        self.message_id = message_id
        self.content = content
        self.from_pub_hex = from_pub_hex
        self.timestamp = timestamp

# Mock InboxEntry class for search tests
class MockInboxEntry:
    def __init__(self, message, thread_id, source="dm"):
        self.message = message
        self.thread_id = thread_id
        self.source = source

@pytest.fixture
def entries():
    m1 = MockMessage("1", "Hello world, this is Proxion.", "alice", 1700000000)
    m2 = MockMessage("2", "Secret meeting at noon.", "bob", 1700000001)
    m3 = MockMessage("3", "Don't forget the milk.", "alice", 1700000002)
    return [
        MockInboxEntry(m1, "t1"),
        MockInboxEntry(m2, "t2"),
        MockInboxEntry(m3, "t1")
    ]

def test_search_finds_matching_content(entries):
    results = search_messages("secret", entries)
    assert len(results) == 1
    assert results[0].message_id == "2"
    assert results[0].thread_id == "t2"

def test_search_case_insensitive_default(entries):
    # search_messages currently uses lower() on both query and content
    results = search_messages("HELLO", entries)
    assert len(results) == 1
    assert results[0].message_id == "1"

def test_search_case_sensitive_no_match(entries):
    # Note: Current implementation in search.py is case-insensitive (query_lower = query.lower())
    # So this test will FAIL if I follow the spec's requirement for case_sensitive parameter.
    # The spec A02 says "test_search_case_sensitive_no_match — Query 'hello' does NOT match 'Hello World' when case_sensitive=True."
    # I should check if search_messages supports case_sensitive.
    # Looking at my research: def search_messages(query: str, entries: List[InboxEntry], limit: int = 50) -> List[SearchResult]:
    # It does NOT have a case_sensitive parameter yet.
    # I will add the parameter to the test and expect failure (or it won't even compile if I pass the arg).
    # I'll write the test with the argument to define the contract.
    try:
        results = search_messages("HELLO", entries, case_sensitive=True)
        assert len(results) == 0
    except TypeError:
        pytest.fail("search_messages does not support case_sensitive parameter yet")

def test_search_limit_respected(entries):
    # Only 3 entries, so let's mock more if needed, or search for something common
    # m1 has alice, m3 has alice.
    # But content search... m1 has "is", m2 has "at", m3 has "the".
    # I'll search for something present in two messages.
    m1 = MockMessage("1", "X", "a", 1)
    m2 = MockMessage("2", "X", "b", 2)
    m3 = MockMessage("3", "X", "a", 3)
    ex_entries = [MockInboxEntry(m, "t") for m in [m1, m2, m3]]
    
    results = search_messages("X", ex_entries, limit=2)
    assert len(results) == 2

def test_search_snippet_contains_query(entries):
    results = search_messages("Proxion", entries)
    assert len(results) == 1
    # snippet formatting from search.py: snippet = re.sub(f"(?i)({re.escape(query)})", r"==\1==", snippet)
    assert "==Proxion==" in results[0].snippet
