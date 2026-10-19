"""Self-signed TLS certificate generation for first-run gateway setup.

Generates an RSA-2048 / SHA-256 cert valid for 365 days with SAN for localhost
and the machine hostname. Idempotent: returns existing paths if cert is still valid.

The cert is a leaf (CA:FALSE, serverAuth only), so a user who trusts it to get
past the browser warning has not trusted a key that could sign certs for other
sites. The key file is readable by its owner only.
"""
from __future__ import annotations
import datetime
import ipaddress
import os
import socket
from pathlib import Path


def ensure_self_signed_cert(cert_dir: Path) -> tuple[Path, Path]:
    """Return (cert_pem_path, key_pem_path), generating them if absent or expired."""
    cert_dir.mkdir(parents=True, exist_ok=True)
    cert_path = cert_dir / "cert.pem"
    key_path = cert_dir / "key.pem"

    if cert_path.exists() and key_path.exists():
        if _cert_still_valid(cert_path):
            _restrict_key_file(key_path)
            return cert_path, key_path

    _generate(cert_path, key_path)
    return cert_path, key_path


def _cert_still_valid(cert_path: Path) -> bool:
    try:
        from cryptography import x509
        from cryptography.hazmat.backends import default_backend
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes(), default_backend())
        # cryptography >=42 has not_valid_after_utc (timezone-aware);
        # older versions have not_valid_after (timezone-naive, UTC).
        try:
            expiry = cert.not_valid_after_utc
            unexpired = expiry > datetime.datetime.now(datetime.timezone.utc)
        except AttributeError:
            expiry = cert.not_valid_after  # type: ignore[attr-defined]
            unexpired = expiry > datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        if not unexpired:
            return False
        # Certs from older versions were marked CA:TRUE. Replace them so a
        # trusted self-signed cert can never act as a signing authority.
        try:
            bc = cert.extensions.get_extension_for_class(x509.BasicConstraints).value
            if bc.ca:
                return False
        except x509.ExtensionNotFound:
            pass
        return True
    except Exception:
        return False


def _restrict_key_file(key_path: Path) -> None:
    """Owner-only permissions on the private key (no-op where chmod is not POSIX)."""
    try:
        os.chmod(key_path, 0o600)
    except OSError:
        pass


def _generate(cert_path: Path, key_path: Path) -> None:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.backends import default_backend
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    key = rsa.generate_private_key(
        public_exponent=65537, key_size=2048, backend=default_backend()
    )

    hostname = socket.gethostname()
    subject = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "proxion-gateway"),
    ])
    san_entries: list[x509.GeneralName] = [
        x509.DNSName("localhost"),
        x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
        x509.IPAddress(ipaddress.IPv6Address("::1")),
    ]
    if hostname and hostname != "localhost":
        try:
            san_entries.append(x509.DNSName(hostname))
        except Exception:
            pass

    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=365))
        .add_extension(x509.SubjectAlternativeName(san_entries), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(
            digital_signature=True, key_encipherment=True, content_commitment=False,
            data_encipherment=False, key_agreement=False, key_cert_sign=False,
            crl_sign=False, encipher_only=False, decipher_only=False,
        ), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(key, hashes.SHA256(), default_backend())
    )

    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )
    # Create the key owner-only from the start rather than chmod-ing after the
    # write, so it is never briefly world-readable.
    try:
        key_path.unlink()
    except FileNotFoundError:
        pass
    fd = os.open(str(key_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(key_pem)
    _restrict_key_file(key_path)
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
