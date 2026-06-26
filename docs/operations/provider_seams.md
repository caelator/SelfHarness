# Provider Extension Seams

Provider seams define contracts for integrations that require operator-owned
infrastructure: secret managers, OAuth/OIDC token sources, registry credential
providers, and KMS/HSM signing services.

These seams are release/operator extension points. They do not contact cloud
providers, registries, Harbor, Docker, PyPI, Sigstore, or scanners by
themselves, and they do not change audit schemas, corpus schemas, readiness
hashes, or benchmark reproduction claims.

## Protocols

The `self_harness.providers` package exposes:

- `SecretResolver.resolve(name) -> str`
- `OAuthTokenProvider.token(scope, deadline) -> OAuthToken`
- `RegistryCredentialProvider.credentials_for(registry) -> RegistryCredential`
- `KmsSigner.sign(payload, deadline=...) -> KmsSignature`

Provider implementations must keep private keys, tokens, passwords, registry
auth files, and secret-manager responses outside audit artifacts and release
notes. Value objects redact token/password fields in `repr`.

## Registry

`ProviderRegistry` maps provider names to factories. It is intentionally local
process state; production deployments should register provider packages during
operator-controlled startup rather than relying on implicit network discovery.

```python
from self_harness.providers import ProviderRegistry, StaticSecretResolver

registry = ProviderRegistry()
registry.register_secret_resolver(
    "fixture",
    lambda: StaticSecretResolver({"harbor-auth": "Bearer test-token"}),
)
resolver = registry.secret_resolver("fixture")
```

## Static Providers

Static providers are included for tests, examples, and local dry-run demos:

- `StaticSecretResolver`
- `StaticOAuthTokenProvider`
- `StaticRegistryCredentialProvider`
- `StaticKmsSigner`

They are not production KMS, HSM, OAuth, OIDC, registry, or secret-manager
implementations. Do not load production secrets into static providers.

## Future Provider Packages

Provider packages should implement these protocols without changing
Self-Harness audit writers. A production adapter can translate a provider
response into existing operator surfaces, such as an Authorization header,
Trivy registry config path, or external signer response, while preserving the
same release/operator boundary.
