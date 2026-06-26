# Harbor Discovery

Self-Harness can discover Harbor artifact digests before scanner or Harbor
runs. Discovery artifacts are release/operator material. They do not change
audit schemas, corpus schemas, readiness hashes, or benchmark reproduction
claims.

## Dry Run

Dry-run mode constructs the Harbor REST request without network access:

```bash
python scripts/harbor_discovery.py \
  --dry-run \
  --url https://harbor.example \
  --project terminal-bench \
  --repository agents/verifier \
  --reference stable
```

The repository path is URL-encoded in the request, so nested Harbor repository
names such as `agents/verifier` are represented safely.

## Replay

Replay mode parses a captured Harbor artifact response and returns the artifact
digest, tags, media type, and child digests:

```bash
python scripts/harbor_discovery.py \
  --url https://harbor.example \
  --project terminal-bench \
  --repository agents/verifier \
  --reference stable \
  --replay tests/fixtures/harbor/harbor_artifact_valid.json
```

Replay mode is deterministic and is the CI path used by
`make harbor-discovery-check`.

## Live

Live mode omits `--dry-run` and `--replay`. It uses stdlib HTTP only and
requires an environment variable containing the full `Authorization` header:

```bash
export HARBOR_AUTHORIZATION='Bearer <token>'
python scripts/harbor_discovery.py \
  --url https://harbor.example \
  --project terminal-bench \
  --repository agents/verifier \
  --reference stable \
  --authorization-env HARBOR_AUTHORIZATION
```

Authorization values are redacted in JSON output. OAuth, OIDC refresh, robot
account minting, and Docker config parsing are intentionally deferred to
provider-specific helpers.

## Policy Binding

The discovered image and digest can be checked with the existing image-policy
logic before scanner or Harbor runs. Missing, malformed, or invalid digest
fields fail closed.

## Boundaries

- Dry-run and replay do not contact Harbor.
- Live discovery is not executed in CI.
- The parser targets the Harbor v2 artifact JSON shape and requires a
  `sha256:<64 lowercase hex>` digest.
- Multi-architecture platform selection is deferred; discovery returns the
  top-level digest and child digests for the caller to interpret.
