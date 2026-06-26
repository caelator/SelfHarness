PYTHON ?= .venv/bin/python
SOURCE_DATE_EPOCH ?= 315532800
ARTIFACT_DIR ?= dist/reproduction-artifacts
REPRODUCTION_BUNDLE ?= $(ARTIFACT_DIR)/bundle.json
REPRODUCTION_BUNDLE_SIGNATURE ?= $(ARTIFACT_DIR)/bundle.sig
CAPTURE_MANIFEST ?= dist/self-harness-capture-manifest.json
CAPTURE_MANIFEST_SIGNATURE ?= dist/self-harness-capture-manifest.sig
CAPTURE_REHEARSAL_DIR ?= dist/self-harness-capture-rehearsal
CAPTURE_REHEARSAL_REPORT ?= dist/self-harness-capture-rehearsal.json
LIVE_AUDIT_VERIFY_DIR ?= dist/self-harness-live-audit-verify
LIVE_AUDIT_VERIFY_RUN_DIR ?= $(LIVE_AUDIT_VERIFY_DIR)/harbor-run
LIVE_AUDIT_VERIFY_AUDIT_DIR ?= $(LIVE_AUDIT_VERIFY_DIR)/audit
LIVE_AUDIT_VERIFY_ARTIFACT ?= $(LIVE_AUDIT_VERIFY_DIR)/live_harbor_audit.json
LIVE_AUDIT_VERIFY_PROVENANCE ?= $(LIVE_AUDIT_VERIFY_DIR)/live-audit-provenance.json
LIVE_AUDIT_VERIFY_SIGNATURE ?= $(LIVE_AUDIT_VERIFY_DIR)/live-audit-provenance.sig
LIVE_AUDIT_VERIFY_KEY ?= $(LIVE_AUDIT_VERIFY_DIR)/live-audit-provenance.ed25519
LIVE_AUDIT_VERIFY_REPORT ?= dist/self-harness-audit-verify-live.json
CAPTURE_REHEARSAL_ID ?= terminal-bench-2.0-rehearsal-001
CAPTURE_MANIFEST_ID ?= terminal-bench-2.0-capture-plan-001
CAPTURE_MANIFEST_BUNDLE_ID ?= terminal-bench-2.0-operator-run-001
CAPTURE_MANIFEST_OPERATOR_LABEL ?= self-harness-operator
CAPTURE_MANIFEST_CREATED_AT ?= 2026-06-24T00:00:00Z
CAPTURE_MANIFEST_RUN_ID ?= terminal-bench-2.0-live-001
CAPTURE_MANIFEST_SOURCE_PROVIDER ?= harbor
CAPTURE_MANIFEST_SOURCE_CAPTURED_AFTER ?= 2026-06-24T00:00:00Z
CAPTURE_MANIFEST_SOURCE_CAPTURED_BEFORE ?= 2026-06-25T00:00:00Z
CAPTURE_MANIFEST_EVALUATOR ?= terminal-bench-verifier
CAPTURE_MANIFEST_TOOL_SET ?= minimal-terminal-tools
CAPTURE_MANIFEST_TOOL_BUDGET_JSON ?= {"max_tokens":8192,"max_tool_calls":100}
CAPTURE_MANIFEST_OUTBOUND_BANDWIDTH_CAP_BPS ?= 2000000
CAPTURE_MANIFEST_MIRRORED_RESOURCE ?= https://resources.example/terminal-bench
CAPTURE_MANIFEST_SIGNING_PROVIDER ?= local-fixture
CAPTURE_MANIFEST_KEY_ID ?= capture-manifest-check
REPRODUCTION_BUNDLE_ID ?=
REPRODUCTION_BUNDLE_OPERATOR_LABEL ?=
REPRODUCTION_BUNDLE_CREATED_AT ?=
REPRODUCTION_BUNDLE_SOURCE_PROVIDER ?=
REPRODUCTION_BUNDLE_SOURCE_CAPTURED_AT ?=
READINESS_BASELINE_CATALOG ?= docs/operations/readiness_matrix.json
READINESS_CANDIDATE_CATALOG ?= docs/operations/readiness_matrix.json
REPRODUCTION_AUDIT_VERIFY_RESULT ?= $(LIVE_AUDIT_VERIFY_REPORT)

