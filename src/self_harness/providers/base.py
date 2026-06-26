from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class ProviderError(RuntimeError):
    """Raised when a release/operator provider cannot satisfy its contract."""


class SecretResolutionError(ProviderError):
    """Raised when a secret resolver cannot return a requested secret."""


@dataclass(frozen=True)
class OAuthToken:
    access_token: str
    expires_at: float
    scope: str
    token_type: str = "Bearer"

    def __repr__(self) -> str:
        return (
            f"OAuthToken(access_token=<redacted>, expires_at={self.expires_at!r}, "
            f"scope={self.scope!r}, token_type={self.token_type!r})"
        )


@dataclass(frozen=True)
class RegistryCredential:
    registry: str
    username: str | None = None
    password: str | None = None
    registry_config_path: str | None = None

    def __repr__(self) -> str:
        return (
            f"RegistryCredential(registry={self.registry!r}, username={self.username!r}, "
            "password=<redacted>, "
            f"registry_config_path={self.registry_config_path!r})"
        )


@dataclass(frozen=True)
class KmsSignature:
    signature_b64: str
    public_key_b64: str
    fingerprint: str
    key_id: str
    provider: str


class SecretResolver(Protocol):
    def resolve(self, name: str) -> str:
        """Return a secret value for an operator-owned secret name."""


class OAuthTokenProvider(Protocol):
    def token(self, scope: str, deadline: float) -> OAuthToken:
        """Return an OAuth access token for a provider-owned scope."""


class RegistryCredentialProvider(Protocol):
    def credentials_for(self, registry: str) -> RegistryCredential:
        """Return credentials or a registry-config path for a registry host."""


class KmsSigner(Protocol):
    def sign(self, payload: bytes, *, deadline: float) -> KmsSignature:
        """Sign canonical payload bytes using an external KMS/HSM/provider boundary."""
