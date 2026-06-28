"""Research-radar integration for self-harness.

When enabled, this module calls the research-radar binary to:
1. Auto-create a monitoring profile from the project directory
2. Scan for relevant papers/findings
3. Export findings and convert them to self-harness shapes

The findings are injected into the ProposerContext as additional
held-in patterns (mechanism="external_research_signal") and as
memory_sources on the HarnessSpec, so the LLM proposer sees real
research alongside runtime failure patterns.
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from self_harness.types import FailurePattern, FailureSignature, Split


def _find_radar_binary() -> str | None:
    """Locate the research-radar binary."""
    for candidate in [
        os.path.expanduser("~/.local/bin/research-radar"),
        "research-radar",
    ]:
        try:
            result = subprocess.run(
                [candidate, "--help"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                return candidate
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return None


@dataclass(frozen=True)
class ResearchConfig:
    """Configuration for research-radar integration.

    If `enabled` is False, all methods are no-ops.
    """
    enabled: bool = False
    # Project directory to derive keywords from
    project_dir: str = "."
    # Profile name override (default: derived from project_dir basename)
    profile_name: str | None = None
    # Path to the research-radar binary (auto-detected if None)
    binary_path: str | None = None
    # Maximum findings to inject per round
    max_findings: int = 10
    # Minimum confidence for injected findings
    min_confidence: float = 0.0
    # Skip the scan step (use cached findings only)
    skip_scan: bool = False


@dataclass
class ResearchFindings:
    """Container for research findings ready to inject into self-harness."""
    memory_sources: list[str] = field(default_factory=list)
    patterns: list[FailurePattern] = field(default_factory=list)
    raw: list[dict[str, Any]] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.raw


class ResearchIntegrator:
    """Manages the research-radar integration lifecycle.

    Usage:
        integrator = ResearchIntegrator(ResearchConfig(enabled=True, project_dir="."))
        integrator.ensure_profile()  # called once at startup
        findings = integrator.fetch_findings()  # called each round
        # inject findings.patterns into ProposerContext
        # inject findings.memory_sources into HarnessSpec
    """

    def __init__(self, config: ResearchConfig) -> None:
        self.config = config
        self._binary: str | None = config.binary_path
        self._profile_ensured = False

    @property
    def binary(self) -> str | None:
        if self._binary is None:
            self._binary = _find_radar_binary()
        return self._binary

    @property
    def is_available(self) -> bool:
        return self.config.enabled and self.binary is not None

    def ensure_profile(self) -> str | None:
        """Auto-create a research-radar profile for the project if one doesn't exist.

        Returns the profile name, or None if unavailable.
        """
        if not self.is_available:
            return None
        if self._profile_ensured:
            return self.config.profile_name or Path(self.config.project_dir).resolve().name

        binary = self.binary
        assert binary is not None
        name = self.config.profile_name or Path(self.config.project_dir).resolve().name

        # Check if a profile with this name already exists
        try:
            result = subprocess.run(
                [binary, "profile", "list"],
                capture_output=True, text=True, timeout=10,
            )
            if name in result.stdout:
                self._profile_ensured = True
                return name
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

        # Create the profile
        try:
            subprocess.run(
                [binary, "auto-profile", "--dir", self.config.project_dir, "--name", name],
                capture_output=True, text=True, timeout=30,
            )
            self._profile_ensured = True
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        return name

    def scan(self) -> bool:
        """Trigger a research-radar scan. Returns True if successful."""
        if not self.is_available or self.config.skip_scan:
            return False
        binary = self.binary
        assert binary is not None
        try:
            result = subprocess.run(
                [binary, "scan-once"],
                capture_output=True, text=True, timeout=120,
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    def fetch_findings(self) -> ResearchFindings:
        """Export findings from research-radar and convert to self-harness shapes.

        Triggers a scan first (unless skip_scan), then exports and converts.
        """
        if not self.is_available:
            return ResearchFindings()

        self.ensure_profile()
        self.scan()

        binary = self.binary
        assert binary is not None
        try:
            result = subprocess.run(
                [
                    binary, "export-findings",
                    "--limit", str(self.config.max_findings),
                    "--min-confidence", str(self.config.min_confidence),
                ],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0:
                return ResearchFindings()
            data = json.loads(result.stdout)
        except (json.JSONDecodeError, subprocess.TimeoutExpired, FileNotFoundError):
            return ResearchFindings()

        return self._convert(data.get("findings", []))

    def _convert(self, findings: list[dict[str, Any]]) -> ResearchFindings:
        """Convert raw finding dicts to self-harness shapes."""
        memory_sources: list[str] = []
        patterns: list[FailurePattern] = []

        for i, f in enumerate(findings[: self.config.max_findings]):
            title = f.get("title", "untitled")
            summary = f.get("summary", "")
            action = f.get("suggested_action", "")
            domain = f.get("domain", "general")
            confidence = f.get("confidence", 0.0)
            urgency = f.get("urgency", "low")
            source_url = f.get("source_url", "")
            finding_id = f.get("id", str(i))

            # Memory source string for the proposer prompt
            ms = f"[research] {title} — {action}"
            if source_url:
                ms += f" (source: {source_url})"
            ms += f" (confidence: {confidence:.0%}, urgency: {urgency})"
            memory_sources.append(ms)

            # FailurePattern with external_research_signal mechanism
            patterns.append(
                FailurePattern(
                    id=f"research__{finding_id[:12]}",
                    split=Split.HELD_IN,
                    signature=FailureSignature(
                        terminal_cause="research_opportunity",
                        causal_status="external_signal",
                        mechanism="external_research_signal",
                    ),
                    support=max(1, int(float(confidence) * 10)),
                    task_ids=[],
                    symptoms=[f"Research finding: {title}", f"Domain: {domain}"],
                    verifier_evidence=[summary, action],
                )
            )

        return ResearchFindings(
            memory_sources=memory_sources,
            patterns=patterns,
            raw=findings,
        )
