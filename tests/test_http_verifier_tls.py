import datetime as dt
import ipaddress
import json
import ssl
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from self_harness.adapters.http_verifier import HttpVerifierRunner, HttpVerifierTaskAdapter
from self_harness.corpus import TaskCorpus
from self_harness.exceptions import TaskLoadError
from self_harness.harness import initial_harness
from self_harness.types import Split, Task


def test_http_verifier_uses_operator_supplied_mtls_material(tmp_path: Path) -> None:
    pytest.importorskip("cryptography")
    tls = _write_tls_fixture(tmp_path)

    with _mtls_verifier_server(tls) as url:
        passed = HttpVerifierRunner(
            url,
            tls_ca_bundle=tls.ca_cert,
            tls_client_cert=tls.client_cert,
            tls_client_key=tls.client_key,
        ).run(_task("pass"), initial_harness())
        failed = HttpVerifierRunner(url, tls_ca_bundle=tls.ca_cert).run(_task("pass"), initial_harness())

    assert passed.passed
    assert not failed.passed
    assert failed.outcome.mechanism == "http-tls-error"


def test_http_verifier_rejects_tls_and_secret_metadata() -> None:
    adapter = HttpVerifierTaskAdapter(verifier_url="https://127.0.0.1/verify")
    corpus = TaskCorpus(
        corpus_version="1",
        corpus_id="bad-http",
        tasks=[
            Task(
                "bad",
                Split.HELD_IN,
                "http_verifier",
                "bad",
                {"verifier_selector": "pass", "tls_client_key": "secret"},
            )
        ],
    )

    with pytest.raises(TaskLoadError):
        adapter.load(corpus)


class _TlsFixture:
    def __init__(self, ca_cert: Path, server_cert: Path, server_key: Path, client_cert: Path, client_key: Path) -> None:
        self.ca_cert = ca_cert
        self.server_cert = server_cert
        self.server_key = server_key
        self.client_cert = client_cert
        self.client_key = client_key


def _write_tls_fixture(tmp_path: Path) -> _TlsFixture:
    x509 = pytest.importorskip("cryptography.x509")
    hashes = pytest.importorskip("cryptography.hazmat.primitives.hashes")
    serialization = pytest.importorskip("cryptography.hazmat.primitives.serialization")
    rsa = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.rsa")
    oid = pytest.importorskip("cryptography.x509.oid")
    NameOID = oid.NameOID
    ExtendedKeyUsageOID = oid.ExtendedKeyUsageOID

    now = dt.datetime.now(dt.UTC)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "SelfHarness Test CA")])
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_subject)
        .issuer_name(ca_subject)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=7))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    server_key, server_cert = _signed_cert(
        "localhost",
        ca_subject,
        ca_key,
        ca_cert,
        now,
        x509,
        hashes,
        rsa,
        NameOID,
        ExtendedKeyUsageOID,
        is_server=True,
    )
    client_key, client_cert = _signed_cert(
        "self-harness-client",
        ca_subject,
        ca_key,
        ca_cert,
        now,
        x509,
        hashes,
        rsa,
        NameOID,
        ExtendedKeyUsageOID,
        is_server=False,
    )

    ca_cert_path = tmp_path / "ca.pem"
    server_cert_path = tmp_path / "server.pem"
    server_key_path = tmp_path / "server.key"
    client_cert_path = tmp_path / "client.pem"
    client_key_path = tmp_path / "client.key"
    ca_cert_path.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
    server_cert_path.write_bytes(server_cert.public_bytes(serialization.Encoding.PEM))
    server_key_path.write_bytes(
        server_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    client_cert_path.write_bytes(client_cert.public_bytes(serialization.Encoding.PEM))
    client_key_path.write_bytes(
        client_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    return _TlsFixture(ca_cert_path, server_cert_path, server_key_path, client_cert_path, client_key_path)


def _signed_cert(
    common_name: str,
    ca_subject: object,
    ca_key: object,
    ca_cert: object,
    now: dt.datetime,
    x509: object,
    hashes: object,
    rsa: object,
    NameOID: object,
    ExtendedKeyUsageOID: object,
    *,
    is_server: bool,
) -> tuple[object, object]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=7))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
    )
    if is_server:
        builder = builder.add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        builder = builder.add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
    else:
        builder = builder.add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]),
            critical=False,
        )
    return key, builder.sign(private_key=ca_key, algorithm=hashes.SHA256())


@contextmanager
def _mtls_verifier_server(tls: _TlsFixture) -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.load_cert_chain(certfile=tls.server_cert, keyfile=tls.server_key)
    context.load_verify_locations(cafile=tls.ca_cert)
    context.verify_mode = ssl.CERT_REQUIRED
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"https://127.0.0.1:{server.server_port}/verify"
    finally:
        server.shutdown()
        thread.join(timeout=2)


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        json.loads(self.rfile.read(length).decode("utf-8"))
        payload = {
            "passed": True,
            "failure_category": None,
            "mechanism": "mtls-http-pass",
            "message": "ok",
        }
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def _task(id_: str) -> Task:
    return Task(
        id=id_,
        split=Split.HELD_IN,
        failure_mode="http_verifier",
        description=id_,
        metadata={"verifier_selector": "pass"},
    )
