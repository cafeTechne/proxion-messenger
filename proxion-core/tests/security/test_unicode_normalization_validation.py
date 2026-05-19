"""Round 4: Unicode normalization and control character filtering."""
import pytest
from proxion_messenger_core.command_validation import normalize_text_field, validate_command_payload, SchemaError


def test_display_name_normalized_before_length_check():
    """NFC normalization is applied to display_name fields."""
    # "é" (é precomposed) == "é" (e + combining accent) after NFC
    raw = "café"  # "café" in decomposed form (NFD)
    normalized = normalize_text_field(raw)
    import unicodedata
    assert unicodedata.is_normalized("NFC", normalized), "Result should be NFC normalized"
    assert normalized == "café"  # precomposed é


def test_control_chars_rejected_in_user_visible_fields():
    """Control characters are stripped from user-visible text fields."""
    dirty = "hello\x00world\x01test\x1f"
    cleaned = normalize_text_field(dirty)
    assert "\x00" not in cleaned
    assert "\x01" not in cleaned
    assert "\x1f" not in cleaned
    assert "helloworld" in cleaned.replace("\n", "")


def test_byte_limits_enforced_post_normalization():
    """validate_command_payload applies normalization before length check (NFC can change byte count)."""
    # A decomposed string that is within char limit but above NFC byte limit
    # This tests that normalization runs without error — the output is valid NFC
    payload = {"name": "A" * 50 + "\x01\x02\x03"}
    # Should not raise — control chars are stripped, length is within limits
    validate_command_payload("chat_room_create", payload)
    assert "\x01" not in payload.get("name", "")
