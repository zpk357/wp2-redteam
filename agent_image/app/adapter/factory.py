"""Construct the single supported TRACE-ReAct execution backend."""

from __future__ import annotations

import os
from collections.abc import Callable

from app.adapter.base import AdapterConfigurationError, AgentAdapter
from app.protocol import ExecutionBackend, ExecutionRequest, ModelProvider

STAGE7_DETERMINISTIC_MODEL_NAME = "trace-g-stage7-deterministic"
STAGE7_DETERMINISTIC_MODEL_DIGEST = (
    "sha256:afbc35238fa47048fb89d9935f1ad434d08782ab5372399e0fa1f9fc5fe7047d"
)
STAGE7_CONTROL_MODEL_NAME = "trace-g-stage7-control"
STAGE7_CONTROL_MODEL_DIGEST = (
    "sha256:dfa9d729fe1d0f69f77b39fe693ca8ee6f531d6415edf609d1ed9fc905682740"
)


class AdapterFactory:
    def __init__(
        self,
        *,
        trace_adapter_factory: Callable[[], AgentAdapter] | None = None,
    ) -> None:
        self.trace_adapter_factory = trace_adapter_factory

    def create(self, request: ExecutionRequest) -> AgentAdapter:
        if request.execution_backend == ExecutionBackend.TRACE_REACT_V2:
            return self._create_trace_adapter(request)
        raise AdapterConfigurationError(
            "unknown_execution_backend",
            f"unsupported execution backend: {request.execution_backend}",
        )

    def _create_trace_adapter(self, request: ExecutionRequest) -> AgentAdapter:
        if self.trace_adapter_factory is not None:
            return self.trace_adapter_factory()
        if (
            request.office_v2_execution is not None
            and os.environ.get("TRACE_G_FORMAL_AGENT") != "1"
        ):
            raise AdapterConfigurationError(
                "v2_requires_formal_agent_runtime",
                "Office V2 execution requires the formal LangGraph Agent runtime",
            )
        if os.environ.get("TRACE_G_FORMAL_AGENT") == "1":
            if os.environ.get("TRACE_G_STAGE7_DETERMINISTIC_PROVIDER") == "1":
                from app.adapter.langgraph_react_runtime import LangGraphReactRuntime

                model = request.model
                if model is not None and model.model_name == STAGE7_CONTROL_MODEL_NAME:
                    self._require_stage7_control_model(request)
                    from app.agent.office_v2_stage7_control_provider import (
                        OfficeV2Stage7ControlProvider,
                    )

                    return LangGraphReactRuntime(
                        provider_factory=OfficeV2Stage7ControlProvider.from_request
                    )
                self._require_stage7_deterministic_model(request)
                from app.agent.office_v2_stage7_provider import OfficeV2Stage7Provider

                return LangGraphReactRuntime(provider_factory=OfficeV2Stage7Provider.from_request)
            if request.office_v2_execution is None:
                raise AdapterConfigurationError(
                    "formal_agent_requires_office_v2",
                    "formal live Agent execution requires an Office V2 envelope",
                )
            self._require_locked_in_container_model(request)
            from app.adapter.langgraph_react_runtime import LangGraphReactRuntime

            return LangGraphReactRuntime()
        from app.adapter.trace_react_adapter import TraceReactAdapter

        if request.model is not None and request.model.provider == ModelProvider.OLLAMA:
            from app.agent.ollama_react_provider import OllamaReactProvider

            return TraceReactAdapter(provider=OllamaReactProvider(request.model))
        return TraceReactAdapter()

    @staticmethod
    def _require_locked_in_container_model(request: ExecutionRequest) -> None:
        model = request.model
        expected_name = os.environ.get("TRACE_G_MODEL_NAME")
        expected_digest = os.environ.get("TRACE_G_MODEL_DIGEST", "").lower()
        expected_endpoint = os.environ.get(
            "TRACE_G_OLLAMA_ENDPOINT", "http://127.0.0.1:11434"
        ).rstrip("/")
        if model is None or model.provider != ModelProvider.OLLAMA:
            raise AdapterConfigurationError(
                "formal_agent_requires_ollama",
                "formal Agent execution requires its locked in-container Ollama model",
            )
        if (
            model.model_name != expected_name
            or model.model_digest != expected_digest
            or model.endpoint is None
            or model.endpoint.rstrip("/") != expected_endpoint
        ):
            raise AdapterConfigurationError(
                "formal_agent_model_identity_mismatch",
                "requested model identity differs from the locked in-container model",
            )

    @staticmethod
    def _require_stage7_deterministic_model(request: ExecutionRequest) -> None:
        model = request.model
        if request.office_v2_execution is None:
            raise AdapterConfigurationError(
                "stage7_provider_requires_office_v2",
                "the Stage 7 deterministic provider only accepts Office V2 envelopes",
            )
        if (
            model is None
            or model.provider != ModelProvider.FAKE
            or model.model_name != STAGE7_DETERMINISTIC_MODEL_NAME
            or model.model_digest != STAGE7_DETERMINISTIC_MODEL_DIGEST
            or model.endpoint is not None
        ):
            raise AdapterConfigurationError(
                "stage7_provider_identity_mismatch",
                "the request does not match the locked Stage 7 deterministic identity",
            )

    @staticmethod
    def _require_stage7_control_model(request: ExecutionRequest) -> None:
        model = request.model
        if request.office_v2_execution is None:
            raise AdapterConfigurationError(
                "stage7_control_requires_office_v2",
                "the Stage 7.10 control provider only accepts Office V2 envelopes",
            )
        if (
            model is None
            or model.provider != ModelProvider.FAKE
            or model.model_name != STAGE7_CONTROL_MODEL_NAME
            or model.model_digest != STAGE7_CONTROL_MODEL_DIGEST
            or model.endpoint is not None
        ):
            raise AdapterConfigurationError(
                "stage7_control_identity_mismatch",
                "the request does not match the locked Stage 7.10 control identity",
            )