.PHONY: test invariants audit-verify audit-verify-live readiness readiness-matrix readiness-drift-check readiness-promotion-check reproduction-readiness-check reproduction-readiness-artifact-shape-lint reproduction-bundle-build reproduction-bundle-sign reproduction-bundle-check reproduction-readiness-bundle-verify capture-manifest-build capture-rehearsal capture-manifest-check capture-manifest-diff-check capture-extract-check capture-admit-check lint typecheck build reproducible-build-check provenance provenance-sign vuln-check scanner-check harbor-discovery-check model-backend-preflight container-preflight operator-check operator-promotion-check operator-policy-binding-check attestation-check migration-check release-candidate-evidence release-candidate-evidence-reproduction smoke release-smoke sbom check

test:
	$(PYTHON) -m pytest -q

invariants:
	$(PYTHON) -m pytest -q tests/invariants

audit-verify:
	mkdir -p dist
	rm -rf dist/self-harness-audit-verify-run
	$(PYTHON) -m self_harness.cli demo --rounds 1 --seed 0 --out dist/self-harness-audit-verify-run
	$(PYTHON) -m self_harness.cli audit-trajectory dist/self-harness-audit-verify-run
	$(PYTHON) -m self_harness.cli audit-verify dist/self-harness-audit-verify-run --json --out dist/self-harness-audit-verify.json

audit-verify-live:
	mkdir -p "$(LIVE_AUDIT_VERIFY_DIR)" dist
	rm -rf "$(LIVE_AUDIT_VERIFY_RUN_DIR)" "$(LIVE_AUDIT_VERIFY_AUDIT_DIR)"
	$(PYTHON) scripts/build_live_audit_verify_fixture.py --run-dir "$(LIVE_AUDIT_VERIFY_RUN_DIR)" --audit-dir "$(LIVE_AUDIT_VERIFY_AUDIT_DIR)" --manifest tests/fixtures/terminal_bench/manifest.json --live-harbor-audit "$(LIVE_AUDIT_VERIFY_ARTIFACT)" --provenance "$(LIVE_AUDIT_VERIFY_PROVENANCE)" --signature "$(LIVE_AUDIT_VERIFY_SIGNATURE)" --private-key "$(LIVE_AUDIT_VERIFY_KEY)" --public-key "$(LIVE_AUDIT_VERIFY_KEY).pub"
	$(PYTHON) scripts/audit_verify_live.py --audit-dir "$(LIVE_AUDIT_VERIFY_AUDIT_DIR)" --live-harbor-audit "$(LIVE_AUDIT_VERIFY_ARTIFACT)" --provenance "$(LIVE_AUDIT_VERIFY_PROVENANCE)" --provenance-signature "$(LIVE_AUDIT_VERIFY_SIGNATURE)" --public-key "$(LIVE_AUDIT_VERIFY_KEY).pub" --require-signature --json --out "$(LIVE_AUDIT_VERIFY_REPORT)"

readiness: invariants audit-verify

readiness-matrix:
	mkdir -p dist
	$(PYTHON) scripts/readiness_matrix_report.py --catalog docs/operations/readiness_matrix.json --out dist/self-harness-readiness-matrix.json

lint:
	$(PYTHON) -m ruff check src tests scripts

typecheck:
	$(PYTHON) -m mypy src

build:
	SOURCE_DATE_EPOCH=$(SOURCE_DATE_EPOCH) PYTHONHASHSEED=0 $(PYTHON) -m build --no-isolation

