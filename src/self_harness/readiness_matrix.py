from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import cast

from self_harness.types import stable_json_dumps

READINESS_MATRIX_CATALOG_SCHEMA_VERSION = "1.1"
SUPPORTED_READINESS_MATRIX_CATALOG_SCHEMA_VERSIONS = frozenset({"1.0", "1.1"})
READINESS_MATRIX_REPORT_SCHEMA_VERSION = "1.0"
READINESS_MATRIX_BOUNDARY = (
    "release/operator live-dependency readiness catalog only; validates checked-in declarative "
    "blocker metadata, does not probe the environment, run Harbor, Docker, registries, scanners, "
    "PyPI, Sigstore, models, or cloud providers, and is not benchmark reproduction evidence"
)

ALLOWED_READINESS_DOMAINS = frozenset(
    {
        "docker",
        "harbor",
        "kms",
        "model",
        "pypi",
        "registry",
        "scanner-db",
        "secret",
        "sigstore",
        "trivy",
    }
)
ALLOWED_READINESS_STATUSES = frozenset({"blocked", "optional", "provisioned"})
ALLOWED_READINESS_PREFLIGHT_SURFACES = frozenset(
    {
        "attestation_check",
        "container_preflight",
        "harbor_discovery_check",
        "model_backend_preflight",
        "none",
        "operator_preflight",
        "release_smoke",
        "scanner_check",
    }
)
ALLOWED_READINESS_OPERATOR_ACTIONS = frozenset({"configure", "discover", "provision", "publish", "scan", "sign"})
KNOWN_READINESS_AFFECTS = frozenset(
    {
        "container-demo --mode live",
        "corpus-sign external signer",
        "harbor-discovery live",
        "LLMProposer AnthropicClaudeClient",
        "LLMProposer GLMClient",
        "LLMProposer MiniMaxClient",
        "LLMProposer QwenClient",
        "operator-promotion external signer",
        "release workflow PyPI publish",
        "scanner DB mirror registry config",
        "scripts/container_preflight_report.py",
        "scripts/model_backend_preflight.py",
        "scripts/harbor_discovery.py",
        "scripts/scanner_db_update.py live",
        "scripts/scanner_run.py live",
        "scripts/vuln_check.py --format trivy",
        "terminal-bench --mode live",
        "terminal-bench-capture",
        "terminal-bench-preflight",
        "verify-attestation --backend sigstore",
    }
)

_CATALOG_FIELDS = frozenset({"schema_version", "entries"})
_ENTRY_FIELDS = frozenset(
    {
        "dependency",
        "domain",
        "status",
        "affects",
        "offline_fixture",
        "operator_remediation",
        "preflight_surface",
        "reproduction_relevant",
        "operator_action",
    }
)


class ReadinessMatrixError(ValueError):
    """Raised when a readiness matrix catalog is malformed."""


@dataclass(frozen=True)
class ReadinessMatrixEntry:
    dependency: str
    domain: str
    status: str
    affects: tuple[str, ...]
    offline_fixture: Path | None
    operator_remediation: str
    reproduction_relevant: bool
    preflight_surface: str = "none"
    operator_action: str = "provision"


@dataclass(frozen=True)
class ReadinessMatrixCatalog:
    schema_version: str
    entries: tuple[ReadinessMatrixEntry, ...]
    path: Path


@dataclass(frozen=True)
class ReadinessMatrixRow:
    dependency: str
    domain: str
    status: str
    affects: tuple[str, ...]
    offline_fixture: str | None
    operator_remediation: str
    reproduction_relevant: bool
    preflight_surface: str
    operator_action: str


@dataclass(frozen=True)
class ReadinessMatrixReport:
    schema_version: str
    catalog_path: str
    ok: bool
    rows: tuple[ReadinessMatrixRow, ...]
    live_execution_blocked: bool
    blocked_count: int
    optional_count: int
    provisioned_count: int
    report_hash: str
    reproduction_claimed: bool
    boundary: str


def load_readiness_matrix_catalog(path: Path, *, repo_root: Path | None = None) -> ReadinessMatrixCatalog:
    data = _load_catalog_data(path)
    unknown_fields = set(data) - _CATALOG_FIELDS
    if unknown_fields:
        raise ReadinessMatrixError(f"unknown readiness matrix catalog field(s): {_format_fields(unknown_fields)}")
    schema_version = data.get("schema_version")
    if schema_version not in SUPPORTED_READINESS_MATRIX_CATALOG_SCHEMA_VERSIONS:
        raise ReadinessMatrixError(f"unsupported readiness matrix schema_version: {schema_version!r}")
    raw_entries = data.get("entries")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise ReadinessMatrixError("readiness matrix entries must be a non-empty list")

    effective_repo_root = _resolve_repo_root(path) if repo_root is None else repo_root.resolve()
    entries = tuple(_load_entries(raw_entries, repo_root=effective_repo_root))
    return ReadinessMatrixCatalog(
        schema_version=str(schema_version),
        entries=entries,
        path=path,
    )


