from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from self_harness.image_policy import ImagePolicyError, validate_image_digest

HARBOR_DISCOVERY_SCHEMA_VERSION = "1.0"

HarborDiscoveryMode = Literal["dry-run", "replay", "live"]


@dataclass(frozen=True)
class HarborDiscoveryCommand:
    url: str
    project: str
    repository: str
    reference: str
    authorization_header: str | None = None


@dataclass(frozen=True)
class HarborDiscoveryRequest:
    method: str
    url: str
    headers: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class DiscoveredHarborImage:
    image: str
    digest: str
    reference: str
    tags: tuple[str, ...]
    media_type: str | None = None
    child_digests: tuple[str, ...] = ()


@dataclass(frozen=True)
class HarborDiscoveryResult:
    schema_version: str
    ok: bool
    mode: HarborDiscoveryMode
    source: str
    request: HarborDiscoveryRequest
    discovered_images: tuple[DiscoveredHarborImage, ...]
    reason: str | None = None


class HarborDiscoveryError(RuntimeError):
    """Raised when Harbor discovery input or response data is invalid."""


def build_harbor_discovery_request(command: HarborDiscoveryCommand) -> HarborDiscoveryRequest:
    _validate_command(command)
    base = command.url.rstrip("/")
    project = quote(command.project, safe="")
    repository = quote(command.repository, safe="")
    reference = quote(command.reference, safe="")
    query = urlencode(
        {
            "with_tag": "true",
            "with_label": "false",
            "with_scan_overview": "false",
            "with_signature": "false",
            "with_immutable_status": "false",
            "with_accessory": "false",
        }
    )
    headers = {"Accept": "application/json"}
    if command.authorization_header is not None:
        headers["Authorization"] = command.authorization_header
    return HarborDiscoveryRequest(
        method="GET",
        url=f"{base}/api/v2.0/projects/{project}/repositories/{repository}/artifacts/{reference}?{query}",
        headers=tuple(sorted(headers.items())),
    )


def parse_harbor_artifact_response(
    payload: bytes | str,
    *,
    image: str,
    reference: str,
) -> tuple[DiscoveredHarborImage, ...]:
    try:
        data = json.loads(payload.decode("utf-8") if isinstance(payload, bytes) else payload)
    except json.JSONDecodeError as exc:
        raise HarborDiscoveryError("invalid Harbor artifact JSON") from exc
    if not isinstance(data, dict):
        raise HarborDiscoveryError("Harbor artifact response must be a JSON object")
    digest = _digest(data.get("digest"), "artifact digest")
    tags = _tags(data.get("tags"))
    media_type = _optional_str(data.get("media_type"), "media_type")
    child_digests = _child_digests(data.get("references"))
    return (
        DiscoveredHarborImage(
            image=image,
            digest=digest,
            reference=reference,
            tags=tags,
            media_type=media_type,
            child_digests=child_digests,
        ),
    )


def run_harbor_discovery(
    command: HarborDiscoveryCommand,
    *,
    dry_run: bool = False,
    replay_response: Path | None = None,
    timeout_seconds: int = 15,
) -> HarborDiscoveryResult:
    if dry_run and replay_response is not None:
        raise HarborDiscoveryError("--dry-run and --replay are mutually exclusive")
    if timeout_seconds <= 0:
        raise HarborDiscoveryError("Harbor discovery timeout must be positive")
    request = build_harbor_discovery_request(command)
    image = _image(command)
    if dry_run:
        return _result(mode="dry-run", ok=True, source="dry-run", request=request, discovered_images=())
    if replay_response is not None:
        try:
            payload = replay_response.read_text(encoding="utf-8")
        except OSError as exc:
            return _result(
                mode="replay",
                ok=False,
                source=str(replay_response),
                request=request,
                reason=str(exc),
                discovered_images=(),
            )
        try:
            images = parse_harbor_artifact_response(payload, image=image, reference=command.reference)
        except HarborDiscoveryError as exc:
            return _result(
                mode="replay",
                ok=False,
                source=str(replay_response),
                request=request,
                reason=str(exc),
                discovered_images=(),
            )
        return _result(mode="replay", ok=True, source=str(replay_response), request=request, discovered_images=images)

    if command.authorization_header is None:
        return _result(
            mode="live",
            ok=False,
            source=request.url,
            request=request,
            reason="live Harbor discovery requires authorization material",
            discovered_images=(),
        )
    try:
        urllib_request = Request(request.url, method=request.method)
        for key, value in request.headers:
            urllib_request.add_header(key, value)
        with urlopen(urllib_request, timeout=timeout_seconds) as response:  # noqa: S310 - operator-supplied URL.
            payload = response.read()
    except HTTPError as exc:
        return _result(
            mode="live",
            ok=False,
            source=request.url,
            request=request,
            reason=f"Harbor discovery HTTP {exc.code}",
            discovered_images=(),
        )
    except (OSError, URLError) as exc:
        return _result(
            mode="live",
            ok=False,
            source=request.url,
            request=request,
            reason=str(exc),
            discovered_images=(),
        )
    try:
        images = parse_harbor_artifact_response(payload, image=image, reference=command.reference)
    except HarborDiscoveryError as exc:
        return _result(
            mode="live",
            ok=False,
            source=request.url,
            request=request,
            reason=str(exc),
            discovered_images=(),
        )
    return _result(mode="live", ok=True, source=request.url, request=request, discovered_images=images)