reproducible-build-check: build
	$(PYTHON) scripts/verify_reproducible_build.py --sdist $$(ls dist/*.tar.gz) --wheel $$(ls dist/*.whl) --repo-root . --source-date-epoch "$(SOURCE_DATE_EPOCH)" --out dist/self-harness-reproducible-build.json

provenance: build
	$(PYTHON) scripts/build_provenance.py --repo-root .

provenance-sign: provenance
	@manifest=$$(ls dist/*-provenance.json); \
	set -- --manifest "$$manifest"; \
	if [ -n "$$RELEASE_PROVENANCE_EXTERNAL_SIGNER" ]; then \
		set -- "$$@" --external-signer "$$RELEASE_PROVENANCE_EXTERNAL_SIGNER" --provider "$${RELEASE_PROVENANCE_PROVIDER:-external}"; \
	elif [ -n "$$RELEASE_PROVENANCE_KEY" ]; then \
		set -- "$$@" --private-key "$$RELEASE_PROVENANCE_KEY" --provider "$${RELEASE_PROVENANCE_PROVIDER:-local-pem}"; \
		if [ -n "$$RELEASE_PROVENANCE_PUBLIC_KEY" ]; then set -- "$$@" --public-key "$$RELEASE_PROVENANCE_PUBLIC_KEY"; fi; \
		if [ -n "$$RELEASE_PROVENANCE_PASSPHRASE_ENV" ]; then set -- "$$@" --passphrase-env "$$RELEASE_PROVENANCE_PASSPHRASE_ENV"; fi; \
	else \
		echo "set RELEASE_PROVENANCE_KEY or RELEASE_PROVENANCE_EXTERNAL_SIGNER"; \
		exit 2; \
	fi; \
	if [ -n "$$RELEASE_PROVENANCE_KEY_ID" ]; then set -- "$$@" --key-id "$$RELEASE_PROVENANCE_KEY_ID"; fi; \
	if [ -n "$$RELEASE_PROVENANCE_FINGERPRINT" ]; then set -- "$$@" --fingerprint "$$RELEASE_PROVENANCE_FINGERPRINT"; fi; \
	$(PYTHON) scripts/sign_provenance.py "$$@"

vuln-check: build
	@args="--wheel $$(ls dist/*.whl) --out dist/self-harness-vuln-report.json"; \
	if [ -n "$$VULN_POLICY" ]; then args="$$args --policy $$VULN_POLICY"; fi; \
	$(PYTHON) scripts/vuln_check.py $$args

scanner-check:
	$(PYTHON) scripts/scanner_run.py --dry-run --image registry.example/trusted/verifier:1 --digest sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc --out dist/self-harness-trivy-report.json --db-registry-config tests/fixtures/vuln/trivy_registry_config.json --result-out dist/self-harness-scanner-check.json
	$(PYTHON) scripts/scanner_db_update.py --dry-run --cache-dir tests/fixtures/vuln/trivy_db --db-registry-config tests/fixtures/vuln/trivy_registry_config.json --result-out dist/self-harness-scanner-db-update.json
	@tmp=$$(mktemp -d); \
	$(PYTHON) scripts/scanner_run.py --image registry.example/trusted/verifier:1 --digest sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc --out "$$tmp/self-harness-trivy-report.json" --replay tests/fixtures/vuln/trivy_fresh_with_timestamp.json --db-dir tests/fixtures/vuln/trivy_db --db-freshness-policy tests/fixtures/vuln/scanner_db_freshness_policy.json --today 2026-06-24; \
	code=$$?; rm -rf "$$tmp"; exit $$code

harbor-discovery-check:
	$(PYTHON) scripts/harbor_discovery.py --dry-run --url https://harbor.example --project terminal-bench --repository agents/verifier --reference stable
	$(PYTHON) scripts/harbor_discovery.py --url https://harbor.example --project terminal-bench --repository agents/verifier --reference stable --replay tests/fixtures/harbor/harbor_artifact_valid.json --result-out dist/self-harness-harbor-discovery.json

model-backend-preflight:
	mkdir -p dist
	@mode=$${MODEL_BACKEND_PREFLIGHT_MODE:-dry-run}; \
	$(PYTHON) scripts/model_backend_preflight.py --mode "$$mode" --out dist/self-harness-model-backend-preflight.json; \
	code=$$?; \
	if [ "$$code" -ne 0 ] && { [ "$$code" -ne 2 ] || [ "$$mode" != "dry-run" ]; }; then exit "$$code"; fi

container-preflight:
	mkdir -p dist
	$(PYTHON) scripts/container_preflight_report.py --mode offline --image "$${CONTAINER_PREFLIGHT_IMAGE:-registry.example/trusted/verifier:1}" --out dist/self-harness-container-preflight.json

operator-check: readiness-matrix
	$(PYTHON) scripts/operator_preflight.py --bundle tests/fixtures/operator_bundle/valid.json --today 2026-06-24 --db-registry-config tests/fixtures/vuln/trivy_registry_config.json --harbor-url https://harbor.example --harbor-project terminal-bench --harbor-repository agents/verifier --harbor-reference stable --harbor-replay tests/fixtures/harbor/harbor_artifact_valid.json --result-out dist/self-harness-operator-preflight.json

readiness-drift-check: operator-check scanner-check harbor-discovery-check container-preflight attestation-check smoke
	@args="--catalog docs/operations/readiness_matrix.json --operator-preflight-result dist/self-harness-operator-preflight.json --scanner-result dist/self-harness-scanner-check.json --harbor-discovery-result dist/self-harness-harbor-discovery.json --release-smoke-result dist/self-harness-release-smoke.json --container-preflight-result dist/self-harness-container-preflight.json --attestation-result dist/self-harness-attestation.json"; \
	if [ -f dist/self-harness-model-backend-preflight.json ]; then args="$$args --model-backend-preflight-result dist/self-harness-model-backend-preflight.json"; fi; \
	$(PYTHON) scripts/readiness_drift_report.py $$args --out dist/self-harness-readiness-drift.json

readiness-promotion-check:
	mkdir -p dist
	@args="--baseline-catalog $(READINESS_BASELINE_CATALOG) --candidate-catalog $(READINESS_CANDIDATE_CATALOG)"; \
	if [ -f dist/self-harness-operator-preflight.json ]; then args="$$args --operator-preflight-result dist/self-harness-operator-preflight.json"; fi; \
	if [ -f dist/self-harness-scanner-check.json ]; then args="$$args --scanner-result dist/self-harness-scanner-check.json"; fi; \
	if [ -f dist/self-harness-harbor-discovery.json ]; then args="$$args --harbor-discovery-result dist/self-harness-harbor-discovery.json"; fi; \
	if [ -f dist/self-harness-release-smoke.json ]; then args="$$args --release-smoke-result dist/self-harness-release-smoke.json"; fi; \
	if [ -f dist/self-harness-model-backend-preflight.json ]; then args="$$args --model-backend-preflight-result dist/self-harness-model-backend-preflight.json"; fi; \
	if [ -f dist/self-harness-container-preflight.json ]; then args="$$args --container-preflight-result dist/self-harness-container-preflight.json"; fi; \
	if [ -f dist/self-harness-attestation.json ]; then args="$$args --attestation-result dist/self-harness-attestation.json"; fi; \
	$(PYTHON) scripts/readiness_promotion_report.py $$args --out dist/self-harness-readiness-promotion.json

reproduction-readiness-check: readiness-matrix audit-verify audit-verify-live
	@args="--requirements docs/operations/benchmark_reproduction_requirements.json --readiness-matrix-result dist/self-harness-readiness-matrix.json --audit-verify-result $(REPRODUCTION_AUDIT_VERIFY_RESULT)"; \
	if [ -f "$(REPRODUCTION_BUNDLE)" ]; then \
		args="$$args --reproduction-bundle $(REPRODUCTION_BUNDLE)"; \
		if [ -f "$(REPRODUCTION_BUNDLE_SIGNATURE)" ]; then args="$$args --reproduction-bundle-signature $(REPRODUCTION_BUNDLE_SIGNATURE)"; fi; \
		if [ -n "$$REPRODUCTION_BUNDLE_PUBLIC_KEY" ]; then args="$$args --reproduction-bundle-public-key $$REPRODUCTION_BUNDLE_PUBLIC_KEY"; fi; \
	else \
		if [ -f dist/self-harness-model-backend-preflight.json ]; then args="$$args --artifact model_backend_preflight_report=dist/self-harness-model-backend-preflight.json"; fi; \
	fi; \
	$(PYTHON) scripts/reproduction_readiness_report.py $$args --out dist/self-harness-reproduction-readiness.json; \
	code=$$?; \
	if [ "$$code" -ne 0 ] && [ "$$code" -ne 2 ]; then exit "$$code"; fi; \
	exit 0

reproduction-readiness-artifact-shape-lint:
	$(PYTHON) scripts/reproduction_readiness_artifact_shape_lint.py --artifact-dir $(ARTIFACT_DIR) --out dist/self-harness-reproduction-artifact-shapes.json

reproduction-bundle-build:
	@if [ -z "$(REPRODUCTION_BUNDLE_ID)" ] || [ -z "$(REPRODUCTION_BUNDLE_OPERATOR_LABEL)" ] || [ -z "$(REPRODUCTION_BUNDLE_CREATED_AT)" ] || [ -z "$(REPRODUCTION_BUNDLE_SOURCE_PROVIDER)" ] || [ -z "$(REPRODUCTION_BUNDLE_SOURCE_CAPTURED_AT)" ]; then \
		echo "set REPRODUCTION_BUNDLE_ID, REPRODUCTION_BUNDLE_OPERATOR_LABEL, REPRODUCTION_BUNDLE_CREATED_AT, REPRODUCTION_BUNDLE_SOURCE_PROVIDER, and REPRODUCTION_BUNDLE_SOURCE_CAPTURED_AT"; \
		exit 2; \
	fi
	$(PYTHON) scripts/reproduction_bundle_build.py --artifact-dir "$(ARTIFACT_DIR)" --bundle-id "$(REPRODUCTION_BUNDLE_ID)" --operator-label "$(REPRODUCTION_BUNDLE_OPERATOR_LABEL)" --created-at "$(REPRODUCTION_BUNDLE_CREATED_AT)" --source-provider "$(REPRODUCTION_BUNDLE_SOURCE_PROVIDER)" --source-captured-at "$(REPRODUCTION_BUNDLE_SOURCE_CAPTURED_AT)" --out "$(REPRODUCTION_BUNDLE)"

reproduction-bundle-sign:
	@set -- --bundle "$(REPRODUCTION_BUNDLE)" --out "$(REPRODUCTION_BUNDLE_SIGNATURE)"; \
	if [ -n "$$REPRODUCTION_BUNDLE_EXTERNAL_SIGNER" ]; then \
		set -- "$$@" --external-signer "$$REPRODUCTION_BUNDLE_EXTERNAL_SIGNER" --provider "$${REPRODUCTION_BUNDLE_SIGNATURE_PROVIDER:-external}"; \
	elif [ -n "$$REPRODUCTION_BUNDLE_KEY" ]; then \
		set -- "$$@" --private-key "$$REPRODUCTION_BUNDLE_KEY" --provider "$${REPRODUCTION_BUNDLE_SIGNATURE_PROVIDER:-local-pem}"; \
		if [ -n "$$REPRODUCTION_BUNDLE_PUBLIC_KEY" ]; then set -- "$$@" --public-key "$$REPRODUCTION_BUNDLE_PUBLIC_KEY"; fi; \
		if [ -n "$$REPRODUCTION_BUNDLE_PASSPHRASE_ENV" ]; then set -- "$$@" --passphrase-env "$$REPRODUCTION_BUNDLE_PASSPHRASE_ENV"; fi; \
	else \
		echo "set REPRODUCTION_BUNDLE_KEY or REPRODUCTION_BUNDLE_EXTERNAL_SIGNER"; \
		exit 2; \
	fi; \
	if [ -n "$$REPRODUCTION_BUNDLE_KEY_ID" ]; then set -- "$$@" --key-id "$$REPRODUCTION_BUNDLE_KEY_ID"; fi; \
	if [ -n "$$REPRODUCTION_BUNDLE_FINGERPRINT" ]; then set -- "$$@" --fingerprint "$$REPRODUCTION_BUNDLE_FINGERPRINT"; fi; \
	$(PYTHON) scripts/sign_reproduction_bundle.py "$$@"

reproduction-bundle-check: reproduction-bundle-build reproduction-bundle-sign reproduction-readiness-bundle-verify

reproduction-readiness-bundle-verify:
	@args="--bundle $(REPRODUCTION_BUNDLE) --signature $(REPRODUCTION_BUNDLE_SIGNATURE) --require-signature --out dist/self-harness-reproduction-bundle.json"; \
	if [ -n "$$REPRODUCTION_BUNDLE_PUBLIC_KEY" ]; then args="$$args --public-key $$REPRODUCTION_BUNDLE_PUBLIC_KEY"; fi; \
	$(PYTHON) scripts/reproduction_bundle_verify.py $$args

capture-manifest-build:
	mkdir -p dist
	$(PYTHON) scripts/capture_manifest_build.py --manifest-id "$(CAPTURE_MANIFEST_ID)" --bundle-id "$(CAPTURE_MANIFEST_BUNDLE_ID)" --operator-label "$(CAPTURE_MANIFEST_OPERATOR_LABEL)" --created-at "$(CAPTURE_MANIFEST_CREATED_AT)" --run-id "$(CAPTURE_MANIFEST_RUN_ID)" --model-backend minimax --model-backend qwen --model-backend glm --evaluator "$(CAPTURE_MANIFEST_EVALUATOR)" --tool-set "$(CAPTURE_MANIFEST_TOOL_SET)" --tool-budget-json '$(CAPTURE_MANIFEST_TOOL_BUDGET_JSON)' --outbound-bandwidth-cap-bps "$(CAPTURE_MANIFEST_OUTBOUND_BANDWIDTH_CAP_BPS)" --mirrored-resource "$(CAPTURE_MANIFEST_MIRRORED_RESOURCE)" --source-provider "$(CAPTURE_MANIFEST_SOURCE_PROVIDER)" --source-captured-after "$(CAPTURE_MANIFEST_SOURCE_CAPTURED_AFTER)" --source-captured-before "$(CAPTURE_MANIFEST_SOURCE_CAPTURED_BEFORE)" --signing-provider "$(CAPTURE_MANIFEST_SIGNING_PROVIDER)" --key-id "$(CAPTURE_MANIFEST_KEY_ID)" --out "$(CAPTURE_MANIFEST)"

capture-rehearsal: capture-manifest-build readiness-matrix
	@tmp=$$(mktemp -d); \
	trap 'rm -rf "$$tmp"' EXIT; \
	$(PYTHON) -m self_harness.cli corpus-keygen --out "$$tmp/capture-manifest.ed25519" --force; \
	$(PYTHON) scripts/sign_capture_manifest.py --manifest "$(CAPTURE_MANIFEST)" --private-key "$$tmp/capture-manifest.ed25519" --public-key "$$tmp/capture-manifest.ed25519.pub" --provider "$(CAPTURE_MANIFEST_SIGNING_PROVIDER)" --key-id "$(CAPTURE_MANIFEST_KEY_ID)" --out "$(CAPTURE_MANIFEST_SIGNATURE)"; \
	$(PYTHON) scripts/capture_manifest_verify.py --manifest "$(CAPTURE_MANIFEST)" --signature "$(CAPTURE_MANIFEST_SIGNATURE)" --public-key "$$tmp/capture-manifest.ed25519.pub" --require-signature --out dist/self-harness-capture-manifest-report.json; \
	$(PYTHON) scripts/capture_rehearsal.py --manifest "$(CAPTURE_MANIFEST)" --manifest-signature "$(CAPTURE_MANIFEST_SIGNATURE)" --public-key "$$tmp/capture-manifest.ed25519.pub" --require-manifest-signature --rehearsal-id "$(CAPTURE_REHEARSAL_ID)" --operator-label "$(CAPTURE_MANIFEST_OPERATOR_LABEL)" --out-dir "$(CAPTURE_REHEARSAL_DIR)" --readiness-matrix-result dist/self-harness-readiness-matrix.json --bundle-private-key "$$tmp/capture-manifest.ed25519" --bundle-public-key "$$tmp/capture-manifest.ed25519.pub" --bundle-signature-provider "$(CAPTURE_MANIFEST_SIGNING_PROVIDER)" --bundle-key-id "$(CAPTURE_MANIFEST_KEY_ID)" --require-bundle-signature --report-out "$(CAPTURE_REHEARSAL_REPORT)"

capture-manifest-check: capture-rehearsal
	$(PYTHON) -m pytest -q tests/test_capture_manifest.py tests/test_capture_manifest_build.py tests/test_capture_rehearsal.py

capture-manifest-diff-check:
	$(PYTHON) -m pytest -q tests/test_capture_manifest.py -k diff

capture-extract-check:
	$(PYTHON) -m pytest -q tests/test_capture_extract.py

capture-admit-check:
	$(PYTHON) -m pytest -q tests/test_capture_admit.py

operator-promotion-check:
	mkdir -p dist
	rm -f dist/self-harness-operator-promotion.ed25519
	@tmp=$$(mktemp -d); \
	trap 'rm -rf "$$tmp"' EXIT; \
	$(PYTHON) -m self_harness.cli corpus-keygen --out "$$tmp/self-harness-operator-promotion.ed25519" --force; \
	cp "$$tmp/self-harness-operator-promotion.ed25519.pub" dist/self-harness-operator-promotion.ed25519.pub; \
	$(PYTHON) -m self_harness.cli operator-promotion sign --manifest tests/fixtures/operator_promotion/valid.json --private-key "$$tmp/self-harness-operator-promotion.ed25519" --public-key dist/self-harness-operator-promotion.ed25519.pub --provider local-fixture --key-id operator-promotion-check --out dist/self-harness-operator-promotion.sig; \
	$(PYTHON) scripts/operator_promotion_preflight.py --promotion tests/fixtures/operator_promotion/valid.json --signature dist/self-harness-operator-promotion.sig --trusted-public-key dist/self-harness-operator-promotion.ed25519.pub --result-out dist/self-harness-operator-promotion-preflight.json

operator-policy-binding-check:
	mkdir -p dist
	$(PYTHON) scripts/operator_policy_binding_verify.py --bundle tests/fixtures/operator_bundle/valid.json --promotion tests/fixtures/operator_promotion/valid.json --today 2026-06-24 --result-out dist/self-harness-operator-policy-binding.json

attestation-check: provenance
	@wheel=$$(ls dist/*.whl); \
	$(PYTHON) scripts/build_structural_attestation_fixture.py --bundle tests/fixtures/attestations/sigstore_bundle.json --material "$$wheel" --out dist/self-harness-pypi-attestation.json; \
	$(PYTHON) scripts/verify_attestation.py --bundle dist/self-harness-pypi-attestation.json --material "$$wheel" --trust-root tests/fixtures/attestations/trust_root.json --backend structural --out dist/self-harness-attestation.json

migration-check:
	$(PYTHON) -m pytest -q tests/test_audit_migration.py tests/test_audit_migration_framework.py

release-candidate-evidence: provenance reproducible-build-check vuln-check scanner-check harbor-discovery-check readiness-matrix readiness-drift-check readiness-promotion-check operator-check operator-promotion-check operator-policy-binding-check attestation-check audit-verify
	$(PYTHON) scripts/release_candidate_evidence.py --readiness-hash tests/fixtures/canonical_audit_hash.txt --vuln-report dist/self-harness-vuln-report.json --scanner-result dist/self-harness-scanner-check.json --scanner-db-update-result dist/self-harness-scanner-db-update.json --harbor-discovery-result dist/self-harness-harbor-discovery.json --operator-preflight-result dist/self-harness-operator-preflight.json --operator-promotion-result dist/self-harness-operator-promotion-preflight.json --operator-policy-binding-result dist/self-harness-operator-policy-binding.json --readiness-matrix-result dist/self-harness-readiness-matrix.json --readiness-drift-result dist/self-harness-readiness-drift.json --readiness-promotion-result dist/self-harness-readiness-promotion.json --reproducible-build-result dist/self-harness-reproducible-build.json --attestation-result dist/self-harness-attestation.json --audit-verify-result dist/self-harness-audit-verify.json --provenance $$(ls dist/*-provenance.json) --out dist/self-harness-release-candidate-evidence.json

release-candidate-evidence-reproduction: release-candidate-evidence reproduction-readiness-bundle-verify reproduction-readiness-check
	$(PYTHON) scripts/release_candidate_evidence.py --readiness-hash tests/fixtures/canonical_audit_hash.txt --vuln-report dist/self-harness-vuln-report.json --scanner-result dist/self-harness-scanner-check.json --scanner-db-update-result dist/self-harness-scanner-db-update.json --harbor-discovery-result dist/self-harness-harbor-discovery.json --operator-preflight-result dist/self-harness-operator-preflight.json --operator-promotion-result dist/self-harness-operator-promotion-preflight.json --operator-policy-binding-result dist/self-harness-operator-policy-binding.json --readiness-matrix-result dist/self-harness-readiness-matrix.json --readiness-drift-result dist/self-harness-readiness-drift.json --reproducible-build-result dist/self-harness-reproducible-build.json --reproduction-readiness-result dist/self-harness-reproduction-readiness.json --reproduction-bundle-result dist/self-harness-reproduction-bundle.json --require-reproduction-readiness --attestation-result dist/self-harness-attestation.json --audit-verify-result dist/self-harness-audit-verify.json --provenance $$(ls dist/*-provenance.json) --out dist/self-harness-release-candidate-evidence-reproduction.json

smoke: provenance
	$(PYTHON) scripts/release_smoke.py --wheel $$(ls dist/*.whl) --sdist $$(ls dist/*.tar.gz) --provenance $$(ls dist/*-provenance.json) --repo-root . --out dist/self-harness-release-smoke.json

release-smoke: check readiness release-candidate-evidence smoke

sbom:
	mkdir -p sbom && cyclonedx-py environment -o sbom/self_harness-sbom.json

check: lint typecheck test
