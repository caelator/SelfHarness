"""Signing helpers for production corpus provenance workflows."""

from self_harness.signing.external_signer import (
    DEFAULT_SIGNER_MAX_OUTPUT_BYTES,
    DEFAULT_SIGNER_TIMEOUT_SECONDS,
    EXTERNAL_SIGNER_PROTOCOL_VERSION,
    ExternalSignerResponse,
    parse_external_signer_command,
    sign_corpus_with_external_signer,
    sign_payload_with_external_signer,
)
from self_harness.signing.external_signer_errors import (
    EXTERNAL_SIGNER_ERROR_SCHEMA_VERSION,
    ExternalSignerError,
    ExternalSignerFailure,
)

__all__ = [
    "DEFAULT_SIGNER_MAX_OUTPUT_BYTES",
    "DEFAULT_SIGNER_TIMEOUT_SECONDS",
    "EXTERNAL_SIGNER_ERROR_SCHEMA_VERSION",
    "EXTERNAL_SIGNER_PROTOCOL_VERSION",
    "ExternalSignerError",
    "ExternalSignerFailure",
    "ExternalSignerResponse",
    "parse_external_signer_command",
    "sign_corpus_with_external_signer",
    "sign_payload_with_external_signer",
]
