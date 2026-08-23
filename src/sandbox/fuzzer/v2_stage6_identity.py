"""Complete model and role identity required by the Office V2 real-model runtime."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from sandbox.agent_prompts import OFFICE_AGENT_BASE_RULES_V2_DIGEST
from sandbox.mutation.v2_brief import V2_MUTATION_PROMPT_IDENTITY_DIGEST
from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.models import (
    Identifier,
    OfficeV2Contract,
    Sha256Digest,
)

from .v2_identity import build_v2_campaign_identity_lock

STAGE6_MODEL_NAME = "qwen3.5:27b-q4_K_M"
STAGE6_PLAN_DIGEST = "sha256:9b47f4ce833ba7bc767500050861174aedf3c0962dda847d477c38feb99174a0"


class Stage6Role(StrEnum):
    AGENT = "agent"
    MUTATOR = "mutator"


class Stage6InferenceConfig(OfficeV2Contract):
    num_ctx: Literal[8192] = 8192
    num_predict: int = Field(gt=0, le=4096)
    temperature: Literal["0.2", "0.7"]
    top_p: Literal["0.8"] = "0.8"
    top_k: Literal[20] = 20
    thinking: bool
    config_digest: Sha256Digest

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"config_digest"}, exclude_none=False)

    @model_validator(mode="after")
    def digest_matches(self) -> Self:
        if self.config_digest != sha256_digest(self.digest_payload()):
            raise ValueError("Stage 6 inference config digest does not match")
        return self


class Stage6RoleIdentity(OfficeV2Contract):
    role: Stage6Role
    image_reference: str = Field(
        pattern=r"^[a-z0-9][a-z0-9./_-]{0,200}:[a-z0-9][a-z0-9._-]{0,127}$"
    )
    image_id: Sha256Digest
    image_archive_sha256: Sha256Digest | None = None
    image_build_receipt_digest: Sha256Digest | None = None
    prompt_identity_digest: Sha256Digest
    provider_identity: Identifier
    inference: Stage6InferenceConfig
    role_digest: Sha256Digest

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"role_digest"}, exclude_none=True)

    @model_validator(mode="after")
    def role_contract_matches(self) -> Self:
        if (self.image_archive_sha256 is None) == (
            self.image_build_receipt_digest is None
        ):
            raise ValueError(
                "Stage 6 role requires exactly one image delivery identity"
            )
        expected_prompt = (
            OFFICE_AGENT_BASE_RULES_V2_DIGEST
            if self.role is Stage6Role.AGENT
            else V2_MUTATION_PROMPT_IDENTITY_DIGEST
        )
        if self.prompt_identity_digest != expected_prompt:
            raise ValueError("Stage 6 role prompt identity drifted")
        if self.role is Stage6Role.AGENT:
            if (
                self.inference.thinking is not True
                or self.inference.num_predict != 4096
                or self.inference.temperature != "0.2"
            ):
                raise ValueError("Stage 6 Agent inference configuration drifted")
        elif (
            self.inference.thinking is not False
            or self.inference.num_predict != 2048
            or self.inference.temperature != "0.7"
        ):
            raise ValueError("Stage 6 Mutator inference configuration drifted")
        if self.role_digest != sha256_digest(self.digest_payload()):
            raise ValueError("Stage 6 role digest does not match")
        return self


class Stage6ModelLock(OfficeV2Contract):
    schema_version: Literal["office-v2-stage6-model-lock-v1"]
    plan_digest: Literal[STAGE6_PLAN_DIGEST]
    model_name: Literal[STAGE6_MODEL_NAME]
    quantization: Literal["Q4_K_M"]
    registry: Literal["registry.ollama.ai"]
    manifest_digest: Sha256Digest
    config_digest: Sha256Digest
    chat_protocol_digest: Sha256Digest
    layer_digests: tuple[Sha256Digest, ...] = Field(min_length=1)
    archive_sha256: Sha256Digest | None = None
    archive_bytes: int | None = Field(default=None, gt=0)
    model_build_receipt_digest: Sha256Digest | None = None
    ollama_image_reference: str = Field(
        pattern=r"^[a-z0-9][a-z0-9./_-]{0,200}:[a-z0-9][a-z0-9._-]{0,127}$"
    )
    ollama_image_id: Sha256Digest
    ollama_version: Identifier
    controller_image_reference: str = Field(
        pattern=r"^[a-z0-9][a-z0-9./_-]{0,200}:[a-z0-9][a-z0-9._-]{0,127}$"
    )
    controller_image_id: Sha256Digest
    controller_archive_sha256: Sha256Digest | None = None
    controller_build_receipt_digest: Sha256Digest | None = None
    campaign_identity_digest: Sha256Digest
    roles: tuple[Stage6RoleIdentity, Stage6RoleIdentity]
    lock_digest: Sha256Digest

    @field_validator("roles")
    @classmethod
    def roles_are_canonical(
        cls, value: tuple[Stage6RoleIdentity, Stage6RoleIdentity]
    ) -> tuple[Stage6RoleIdentity, Stage6RoleIdentity]:
        return tuple(sorted(value, key=lambda item: item.role.value))

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"lock_digest"}, exclude_none=True)

    @model_validator(mode="after")
    def complete_identity_matches(self) -> Self:
        archive_delivery = (
            self.archive_sha256 is not None
            and self.archive_bytes is not None
            and self.model_build_receipt_digest is None
        )
        online_delivery = (
            self.archive_sha256 is None
            and self.archive_bytes is None
            and self.model_build_receipt_digest is not None
        )
        if not (archive_delivery or online_delivery):
            raise ValueError(
                "Stage 6 model requires exactly one archive or online receipt identity"
            )
        if (self.controller_archive_sha256 is None) == (
            self.controller_build_receipt_digest is None
        ):
            raise ValueError(
                "Stage 6 controller requires exactly one archive or online receipt identity"
            )
        by_role = {item.role: item for item in self.roles}
        if len(by_role) != 2 or set(by_role) != set(Stage6Role):
            raise ValueError("Stage 6 lock requires Agent and Mutator roles exactly once")
        if by_role[Stage6Role.AGENT].image_id == by_role[Stage6Role.MUTATOR].image_id:
            raise ValueError("Stage 6 Agent and Mutator image identities must differ")
        expected_campaign = build_v2_campaign_identity_lock().identity_digest
        if self.campaign_identity_digest != expected_campaign:
            raise ValueError("Stage 6 Campaign identity drifted")
        if self.lock_digest != sha256_digest(self.digest_payload()):
            raise ValueError("Stage 6 model lock digest does not match")
        return self


class Stage6RepairFileIdentity(OfficeV2Contract):
    path: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._/-]{0,300}$")
    sha256: Sha256Digest


class Stage6RepairRolePlan(OfficeV2Contract):
    role: Stage6Role
    base_image_reference: str
    base_image_id: Sha256Digest
    final_image_reference: str
    dockerfile: str
    dockerfile_sha256: Sha256Digest
    copied_files: tuple[Stage6RepairFileIdentity, ...] = Field(min_length=1)
    role_plan_digest: Sha256Digest

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(
            mode="json", exclude={"role_plan_digest"}, exclude_none=False
        )

    @model_validator(mode="after")
    def role_plan_matches(self) -> Self:
        if self.base_image_reference == self.final_image_reference:
            raise ValueError("repair role must not overwrite its immutable base tag")
        if self.role_plan_digest != sha256_digest(self.digest_payload()):
            raise ValueError("Stage 6 repair role plan digest does not match")
        return self


class Stage6RepairPlanLock(OfficeV2Contract):
    schema_version: Literal["office-v2-stage6-repair-plan-v2"]
    source_revision: str = Field(pattern=r"^[0-9a-f]{7,40}$")
    source_archive_sha256: Sha256Digest
    source_archive_bytes: int = Field(gt=0)
    model_digest: Sha256Digest
    base_model_lock_digest: Sha256Digest
    controller_image_reference: str
    controller_image_id: Sha256Digest
    roles: tuple[Stage6RepairRolePlan, Stage6RepairRolePlan]
    lock_digest: Sha256Digest

    @field_validator("roles")
    @classmethod
    def repair_roles_are_canonical(
        cls, value: tuple[Stage6RepairRolePlan, Stage6RepairRolePlan]
    ) -> tuple[Stage6RepairRolePlan, Stage6RepairRolePlan]:
        return tuple(sorted(value, key=lambda item: item.role.value))

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"lock_digest"}, exclude_none=False)

    @model_validator(mode="after")
    def repair_plan_matches(self) -> Self:
        if {item.role for item in self.roles} != set(Stage6Role):
            raise ValueError("repair plan requires Agent and Mutator exactly once")
        if self.lock_digest != sha256_digest(self.digest_payload()):
            raise ValueError("Stage 6 repair plan digest does not match")
        return self


class Stage6AppliedRoleIdentity(OfficeV2Contract):
    role: Stage6Role
    image_reference: str
    image_id: Sha256Digest
    image_build_receipt_digest: Sha256Digest


class Stage6RepairApplicationReceipt(OfficeV2Contract):
    schema_version: Literal["office-v2-stage6-repair-application-v1"]
    repair_lock_digest: Sha256Digest
    active_model_lock_digest: Sha256Digest
    roles: tuple[Stage6AppliedRoleIdentity, Stage6AppliedRoleIdentity]
    receipt_digest: Sha256Digest

    @field_validator("roles")
    @classmethod
    def applied_roles_are_canonical(
        cls, value: tuple[Stage6AppliedRoleIdentity, Stage6AppliedRoleIdentity]
    ) -> tuple[Stage6AppliedRoleIdentity, Stage6AppliedRoleIdentity]:
        return tuple(sorted(value, key=lambda item: item.role.value))

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(
            mode="json", exclude={"receipt_digest"}, exclude_none=False
        )

    @model_validator(mode="after")
    def application_receipt_matches(self) -> Self:
        if {item.role for item in self.roles} != set(Stage6Role):
            raise ValueError("repair receipt requires Agent and Mutator exactly once")
        if self.receipt_digest != sha256_digest(self.digest_payload()):
            raise ValueError("Stage 6 repair application receipt digest does not match")
        return self


def seal_inference_config(**values: object) -> Stage6InferenceConfig:
    draft = Stage6InferenceConfig.model_construct(
        **values, config_digest="sha256:" + "0" * 64
    )
    return Stage6InferenceConfig(
        **values, config_digest=sha256_digest(draft.digest_payload())
    )


def seal_role_identity(**values: object) -> Stage6RoleIdentity:
    draft = Stage6RoleIdentity.model_construct(
        **values, role_digest="sha256:" + "0" * 64
    )
    return Stage6RoleIdentity(
        **values, role_digest=sha256_digest(draft.digest_payload())
    )


def seal_stage6_model_lock(**values: object) -> Stage6ModelLock:
    payload = {
        "schema_version": "office-v2-stage6-model-lock-v1",
        "plan_digest": STAGE6_PLAN_DIGEST,
        "model_name": STAGE6_MODEL_NAME,
        "quantization": "Q4_K_M",
        "registry": "registry.ollama.ai",
        "campaign_identity_digest": build_v2_campaign_identity_lock().identity_digest,
        **values,
    }
    if "roles" in payload:
        payload["roles"] = tuple(
            sorted(payload["roles"], key=lambda item: item.role.value)
        )
    draft = Stage6ModelLock.model_construct(
        **payload, lock_digest="sha256:" + "0" * 64
    )
    return Stage6ModelLock(
        **payload, lock_digest=sha256_digest(draft.digest_payload())
    )


def seal_repair_role_plan(**values: object) -> Stage6RepairRolePlan:
    draft = Stage6RepairRolePlan.model_construct(
        **values, role_plan_digest="sha256:" + "0" * 64
    )
    return Stage6RepairRolePlan(
        **values, role_plan_digest=sha256_digest(draft.digest_payload())
    )


def seal_repair_plan_lock(**values: object) -> Stage6RepairPlanLock:
    payload = {"schema_version": "office-v2-stage6-repair-plan-v2", **values}
    if "roles" in payload:
        payload["roles"] = tuple(
            sorted(payload["roles"], key=lambda item: item.role.value)
        )
    draft = Stage6RepairPlanLock.model_construct(
        **payload, lock_digest="sha256:" + "0" * 64
    )
    return Stage6RepairPlanLock(
        **payload, lock_digest=sha256_digest(draft.digest_payload())
    )


def seal_repair_application_receipt(
    **values: object,
) -> Stage6RepairApplicationReceipt:
    payload = {
        "schema_version": "office-v2-stage6-repair-application-v1",
        **values,
    }
    if "roles" in payload:
        payload["roles"] = tuple(
            sorted(payload["roles"], key=lambda item: item.role.value)
        )
    draft = Stage6RepairApplicationReceipt.model_construct(
        **payload, receipt_digest="sha256:" + "0" * 64
    )
    return Stage6RepairApplicationReceipt(
        **payload, receipt_digest=sha256_digest(draft.digest_payload())
    )


__all__ = [
    "STAGE6_MODEL_NAME",
    "STAGE6_PLAN_DIGEST",
    "Stage6AppliedRoleIdentity",
    "Stage6InferenceConfig",
    "Stage6ModelLock",
    "Stage6RepairApplicationReceipt",
    "Stage6RepairFileIdentity",
    "Stage6RepairPlanLock",
    "Stage6RepairRolePlan",
    "Stage6Role",
    "Stage6RoleIdentity",
    "seal_inference_config",
    "seal_repair_application_receipt",
    "seal_repair_plan_lock",
    "seal_repair_role_plan",
    "seal_role_identity",
    "seal_stage6_model_lock",
]
