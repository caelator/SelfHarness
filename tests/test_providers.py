import pytest

from self_harness.providers import (
    KmsSignature,
    OAuthToken,
    ProviderError,
    ProviderRegistry,
    RegistryCredential,
    SecretResolutionError,
    StaticKmsSigner,
    StaticOAuthTokenProvider,
    StaticRegistryCredentialProvider,
    StaticSecretResolver,
)


def test_static_secret_resolver_returns_values_and_rejects_missing() -> None:
    resolver = StaticSecretResolver({"harbor-auth": "Bearer token"})

    assert resolver.resolve("harbor-auth") == "Bearer token"
    with pytest.raises(SecretResolutionError, match="secret not found"):
        resolver.resolve("missing")


def test_static_oauth_token_redacts_repr_and_checks_deadline() -> None:
    token = OAuthToken(access_token="secret-token", expires_at=200.0, scope="harbor")
    provider = StaticOAuthTokenProvider({"harbor": token})

    assert provider.token("harbor", deadline=100.0) == token
    assert "secret-token" not in repr(token)
    with pytest.raises(SecretResolutionError, match="expires before deadline"):
        provider.token("harbor", deadline=300.0)


def test_static_registry_credential_redacts_password() -> None:
    credential = RegistryCredential(registry="registry.example", username="robot", password="secret")
    provider = StaticRegistryCredentialProvider({"registry.example": credential})

    assert provider.credentials_for("registry.example") == credential
    assert "secret" not in repr(credential)


def test_static_kms_signer_returns_fixture_signature() -> None:
    signature = KmsSignature(
        signature_b64="c2lnbmF0dXJl",
        public_key_b64="cHVibGlj",
        fingerprint="0" * 64,
        key_id="fixture",
        provider="static",
    )
    signer = StaticKmsSigner(signature)

    assert signer.sign(b"payload", deadline=100.0) == signature
    with pytest.raises(SecretResolutionError, match="payload"):
        signer.sign(b"", deadline=100.0)


def test_provider_registry_registers_and_rejects_duplicates() -> None:
    registry = ProviderRegistry()
    registry.register_secret_resolver("static", lambda: StaticSecretResolver({"x": "y"}))

    assert registry.secret_resolver("static").resolve("x") == "y"
    with pytest.raises(ProviderError, match="already registered"):
        registry.register_secret_resolver("static", lambda: StaticSecretResolver({}))
    with pytest.raises(ProviderError, match="not registered"):
        registry.secret_resolver("missing")
