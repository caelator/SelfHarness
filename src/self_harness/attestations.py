from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal, Protocol, cast

from self_harness.types import stable_json_dumps

ATTESTATION_SCHEMA_VERSION = "1.0"
ATTESTATION_TRUST_ROOT_SCHEMA_VERSION = "1.0"
ATTESTATION_BOUNDARY = (
    "release/operator attestation pre-validation only; structural mode checks local attestation shape, "
    "material digests, certificate identity, and transparency-log fields without contacting Fulcio, Rekor, "
    "PyPI, Sigstore, Harbor, Docker, registries, scanners, models, or cloud providers, and is not benchmark "
    "reproduction evidence"
)
PYPI_ATTESTATION_TYPES = frozenset(
    {
        "https://docs.pypi.org/attestations/publish/v1",
        "https://pypi.org/attestations/publish/v1",
    }
)
SIGSTORE_BUNDLE_TYPES = frozenset({"sigstore", "sigstore-bundle"})
BackendName = Literal["structural", "sigstore"]


@dataclass(frozen=True)
class AttestationTrustRoot:
    schema_version: str
    expected_certificate_issuer: str
    allowed_subject_alternative_names: tuple[str, ...]
    fulcio_certificate_paths: tuple[Path, ...]
    rekor_public_key_path: Path
    path: Path
    sigstore_client_trust_config_path: Path | None = None
    sigstore_trusted_root_path: Path | None = None


@dataclass(frozen=True)
class SigstoreBundle:
    certificate_chain_pem: tuple[str, ...]
    signature_b64: str
    tlog_entries: tuple[dict[str, object], ...]
    raw_bundle: dict[str, object]
    bundle_type: str | None = None


@dataclass(frozen=True)
class PyPIAttestation:
    attestation_type: str
    materials: tuple[dict[str, object], ...]
    claim: dict[str, object]
    bundle: SigstoreBundle


@dataclass(frozen=True)
class AttestationCheck:
    name: str
    status: str
    detail: str
    path: str | None = None
    metadata: dict[str, object] | None = None


@dataclass(frozen=True)
class AttestationVerificationReport:
    schema_version: str
    attestation_path: str
    material_path: str
    material_sha256: str | None
    trust_root_path: str
    backend: str
    cryptographic_valid: bool | None
    ok: bool
    checks: tuple[AttestationCheck, ...]
    report_hash: str
    reproduction_claimed: bool
    boundary: str


class AttestationError(RuntimeError):
    """Raised when attestation material is missing, malformed, or unsafe to trust."""


class AttestationVerifierBackend(Protocol):
    name: str

    def verify(
        self,
        *,
        attestation_path: Path,
        material_path: Path,
        trust_root: AttestationTrustRoot,
    ) -> bool | None:
        """Return cryptographic validity when this backend can prove it."""


class StructuralAttestationVerifier:
    name = "structural"

    def verify(
        self,
        *,
        attestation_path: Path,
        material_path: Path,
        trust_root: AttestationTrustRoot,
    ) -> bool | None:
        return None


class SigstorePythonVerifier:
    name = "sigstore"

    def __init__(
        self,
        verify_callable: Callable[[Path, Path, AttestationTrustRoot], bool] | None = None,
    ) -> None:
        self._verify_callable = verify_callable

    def verify(
        self,
        *,
        attestation_path: Path,
        material_path: Path,
        trust_root: AttestationTrustRoot,
    ) -> bool:
        if self._verify_callable is not None:
            return self._verify_callable(attestation_path, material_path, trust_root)
        bundle = _load_sigstore_python_bundle(attestation_path)
        verifier = _sigstore_verifier_from_trust_root(trust_root)
        policy = _sigstore_policy_from_trust_root(trust_root)
        try:
            verifier.verify_artifact(material_path.read_bytes(), bundle, policy)
        except OSError as exc:
            raise AttestationError(f"could not read attestation material: {material_path}") from exc
        except _sigstore_verification_error_types():
            return False
        return True


