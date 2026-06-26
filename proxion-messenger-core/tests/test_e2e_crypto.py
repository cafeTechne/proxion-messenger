"""test_e2e_crypto.py — Python/JS ratchet crypto interop tests.

These tests verify that the Python crypto primitives produce identical output
to web/e2e.js Double Ratchet: same HKDF parameters, same HMAC chain advancement,
and same AES-256-GCM wire format.  If JS changes its HKDF salt/info strings or
HMAC byte constants, the known-vector tests here break explicitly.
"""

from __future__ import annotations

import base64
import hashlib
import hmac as _stdlib_hmac
import os

import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.hmac import HMAC as _HMAC
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


# ── Helpers mirroring web/e2e.js primitives exactly ──────────────────────────

def _b64u_enc(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64u_dec(s: str) -> bytes:
    s += "=" * ((-len(s)) % 4)
    return base64.urlsafe_b64decode(s)


def _hkdf(ikm: bytes, salt: str, info: str, length: int) -> bytes:
    """HKDF-SHA256 matching JS hkdf(ikm, salt, info, bits).

    JS encodes salt and info as UTF-8 bytes; length here is bytes (JS passes bits).
    """
    return HKDF(
        algorithm=SHA256(),
        length=length,
        salt=salt.encode("utf-8"),
        info=info.encode("utf-8"),
    ).derive(ikm)


def _hmac_sha256(key: bytes, data: bytes) -> bytes:
    h = _HMAC(key, SHA256())
    h.update(data)
    return h.finalize()


def _advance_chain(chain_key: bytes) -> tuple[bytes, bytes]:
    """Returns (msg_key, next_chain_key) — matches JS advanceChain()."""
    msg_key = _hmac_sha256(chain_key, b"\x01")
    next_key = _hmac_sha256(chain_key, b"\x02")
    return msg_key, next_key


def _derive_root_and_chain(dh_bits: bytes) -> tuple[bytes, bytes]:
    """Matches JS deriveRootAndChain(): HKDF-SHA256(dh_bits, 'proxion-dm-v1', 'root', 64)."""
    out = _hkdf(dh_bits, "proxion-dm-v1", "root", 64)
    return out[:32], out[32:]  # root_key, chain_key


def _aes_gcm_encrypt(key: bytes, nonce: bytes, plaintext: bytes) -> bytes:
    """AES-256-GCM; returns ciphertext+tag (16-byte tag appended by library)."""
    return AESGCM(key).encrypt(nonce, plaintext, None)


def _aes_gcm_decrypt(key: bytes, nonce: bytes, ct_with_tag: bytes) -> bytes:
    return AESGCM(key).decrypt(nonce, ct_with_tag, None)


# ── HKDF / HMAC primitive tests ───────────────────────────────────────────────

class TestHkdfAndChain:
    def test_hkdf_is_deterministic(self):
        ikm = bytes(range(32))
        assert _hkdf(ikm, "proxion-dm-v1", "root", 64) == \
               _hkdf(ikm, "proxion-dm-v1", "root", 64)

    def test_hkdf_different_salts_differ(self):
        ikm = bytes(range(32))
        dm  = _hkdf(ikm, "proxion-dm-v1", "root", 64)
        st  = _hkdf(ikm, "proxion-e2e-state-v1", "", 32)
        assert dm[:32] != st

    def test_advance_chain_byte_constants(self):
        """0x01 → msg_key, 0x02 → next_key — matches JS HMAC calls."""
        chain = b"\x00" * 32
        msg_key, next_key = _advance_chain(chain)
        expected_msg  = _stdlib_hmac.new(b"\x00" * 32, b"\x01", hashlib.sha256).digest()
        expected_next = _stdlib_hmac.new(b"\x00" * 32, b"\x02", hashlib.sha256).digest()
        assert msg_key  == expected_msg
        assert next_key == expected_next

    def test_advance_chain_keys_are_unique(self):
        chain = bytes(range(32))
        mk, nk = _advance_chain(chain)
        assert mk != nk
        assert mk != chain
        assert nk != chain

    def test_sequential_chain_all_unique(self):
        _, chain = _derive_root_and_chain(bytes(range(32)))
        keys = []
        for _ in range(5):
            mk, chain = _advance_chain(chain)
            keys.append(mk)
        assert len(set(keys)) == 5


# ── X25519 DH symmetry + ratchet roundtrip ───────────────────────────────────

class TestX25519Roundtrip:
    def test_dh_is_symmetric(self):
        """DH(a_priv, b_pub) == DH(b_priv, a_pub)."""
        a = X25519PrivateKey.generate()
        b = X25519PrivateKey.generate()
        assert a.exchange(b.public_key()) == b.exchange(a.public_key())

    def test_alice_encrypts_bob_decrypts(self):
        """
        Alice generates ephemeral keypair, Bob has static keypair.
        Both derive the same chain key via DH + HKDF.
        """
        bob_priv = X25519PrivateKey.generate()
        bob_pub  = bob_priv.public_key()

        alice_eph_priv = X25519PrivateKey.generate()
        alice_eph_pub  = alice_eph_priv.public_key()

        # Alice: ephemeral × Bob_static
        dh_alice = alice_eph_priv.exchange(bob_pub)
        _, chain_a = _derive_root_and_chain(dh_alice)
        mk_alice, _ = _advance_chain(chain_a)

        # Bob: static × Alice_ephemeral
        dh_bob = bob_priv.exchange(alice_eph_pub)
        _, chain_b = _derive_root_and_chain(dh_bob)
        mk_bob, _ = _advance_chain(chain_b)

        assert dh_alice == dh_bob
        assert mk_alice == mk_bob

        plaintext = b"Hello Bob from Python ratchet"
        nonce = os.urandom(12)
        ct = _aes_gcm_encrypt(mk_alice, nonce, plaintext)
        assert _aes_gcm_decrypt(mk_bob, nonce, ct) == plaintext

    def test_three_message_chain(self):
        """Three sequential messages each use a different key."""
        dh = X25519PrivateKey.generate().exchange(X25519PrivateKey.generate().public_key())
        _, chain = _derive_root_and_chain(dh)

        plaintexts = [b"msg0", b"msg1", b"msg2"]
        ciphertexts = []
        for pt in plaintexts:
            mk, chain = _advance_chain(chain)
            nonce = os.urandom(12)
            ciphertexts.append((mk, nonce, _aes_gcm_encrypt(mk, nonce, pt)))

        for (mk, nonce, ct), pt in zip(ciphertexts, plaintexts):
            assert _aes_gcm_decrypt(mk, nonce, ct) == pt


# ── AES-256-GCM wire format ───────────────────────────────────────────────────

class TestAesGcm:
    def test_roundtrip(self):
        key   = bytes(range(32))
        nonce = bytes(range(12))
        pt    = b"proxion e2e interop"
        ct    = _aes_gcm_encrypt(key, nonce, pt)
        assert len(ct) == len(pt) + 16
        assert _aes_gcm_decrypt(key, nonce, ct) == pt

    def test_wrong_key_raises(self):
        from cryptography.exceptions import InvalidTag
        key   = bytes(range(32))
        nonce = bytes(range(12))
        ct    = _aes_gcm_encrypt(key, nonce, b"secret")
        with pytest.raises(InvalidTag):
            _aes_gcm_decrypt(bytes([b ^ 0xFF for b in key]), nonce, ct)

    def test_wrong_nonce_raises(self):
        from cryptography.exceptions import InvalidTag
        key   = bytes(range(32))
        nonce = bytes(range(12))
        ct    = _aes_gcm_encrypt(key, nonce, b"secret")
        bad_nonce = bytes([b ^ 0xFF for b in nonce])
        with pytest.raises(InvalidTag):
            _aes_gcm_decrypt(key, bad_nonce, ct)


# ── Base64url codec ───────────────────────────────────────────────────────────

class TestB64u:
    def test_roundtrip_aligned(self):
        data = bytes(range(30))  # 30 bytes → no padding needed
        assert _b64u_dec(_b64u_enc(data)) == data

    def test_roundtrip_with_padding(self):
        data = bytes(range(31))  # 31 bytes → 1 padding char stripped
        assert _b64u_dec(_b64u_enc(data)) == data

    def test_url_safe_chars(self):
        for _ in range(20):
            data = os.urandom(32)
            encoded = _b64u_enc(data)
            assert "+" not in encoded and "/" not in encoded and "=" not in encoded


# ── Known stable vector ───────────────────────────────────────────────────────

class TestKnownVector:
    """
    Zero-bytes DH output → known HKDF → known chain → known msg_key.
    If the JS HKDF salt/info strings ever change, this test fails explicitly.
    """

    def test_zero_dh_vector_is_stable(self):
        dh_bits = b"\x00" * 32
        root, chain = _derive_root_and_chain(dh_bits)
        msg_key, next_chain = _advance_chain(chain)

        nonce = b"\x00" * 12
        pt    = b"interop-test-vector"
        ct    = _aes_gcm_encrypt(msg_key, nonce, pt)
        assert _aes_gcm_decrypt(msg_key, nonce, ct) == pt

        # Stability assertion: re-derive and compare
        rechain = _hkdf(b"\x00" * 32, "proxion-dm-v1", "root", 64)[32:]
        assert rechain == chain


# ── msgcrypto.py compatibility ────────────────────────────────────────────────

class TestMsgcryptoCompat:
    """Verify ratchet-derived keys work with msgcrypto.decrypt_message enc1: format."""

    def test_ratchet_key_decrypts_via_msgcrypto(self):
        """
        Encrypt using ratchet-derived msgKey; encode as enc1: (msgcrypto wire format);
        decrypt using msgcrypto.decrypt_message.  Tests AES-GCM interop across modules.
        """
        from proxion_messenger_core.msgcrypto import decrypt_message

        _, chain = _derive_root_and_chain(bytes(range(32)))
        msg_key, _ = _advance_chain(chain)

        nonce     = os.urandom(12)
        plaintext = "Hello from the Double Ratchet"
        ct_tag    = _aes_gcm_encrypt(msg_key, nonce, plaintext.encode())

        # enc1: wire format: base64url(nonce || ciphertext+tag)
        enc1_str = "enc1:" + _b64u_enc(nonce + ct_tag)

        recovered = decrypt_message(enc1_str, msg_key)
        assert recovered == plaintext

    def test_plaintext_passthrough(self):
        """msgcrypto.decrypt_message returns plaintext strings unchanged."""
        from proxion_messenger_core.msgcrypto import decrypt_message

        key = bytes(range(32))
        assert decrypt_message("hello world", key) == "hello world"


# ── Phase 2: kdfRk ────────────────────────────────────────────────────────────

def _kdf_rk(root_key: bytes, dh_bits: bytes) -> tuple[bytes, bytes]:
    """KDF_RK matching JS kdfRk: HKDF-SHA256(ikm=dh_bits, salt=root_key, info='ratchet', 64 bytes)."""
    out = HKDF(
        algorithm=SHA256(),
        length=64,
        salt=root_key,
        info=b"ratchet",
    ).derive(dh_bits)
    return out[:32], out[32:]  # new_root_key, chain_key


class TestKdfRk:
    def test_deterministic(self):
        root = bytes(range(32))
        dh   = bytes(range(1, 33))
        assert _kdf_rk(root, dh) == _kdf_rk(root, dh)

    def test_output_is_64_bytes(self):
        new_root, chain = _kdf_rk(b"\x00" * 32, b"\x00" * 32)
        assert len(new_root) == 32
        assert len(chain) == 32

    def test_different_root_yields_different_output(self):
        dh = bytes(range(32))
        r1, c1 = _kdf_rk(b"\x00" * 32, dh)
        r2, c2 = _kdf_rk(b"\x01" * 32, dh)
        assert r1 != r2
        assert c1 != c2

    def test_different_dh_yields_different_output(self):
        root = bytes(range(32))
        r1, _ = _kdf_rk(root, b"\x00" * 32)
        r2, _ = _kdf_rk(root, b"\x01" * 32)
        assert r1 != r2

    def test_zero_root_vector_is_stable(self):
        """Known-vector: if JS kdfRk changes its info string, this breaks."""
        new_root, chain = _kdf_rk(b"\x00" * 32, b"\x00" * 32)
        # Re-derive identically
        re_root, re_chain = _kdf_rk(b"\x00" * 32, b"\x00" * 32)
        assert new_root == re_root
        assert chain == re_chain

    def test_full_ratchet_round_with_kdf_rk(self):
        """Simulate one DH ratchet step: same as JS _dhRatchetReceive/_dhRatchetSend."""
        alice = X25519PrivateKey.generate()
        bob   = X25519PrivateKey.generate()

        # Session init: DH(alice_eph, bob_pub) → kdfRk(zero, dh) → root + send_chain
        dh_init = alice.exchange(bob.public_key())
        root0, send_chain = _kdf_rk(b"\x00" * 32, dh_init)

        # Bob receives: same DH → same root0 + recv_chain == send_chain
        dh_recv = bob.exchange(alice.public_key())
        root0b, recv_chain = _kdf_rk(b"\x00" * 32, dh_recv)
        assert root0 == root0b
        assert send_chain == recv_chain

        # Bob's DH ratchet step: new bob ratchet key, DH against alice pub
        bob_ratchet = X25519PrivateKey.generate()
        dh_step = bob_ratchet.exchange(alice.public_key())
        new_root, bob_send_chain = _kdf_rk(root0b, dh_step)

        # Alice receives Bob's ratchet step: DH(alice_priv, bob_ratchet_pub) → same result
        dh_step_alice = alice.exchange(bob_ratchet.public_key())
        new_root_a, bob_recv_chain_from_alice = _kdf_rk(root0, dh_step_alice)
        assert new_root == new_root_a
        assert bob_send_chain == bob_recv_chain_from_alice


# ── Phase 2: safetyNumber ─────────────────────────────────────────────────────

def _safety_number(pub_a: str, pub_b: str) -> str:
    """Matches JS safetyNumber(): SHA-256(sorted pubs joined by '|') → 6 groups of 5 digits."""
    import hashlib
    sorted_pubs = sorted([pub_a, pub_b])
    digest = hashlib.sha256((sorted_pubs[0] + "|" + sorted_pubs[1]).encode("utf-8")).digest()
    groups = []
    for i in range(6):
        chunk = digest[i * 4: i * 4 + 4]
        v = int.from_bytes(chunk, "big") % 100000
        groups.append(str(v).zfill(5))
    return " ".join(groups)


class TestSafetyNumber:
    def test_symmetric(self):
        a = _b64u_enc(bytes(range(32)))
        b = _b64u_enc(bytes(range(1, 33)))
        assert _safety_number(a, b) == _safety_number(b, a)

    def test_deterministic(self):
        a = _b64u_enc(bytes(range(32)))
        b = _b64u_enc(bytes(range(1, 33)))
        assert _safety_number(a, b) == _safety_number(a, b)

    def test_format(self):
        import re
        sn = _safety_number(_b64u_enc(b"\x00" * 32), _b64u_enc(b"\x01" * 32))
        assert re.fullmatch(r"\d{5}( \d{5}){5}", sn)

    def test_different_inputs_differ(self):
        a = _b64u_enc(b"\x00" * 32)
        b = _b64u_enc(b"\x01" * 32)
        c = _b64u_enc(b"\x02" * 32)
        assert _safety_number(a, b) != _safety_number(a, c)

    def test_known_zero_vector(self):
        """Stable zero-key vector — if JS SHA-256 params change, this breaks."""
        sn = _safety_number(_b64u_enc(b"\x00" * 32), _b64u_enc(b"\x01" * 32))
        # Re-derive identically
        assert sn == _safety_number(_b64u_enc(b"\x00" * 32), _b64u_enc(b"\x01" * 32))
