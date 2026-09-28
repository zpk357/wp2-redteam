"""Framework-independent Agent adapter interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from app.protocol import ExecutionRequest, TraceEvent


class AgentAdapter(ABC):
    last_checkpoint_digests: tuple = ()
    last_final_state_digest: str | None = None

    @abstractmethod
    async def execute(self, request: ExecutionRequest) -> AsyncIterator[TraceEvent]:
        raise NotImplementedError


class AdapterExecutionError(RuntimeError):
    """A closed, machine-readable adapter failure."""

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code


class AdapterConfigurationError(AdapterExecutionError):
    pass


class AgentNoSubmitError(AdapterExecutionError):
    def __init__(
        self,
        *,
        limit_type: str | None = None,
        last_agent_error: str | None = None,
    ) -> None:
        details = []
        if limit_type:
            details.append(f"limit={limit_type}")
        if last_agent_error:
            details.append(f"last_agent_error={last_agent_error}")
        suffix = f" ({', '.join(details)})" if details else ""
        super().__init__("agent_no_submit", f"agent stopped without a valid submit{suffix}")
