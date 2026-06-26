# Paper Model Backend Preflight

`scripts/model_backend_preflight.py` validates the chat-completions contract for
the three model backends evaluated in the Self-Harness paper:

- MiniMax M2.5 through operator-owned MiniMax hosted API credentials;
- Qwen3.5-35B-A3B through an operator-provisioned SGLang-compatible endpoint;
- GLM-5.2 through operator-owned Z.ai credentials.

The report is release/operator readiness evidence only. It does not run
Terminal-Bench, compare harness variants, score tasks, or claim benchmark
reproduction. Every report writes `reproduction_claimed: false`.

## Modes

Dry-run is the default and never contacts model providers:

```bash
make model-backend-preflight
```

The CLI writes `dist/self-harness-model-backend-preflight.json`. In dry-run
mode the report is intentionally `ok: false` and each backend check is
`not-run`. The Make target preserves the dry-run report for inspection.

Replay mode validates parser and usage-accounting behavior from checked-in mock
chat-completion fixtures:

```bash
.venv/bin/python scripts/model_backend_preflight.py \
  --mode replay \
  --replay tests/fixtures/model_backend \
  --out dist/self-harness-model-backend-preflight.json
```

Replay reports are contract-test evidence only. Readiness drift and benchmark
reproduction readiness require `mode: live` before a model-backend preflight
artifact can cover a provisioned paper model row.

Live mode is operator-invoked only:

```bash
MODEL_BACKEND_PREFLIGHT_MODE=live make model-backend-preflight
```

Live mode issues one tiny chat completion per selected backend and records only
non-secret metadata, token usage when returned by the provider, and a hash of
the response text.

## Environment

MiniMax live preflight requires:

- `MINIMAX_BASE_URL`
- `MINIMAX_API_KEY`

Qwen live preflight requires:

- `QWEN_SGLANG_BASE_URL`

GLM live preflight requires:

- `ZAI_BASE_URL`, usually `https://api.z.ai/api/paas/v4`
- `ZAI_API_KEY`

Endpoint values are used to construct a `/chat/completions` request. Secrets are
not written to the report.

## Readiness Flow

The readiness matrix keeps the paper model rows `blocked` by default and binds
them to the `model_backend_preflight` surface. Operators should reclassify a
row to `provisioned` only after the corresponding live backend preflight report
passes in an operator-owned environment.

`make readiness-drift-check` does not run this preflight. If
`dist/self-harness-model-backend-preflight.json` already exists, drift ingests
it as an existing surface artifact. If it is absent, blocked model rows remain
advisory, while provisioned model rows fail closed for missing surface evidence.

`make reproduction-readiness-check` also ingests the artifact when it exists,
mapping it to the `model_backend_preflight_report` artifact class. This can
satisfy only the model-backend artifact requirement; live Terminal-Bench,
Harbor, Docker, network-control, artifact-ingest, proposer LLM request-log,
PyPI, and Sigstore evidence are still required before `reproduction_ready` can
become true.

For paper reproduction, model preflight is deliberately insufficient by itself:
the bundle verifier also expects `proposer_llm_request_log` evidence and applies
`cross_artifact_proposer_model_binding` plus
`cross_artifact_proposer_round_count`. Those bindings prove the captured
proposer traffic used the same paper backend set declared by the fixed protocol
and that its round count and attempted proposal width match the protocol's
Self-Harness `T` and `K`, without storing raw prompts, responses, or
credentials in the reproduction bundle.
