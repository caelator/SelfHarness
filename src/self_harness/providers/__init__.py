"""Provider extension seams for release/operator integrations."""

from self_harness.providers.base import (
    KmsSignature,
    KmsSigner,
    OAuthToken,
    OAuthTokenProvider,
    ProviderError,
    RegistryCredential,
    RegistryCredentialProvider,
    SecretResolutionError,
    SecretResolver,
)
from self_harness.providers.registry import ProviderRegistry, provider_registry
from self_harness.providers.static import (
    StaticKmsSigner,
    StaticOAuthTokenProvider,
    StaticRegistryCredentialProvider,
    StaticSecretResolver,
)

__all__ = [
    "KmsSignature",
    "KmsSigner",
    "OAuthToken",
    "OAuthTokenProvider",
    "ProviderError",
    "ProviderRegistry",
    "RegistryCredential",
    "RegistryCredentialProvider",
    "SecretResolutionError",
    "SecretResolver",
    "StaticKmsSigner",
    "StaticOAuthTokenProvider",
    "StaticRegistryCredentialProvider",
    "StaticSecretResolver",
    "provider_registry",
]
