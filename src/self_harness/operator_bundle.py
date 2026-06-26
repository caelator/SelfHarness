from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

OPERATOR_POLICY_BUNDLE_VERSION = "1"
OPERATOR_POLICY_BUNDLE_FIELDS = frozenset(
    {
        "bundle_version",
        "owner",
        "expires_on",
        "image_policy",
        "freshness_policy",
        "vulnerability_policy",
        "scanner_db_freshness_policy",
        "trusted_public_keys",
    }
)


@dataclass(frozen=True)
class OperatorPolicyBundle:
    bundle_version: str
    owner: str
    expires_on: date
    path: Path
    image_policy: Path | None = None
    freshness_policy: Path | None = None
    vulnerability_policy: Path | None = None
    scanner_db_freshness_policy: Path | None = None
    trusted_public_keys: tuple[Path, ...] = ()


class OperatorPolicyBundleError(RuntimeError):
    """Raised when an operator policy bundle is missing, malformed, or expired."""


def load_operator_policy_bundle(path: Path, *, today: date | None = None) -> OperatorPolicyBundle:
    bundle_path = Path(path)
    data = _read_json_object(bundle_path)
    unknown = sorted(set(data) - OPERATOR_POLICY_BUNDLE_FIELDS)
    if unknown:
        raise OperatorPolicyBundleError(f"operator policy bundle has unknown fields: {', '.join(unknown)}")
    version = _required_str(data, "bundle_version")
    if version != OPERATOR_POLICY_BUNDLE_VERSION:
        raise OperatorPolicyBundleError(f"unsupported operator policy bundle version: {version}")
    owner = _required_str(data, "owner")
    expires_on = _required_date(data, "expires_on")
    evaluation_date = date.today() if today is None else today
    if expires_on < evaluation_date:
        raise OperatorPolicyBundleError(
            f"operator policy bundle expired on {expires_on.isoformat()}; evaluated at {evaluation_date.isoformat()}"
        )
    base_dir = bundle_path.parent
    return OperatorPolicyBundle(
        bundle_version=version,
        owner=owner,
        expires_on=expires_on,
        path=bundle_path,
        image_policy=_optional_existing_path(data, "image_policy", base_dir),
        freshness_policy=_optional_existing_path(data, "freshness_policy", base_dir),
        vulnerability_policy=_optional_existing_path(data, "vulnerability_policy", base_dir),
        scanner_db_freshness_policy=_optional_existing_path(data, "scanner_db_freshness_policy", base_dir),
        trusted_public_keys=_existing_path_list(data, "trusted_public_keys", base_dir),
    )


def operator_policy_bundle_to_jsonable(bundle: OperatorPolicyBundle) -> dict[str, object]:
    return {
        "bundle_version": bundle.bundle_version,
        "owner": bundle.owner,
        "expires_on": bundle.expires_on.isoformat(),
        "path": str(bundle.path),
        "image_policy": _optional_path(bundle.image_policy),
        "freshness_policy": _optional_path(bundle.freshness_policy),
        "vulnerability_policy": _optional_path(bundle.vulnerability_policy),
        "scanner_db_freshness_policy": _optional_path(bundle.scanner_db_freshness_policy),
        "trusted_public_keys": [str(path) for path in bundle.trusted_public_keys],
    }


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise OperatorPolicyBundleError(f"missing operator policy bundle: {path}") from exc
    except json.JSONDecodeError as exc:
        raise OperatorPolicyBundleError(f"invalid operator policy bundle JSON: {path}") from exc
    if not isinstance(data, dict):
        raise OperatorPolicyBundleError("operator policy bundle JSON must be an object")
    return data


def _required_str(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise OperatorPolicyBundleError(f"operator policy bundle missing non-empty string field: {key}")
    return value


def _required_date(data: dict[str, Any], key: str) -> date:
    value = _required_str(data, key)
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise OperatorPolicyBundleError(f"operator policy bundle {key} must use YYYY-MM-DD") from exc


def _optional_existing_path(data: dict[str, Any], key: str, base_dir: Path) -> Path | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise OperatorPolicyBundleError(f"operator policy bundle {key} must be a non-empty path string")
    path = _resolve_bundle_path(value, base_dir)
    if not path.is_file():
        raise OperatorPolicyBundleError(f"operator policy bundle {key} file does not exist: {path}")
    return path


def _existing_path_list(data: dict[str, Any], key: str, base_dir: Path) -> tuple[Path, ...]:
    value = data.get(key, [])
    if not isinstance(value, list):
        raise OperatorPolicyBundleError(f"operator policy bundle {key} must be a list of path strings")
    paths: list[Path] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item:
            raise OperatorPolicyBundleError(f"operator policy bundle {key}[{index}] must be a non-empty path string")
        path = _resolve_bundle_path(item, base_dir)
        if not path.is_file():
            raise OperatorPolicyBundleError(f"operator policy bundle {key}[{index}] file does not exist: {path}")
        paths.append(path)
    return tuple(paths)


def _resolve_bundle_path(value: str, base_dir: Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()


def _optional_path(path: Path | None) -> str | None:
    return str(path) if path is not None else None
