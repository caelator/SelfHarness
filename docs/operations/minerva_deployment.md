# Minerva Deployment

`scripts/deploy_minerva.sh` deploys the working tree to the network host
`minerva` for operator testing.

The deployer uses release directories under
`~/deployments/self-harness/releases/<release-id>` and promotes
`~/deployments/self-harness/current` only after the remote install and checks
pass. The remote Python environment is isolated inside the release directory and
is created with `uv` using Python 3.11 by default. The deployer also installs
`pip` into the venv because the reproducible-build verifier rebuilds the wheel
through `python -m pip`. The deploy profile installs the `dev`, `release`,
`provenance`, and `sigstore` extras so the remote typecheck and attestation
surfaces match the local release checks. The installer allows prerelease
dependencies because Sigstore 3.x pins one transitive beta package.

Run:

```bash
scripts/deploy_minerva.sh
```

Useful overrides:

```bash
MINERVA_HOST=minerva scripts/deploy_minerva.sh
MINERVA_ROOT=~/deployments/self-harness scripts/deploy_minerva.sh
MINERVA_RELEASE_ID=manual-001 scripts/deploy_minerva.sh
MINERVA_RUN_CHECKS=0 scripts/deploy_minerva.sh
```

The default remote verification runs:

```bash
self-harness demo --rounds 2 --seed 0 --evaluation-repeats 2
make check
make release-candidate-evidence
make reproduction-readiness-check
```

The final readiness check is expected to keep
`reproduction_ready:false` until live Harbor/Docker, paper model backends, live
captures, and publication provenance are supplied.

Start the web operator console on the promoted release:

```bash
cd ~/deployments/self-harness/current
set -a
. ./.env.minerva
set +a
nohup .venv/bin/self-harness ui --proposer glm --host 127.0.0.1 --port 8765 --root . --runs-dir runs \
  > var/self-harness-ui.log 2>&1 &
echo $! > var/self-harness-ui.pid
```

Use an SSH tunnel from the workstation:

```bash
ssh -L 8765:127.0.0.1:8765 minerva
```
