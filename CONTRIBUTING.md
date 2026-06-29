# Contributing To SelfHarness

Thanks for helping make SelfHarness better. The project is MIT licensed and
welcomes focused, evidence-backed improvements.

## Development Rules

- Keep runtime changes narrow and covered by tests.
- Do not weaken reproduction-boundary language. Local green gates are not a
  Terminal-Bench reproduction claim.
- Use trusted corpora and verifier surfaces only. Corpus JSON must not become a
  way to select arbitrary Python code, shell commands, endpoints, or secrets.
- Preserve deterministic audit output unless a change explicitly migrates an
  audit schema and documents the migration.
- Prefer additive provider seams over hard dependencies on one model SDK.

## Setup

```bash
python -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest
```

For focused CLI work:

```bash
.venv/bin/python -m pytest tests/test_cli_agent.py tests/test_cli_home.py -q
.venv/bin/python -m ruff check src/self_harness/cli.py src/self_harness/cli_agent src/self_harness/cli_home.py tests/test_cli_agent.py tests/test_cli_home.py
```

## Pull Request Checklist

- Explain the operator/researcher problem being solved.
- Include the commands you ran.
- Note any reproduction-readiness boundary explicitly.
- Add or update docs when CLI behavior, audit artifacts, release gates, or
  provider configuration changes.
- Do not include API keys, service credentials, private corpora, or raw live
  operator artifacts.

## Source Of Truth

Development currently treats `minerva:~/Documents/SelfHarness` as the canonical
working tree. See [docs/governance/source_of_truth.md](docs/governance/source_of_truth.md).
