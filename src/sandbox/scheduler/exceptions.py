"""Compatibility exports for scheduler-specific errors."""

from sandbox.errors import (
    CleanupError,
    InfrastructureError,
    PermanentInfrastructureError,
    SandboxConfigurationError,
)

__all__ = [
    "CleanupError",
    "InfrastructureError",
    "PermanentInfrastructureError",
    "SandboxConfigurationError",
]