def harbor_discovery_result_to_jsonable(result: HarborDiscoveryResult) -> dict[str, object]:
    return {
        "schema_version": result.schema_version,
        "ok": result.ok,
        "mode": result.mode,
        "source": result.source,
        "request": {
            "method": result.request.method,
            "url": result.request.url,
            "headers": [
                {"name": name, "value": _redact_header(name, value)} for name, value in result.request.headers
            ],
        },
        "discovered_images": [
            {
                "image": image.image,
                "digest": image.digest,
                "reference": image.reference,
                "tags": list(image.tags),
                "media_type": image.media_type,
                "child_digests": list(image.child_digests),
            }
            for image in result.discovered_images
        ],
        "reason": result.reason,
    }


def _validate_command(command: HarborDiscoveryCommand) -> None:
    if not command.url.startswith(("http://", "https://")):
        raise HarborDiscoveryError("Harbor URL must start with http:// or https://")
    for label, value in (
        ("project", command.project),
        ("repository", command.repository),
        ("reference", command.reference),
    ):
        if not value:
            raise HarborDiscoveryError(f"Harbor discovery {label} must be non-empty")


def _result(
    *,
    mode: HarborDiscoveryMode,
    ok: bool,
    source: str,
    request: HarborDiscoveryRequest,
    discovered_images: tuple[DiscoveredHarborImage, ...],
    reason: str | None = None,
) -> HarborDiscoveryResult:
    return HarborDiscoveryResult(
        schema_version=HARBOR_DISCOVERY_SCHEMA_VERSION,
        ok=ok,
        mode=mode,
        source=source,
        request=request,
        discovered_images=discovered_images,
        reason=reason,
    )


def _image(command: HarborDiscoveryCommand) -> str:
    host = command.url.removeprefix("https://").removeprefix("http://").split("/", 1)[0]
    return f"{host}/{command.project}/{command.repository}"


def _digest(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise HarborDiscoveryError(f"Harbor artifact missing {label}")
    try:
        return validate_image_digest(value)
    except ImagePolicyError as exc:
        raise HarborDiscoveryError(exc.decision.message) from exc


def _tags(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise HarborDiscoveryError("Harbor artifact tags must be a list")
    tags: list[str] = []
    for row in value:
        if not isinstance(row, dict):
            raise HarborDiscoveryError("Harbor artifact tag entries must be objects")
        name = row.get("name")
        if not isinstance(name, str) or not name:
            raise HarborDiscoveryError("Harbor artifact tag entry missing name")
        tags.append(name)
    return tuple(sorted(tags))


def _child_digests(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise HarborDiscoveryError("Harbor artifact references must be a list")
    digests: list[str] = []
    for row in value:
        if not isinstance(row, dict):
            raise HarborDiscoveryError("Harbor artifact reference entries must be objects")
        digest = row.get("child_digest") or row.get("digest")
        if digest is not None:
            digests.append(_digest(digest, "child digest"))
    return tuple(sorted(digests))


def _optional_str(value: object, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise HarborDiscoveryError(f"Harbor artifact {label} must be a non-empty string")
    return value


def _redact_header(name: str, value: str) -> str:
    if name.lower() == "authorization":
        return "<redacted>"
    return value
