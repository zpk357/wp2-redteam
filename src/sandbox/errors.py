"""Domain errors used across scheduler, protocol, and engine layers."""


class SandboxError(RuntimeError):
    """Base exception for the week-one sandbox."""


class InfrastructureError(SandboxError):
    """Docker, protocol, storage, or cleanup infrastructure failed."""


class SandboxConfigurationError(InfrastructureError):
    """The requested sandbox cannot satisfy a deterministic configuration invariant."""


class PermanentInfrastructureError(InfrastructureError):
    """The infrastructure rejected an operation with a non-retryable response."""


class RuntimeTimeoutError(InfrastructureError):
    """A Runtime request or execution deadline expired."""


class RuntimeTransportError(InfrastructureError):
    """The isolated Runtime could not be reached over its configured transport."""


class ProtocolError(InfrastructureError):
    """The Runtime returned an invalid JSON-RPC response."""

    def __init__(
        self,
        message: str,
        *,
        code: int | None = None,
        data: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.data = data


class TraceIntegrityError(InfrastructureError):
    """Trace events were missing, duplicated, or inconsistent."""


class CleanupError(InfrastructureError):
    """A sandbox container could not be confirmed removed."""