def verify_attestation(
    attestation_path: Path,
    *,
    material_path: Path,
    trust_root_path: Path,
    backend: BackendName = "structural",
    verifier: AttestationVerifierBackend | None = None,
) -> AttestationVerificationReport:
    checks: list[AttestationCheck] = []
    material_digest: str | None = None
    cryptographic_valid: bool | None = None
    trust_root: AttestationTrustRoot | None = None

    try:
        trust_root = load_attestation_trust_root(trust_root_path)
        _add_check(checks, name="trust_root", passed=True, detail="trust root loaded", path=trust_root_path)
    except AttestationError as exc:
        _add_check(checks, name="trust_root", passed=False, detail=str(exc), path=trust_root_path)

    try:
        material_digest = _file_sha256(material_path)
        _add_check(
            checks,
            name="material_digest",
            passed=True,
            detail="material digest computed",
            path=material_path,
            metadata={"sha256": material_digest},
        )
    except (OSError, AttestationError) as exc:
        _add_check(checks, name="material_digest", passed=False, detail=str(exc), path=material_path)

    attestation: PyPIAttestation | None = None
    if trust_root is not None and material_digest is not None:
        try:
            attestation = load_pypi_attestation(attestation_path)
            _add_check(
                checks,
                name="attestation_schema",
                passed=True,
                detail="attestation schema loaded",
                path=attestation_path,
            )
            _check_materials(
                checks,
                attestation=attestation,
                material_digest=material_digest,
                material_path=material_path,
            )
            _check_bundle_structure(checks, bundle=attestation.bundle, attestation_path=attestation_path)
            _check_certificate_identity(
                checks,
                bundle=attestation.bundle,
                trust_root=trust_root,
                attestation_path=attestation_path,
            )
        except AttestationError as exc:
            _add_check(checks, name="attestation_schema", passed=False, detail=str(exc), path=attestation_path)

    backend_impl = verifier or _backend(backend)
    if trust_root is not None and material_digest is not None and attestation is not None:
        try:
            cryptographic_valid = backend_impl.verify(
                attestation_path=attestation_path,
                material_path=material_path,
                trust_root=trust_root,
            )
            if backend_impl.name == "structural":
                _add_check(
                    checks,
                    name="cryptographic_verification",
                    passed=True,
                    detail="structural backend does not perform cryptographic verification",
                    path=attestation_path,
                    metadata={"cryptographic_valid": None},
                )
            else:
                _add_check(
                    checks,
                    name="cryptographic_verification",
                    passed=cryptographic_valid is True,
                    detail="cryptographic backend verification passed"
                    if cryptographic_valid is True
                    else "cryptographic backend verification failed",
                    path=attestation_path,
                    metadata={"cryptographic_valid": cryptographic_valid},
                )
        except AttestationError as exc:
            cryptographic_valid = False if backend_impl.name != "structural" else None
            _add_check(checks, name="cryptographic_verification", passed=False, detail=str(exc), path=attestation_path)

    ok = all(check.status == "pass" for check in checks)
    return _report(
        attestation_path=attestation_path,
        material_path=material_path,
        material_sha256=material_digest,
        trust_root_path=trust_root_path,
        backend=backend_impl.name,
        cryptographic_valid=cryptographic_valid,
        ok=ok,
        checks=tuple(checks),
    )


