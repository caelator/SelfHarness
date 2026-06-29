# Source Of Truth

Status: stable
Audience: maintainers and operators
License: MIT; see [../../LICENSE](../../LICENSE)

SelfHarness currently uses Minerva as the canonical development host:

- Canonical working tree: `minerva:~/Documents/SelfHarness`
- Public remote: `https://github.com/caelator/SelfHarness.git`
- Local mirrors are convenience workspaces and must be treated as stale until
  synchronized against Minerva.

## Practical Rule

When code, docs, or release artifacts disagree, verify Minerva first. After a
local edit is made in a mirror, sync it to Minerva, run the focused checks
there, and push from the Minerva tree.

## What Minerva Is Not

Minerva is not an external specification authority for the Self-Harness paper.
The paper, repository source, release docs, and generated audit artifacts remain
the evidence sources for protocol and implementation claims.

## Reproduction Boundary

Do not use successful local demos, focused tests, or offline fixtures as proof
of Terminal-Bench reproduction. The repo's readiness tooling is intentionally
allowed to report `reproduction_ready: false` while local package and audit
gates are green.
