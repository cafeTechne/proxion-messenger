"""Tests for self-contained Connect ID codec."""
import pytest

from proxion_messenger_core.connect_id import (
    encode_connect_id,
    decode_connect_id,
    is_valid_connect_id,
    CONNECT_ID_PREFIX,
)

DID = "did:key:z6MkhaXgBZDvotDkL5257faiztiGiC2QtKLGpbnnEGta2doK"
URL = "https://gateway.alice.example"


def test_connect_id_encodes_and_decodes_did_and_url():
    cid = encode_connect_id(DID, URL)
    assert cid.startswith(CONNECT_ID_PREFIX)
    assert "#" in cid

    decoded = decode_connect_id(cid)
    assert decoded["did"] == DID
    assert decoded["url"] == URL


def test_invalid_checksum_connect_id_rejected():
    cid = encode_connect_id(DID, URL)
    # Corrupt the checksum (last 4 chars)
    bad_cid = cid[:-4] + "zzzz"
    assert is_valid_connect_id(bad_cid) is False
    with pytest.raises(ValueError, match="checksum"):
        decode_connect_id(bad_cid)


def test_connect_id_roundtrip_is_stable():
    cid1 = encode_connect_id(DID, URL)
    decoded = decode_connect_id(cid1)
    cid2 = encode_connect_id(decoded["did"], decoded["url"])
    assert cid1 == cid2
    assert is_valid_connect_id(cid1)