def load_attestation_trust_root(path: Path) -> AttestationTrustRoot:
    data = _read_json_object(path, label="attestation trust root")
    schema_version = _required_str(data, "schema_version", label="attestation trust root")
    if schema_version != ATTESTATION_TRUST_ROOT_SCHEMA_VERSION:
        raise AttestationError(f"unsupported attestation trust root schema_version: {schema_version}")
    base_dir = path.parent
    expected_certificate_issuer = _required_str(data, "expected_certificate_issuer", label="attestation trust root")
    allowed_subject_alternative_names = tuple(
        _string_list(data.get("allowed_subject_alternative_names"), "allowed_subject_alternative_names")
    )
    if not allowed_subject_alternative_names:
        raise AttestationError("attestation trust root must include allowed_subject_alternative_names")
    certificate_paths = tuple(
        _existing_relative_paths(data.get("fulcio_certificate_paths"), base_dir, "fulcio_certificate_paths")
    )
    if not certificate_paths:
        raise AttestationError("attestation trust root must include fulcio_certificate_paths")
    rekor_public_key_path = _existing_relative_path(
        _required_str(data, "rekor_public_key_path", label="attestation trust root"),
        base_dir,
        "rekor_public_key_path",
    )
    sigstore_client_trust_config_path = _optional_existing_relative_path(
        data.get("sigstore_client_trust_config_path"),
        base_dir,
        "sigstore_client_trust_config_path",
    )
    sigstore_trusted_root_path = _optional_existing_relative_path(
        data.get("sigstore_trusted_root_path"),
        base_dir,
        "sigstore_trusted_root_path",
    )
    return AttestationTrustRoot(
        schema_version=schema_version,
        expected_certificate_issuer=expected_certificate_issuer,
        allowed_subject_alternative_names=allowed_subject_alternative_names,
        fulcio_certificate_paths=certificate_paths,
        rekor_public_key_path=rekor_public_key_path,
        path=path,
        sigstore_client_trust_config_path=sigstore_client_trust_config_path,
        sigstore_trusted_root_path=sigstore_trusted_root_path,
    )


def load_pypi_attestation(path: Path) -> PyPIAttestation:
    data = _read_json_object(path, label="PyPI attestation")
    attestation_type = _required_str(data, "_type", label="PyPI attestation")
    if attestation_type not in PYPI_ATTESTATION_TYPES:
        raise AttestationError(f"unsupported PyPI attestation _type: {attestation_type}")
    materials = tuple(_object_list(data.get("materials"), "materials"))
    if not materials:
        raise AttestationError("PyPI attestation must include materials")
    claim = _required_object(data.get("claim"), "claim")
    bundle = load_sigstore_bundle_from_object(data.get("bundle"))
    return PyPIAttestation(attestation_type=attestation_type, materials=materials, claim=claim, bundle=bundle)


def load_sigstore_bundle_from_object(value: object) -> SigstoreBundle:
    data = _required_object(value, "bundle")
    if _looks_like_canonical_sigstore_bundle(data) and not _looks_like_structural_bundle(data):
        return _canonical_sigstore_bundle_from_object(data)
    bundle_type = data.get("bundle_type")
    if bundle_type is not None and (not isinstance(bundle_type, str) or bundle_type not in SIGSTORE_BUNDLE_TYPES):
        raise AttestationError("unsupported Sigstore bundle_type")
    certificates = tuple(_string_list(data.get("certificate_chain_pem"), "certificate_chain_pem"))
    signature_b64 = _required_str(data, "signature_b64", label="Sigstore bundle")
    tlog_entries = tuple(_object_list(data.get("tlog_entries"), "tlog_entries"))
    return SigstoreBundle(
        certificate_chain_pem=certificates,
        signature_b64=signature_b64,
        tlog_entries=tlog_entries,
        raw_bundle=data,
        bundle_type=bundle_type if isinstance(bundle_type, str) else None,
    )


def attestation_report_to_jsonable(report: AttestationVerificationReport) -> dict[str, object]:
    return {
        "schema_version": report.schema_version,
        "attestation_path": report.attestation_path,
        "material_path": report.material_path,
        "material_sha256": report.material_sha256,
        "trust_root_path": report.trust_root_path,
        "backend": report.backend,
        "cryptographic_valid": report.cryptographic_valid,
        "ok": report.ok,
        "checks": [
            {
                "name": check.name,
                "status": check.status,
                "detail": check.detail,
                "path": check.path,
                "metadata": check.metadata,
            }
            for check in report.checks
        ],
        "report_hash": report.report_hash,
        "reproduction_claimed": report.reproduction_claimed,
        "boundary": report.boundary,
    }


