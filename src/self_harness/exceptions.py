"""Project-specific exceptions for Self-Harness."""


class SelfHarnessError(Exception):
    """Base exception for package-level errors."""


class InvalidConfigError(SelfHarnessError, ValueError):
    """Raised when runtime configuration is invalid."""


class InvalidPatchError(SelfHarnessError, ValueError):
    """Raised when a harness patch violates the editable surface contract."""


class InvalidProposalError(SelfHarnessError, ValueError):
    """Raised when a proposal cannot produce a valid candidate harness."""


class EvaluationError(SelfHarnessError, RuntimeError):
    """Raised when candidate evaluation fails before producing valid results."""


class InProcessVerifierError(SelfHarnessError, RuntimeError):
    """Raised when a trusted in-process verifier violates its contract."""


class HttpVerifierError(SelfHarnessError, RuntimeError):
    """Raised when a trusted HTTP verifier violates its contract."""


class ContainerVerifierError(SelfHarnessError, RuntimeError):
    """Raised when a trusted container verifier violates its contract."""


class LLMClientError(SelfHarnessError, RuntimeError):
    """Raised when an LLM provider client cannot complete a request."""


class LLMRequestError(LLMClientError):
    """Raised when an LLM provider returns a non-retryable request error."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        self.status_code = status_code
        super().__init__(message)


class AuditCorruptError(SelfHarnessError, RuntimeError):
    """Raised when audit artifacts are missing, unsupported, or corrupt."""


class AuditVerificationError(SelfHarnessError, RuntimeError):
    """Raised when audit integrity verification cannot produce a report."""


class PaperFidelityError(SelfHarnessError, RuntimeError):
    """Raised when a paper-fidelity invariant is violated."""


class TaskLoadError(SelfHarnessError, RuntimeError):
    """Raised when external task definitions cannot be loaded."""

    def __init__(self, message: str, *, reason: str = "invalid-corpus") -> None:
        self.reason = reason
        super().__init__(message)


class CorpusSigningError(SelfHarnessError, RuntimeError):
    """Raised when corpus key generation, signing, or fingerprinting fails."""


class KeyringError(SelfHarnessError, RuntimeError):
    """Raised when a corpus trust keyring is invalid or cannot be updated."""
