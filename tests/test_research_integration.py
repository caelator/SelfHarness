"""Tests for research-radar integration."""

from __future__ import annotations

from self_harness.research import ResearchConfig, ResearchFindings, ResearchIntegrator
from self_harness.types import Split


def test_research_config_defaults():
    config = ResearchConfig()
    assert not config.enabled
    assert config.project_dir == "."
    assert config.max_findings == 10


def test_research_findings_empty():
    findings = ResearchFindings()
    assert findings.is_empty
    assert findings.memory_sources == []
    assert findings.patterns == []


def test_research_findings_not_empty():
    findings = ResearchFindings(raw=[{"id": "1"}])
    assert not findings.is_empty


def test_convert_finding_to_pattern():
    integrator = ResearchIntegrator(ResearchConfig(enabled=False))
    findings = integrator._convert([
        {
            "id": "test-001",
            "title": "Test Paper",
            "summary": "A summary",
            "suggested_action": "Do the thing",
            "domain": "rust-async",
            "confidence": 0.9,
            "urgency": "high",
            "source_url": "https://arxiv.org/abs/1234",
        }
    ])
    assert len(findings.patterns) == 1
    pattern = findings.patterns[0]
    assert pattern.split == Split.HELD_IN
    assert pattern.signature.mechanism == "external_research_signal"
    assert pattern.support == 9  # 0.9 * 10 = 9
    assert "Test Paper" in findings.memory_sources[0]


def test_convert_multiple_findings():
    integrator = ResearchIntegrator(ResearchConfig(enabled=False, max_findings=3))
    raw = [
        {"id": str(i), "title": f"Paper {i}", "summary": "", "suggested_action": "", "confidence": 0.5}
        for i in range(5)
    ]
    findings = integrator._convert(raw)
    assert len(findings.patterns) == 3  # capped at max_findings
    assert len(findings.memory_sources) == 3


def test_disabled_integrator_returns_empty():
    integrator = ResearchIntegrator(ResearchConfig(enabled=False))
    assert not integrator.is_available
    findings = integrator.fetch_findings()
    assert findings.is_empty