def evaluate_readiness_matrix(catalog: ReadinessMatrixCatalog) -> ReadinessMatrixReport:
    rows = tuple(
        ReadinessMatrixRow(
            dependency=entry.dependency,
            domain=entry.domain,
            status=entry.status,
            affects=entry.affects,
            offline_fixture=str(entry.offline_fixture) if entry.offline_fixture is not None else None,
            operator_remediation=entry.operator_remediation,
            reproduction_relevant=entry.reproduction_relevant,
            preflight_surface=entry.preflight_surface,
            operator_action=entry.operator_action,
        )
        for entry in catalog.entries
    )
    blocked_count = sum(1 for entry in catalog.entries if entry.status == "blocked")
    optional_count = sum(1 for entry in catalog.entries if entry.status == "optional")
    provisioned_count = sum(1 for entry in catalog.entries if entry.status == "provisioned")
    live_execution_blocked = any(
        entry.status == "blocked" and entry.reproduction_relevant for entry in catalog.entries
    )
    report_without_hash = {
        "schema_version": READINESS_MATRIX_REPORT_SCHEMA_VERSION,
        "catalog_path": str(catalog.path),
        "ok": True,
        "rows": [_row_to_jsonable(row) for row in rows],
        "live_execution_blocked": live_execution_blocked,
        "blocked_count": blocked_count,
        "optional_count": optional_count,
        "provisioned_count": provisioned_count,
        "reproduction_claimed": False,
        "boundary": READINESS_MATRIX_BOUNDARY,
    }
    report_hash = sha256((stable_json_dumps(report_without_hash) + "\n").encode("utf-8")).hexdigest()
    return ReadinessMatrixReport(
        schema_version=READINESS_MATRIX_REPORT_SCHEMA_VERSION,
        catalog_path=str(catalog.path),
        ok=True,
        rows=rows,
        live_execution_blocked=live_execution_blocked,
        blocked_count=blocked_count,
        optional_count=optional_count,
        provisioned_count=provisioned_count,
        report_hash=report_hash,
        reproduction_claimed=False,
        boundary=READINESS_MATRIX_BOUNDARY,
    )


def readiness_matrix_report_to_jsonable(report: ReadinessMatrixReport) -> dict[str, object]:
    return {
        "schema_version": report.schema_version,
        "catalog_path": report.catalog_path,
        "ok": report.ok,
        "rows": [_row_to_jsonable(row) for row in report.rows],
        "live_execution_blocked": report.live_execution_blocked,
        "blocked_count": report.blocked_count,
        "optional_count": report.optional_count,
        "provisioned_count": report.provisioned_count,
        "report_hash": report.report_hash,
        "reproduction_claimed": report.reproduction_claimed,
        "boundary": report.boundary,
    }


