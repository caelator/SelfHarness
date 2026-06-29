# SelfHarness Documentation

Status: stable public index
Audience: researchers, operators, and contributors
License: MIT; see [../LICENSE](../LICENSE)

SelfHarness is documented around one boundary: harness evolution may be
automatic, but trust, evidence, and promotion stay explicit.

![SelfHarness architecture](assets/self-harness-architecture.svg)

## Reading Path

1. Start with the project overview in [../README.md](../README.md).
2. Review the harness loop and editable-surface model in the architecture docs.
3. Run the deterministic demo and inspect the generated audit artifacts.
4. Move to trusted-corpus operations only after the local audit path is clear.
5. Treat reproduction-readiness docs as release/operator gates, not marketing
   claims.

## Core Concepts

- Self-Harness loop: evaluate, mine weaknesses, propose bounded harness edits,
  validate, promote, and audit.
- Editable surfaces: system, bootstrap, execution, verification,
  failure-recovery, runtime policy, tools, skills, memory sources, and
  subagents.
- Split discipline: proposers see held-in failures and summaries; promotion is
  validated with aggregate pass counts across held-in and held-out splits.
- Audit discipline: accepted, rejected, and invalid proposals carry explicit
  reasons and repeat metadata.
- Reproduction discipline: local evidence is valuable, but Terminal-Bench
  reproduction requires live operator-owned evidence bundles.

## Diagrams

- [Self-Harness loop](assets/self-harness-loop.svg)
- [System architecture](assets/self-harness-architecture.svg)
- [CLI control plane](assets/cli-control-plane.svg)
- [Reproduction boundary](assets/reproduction-boundary.svg)

## Operations

- [Code CLI](operations/code_cli.md)
- [Web interface](operations/web_interface.md)
- [Agentic runner](operations/agentic_runner.md)
- [Readiness matrix](operations/readiness_matrix.md)
- [Benchmark reproduction readiness](operations/benchmark_reproduction_readiness.md)
- [Release verification](operations/release_verification.md)
- [Minerva deployment](operations/minerva_deployment.md)

## Architecture

- [Audit schema policy](architecture/audit_schema_policy.md)
- [Benchmark report schema](architecture/benchmark_report_schema.md)
- [Harbor artifact layout](architecture/harbor_artifact_layout.md)
- [Provider extension seams](operations/provider_seams.md)

The many `glm_p*_plan.md` and `glm_p*_convergence.md` files are retained as
review history for production slices. They are evidence of design review, not a
replacement for current source verification.

## Governance

- [Source of truth](governance/source_of_truth.md)
- [Contributing](../CONTRIBUTING.md)
- [Security](../SECURITY.md)
- [Code of conduct](../CODE_OF_CONDUCT.md)
- [Changelog](../CHANGELOG.md)

## License

SelfHarness is released under the MIT License. See [../LICENSE](../LICENSE).
