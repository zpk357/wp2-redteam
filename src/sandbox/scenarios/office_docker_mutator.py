"""Formal self-contained Docker provider for office LLM mutation."""

from __future__ import annotations

import asyncio
import base64
import json
import socket
from typing import Any

import docker
from docker.types import DeviceRequest

from sandbox.agent_prompts import (
    OFFICE_MUTATOR_SYSTEM_PROMPT_DIGEST,
    OFFICE_MUTATOR_SYSTEM_PROMPT_VERSION,
)
from sandbox.replay.digests import sha256_bytes, sha256_digest
from sandbox.scenarios.candidate_generation import OFFICE_V1_CANDIDATE_CATALOG
from sandbox.scenarios.models import TestCase
from sandbox.scenarios.office_mutation import (
    OfficeMutationCandidate,
    OfficeMutationPlan,
    OfficeMutationProviderError,
    OfficeMutationProviderFailureKind,
    OfficeMutationProviderIdentity,
    OfficeMutationProviderKind,
    OfficeMutationProviderResult,
)
from sandbox.scenarios.office_mutation_batch import (
    OfficeMutationSubBatchRequest,
    office_mutation_sub_batch_request_digest,
)

MUTATOR_PROVIDER_VERSION = "office-docker-ollama-mutator-v1"
MUTATOR_RESPONSE_SCHEMA_VERSION = "office-mutation-expressions-v1"
_MAX_RESPONSE_BYTES = 1024 * 1024


