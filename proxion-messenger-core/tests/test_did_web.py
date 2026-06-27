"""Tests for did:web resolution (C4, gated)."""
import json
import os

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from proxion_messenger_core import did_web
from proxion_messenger_core.didkey import pub_key_to_did


def _make_pub():
    priv = Ed25519PrivateKey.generate()
    raw = priv.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return raw


def _make_did_doc(did, pub_bytes):
    # publicKeyMultibase == the base58 suffix of the did:key for this pub key
    multibase = pub_key_to_did(pub_bytes)[len("did:key:"):]  # "z6Mk..."
    return {
        "id": did,
        "verificationMethod": [{
            "id": f"{did}#key-1",
            "type": "Ed25519VerificationKey2020",
            "controller": did,
            "publicKeyMultibase": multibase,
        }],
    }


@pytest.fixture(autouse=True)
def _clear_cache():
    did_web._CACHE.clear()
    yield
    did_web._CACHE.clear()


# ---- did_web_to_url ----

def test_url_bare_domain():
    assert did_web.did_web_to_url("did:web:example.com") == "https://example.com/.well-known/did.json"


def test_url_with_path():
    assert did_web.did_web_to_url("did:web:example.com:u:alice") == "https://example.com/u/alice/did.json"


def test_url_with_port_percent_encoded():
    assert did_web.did_web_to_url("did:web:example.com%3A8443") == "https://example.com:8443/.well-known/did.json"


@pytest.mark.parametrize("bad", ["did:key:z6Mk", "did:web:", "did:web:bad/domain", "did:web:..:x"])
def test_url_rejects_malformed(bad):
    with pytest.raises(ValueError):
        did_web.did_web_to_url(bad)


# ---- _extract_ed25519_pub ----

def test_extract_returns_pub_bytes():
    pub = _make_pub()
    did = "did:web:example.com"
    assert did_web._extract_ed25519_pub(_make_did_doc(did, pub), did) == pub


def test_extract_rejects_id_mismatch():
    pub = _make_pub()
    doc = _make_did_doc("did:web:evil.com", pub)
    with pytest.raises(ValueError):
        did_web._extract_ed25519_pub(doc, "did:web:example.com")


def test_extract_rejects_no_ed25519_method():
    doc = {"id": "did:web:example.com", "verificationMethod": [
        {"type": "JsonWebKey2020", "publicKeyJwk": {"kty": "EC"}},
    ]}
    with pytest.raises(ValueError):
        did_web._extract_ed25519_pub(doc, "did:web:example.com")


# ---- resolve_did_web (gated + cached) ----

@pytest.mark.asyncio
async def test_resolve_disabled_by_default(monkeypatch):
    monkeypatch.delenv("PROXION_ENABLE_DID_WEB", raising=False)
    with pytest.raises(ValueError, match="disabled"):
        await did_web.resolve_did_web("did:web:example.com")


@pytest.mark.asyncio
async def test_resolve_fetches_and_extracts(monkeypatch):
    monkeypatch.setenv("PROXION_ENABLE_DID_WEB", "1")
    pub = _make_pub()
    did = "did:web:example.com"
    fetched = {"url": None}

    async def fake_get(url, **kw):
        fetched["url"] = url
        return json.dumps(_make_did_doc(did, pub)).encode()

    monkeypatch.setattr("proxion_messenger_core.network.async_safe_get", fake_get)
    result = await did_web.resolve_did_web(did)
    assert result == pub
    assert fetched["url"] == "https://example.com/.well-known/did.json"


@pytest.mark.asyncio
async def test_resolve_caches(monkeypatch):
    monkeypatch.setenv("PROXION_ENABLE_DID_WEB", "1")
    pub = _make_pub()
    did = "did:web:example.com"
    calls = {"n": 0}

    async def fake_get(url, **kw):
        calls["n"] += 1
        return json.dumps(_make_did_doc(did, pub)).encode()

    monkeypatch.setattr("proxion_messenger_core.network.async_safe_get", fake_get)
    await did_web.resolve_did_web(did)
    await did_web.resolve_did_web(did)
    assert calls["n"] == 1  # second call served from cache


@pytest.mark.asyncio
async def test_resolve_rejects_bad_json(monkeypatch):
    monkeypatch.setenv("PROXION_ENABLE_DID_WEB", "1")

    async def fake_get(url, **kw):
        return b"not json{"

    monkeypatch.setattr("proxion_messenger_core.network.async_safe_get", fake_get)
    with pytest.raises(ValueError):
        await did_web.resolve_did_web("did:web:example.com")
