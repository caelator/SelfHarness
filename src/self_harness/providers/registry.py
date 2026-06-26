from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TypeVar

from self_harness.providers.base import (
    KmsSigner,
    OAuthTokenProvider,
    ProviderError,
    RegistryCredentialProvider,
    SecretResolver,
)

SecretFactory = Callable[[], SecretResolver]
OAuthFactory = Callable[[], OAuthTokenProvider]
RegistryCredentialFactory = Callable[[], RegistryCredentialProvider]
KmsSignerFactory = Callable[[], KmsSigner]
Factory = TypeVar("Factory", SecretFactory, OAuthFactory, RegistryCredentialFactory, KmsSignerFactory)


@dataclass
class ProviderRegistry:
    secret_resolvers: dict[str, SecretFactory] = field(default_factory=dict)
    oauth_token_providers: dict[str, OAuthFactory] = field(default_factory=dict)
    registry_credential_providers: dict[str, RegistryCredentialFactory] = field(default_factory=dict)
    kms_signers: dict[str, KmsSignerFactory] = field(default_factory=dict)

    def register_secret_resolver(self, name: str, factory: SecretFactory) -> None:
        _register(self.secret_resolvers, name, factory)

    def secret_resolver(self, name: str) -> SecretResolver:
        return _resolve(self.secret_resolvers, name)()

    def register_oauth_token_provider(self, name: str, factory: OAuthFactory) -> None:
        _register(self.oauth_token_providers, name, factory)

    def oauth_token_provider(self, name: str) -> OAuthTokenProvider:
        return _resolve(self.oauth_token_providers, name)()

    def register_registry_credential_provider(self, name: str, factory: RegistryCredentialFactory) -> None:
        _register(self.registry_credential_providers, name, factory)

    def registry_credential_provider(self, name: str) -> RegistryCredentialProvider:
        return _resolve(self.registry_credential_providers, name)()

    def register_kms_signer(self, name: str, factory: KmsSignerFactory) -> None:
        _register(self.kms_signers, name, factory)

    def kms_signer(self, name: str) -> KmsSigner:
        return _resolve(self.kms_signers, name)()


def _register(registry: dict[str, Factory], name: str, factory: Factory) -> None:
    if not name:
        raise ProviderError("provider name must be non-empty")
    if name in registry:
        raise ProviderError(f"provider is already registered: {name}")
    registry[name] = factory


def _resolve(registry: dict[str, Factory], name: str) -> Factory:
    try:
        return registry[name]
    except KeyError as exc:
        raise ProviderError(f"provider is not registered: {name}") from exc


provider_registry = ProviderRegistry()