def _load_catalog_data(path: Path) -> dict[str, object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ReadinessMatrixError(f"missing readiness matrix catalog: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ReadinessMatrixError(f"invalid readiness matrix JSON: {path}") from exc
    if not isinstance(data, dict):
        raise ReadinessMatrixError("readiness matrix catalog must be a JSON object")
    if not all(isinstance(key, str) for key in data):
        raise ReadinessMatrixError("readiness matrix catalog keys must be strings")
    return cast(dict[str, object], data)


def _load_entry(raw_entry: object, *, index: int, repo_root: Path) -> ReadinessMatrixEntry:
    if not isinstance(raw_entry, dict):
        raise ReadinessMatrixError(f"entry {index} must be a JSON object")
    if not all(isinstance(key, str) for key in raw_entry):
        raise ReadinessMatrixError(f"entry {index} keys must be strings")
    entry = cast(dict[str, object], raw_entry)
    unknown_fields = set(entry) - _ENTRY_FIELDS
    if unknown_fields:
        raise ReadinessMatrixError(f"entry {index} has unknown field(s): {_format_fields(unknown_fields)}")

    dependency = _required_string(entry, "dependency", index)
    domain = _required_string(entry, "domain", index)
    if domain not in ALLOWED_READINESS_DOMAINS:
        raise ReadinessMatrixError(f"entry {index} has unknown domain: {domain}")
    status = _required_string(entry, "status", index)
    if status not in ALLOWED_READINESS_STATUSES:
        raise ReadinessMatrixError(f"entry {index} has unknown status: {status}")
    affects = _affects(entry, index)
    offline_fixture = _offline_fixture(entry, index, repo_root)
    operator_remediation = _required_string(entry, "operator_remediation", index)
    reproduction_relevant = _required_bool(entry, "reproduction_relevant", index)
    preflight_surface = _optional_enum(
        entry,
        key="preflight_surface",
        index=index,
        allowed=ALLOWED_READINESS_PREFLIGHT_SURFACES,
        default="none",
    )
    operator_action = _optional_enum(
        entry,
        key="operator_action",
        index=index,
        allowed=ALLOWED_READINESS_OPERATOR_ACTIONS,
        default="provision",
    )
    return ReadinessMatrixEntry(
        dependency=dependency,
        domain=domain,
        status=status,
        affects=affects,
        offline_fixture=offline_fixture,
        operator_remediation=operator_remediation,
        reproduction_relevant=reproduction_relevant,
        preflight_surface=preflight_surface,
        operator_action=operator_action,
    )


def _load_entries(raw_entries: list[object], *, repo_root: Path) -> list[ReadinessMatrixEntry]:
    return [
        _load_entry(raw_entry, index=index, repo_root=repo_root)
        for index, raw_entry in enumerate(raw_entries)
    ]


def _required_string(entry: dict[str, object], key: str, index: int) -> str:
    value = entry.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ReadinessMatrixError(f"entry {index} field {key!r} must be a non-empty string")
    return value


def _required_bool(entry: dict[str, object], key: str, index: int) -> bool:
    value = entry.get(key)
    if not isinstance(value, bool):
        raise ReadinessMatrixError(f"entry {index} field {key!r} must be a boolean")
    return value


def _optional_enum(
    entry: dict[str, object],
    *,
    key: str,
    index: int,
    allowed: frozenset[str],
    default: str,
) -> str:
    value = entry.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise ReadinessMatrixError(f"entry {index} field {key!r} must be a non-empty string")
    if value not in allowed:
        raise ReadinessMatrixError(f"entry {index} has unknown {key}: {value}")
    return value


def _affects(entry: dict[str, object], index: int) -> tuple[str, ...]:
    value = entry.get("affects")
    if not isinstance(value, list) or not value:
        raise ReadinessMatrixError(f"entry {index} field 'affects' must be a non-empty list")
    affects: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ReadinessMatrixError(f"entry {index} field 'affects' must contain non-empty strings")
        if item not in KNOWN_READINESS_AFFECTS:
            raise ReadinessMatrixError(f"entry {index} references unknown affected gate or command: {item}")
        affects.append(item)
    return tuple(affects)


def _offline_fixture(entry: dict[str, object], index: int, repo_root: Path) -> Path | None:
    value = entry.get("offline_fixture")
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ReadinessMatrixError(f"entry {index} field 'offline_fixture' must be a non-empty string or null")
    fixture_path = Path(value)
    if fixture_path.is_absolute():
        raise ReadinessMatrixError(f"entry {index} offline_fixture must be repo-relative: {value}")
    resolved_repo_root = repo_root.resolve()
    resolved_fixture = (resolved_repo_root / fixture_path).resolve()
    try:
        resolved_fixture.relative_to(resolved_repo_root)
    except ValueError as exc:
        raise ReadinessMatrixError(f"entry {index} offline_fixture escapes the repository: {value}") from exc
    if not resolved_fixture.exists():
        raise ReadinessMatrixError(f"entry {index} offline_fixture does not exist: {value}")
    return fixture_path


def _resolve_repo_root(path: Path) -> Path:
    start = path.resolve().parent
    for candidate in (start, *start.parents):
        if (candidate / "pyproject.toml").is_file() and (candidate / "src").is_dir():
            return candidate
    return start


def _row_to_jsonable(row: ReadinessMatrixRow) -> dict[str, object]:
    return {
        "dependency": row.dependency,
        "domain": row.domain,
        "status": row.status,
        "affects": list(row.affects),
        "offline_fixture": row.offline_fixture,
        "operator_remediation": row.operator_remediation,
        "reproduction_relevant": row.reproduction_relevant,
        "preflight_surface": row.preflight_surface,
        "operator_action": row.operator_action,
    }


def _format_fields(fields: set[str]) -> str:
    return ", ".join(sorted(fields))
