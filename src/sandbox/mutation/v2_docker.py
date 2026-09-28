"""Fresh-container Docker provider for the real Office V2 mutation role."""

from __future__ import annotations

import asyncio
import base64
import json
import sys
from typing import Any

import docker
from docker.types import DeviceRequest

from sandbox.protocol import ModelInferenceOptions
from sandbox.replay.digests import sha256_bytes, sha256_digest

from .v2_brief import MinimalFactBrief, MutationCandidateResponse
from .v2_contracts import MutationPlan
from .v2_provider import (
    MutationProviderAttempt,
    MutationProviderResult,
    ProviderAttemptState,
    ProviderFailureClass,
    V2ProviderFailure,
    seal_failed_provider_attempt,
)

_MAX_RESPONSE_BYTES = 1024 * 1024


class DockerOllamaV2MutationProvider:
    provider_id = "provider-docker-ollama-v2"

    def __init__(
        self,
        *,
        image_ref: str,
        model_name: str,
        inference: ModelInferenceOptions,
        gpu_device: str = "0",
        campaign_id: str | None = None,
        client: Any | None = None,
        network_mode: str = "none",
        ollama_endpoint: str = "http://127.0.0.1:11434",
        external_ollama: bool = False,
    ) -> None:
        self.image_ref = image_ref
        self.model_name = model_name
        self.inference = inference
        self.gpu_device = gpu_device
        self.campaign_id = campaign_id
        self.client = client or docker.from_env()
        self.network_mode = network_mode
        self.ollama_endpoint = ollama_endpoint
        self.external_ollama = external_ollama

    async def generate(
        self, *, plan: MutationPlan, brief: MinimalFactBrief, attempt_index: int
    ) -> MutationProviderResult:
        return await asyncio.to_thread(
            self._generate_sync,
            plan=plan,
            brief=brief,
            attempt_index=attempt_index,
        )

    def cleanup_interrupted(self, campaign_id: str) -> None:
        for container in self.client.containers.list(
            all=True,
            filters={
                "label": [
                    "trace-g.component=office-v2-llm-mutator",
                    f"trace-g.campaign-id={campaign_id}",
                ]
            },
        ):
            container.remove(force=True)

    def _generate_sync(
        self, *, plan: MutationPlan, brief: MinimalFactBrief, attempt_index: int
    ) -> MutationProviderResult:
        request_payload = {
            "plan": plan.model_dump(mode="json", exclude_none=False),
            "brief": brief.model_dump(mode="json", exclude_none=False),
            "attempt_index": attempt_index,
            "inference": self.inference.model_dump(mode="json", exclude_none=False),
        }
        request_digest = sha256_digest(request_payload)
        if plan.provider_id != self.provider_id:
            raise self._failure(
                plan, attempt_index, request_digest, ProviderFailureClass.CONFIGURATION_PERMANENT
            )
        encoded = base64.b64encode(
            json.dumps(request_payload, sort_keys=True).encode("utf-8")
        ).decode("ascii")
        execution_id = (
            "office-v2-mutator-"
            + sha256_digest({"request": request_digest, "image": self.image_ref}).removeprefix(
                "sha256:"
            )[:24]
        )
        container = None
        try:
            container = self.client.containers.run(
                self.image_ref,
                detach=True,
                network_mode=self.network_mode,
                read_only=True,
                environment={
                    "TRACE_G_V2_MUTATION_REQUEST_B64": encoded,
                    "TRACE_G_RUNTIME_MODE": "live",
                    "TRACE_G_EXPLORATORY_RUN": "1",
                    "TRACE_G_MODEL_NAME": self.model_name,
                    "TRACE_G_EXTERNAL_OLLAMA": "1" if self.external_ollama else "0",
                    "TRACE_G_OLLAMA_ENDPOINT": self.ollama_endpoint,
                    "OLLAMA_NUM_PARALLEL": "1",
                    "OLLAMA_MAX_LOADED_MODELS": "1",
                    "OLLAMA_KEEP_ALIVE": "0",
                    "OLLAMA_FLASH_ATTENTION": "1",
                    "OLLAMA_KV_CACHE_TYPE": "q8_0",
                },
                labels={
                    "trace-g.component": "office-v2-llm-mutator",
                    "trace-g.execution-id": execution_id,
                    "trace-g.request-digest": request_digest,
                    **(
                        {
                            "trace-g.campaign-id": self.campaign_id,
                            "trace-g.work-item-id": (
                                "mutation." + plan.plan_digest.removeprefix("sha256:")[:24]
                            ),
                            "trace-g.attempt": str(attempt_index),
                        }
                        if self.campaign_id is not None
                        else {}
                    ),
                },
                mem_limit="22g",
                nano_cpus=8_000_000_000,
                pids_limit=512,
                tmpfs={
                    "/tmp": "rw,noexec,nosuid,size=2g,uid=10001,gid=10001",
                },
                device_requests=[
                    DeviceRequest(device_ids=[self.gpu_device], capabilities=[["gpu"]])
                ],
            )
            result = container.wait(timeout=max(30, plan.budget.timeout_ms // 1000 + 30))
            raw = container.logs(stdout=True, stderr=False)
            errors = container.logs(stdout=False, stderr=True)
            if len(raw) > _MAX_RESPONSE_BYTES:
                raise self._failure(
                    plan,
                    attempt_index,
                    request_digest,
                    ProviderFailureClass.PROTOCOL_INTEGRITY_PERMANENT,
                    response=raw,
                )
            if int(result.get("StatusCode", 1)) != 0:
                failure_class, http_status = self._worker_failure(errors)
                raise self._failure(
                    plan,
                    attempt_index,
                    request_digest,
                    failure_class,
                    response=errors[-512:],
                    http_status=http_status,
                )
            try:
                envelope = json.loads(raw)
                if (
                    envelope["schema_version"] != "office-v2-mutator-worker-v1"
                    or envelope["model_name"] != self.model_name
                    or envelope["plan_digest"] != plan.plan_digest
                    or envelope["brief_digest"] != brief.brief_digest
                    or int(envelope["attempt_index"]) != attempt_index
                ):
                    raise ValueError("worker response identity mismatch")
                candidate = MutationCandidateResponse.model_validate(envelope["candidate"])
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise self._failure(
                    plan,
                    attempt_index,
                    request_digest,
                    ProviderFailureClass.PROTOCOL_INTEGRITY_PERMANENT,
                    response=raw,
                ) from exc
            response_digest = sha256_bytes(raw)
            payload = {
                "provider_attempt_id": (
                    "provider-attempt."
                    + sha256_digest(
                        {"request": request_digest, "response": response_digest}
                    ).removeprefix("sha256:")[:24]
                ),
                "mutation_plan_digest": plan.plan_digest,
                "attempt_index": attempt_index,
                "state": ProviderAttemptState.SUCCEEDED,
                "request_digest": request_digest,
                "response_digest": response_digest,
                "response_bytes": len(raw),
                "response_summary": raw[:256].decode("utf-8", errors="replace"),
                "input_tokens": int(envelope.get("prompt_eval_count", 0)),
                "output_tokens": int(envelope.get("eval_count", 0)),
            }
            draft = MutationProviderAttempt.model_construct(
                **payload, attempt_digest="sha256:" + "0" * 64
            )
            attempt = MutationProviderAttempt(
                **payload, attempt_digest=sha256_digest(draft.digest_payload())
            )
            return MutationProviderResult(candidate=candidate, attempt=attempt)
        except V2ProviderFailure:
            raise
        except TimeoutError as exc:
            raise self._failure(
                plan, attempt_index, request_digest, ProviderFailureClass.TIMEOUT_TRANSIENT
            ) from exc
        except docker.errors.DockerException as exc:
            raise self._failure(
                plan, attempt_index, request_digest, ProviderFailureClass.TRANSPORT_TRANSIENT
            ) from exc
        except Exception as exc:
            raise self._failure(
                plan, attempt_index, request_digest, ProviderFailureClass.AMBIGUOUS
            ) from exc
        finally:
            if container is not None:
                primary_error = sys.exc_info()[1]
                try:
                    container.remove(force=True)
                except Exception as exc:
                    if primary_error is not None:
                        primary_error.add_note(f"Mutator container cleanup also failed: {exc}")
                    else:
                        raise self._failure(
                            plan,
                            attempt_index,
                            request_digest,
                            ProviderFailureClass.AMBIGUOUS,
                        ) from exc

    @staticmethod
    def _failure(
        plan: MutationPlan,
        attempt_index: int,
        request_digest: str,
        failure_class: ProviderFailureClass,
        *,
        response: bytes = b"",
        http_status: int | None = None,
    ) -> V2ProviderFailure:
        return V2ProviderFailure(
            failure_class.value,
            attempt=seal_failed_provider_attempt(
                plan=plan,
                attempt_index=attempt_index,
                request_digest=request_digest,
                failure_class=failure_class,
                response_digest=sha256_bytes(response) if response else None,
                response_bytes=len(response),
                response_summary=response[:512].decode("utf-8", errors="replace"),
                http_status=http_status,
            ),
        )

    @staticmethod
    def _worker_failure(
        errors: bytes,
    ) -> tuple[ProviderFailureClass, int | None]:
        try:
            payload = json.loads(errors)
            if payload.get("schema_version") != "office-v2-mutator-worker-error-v1":
                raise ValueError("worker error schema mismatch")
            failure_class = ProviderFailureClass(payload["failure_class"])
            http_status = payload.get("http_status")
            if http_status is not None and (
                not isinstance(http_status, int) or isinstance(http_status, bool)
            ):
                raise ValueError("worker HTTP status is invalid")
            return failure_class, http_status
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return ProviderFailureClass.AMBIGUOUS, None


__all__ = ["DockerOllamaV2MutationProvider"]
