#!/usr/bin/env bash
set -Eeuo pipefail

HOST="${MINERVA_HOST:-minerva}"
REMOTE_ROOT_INPUT="${MINERVA_ROOT:-~/deployments/self-harness}"
RELEASE_ID="${MINERVA_RELEASE_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
PYTHON_VERSION="${MINERVA_PYTHON:-3.11}"
RUN_CHECKS="${MINERVA_RUN_CHECKS:-1}"

ssh_cmd=(ssh -o BatchMode=yes "$HOST")

remote_home="$("${ssh_cmd[@]}" 'printf "%s" "$HOME"')"
case "$REMOTE_ROOT_INPUT" in
  "~")
    remote_root="$remote_home"
    ;;
  "~/"*)
    remote_root="$remote_home/${REMOTE_ROOT_INPUT#"~/"}"
    ;;
  *)
    remote_root="$REMOTE_ROOT_INPUT"
    ;;
esac

remote_release="$remote_root/releases/$RELEASE_ID"

echo "Deploying SelfHarness to $HOST:$remote_release"

"${ssh_cmd[@]}" 'bash -s' -- "$remote_release" <<'REMOTE'
set -Eeuo pipefail
release="$1"
mkdir -p "$release"
REMOTE

rsync -az --delete \
  --exclude '.git/' \
  --exclude '.venv/' \
  --exclude '.mypy_cache/' \
  --exclude '.pytest_cache/' \
  --exclude '.ruff_cache/' \
  --exclude '__pycache__/' \
  --exclude '*.egg-info/' \
  --exclude 'dist/' \
  --exclude 'runs/' \
  --exclude '.DS_Store' \
  ./ "$HOST:$remote_release/"

"${ssh_cmd[@]}" 'bash -s' -- "$remote_release" "$PYTHON_VERSION" <<'REMOTE'
set -Eeuo pipefail
release="$1"
python_version="$2"
cd "$release"

if command -v uv >/dev/null 2>&1; then
  uv_bin="$(command -v uv)"
elif [ -x "$HOME/.local/bin/uv" ]; then
  uv_bin="$HOME/.local/bin/uv"
else
  echo "uv is required on the remote host to create the Python >=3.11 environment." >&2
  exit 2
fi

"$uv_bin" venv --python "$python_version" .venv
"$uv_bin" pip install --python .venv/bin/python pip
"$uv_bin" pip install --prerelease allow --python .venv/bin/python -e '.[dev,release,provenance,sigstore]'
REMOTE

if [ "$RUN_CHECKS" = "1" ]; then
  "${ssh_cmd[@]}" 'bash -s' -- "$remote_release" <<'REMOTE'
set -Eeuo pipefail
release="$1"
cd "$release"
python=".venv/bin/python"

"$python" -m self_harness.cli demo --rounds 2 --seed 0 --evaluation-repeats 2 --out runs/minerva-smoke
"$python" -m self_harness.cli audit-summary runs/minerva-smoke
make PYTHON="$python" check
make PYTHON="$python" release-candidate-evidence
make PYTHON="$python" reproduction-readiness-check
"$python" -c 'import json, pathlib; p=pathlib.Path("dist/self-harness-reproduction-readiness.json"); data=json.loads(p.read_text()); print({"reproduction_ready": data.get("reproduction_ready"), "reproduction_claimed": data.get("reproduction_claimed"), "report_hash": data.get("report_hash")})'
REMOTE
fi

"${ssh_cmd[@]}" 'bash -s' -- "$remote_root" "$remote_release" "$RELEASE_ID" "$HOST" <<'REMOTE'
set -Eeuo pipefail
root="$1"
release="$2"
release_id="$3"
host="$4"
python="$release/.venv/bin/python"

"$python" -c 'import datetime, json, pathlib, sys
release = pathlib.Path(sys.argv[1])
payload = {
    "application": "self-harness",
    "host": sys.argv[3],
    "release_id": sys.argv[2],
    "release_path": str(release),
    "promoted_at": datetime.datetime.now(datetime.UTC).isoformat().replace("+00:00", "Z"),
    "python": str(release / ".venv/bin/python"),
    "reproduction_claimed": False,
}
(release / "deployment.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
' "$release" "$release_id" "$host"

ln -sfn "$release" "$root/current.next"
rm -f "$root/current"
mv -f "$root/current.next" "$root/current"
mkdir -p "$root/shared"
printf '%s\n' "$release" > "$root/shared/last_release"
REMOTE

echo "Promoted $HOST:$remote_root/current -> $remote_release"
echo "Run remotely with:"
echo "  ssh $HOST 'cd $remote_root/current && .venv/bin/self-harness demo --rounds 1 --seed 0 --out runs/manual-smoke'"