class DockerOfficeMutationProvider:
    """Run each semantic mutation request inside a fresh locked Qwen container."""

    def __init__(
        self,
        *,
        image_ref: str,
        image_id: str,
        model_name: str,
        model_digest: str,
        gpu_device: str = "0",
        timeout_seconds: int = 600,
        client: Any | None = None,
    ) -> None:
        self.image_ref = image_ref
        self.image_id = image_id.lower()
        self.gpu_device = gpu_device
        self.timeout_seconds = timeout_seconds
        self.client = client or docker.from_env()
        self.identity = OfficeMutationProviderIdentity(
            kind=OfficeMutationProviderKind.OLLAMA,
            provider_version=MUTATOR_PROVIDER_VERSION,
            model_name=model_name,
            model_digest=model_digest,
            endpoint="http://127.0.0.1:11434",
            prompt_version=OFFICE_MUTATOR_SYSTEM_PROMPT_VERSION,
            response_schema_version=MUTATOR_RESPONSE_SCHEMA_VERSION,
        )
        self._validate_image()

    def _validate_image(self) -> None:
        image = self.client.images.get(self.image_ref)
        if image.id.lower() != self.image_id:
            raise OfficeMutationProviderError(
                "Docker Mutator image identity mismatch",
                kind=OfficeMutationProviderFailureKind.MODEL_MISMATCH,
            )
        labels = image.attrs.get("Config", {}).get("Labels") or {}
        expected = {
            "org.trace-g.runtime": "self-contained-agent-qwen",
            "org.trace-g.model.name": self.identity.model_name,
            "org.trace-g.model.digest": self.identity.model_digest,
            "org.trace-g.mutator-prompt.version": OFFICE_MUTATOR_SYSTEM_PROMPT_VERSION,
            "org.trace-g.mutator-prompt.digest": OFFICE_MUTATOR_SYSTEM_PROMPT_DIGEST,
        }
        if any(labels.get(key) != value for key, value in expected.items()):
            raise OfficeMutationProviderError(
                "Docker Mutator image labels do not match the frozen provider",
                kind=OfficeMutationProviderFailureKind.MODEL_MISMATCH,
            )

    async def mutate_sub_batch(
        self,
        plan: OfficeMutationPlan,
        parent: TestCase,
        request: OfficeMutationSubBatchRequest,
    ) -> OfficeMutationProviderResult:
        return await asyncio.to_thread(self._mutate_sync, plan, parent, request)

    def _mutate_sync(
        self,
        plan: OfficeMutationPlan,
        parent: TestCase,
        request: OfficeMutationSubBatchRequest,
    ) -> OfficeMutationProviderResult:
        request_digest = office_mutation_sub_batch_request_digest(plan, parent, request)
        if plan.provider_identity != self.identity:
            raise OfficeMutationProviderError(
                "mutation plan provider identity does not match Docker provider",
                kind=OfficeMutationProviderFailureKind.MODEL_MISMATCH,
                request_digest=request_digest,
            )
        if parent.attack is None:
            raise OfficeMutationProviderError(
                "office mutation parent has no attack binding",
                kind=OfficeMutationProviderFailureKind.INVALID_SCHEMA,
                request_digest=request_digest,
            )
        tasks = {
            item.task_id: item for item in OFFICE_V1_CANDIDATE_CATALOG.benign_tasks
        }
        objectives = {
            item.objective_id: item
            for item in OFFICE_V1_CANDIDATE_CATALOG.attack_objectives
        }
        carriers = {
            item.carrier_id: item
            for item in OFFICE_V1_CANDIDATE_CATALOG.injection_carriers
        }
        try:
            planned_task = tasks[plan.planned_components.task_id]
            planned_objective = objectives[plan.planned_components.objective_id]
            planned_carrier = carriers[plan.planned_components.carrier_id]
        except KeyError as exc:
            raise OfficeMutationProviderError(
                "mutation plan references an unknown frozen office component",
                kind=OfficeMutationProviderFailureKind.INVALID_SCHEMA,
                request_digest=request_digest,
            ) from exc
        payload = {
            "schema_version": "1.0",
            "request_digest": request_digest,
            "mutation_mode": plan.mode.value,
            "changed_dimensions": [item.value for item in plan.changed_dimensions],
            "preserved_dimensions": [item.value for item in plan.preserved_dimensions],
            "parent_expression": parent.attack.payload,
            "planned_components": plan.planned_components.model_dump(mode="json"),
            "planned_context": {
                "benign_task": planned_task.model_dump(mode="json"),
                "attack_objective": planned_objective.model_dump(mode="json"),
                "injection_carrier": planned_carrier.model_dump(mode="json"),
            },
            "feedback_digest": plan.feedback_digest,
            "expected_risk_gap_ids": list(plan.expected_risk_gap_ids),
            "expected_path": plan.expected_path,
            "operator_id": plan.operator_id,
            "requested_count": request.requested_count,
            "random_seed": request.random_seed,
            "max_output_tokens": request.max_output_tokens,
        }
        encoded = base64.b64encode(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).decode("ascii")
        execution_id = (
            "office-mutator-"
            + sha256_digest(
                {
                    "request_digest": request_digest,
                    "image_id": self.image_id,
                }
            ).removeprefix("sha256:")[:24]
        )
        container = None
        primary_error: BaseException | None = None
        try:
            container = self.client.containers.run(
                self.image_ref,
                detach=True,
                entrypoint=["python", "-m", "app.office_mutator_worker"],
                network_mode="none",
                environment={
                    "TRACE_G_MUTATION_REQUEST_B64": encoded,
                    "TRACE_G_RUNTIME_MODE": "live",
                    "TRACE_G_STARTUP_TIMEOUT_SECONDS": str(self.timeout_seconds),
                },
                labels={
                    "trace-g.component": "office-llm-mutator",
                    "trace-g.execution-id": execution_id,
                    "trace-g.request-digest": request_digest,
                },
                mem_limit="8g",
                nano_cpus=4_000_000_000,
                pids_limit=512,
                tmpfs={"/tmp": "rw,noexec,nosuid,size=1g"},
                device_requests=[
                    DeviceRequest(device_ids=[self.gpu_device], capabilities=[["gpu"]])
                ],
            )
            result = container.wait(timeout=self.timeout_seconds + 30)
            raw = container.logs(stdout=True, stderr=False)
            error_raw = container.logs(stdout=False, stderr=True)
            if len(raw) > _MAX_RESPONSE_BYTES:
                raise OfficeMutationProviderError(
                    "Docker Mutator response exceeded byte limit",
                    kind=OfficeMutationProviderFailureKind.RESPONSE_TOO_LARGE,
                    recoverable=True,
                    request_digest=request_digest,
                    response_bytes=len(raw),
                )
            if int(result.get("StatusCode", 1)) != 0:
                raise OfficeMutationProviderError(
                    "Docker Mutator worker failed",
                    kind=OfficeMutationProviderFailureKind.PROVIDER,
                    recoverable=True,
                    request_digest=request_digest,
                    response_digest=sha256_bytes(raw) if raw else None,
                    response_bytes=len(raw),
                    response_summary=error_raw.decode("utf-8", "replace")[-2000:],
                )
            try:
                response = json.loads(raw)
            except (UnicodeError, ValueError) as exc:
                raise OfficeMutationProviderError(
                    "Docker Mutator returned invalid JSON",
                    kind=OfficeMutationProviderFailureKind.INVALID_JSON,
                    request_digest=request_digest,
                    response_digest=sha256_bytes(raw),
                    response_bytes=len(raw),
                ) from exc
            self._validate_response_identity(response, request_digest)
            expressions = response.get("expressions")
            if not isinstance(expressions, list) or len(expressions) != request.requested_count:
                raise OfficeMutationProviderError(
                    "Docker Mutator expression count mismatch",
                    kind=OfficeMutationProviderFailureKind.INVALID_SCHEMA,
                    request_digest=request_digest,
                    response_digest=sha256_bytes(raw),
                    response_bytes=len(raw),
                )
            if any(
                not isinstance(expression, str) or not expression.strip()
                for expression in expressions
            ):
                raise OfficeMutationProviderError(
                    "Docker Mutator expressions must be non-empty strings",
                    kind=OfficeMutationProviderFailureKind.INVALID_SCHEMA,
                    request_digest=request_digest,
                    response_digest=sha256_bytes(raw),
                    response_bytes=len(raw),
                )
            candidates = tuple(
                OfficeMutationCandidate.create(
                    plan_id=plan.plan_id,
                    ordinal=ordinal,
                    scenario_template_id=plan.planned_components.scenario_template_id,
                    task_id=plan.planned_components.task_id,
                    objective_id=plan.planned_components.objective_id,
                    carrier_id=plan.planned_components.carrier_id,
                    expression=expression.strip(),
                    claimed_operator_id=plan.operator_id,
                    claimed_expected_path=plan.expected_path,
                )
                for ordinal, expression in enumerate(expressions)
            )
            return OfficeMutationProviderResult(
                candidates=candidates,
                request_digest=request_digest,
                response_digest=sha256_bytes(raw),
                response_bytes=len(raw),
                prompt_eval_count=response.get("prompt_eval_count"),
                eval_count=response.get("eval_count"),
                done_reason=response.get("done_reason"),
            )
        except OfficeMutationProviderError as exc:
            primary_error = exc
            raise
        except (TimeoutError, socket.timeout) as exc:
            error = OfficeMutationProviderError(
                "Docker Mutator timed out",
                kind=OfficeMutationProviderFailureKind.TIMEOUT,
                recoverable=True,
                request_digest=request_digest,
            )
            primary_error = error
            raise error from exc
        except Exception as exc:
            error = OfficeMutationProviderError(
                f"Docker Mutator infrastructure failure: {type(exc).__name__}",
                kind=OfficeMutationProviderFailureKind.TRANSPORT,
                recoverable=True,
                request_digest=request_digest,
            )
            primary_error = error
            raise error from exc
        finally:
            if container is not None:
                try:
                    container.remove(force=True)
                except Exception as exc:
                    suffix = (
                        " after a primary provider failure"
                        if primary_error is not None
                        else ""
                    )
                    raise OfficeMutationProviderError(
                        f"Docker Mutator cleanup failed{suffix}",
                        kind=OfficeMutationProviderFailureKind.TRANSPORT,
                        request_digest=request_digest,
                    ) from exc

    def _validate_response_identity(
        self, response: object, request_digest: str
    ) -> None:
        if not isinstance(response, dict):
            raise OfficeMutationProviderError(
                "Docker Mutator response must be an object",
                kind=OfficeMutationProviderFailureKind.INVALID_SCHEMA,
                request_digest=request_digest,
            )
        expected = {
            "request_digest": request_digest,
            "prompt_version": OFFICE_MUTATOR_SYSTEM_PROMPT_VERSION,
            "prompt_digest": OFFICE_MUTATOR_SYSTEM_PROMPT_DIGEST,
            "model_name": self.identity.model_name,
            "model_digest": self.identity.model_digest,
        }
        if any(response.get(key) != value for key, value in expected.items()):
            raise OfficeMutationProviderError(
                "Docker Mutator response identity mismatch",
                kind=OfficeMutationProviderFailureKind.MODEL_MISMATCH,
                request_digest=request_digest,
            )
