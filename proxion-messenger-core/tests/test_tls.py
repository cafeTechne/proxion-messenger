"""Tests: self-signed TLS cert generation."""
from __future__ import annotations
import datetime
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
