# Round 1

CONVERGED: YES

## Verdict
The next most valuable slice that can be implemented without the unavailable live runtime is **P8: Production Hardening & Input Integrity**. This slice addresses three of the explicitly listed remaining gaps: cross-Python readiness, captured-fixture governance, and corpus signing/provenance. It materially improves the "production-ready" posture by verifying the supply chain (corpora and fixtures) and validating support across the declared Python versions.

## Critique
- **Environment Compatibility Gap**: `pyproject.toml` declares support for Python `>=3.11`, but CI only tests Python 3.11. A production-ready package must verify its claimed support matrix.
- **Fixture Integrity Gap**: P7 introduced the capture path for Terminal-Bench fixtures, but the dry-run replay logic does not verify if the underlying task definition has changed. If a task's `verifier_script` or `instruction` changes, replaying a stale captured fixture would yield false confidence (e.g., passing when it should now fail).
- **Supply Chain Integrity Gap**: `load_corpus` verifies internal checksums, but production environments require cryptographic signatures (e.g., Ed25519) to ensure corpora haven't been maliciously altered. The current architecture lacks a signature verification hook.

## Required Changes
1. **Expand CI Matrix**: Update `.github/workflows/ci.yml` to run the `check` and `readiness` gates against a matrix of Python versions (3.11, 3.12, 3.13).
2. **Enforce Fixture Governance**: Modify the Terminal-Bench dry-run adapter to compare the `task_source_hash` of the current manifest against the `task_source_hash` recorded in the captured fixture. Mismatches must raise a `PaperFidelityError` or `TaskLoadError` to prevent stale verification.
3. **Implement Corpus Provenance Verification**: Add an optional `[provenance]` dependency (e.g., `cryptography`). Update `load_corpus` to accept an optional public key and verify an Ed25519 signature over the corpus payload. Add a CLI flag `--require-corpus-signature` to enforce this during local or benchmark runs.

## Revised Plan
**P8 — Production Hardening & Input Integrity**

Scope:
- **CI Matrix Expansion**:
  - Modify `.github/workflows/ci.yml` to use `strategy.matrix.python-version: ["3.11", "3.12", "3.13"]`.
  - Ensure `make check` and `make readiness` pass on all versions.
- **Captured-Fixture Governance**:
  - Update `src/self_harness/adapters/terminal_bench/runner.py`.
  - When loading a fixture for dry-run, extract `task_source_hash` from the fixture JSON.
  - Compare it against the `task_source_hash` generated from the current manifest task.
  - If they differ, raise `PaperFidelityError("dry-run fixture stale: task source hash mismatch")`.
- **Corpus Signing & Provenance**:
  - Add `cryptography` to `[project.optional-dependencies]` under `provenance`.
  - Update `src/self_harness/corpus.py` to support a `signature` field (Base64 Ed25519) in the JSON format.
  - Update `load_corpus()` signature to accept `verify_signature_key: str | None`.
  - If a key is provided, verify the signature against the canonical JSON of the corpus payload (excluding the signature field itself). Fail with `TaskLoadError` if invalid or missing.
  - Add `--require-corpus-signature <path-to-pubkey>` to `self-harness local-demo` and `validate-tasks`.

Acceptance Criteria:
- `make check` and `make readiness` pass locally.
- CI workflow successfully completes on Python 3.11, 3.12, and 3.13.
- Altering a task definition in a manifest and attempting to use a previously captured fixture causes the dry-run to abort with a `PaperFidelityError`.
- A corpus JSON with a valid Ed25519 signature loads successfully when the public key is provided via CLI.
- A tampered corpus JSON fails to load with `TaskLoadError` (reason: `checksum-mismatch` or `invalid-signature`) when verification is enforced.

Out of scope (deferred):
- Live Harbor capture on a provisioned host (blocked by environment).
- Major-version audit migration shims (not yet needed).
- Richer non-subprocess verifier adapters (beyond Terminal-Bench script execution).

## Remaining Open Questions
None blocking. The choice of `cryptography` for Ed25519 is standard, but if a zero-dependency approach is strictly preferred, we could implement a minimal Ed25519 verifier or use `hashlib` with an HMAC secret instead of asymmetric keys. However, `cryptography` is the industry standard for Python package supply chains.

[usage] {"completion_tokens": 2915, "completion_tokens_details": {"reasoning_tokens": 1895}, "prompt_tokens": 12061, "prompt_tokens_details": {"cached_tokens": 0}, "total_tokens": 14976}
