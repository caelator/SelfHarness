#!/usr/bin/env bash
# Launch the SelfHarness operator console in the foreground so you can watch
# request/run activity live and stop it with Ctrl-C.
#
# Secrets (ZAI_API_KEY, ZAI_BASE_URL) are sourced from .env.minerva — they never
# appear on the command line or in process args.
set -euo pipefail
cd "$(dirname "$0")"

ENV_FILE="${SELF_HARNESS_ENV_FILE:-.env.minerva}"
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  . "./$ENV_FILE"
  set +a
  echo "Loaded $ENV_FILE (ZAI_API_KEY length=${#ZAI_API_KEY}, base=${ZAI_BASE_URL:-default})"
else
  echo "WARNING: $ENV_FILE not found — GLM agentic runs/dev-tasks/chat will fail without ZAI_API_KEY." >&2
fi

if [[ -z "${ZAI_API_KEY:-}" ]]; then
  echo "WARNING: ZAI_API_KEY is empty — the console will start but GLM features are disabled." >&2
fi

HOST="${SELF_HARNESS_UI_HOST:-127.0.0.1}"
PORT="${SELF_HARNESS_UI_PORT:-8765}"

echo "Starting SelfHarness console on http://$HOST:$PORT  (Ctrl-C to stop)"
exec .venv/bin/self-harness ui \
  --proposer "${SELF_HARNESS_UI_PROPOSER:-glm}" \
  --host "$HOST" --port "$PORT" \
  --root . --runs-dir runs