def _check_materials(
    checks: list[AttestationCheck],
    *,
    attestation: PyPIAttestation,
    material_digest: str,
    material_path: Path,
) -> None:
    matching = [
        material
        for material in attestation.materials
        if isinstance(material.get("digest"), dict)
        and cast(dict[str, object], material["digest"]).get("sha256") == material_digest
    ]
    _add_check(
        checks,
        name="material_bound",
        passed=bool(matching),
        detail="attestation materials include the material sha256 digest"
        if matching
        else "attestation materials do not include the material sha256 digest",
        path=material_path,
        metadata={"sha256": material_digest, "material_count": len(attestation.materials)},
    )


def _check_bundle_structure(
    checks: list[AttestationCheck],
    *,
    bundle: SigstoreBundle,
    attestation_path: Path,
) -> None:
    _add_check(
        checks,
        name="signature_present",
        passed=_is_base64(bundle.signature_b64),
        detail="bundle signature is present and base64 encoded",
        path=attestation_path,
    )
    _add_check(
        checks,
        name="certificate_chain_present",
        passed=bool(bundle.certificate_chain_pem),
        detail="bundle includes a certificate chain",
        path=attestation_path,
        metadata={"certificate_count": len(bundle.certificate_chain_pem)},
    )
    _add_check(
        checks,
        name="tlog_entries_present",
        passed=bool(bundle.tlog_entries),
        detail="bundle includes transparency log entries",
        path=attestation_path,
        metadata={"tlog_entry_count": len(bundle.tlog_entries)},
    )


def _check_certificate_identity(
    checks: list[AttestationCheck],
    *,
    bundle: SigstoreBundle,
    trust_root: AttestationTrustRoot,
    attestation_path: Path,
) -> None:
    try:
        leaf = _load_leaf_certificate(bundle)
        issuer = leaf.issuer.rfc4514_string()
        sans = _certificate_subject_alternative_names(leaf)
    except AttestationError as exc:
        _add_check(checks, name="certificate_identity", passed=False, detail=str(exc), path=attestation_path)
        return
    _add_check(
        checks,
        name="certificate_issuer",
        passed=issuer == trust_root.expected_certificate_issuer,
        detail="certificate issuer matches trust root"
        if issuer == trust_root.expected_certificate_issuer
        else "certificate issuer does not match trust root",
        path=attestation_path,
        metadata={"actual": issuer, "expected": trust_root.expected_certificate_issuer},
    )
    allowed_sans = set(trust_root.allowed_subject_alternative_names)
    matching_sans = sorted(set(sans) & allowed_sans)
    _add_check(
        checks,
        name="certificate_identity",
        passed=bool(matching_sans),
        detail="certificate identity matches trust root allowlist"
        if matching_sans
        else "certificate identity does not match trust root allowlist",
        path=attestation_path,
        metadata={"subject_alternative_names": sans, "matched": matching_sans},
    )


def _load_leaf_certificate(bundle: SigstoreBundle) -> Any:
    if not bundle.certificate_chain_pem:
        raise AttestationError("bundle certificate chain is empty")
    try:
        from cryptography import x509
    except ImportError as exc:  # pragma: no cover - exercised only without optional dependency.
        raise AttestationError("cryptography is required for structural attestation verification") from exc
    try:
        return x509.load_pem_x509_certificate(bundle.certificate_chain_pem[0].encode("utf-8"))
    except ValueError as exc:
        raise AttestationError("bundle leaf certificate is not valid PEM X.509") from exc


def _certificate_subject_alternative_names(certificate: Any) -> list[str]:
    try:
        from cryptography import x509
    except ImportError as exc:  # pragma: no cover - exercised only without optional dependency.
        raise AttestationError("cryptography is required for structural attestation verification") from exc
    try:
        extension = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName)
    except x509.ExtensionNotFound:
        return []
    names: list[str] = []
    for name in extension.value:
        if isinstance(name, x509.UniformResourceIdentifier | x509.DNSName | x509.RFC822Name):
            names.append(str(name.value))
    return sorted(names)


def _load_sigstore_python_bundle(attestation_path: Path) -> Any:
    data = _read_json_object(attestation_path, label="PyPI attestation")
    raw_bundle = _required_object(data.get("bundle"), "bundle")
    try:
        from sigstore.models import Bundle
    except ImportError as exc:  # pragma: no cover - exercised by import-blocking tests.
        raise AttestationError(f"sigstore backend requires the optional sigstore extra: {exc}") from exc
    try:
        return Bundle.from_json(stable_json_dumps(raw_bundle))
    except Exception as exc:
        raise AttestationError("sigstore backend could not parse canonical Sigstore bundle") from exc


