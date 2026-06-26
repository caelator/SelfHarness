from __future__ import annotations

from dataclasses import dataclass

from self_harness.providers.base import (
    KmsSignature,
    OAuthToken,
    RegistryCredential,
    SecretResolutionError,
)


@dataclass(frozen=True)
class StaticSecretResolver:
    secrets: dict[str, str]

    def resolve(self, name: str) -> str:
        try:
            value = self.secrets[name]
        except KeyError as exc:
            raise SecretResolutionError(f"secret not found: {name}") from exc
        if not value:
            raise SecretResolutionError(f"secret is empty: {name}")
        return value


@dataclass(frozen=True)
class StaticOAuthTokenProvider:
    tokens: dict[str, OAuthToken]

    def token(self, scope: str, deadline: float) -> OAuthToken:
        try:
            token = self.tokens[scope]
        except KeyError as exc:
            raise SecretResolutionError(f"OAuth token scope not found: {scope}") from exc
        if token.expires_at <= deadline:
            raise SecretResolutionError(f"OAuth token expires before deadline: {scope}")
        return token


@dataclass(frozen=True)
class StaticRegistryCredentialProvider:
    credentials: dict[str, RegistryCredential]

    def credentials_for(self, registry: str) -> RegistryCredential:
        try:
            credential = self.credentials[registry]
        except KeyError as exc:
            raise SecretResolutionError(f"registry credential not found: {registry}") from exc
        return credential


@dataclass(frozen=True)
class StaticKmsSigner:
    response: KmsSignature

    def sign(self, payload: bytes, *, deadline: float) -> KmsSignature:
        if not payload:
            raise SecretResolutionError("KMS signer payload must be non-empty")
        return self.response
