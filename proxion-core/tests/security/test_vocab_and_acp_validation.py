"""Tests for ACP term allowlist validation and policy generation stability."""
import pytest

from proxion_messenger_core.acp import (
    validate_acp_predicates, _KNOWN_ACP_PREDICATES,
    set_acp_policy,
)


class TestCoreTermsSourcedFromVocabPackages:
    def test_known_predicates_nonempty(self):
        """_KNOWN_ACP_PREDICATES must contain at least the core ACP terms."""
        assert "allow" in _KNOWN_ACP_PREDICATES
        assert "agent" in _KNOWN_ACP_PREDICATES
        assert "allOf" in _KNOWN_ACP_PREDICATES
        assert "policy" in _KNOWN_ACP_PREDICATES

    def test_known_predicates_includes_mode_terms(self):
        """ACL mode terms (Read/Write/Control) must be in the allowlist."""
        for term in ("Read", "Write", "Control", "Append"):
            assert term in _KNOWN_ACP_PREDICATES, f"Mode term {term!r} missing from allowlist"

    def test_known_predicates_is_frozenset(self):
        assert isinstance(_KNOWN_ACP_PREDICATES, frozenset)


class TestAcpRejectsUnknownCriticalPredicates:
    def test_valid_policy_passes_validation(self):
        """A well-formed ACP policy with known predicates validates cleanly."""
        policy = {
            "@context": "http://www.w3.org/ns/solid/acp#",
            "policy": {
                "allow": ["Read"],
                "allOf": [{"agent": "https://bob.example/profile#me"}],
            },
            "owner": {
                "allow": ["Read", "Write", "Control"],
                "allOf": [{"agent": "https://alice.example/profile#me"}],
            },
        }
        validate_acp_predicates(policy)  # should not raise

    def test_unknown_predicate_raises(self):
        """An unknown top-level predicate raises ValueError."""
        policy = {
            "@context": "http://www.w3.org/ns/solid/acp#",
            "policy": {"allow": ["Read"]},
            "maliciousInject": {"agent": "https://attacker.example/profile#me"},
        }
        with pytest.raises(ValueError, match="Unknown ACP predicate"):
            validate_acp_predicates(policy)

    def test_unknown_nested_predicate_raises(self):
        """An unknown predicate nested inside a known key raises ValueError."""
        policy = {
            "@context": "http://www.w3.org/ns/solid/acp#",
            "policy": {
                "allow": ["Read"],
                "xInjectAgent": "https://attacker.example/profile#me",
            },
        }
        with pytest.raises(ValueError, match="Unknown ACP predicate"):
            validate_acp_predicates(policy)

    def test_json_ld_keywords_are_ignored(self):
        """JSON-LD @ keywords (@context, @type) are never flagged as unknown."""
        policy = {
            "@context": "http://www.w3.org/ns/solid/acp#",
            "@type": "AccessControlResource",
            "policy": {"allow": ["Read"]},
        }
        validate_acp_predicates(policy)  # should not raise

    def test_non_dict_raises_type_error(self):
        with pytest.raises(TypeError):
            validate_acp_predicates(["not", "a", "dict"])


class TestPolicyGenerationStableWithVocabConstants:
    def test_set_acp_policy_puts_valid_json_ld(self):
        """set_acp_policy generates a document whose predicates pass validation."""
        from unittest.mock import MagicMock
        client = MagicMock()
        set_acp_policy(
            client,
            "stash://rooms/r1/",
            "https://alice.example/profile#me",
            "https://bob.example/profile#me",
            subject_modes=["Read"],
        )
        client.put.assert_called_once()
        import json
        _url, body, *_ = client.put.call_args[0]
        doc = json.loads(body.decode("utf-8"))
        validate_acp_predicates(doc)
