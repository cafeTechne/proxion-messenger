"""Tests: self-signed TLS cert generation."""
from __future__ import annotations
import datetime
import os
import pytest
from proxion_messenger_core.tls import ensure_self_signed_cert


def test_ensure_creates_cert_and_key(tmp_path):
    """First call creates cert.pem and key.pem."""
    cert, key = ensure_self_signed_cert(tmp_path)
    assert cert.exists()
    assert key.exists()
    assert cert.name == "cert.pem"
    assert key.name == "key.pem"


def test_ensure_idempotent(tmp_path):
    """Second call returns same paths without regenerating."""
    cert1, key1 = ensure_self_signed_cert(tmp_path)
    mtime1 = cert1.stat().st_mtime
    cert2, key2 = ensure_self_signed_cert(tmp_path)
    assert cert1 == cert2
    assert cert2.stat().st_mtime == mtime1  # file not touched


def test_cert_san_includes_localhost(tmp_path):
    """Generated cert has localhost in SubjectAlternativeName."""
    from cryptography import x509
    from cryptography.hazmat.backends import default_backend
    cert_path, _ = ensure_self_signed_cert(tmp_path)
    cert = x509.load_pem_x509_certificate(cert_path.read_bytes(), default_backend())
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
    dns_names = san.value.get_values_for_type(x509.DNSName)
    assert "localhost" in dns_names


def _load(cert_path):
    from cryptography import x509
    from cryptography.hazmat.backends import default_backend
    return x509.load_pem_x509_certificate(cert_path.read_bytes(), default_backend())


def test_cert_is_a_leaf_not_a_ca(tmp_path):
    """A user who trusts the self-signed cert must not be trusting a CA that
    could sign certs for any other site."""
    from cryptography import x509
    cert = _load(ensure_self_signed_cert(tmp_path)[0])
    bc = cert.extensions.get_extension_for_class(x509.BasicConstraints).value
    assert bc.ca is False
    ku = cert.extensions.get_extension_for_class(x509.KeyUsage).value
    assert ku.key_cert_sign is False
    eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    assert list(eku) == [x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]


def test_legacy_ca_cert_is_replaced(tmp_path):
    """A still-valid CA:TRUE cert written by an older version is regenerated."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "proxion-gateway")])
    now = datetime.datetime.now(datetime.timezone.utc)
    legacy = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
              .public_key(key.public_key()).serial_number(x509.random_serial_number())
              .not_valid_before(now).not_valid_after(now + datetime.timedelta(days=300))
              .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
              .sign(key, hashes.SHA256()))
    (tmp_path / "cert.pem").write_bytes(legacy.public_bytes(serialization.Encoding.PEM))
    (tmp_path / "key.pem").write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption()))
    cert = _load(ensure_self_signed_cert(tmp_path)[0])
    assert cert.serial_number != legacy.serial_number
    assert cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca is False


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
def test_key_file_is_owner_only(tmp_path):
    _, key = ensure_self_signed_cert(tmp_path)
    assert key.stat().st_mode & 0o077 == 0
    # An existing key left group/world-readable by an older version is tightened.
    os.chmod(key, 0o644)
    ensure_self_signed_cert(tmp_path)
    assert key.stat().st_mode & 0o077 == 0
