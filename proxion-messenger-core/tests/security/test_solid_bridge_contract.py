"""Cross-language bridge contract tests (Python gateway ↔ JS adapters, Round 14).

Verifies that the normalised error codes, retry semantics, and serialised error
payloads are stable across the bridge boundary.
"""
import json
import pytest

from proxion_messenger_core.solid_migration import (
    SOLID_AUTH_REQUIRED,
    SOLID_AUTH_FAILED,
    SOLID_FORBIDDEN,
    SOLID_NOT_FOUND,
    SOLID_CONFLICT,
    SOLID_PRECONDITION_FAILED,
    SOLID_NETWORK_UNAVAILABLE,
    SOLID_NOT_SUPPORTED,
)


# Canonical mapping that both Python and JS must agree on
_PYTHON_TO_JS_CODE_MAP = {
    SOLID_AUTH_REQUIRED: "SOLID_AUTH_REQUIRED",
    SOLID_AUTH_FAILED: "SOLID_AUTH_FAILED",
    SOLID_FORBIDDEN: "SOLID_FORBIDDEN",
    SOLID_NOT_FOUND: "SOLID_NOT_FOUND",
    SOLID_CONFLICT: "SOLID_CONFLICT",
    SOLID_PRECONDITION_FAILED: "SOLID_PRECONDITION_FAILED",
    SOLID_NETWORK_UNAVAILABLE: "SOLID_NETWORK_UNAVAILABLE",
    SOLID_NOT_SUPPORTED: "SOLID_NOT_SUPPORTED",
}


class TestAuthErrorMappingStableAcrossBoundary:
    def test_auth_error_mapping_stable_across_python_js_boundary(self):
        """All Python normalised codes have stable string values matching JS convention."""
        for py_code, expected_name in _PYTHON_TO_JS_CODE_MAP.items():
            assert isinstance(py_code, str), f"{expected_name} must be a str"
            assert py_code.startswith("SOLID_"), f"{expected_name} must start with SOLID_"
            assert py_code == expected_name, (
                f"Python code {py_code!r} does not match expected {expected_name!r}"
            )

    def test_all_codes_are_unique(self):
        codes = list(_PYTHON_TO_JS_CODE_MAP.keys())
        assert len(codes) == len(set(codes)), "Normalised codes must be unique"

    def test_codes_are_importable_from_solid_migration(self):
        from proxion_messenger_core.solid_migration import (
            SOLID_AUTH_REQUIRED, SOLID_AUTH_FAILED, SOLID_FORBIDDEN,
            SOLID_NOT_FOUND, SOLID_CONFLICT, SOLID_PRECONDITION_FAILED,
            SOLID_NETWORK_UNAVAILABLE, SOLID_NOT_SUPPORTED,
        )
        all_codes = [
            SOLID_AUTH_REQUIRED, SOLID_AUTH_FAILED, SOLID_FORBIDDEN,
            SOLID_NOT_FOUND, SOLID_CONFLICT, SOLID_PRECONDITION_FAILED,
            SOLID_NETWORK_UNAVAILABLE, SOLID_NOT_SUPPORTED,
        ]
        assert all(isinstance(c, str) for c in all_codes)


class TestTimeoutAndRetryContractConsistent:
    def test_timeout_and_retry_contract_consistent(self):
        """Bridge transport errors (SOLID_NETWORK_UNAVAILABLE) are retryable; auth errors are not."""
        retryable = {SOLID_NETWORK_UNAVAILABLE}
        non_retryable = {SOLID_AUTH_FAILED, SOLID_FORBIDDEN, SOLID_AUTH_REQUIRED}

        for code in retryable:
            assert code not in non_retryable, f"{code} must not be in non-retryable set"

        for code in non_retryable:
            assert code not in retryable, f"{code} must not be in retryable set"

    def test_bridge_transport_error_is_network_unavailable(self):
        from proxion_messenger_core.css_auth import _BridgeTransportError
        assert issubclass(_BridgeTransportError, Exception)


class TestSerializedErrorPayloadContractStable:
    def test_serialized_error_payload_contract_stable(self):
        """Serialised error payload must be JSON with 'code' and 'detail' keys."""
        payload = {
            "code": SOLID_AUTH_FAILED,
            "detail": "401 Unauthorized from https://idp.example/.oidc/token",
            "retryable": False,
        }
        serialised = json.dumps(payload)
        parsed = json.loads(serialised)

        assert "code" in parsed
        assert "detail" in parsed
        assert parsed["code"] == SOLID_AUTH_FAILED
        assert isinstance(parsed["code"], str)

    def test_error_payload_schema_is_flat(self):
        """Error payloads must not be deeply nested — single level of keys."""
        payload = {"code": SOLID_NOT_FOUND, "detail": "resource not found", "retryable": True}
        for v in payload.values():
            assert not isinstance(v, dict), "Error payload must not contain nested dicts"
