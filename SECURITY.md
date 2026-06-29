# Security Policy

SelfHarness is an agentic-evaluation framework. Treat it as infrastructure that
can execute code, call local tools, and process sensitive operator evidence.

## Supported Versions

The public project is pre-1.0. Security fixes target the current `main` branch
unless a release branch is explicitly announced.

## Reporting A Vulnerability

Please open a private security advisory on GitHub if available. If that is not
available, contact the maintainer through the repository owner profile and avoid
posting exploit details publicly until the issue is triaged.

Do not include secrets, API keys, private corpora, or live production artifacts
in public issues.

## Security Boundaries

- Run only trusted corpora.
- Keep signing keys, API keys, and provider credentials outside the repo.
- Use dry-run and structural verifier modes for local checks unless an operator
  intentionally enables live access.
- Treat generated audit and readiness artifacts as evidence, not as authority to
  bypass human review.
- The code CLI can invoke headless coding tools. Use it only in workspaces you
  trust.

## Disclosure Expectations

Reports that demonstrate arbitrary code execution through untrusted corpus
metadata, secret leakage, signature bypass, audit tampering, or unsafe provider
configuration are high priority.
