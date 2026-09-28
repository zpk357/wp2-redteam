"""Construct the single supported TRACE-ReAct execution backend."""

from __future__ import annotations

import os
from collections.abc import Callable

from app.adapter.base import AdapterConfigurationError, AgentAdapter
from app.protocol import AgentRuntimeKind, ExecutionBackend, ExecutionRequest, ModelProvider


class AdapterFactory:
    def __init__(
        self,
        *,
        trace_adapter_factory: Callable[[], AgentAdapter] | None = None,
    ) -> None:
        self.trace_adapter_factory = trace_adapter_factory

    def create(self, request: ExecutionRequest) -> AgentAdapter:
        if request.execution_backend == ExecutionBackend.TRACE_REACT_V2:
            if request.structured_case_execution is not None:
                from app.adapter.structured_v1_adapter import StructuredV1Adapter

                return StructuredV1Adapter()
            return self._create_trace_adapter(request)
        raise AdapterConfigurationError(
            "unknown_execution_backend",
            f"unsupported execution backend: {request.execution_backend}",
        )

    def _create_trace_adapter(self, request: ExecutionRequest) -> AgentAdapter:
        runtime_kind = self._agent_runtime_kind()
        if runtime_kind is AgentRuntimeKind.DEEPSEEK_HARNESS:
            from app.adapter.deepseek_harness_adapter import DeepSeekHarnessAdapter

            return DeepSeekHarnessAdapter()
        if self.trace_adapter_factory is not None:
            return self.trace_adapter_factory()
        if request.office_v2_execution is not None:
            from app.adapter.langgraph_react_runtime import LangGraphReactRuntime

            return LangGraphReactRuntime()
        from app.adapter.trace_react_adapter import TraceReactAdapter

        if request.model is not None and request.model.provider == ModelProvider.OLLAMA:
            from app.agent.ollama_react_provider import OllamaReactProvider

            return TraceReactAdapter(provider=OllamaReactProvider(request.model))
        return TraceReactAdapter()

    @staticmethod
    def _agent_runtime_kind() -> AgentRuntimeKind:
        raw_value = os.environ.get(
            "TRACE_G_AGENT_RUNTIME",
            AgentRuntimeKind.LANGGRAPH.value,
        )
        try:
            return AgentRuntimeKind(raw_value)
        except ValueError as exc:
            raise AdapterConfigurationError(
                "unknown_agent_runtime",
                "the configured Agent runtime kind is unsupported",
            ) from exc