def _sigstore_verifier_from_trust_root(trust_root: AttestationTrustRoot) -> Any:
    try:
        from sigstore._internal.rekor.client import RekorClient
        from sigstore._internal.trust import ClientTrustConfig, TrustedRoot
        from sigstore.verify import Verifier
    except ImportError as exc:  # pragma: no cover - exercised by import-blocking tests.
        raise AttestationError(f"sigstore backend requires the optional sigstore extra: {exc}") from exc

    try:
        if trust_root.sigstore_client_trust_config_path is not None:
            trust_config = ClientTrustConfig.from_json(
                trust_root.sigstore_client_trust_config_path.read_text(encoding="utf-8")
            )
            return Verifier._from_trust_config(trust_config)
        if trust_root.sigstore_trusted_root_path is not None:
            trusted_root = TrustedRoot.from_file(str(trust_root.sigstore_trusted_root_path))
            return Verifier(rekor=RekorClient("https://rekor.invalid"), trusted_root=trusted_root)
    except OSError as exc:
        raise AttestationError("sigstore trust configuration could not be read") from exc
    except Exception as exc:
        raise AttestationError("sigstore trust configuration could not be loaded") from exc

    raise AttestationError(
        "sigstore backend requires sigstore_client_trust_config_path or sigstore_trusted_root_path in the trust root"
    )


def _sigstore_policy_from_trust_root(trust_root: AttestationTrustRoot) -> Any:
    try:
        from sigstore.verify.policy import AnyOf, Identity
    except ImportError as exc:  # pragma: no cover - exercised by import-blocking tests.
        raise AttestationError(f"sigstore backend requires the optional sigstore extra: {exc}") from exc
    try:
        return AnyOf([Identity(identity=identity) for identity in trust_root.allowed_subject_alternative_names])
    except Exception as exc:
        raise AttestationError("sigstore identity policy could not be constructed") from exc


def _sigstore_verification_error_types() -> tuple[type[Exception], ...]:
    try:
        from sigstore.errors import Error, VerificationError
    except ImportError as exc:  # pragma: no cover - exercised by import-blocking tests.
        raise AttestationError(f"sigstore backend requires the optional sigstore extra: {exc}") from exc
    return (Error, VerificationError)


def _looks_like_structural_bundle(data: dict[str, object]) -> bool:
    return any(key in data for key in ("certificate_chain_pem", "signature_b64", "tlog_entries", "bundle_type"))


def _looks_like_canonical_sigstore_bundle(data: dict[str, object]) -> bool:
    return any(key in data for key in ("mediaType", "verificationMaterial", "messageSignature", "dsseEnvelope"))


def _canonical_sigstore_bundle_from_object(data: dict[str, object]) -> SigstoreBundle:
    try:
        from cryptography.hazmat.primitives.serialization import Encoding
        from sigstore.models import Bundle
    except ImportError as exc:  # pragma: no cover - exercised only without optional dependency.
        raise AttestationError("canonical Sigstore bundles require the optional sigstore extra") from exc
    try:
        bundle = Bundle.from_json(stable_json_dumps(data))
        certificate_pem = bundle.signing_certificate.public_bytes(Encoding.PEM).decode("utf-8")
        signature_b64 = base64.b64encode(bundle.signature).decode("ascii")
        log_entry = bundle.log_entry
    except Exception as exc:
        raise AttestationError("invalid canonical Sigstore bundle") from exc
    return SigstoreBundle(
        certificate_chain_pem=(certificate_pem,),
        signature_b64=signature_b64,
        tlog_entries=(
            {
                "integrated_time": log_entry.integrated_time,
                "log_id": log_entry.log_id,
                "log_index": log_entry.log_index,
            },
        ),
        raw_bundle=data,
        bundle_type="sigstore-bundle",
    )


