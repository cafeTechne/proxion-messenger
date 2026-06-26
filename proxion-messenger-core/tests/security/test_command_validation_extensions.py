"""Round 1: Extended command_validation schema tests."""
import pytest

from proxion_messenger_core.command_validation import validate_command_payload, SchemaError


# ---------------------------------------------------------------------------
# resolve_did
# ---------------------------------------------------------------------------

def test_resolve_did_accepts_valid_did_key():
    validate_command_payload("resolve_did", {"did": "did:key:z6MkhaXgBZDvotDkL5257faiztiGiC2QtKLGpbnnEGta2doK"})


def test_resolve_did_rejects_invalid_format():
    with pytest.raises(SchemaError, match="invalid value"):
        validate_command_payload("resolve_did", {"did": "did:web:example.com"})


def test_resolve_did_rejects_missing_field():
    with pytest.raises(SchemaError, match="did"):
        validate_command_payload("resolve_did", {})


def test_resolve_did_rejects_non_base58_char():
    with pytest.raises(SchemaError, match="invalid value"):
        validate_command_payload("resolve_did", {"did": "did:key:z0InvalidChar"})


# ---------------------------------------------------------------------------
# connect_css
# ---------------------------------------------------------------------------

def test_connect_css_schema_enforced():
    """connect_css requires css_url and email."""
    with pytest.raises(SchemaError, match="css_url"):
        validate_command_payload("connect_css", {"email": "a@b.com"})


def test_connect_css_rejects_non_https_url():
    with pytest.raises(SchemaError, match="invalid value"):
        validate_command_payload("connect_css", {
            "css_url": "ftp://evil.example.com", "email": "a@b.com"
        })


def test_connect_css_accepts_https():
    validate_command_payload("connect_css", {
        "css_url": "https://pod.example.com", "email": "user@example.com"
    })


def test_connect_css_accepts_http():
    validate_command_payload("connect_css", {
        "css_url": "http://localhost:3000", "email": "user@example.com"
    })


def test_connect_css_rejects_overlong_url():
    with pytest.raises(SchemaError, match="exceeds max length"):
        validate_command_payload("connect_css", {
            "css_url": "https://" + "a" * 250,
            "email": "a@b.com",
        })


def test_connect_css_rejects_overlong_email():
    with pytest.raises(SchemaError, match="exceeds max length"):
        validate_command_payload("connect_css", {
            "css_url": "https://example.com",
            "email": "a" * 255 + "@example.com",
        })


# ---------------------------------------------------------------------------
# create_webhook
# ---------------------------------------------------------------------------

def test_create_webhook_schema_enforced():
    """create_webhook requires bot_name and direction."""
    with pytest.raises(SchemaError, match="bot_name|direction"):
        validate_command_payload("create_webhook", {})


def test_create_webhook_rejects_invalid_direction():
    with pytest.raises(SchemaError, match="invalid value"):
        validate_command_payload("create_webhook", {
            "bot_name": "MyBot", "direction": "sideways"
        })


def test_create_webhook_accepts_incoming():
    validate_command_payload("create_webhook", {
        "bot_name": "MyBot", "direction": "incoming"
    })


def test_create_webhook_accepts_outgoing():
    validate_command_payload("create_webhook", {
        "bot_name": "MyBot", "direction": "outgoing"
    })


def test_create_webhook_rejects_overlong_bot_name():
    with pytest.raises(SchemaError, match="exceeds max length"):
        validate_command_payload("create_webhook", {
            "bot_name": "B" * 33, "direction": "incoming"
        })


# ---------------------------------------------------------------------------
# set_member_role
# ---------------------------------------------------------------------------

def test_set_member_role_accepts_valid_roles():
    for role in ("owner", "admin", "mod", "member"):
        validate_command_payload("set_member_role", {
            "room_id": "r1", "webid": "did:key:z1", "role": role
        })


def test_set_member_role_rejects_invalid_role():
    with pytest.raises(SchemaError, match="invalid value"):
        validate_command_payload("set_member_role", {
            "room_id": "r1", "webid": "did:key:z1", "role": "superuser"
        })