def _backend(name: BackendName) -> AttestationVerifierBackend:
    if name == "structural":
        return StructuralAttestationVerifier()
    if name == "sigstore":
        return SigstorePythonVerifier()
    raise AttestationError(f"unknown attestation backend: {name}")


def _report(
    *,
    attestation_path: Path,
    material_path: Path,
    material_sha256: str | None,
    trust_root_path: Path,
    backend: str,
    cryptographic_valid: bool | None,
    ok: bool,
    checks: tuple[AttestationCheck, ...],
) -> AttestationVerificationReport:
    report_without_hash = {
        "schema_version": ATTESTATION_SCHEMA_VERSION,
        "attestation_path": str(attestation_path),
        "material_path": str(material_path),
        "material_sha256": material_sha256,
        "trust_root_path": str(trust_root_path),
        "backend": backend,
        "cryptographic_valid": cryptographic_valid,
        "ok": ok,
        "checks": [
            {
                "name": check.name,
                "status": check.status,
                "detail": check.detail,
                "path": check.path,
                "metadata": check.metadata,
            }
            for check in checks
        ],
        "reproduction_claimed": False,
        "boundary": ATTESTATION_BOUNDARY,
    }
    report_hash = sha256((stable_json_dumps(report_without_hash) + "\n").encode("utf-8")).hexdigest()
    return AttestationVerificationReport(
        schema_version=ATTESTATION_SCHEMA_VERSION,
        attestation_path=str(attestation_path),
        material_path=str(material_path),
        material_sha256=material_sha256,
        trust_root_path=str(trust_root_path),
        backend=backend,
        cryptographic_valid=cryptographic_valid,
        ok=ok,
        checks=checks,
        report_hash=report_hash,
        reproduction_claimed=False,
        boundary=ATTESTATION_BOUNDARY,
    )


def _read_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise AttestationError(f"missing {label}: {path}") from exc
    except json.JSONDecodeError as exc:
        raise AttestationError(f"invalid {label} JSON: {path}") from exc
    if not isinstance(data, dict):
        raise AttestationError(f"{label} JSON must be an object")
    return cast(dict[str, Any], data)


def _required_object(value: object, key: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise AttestationError(f"attestation field must be an object: {key}")
    return cast(dict[str, object], value)


def _required_str(data: dict[str, Any], key: str, *, label: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise AttestationError(f"{label} missing non-empty string field: {key}")
    return value


def _string_list(value: object, key: str) -> list[str]:
    if not isinstance(value, list):
        raise AttestationError(f"attestation field must be a list of strings: {key}")
    result: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item:
            raise AttestationError(f"attestation field {key}[{index}] must be a non-empty string")
        result.append(item)
    return result


def _object_list(value: object, key: str) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise AttestationError(f"attestation field must be a list of objects: {key}")
    result: list[dict[str, object]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise AttestationError(f"attestation field {key}[{index}] must be an object")
        result.append(cast(dict[str, object], item))
    return result


def _existing_relative_paths(value: object, base_dir: Path, key: str) -> list[Path]:
    paths = _string_list(value, key)
    return [_existing_relative_path(item, base_dir, key) for item in paths]


def _optional_existing_relative_path(value: object, base_dir: Path, key: str) -> Path | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise AttestationError(f"attestation trust root {key} must be a non-empty string when supplied")
    return _existing_relative_path(value, base_dir, key)


def _existing_relative_path(value: str, base_dir: Path, key: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = base_dir / path
    resolved = path.resolve()
    if not resolved.is_file():
        raise AttestationError(f"attestation trust root {key} file does not exist: {path}")
    return resolved


def _file_sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _is_base64(value: str) -> bool:
    try:
        base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error):
        return False
    return True


def _add_check(
    checks: list[AttestationCheck],
    *,
    name: str,
    passed: bool,
    detail: str,
    path: Path | None,
    metadata: dict[str, object] | None = None,
) -> None:
    checks.append(
        AttestationCheck(
            name=name,
            status="pass" if passed else "fail",
            detail=detail,
            path=str(path) if path is not None else None,
            metadata=metadata,
        )
    )
