"""Run identity contracts shared by the sandbox kernel and its callers.

These three contracts describe one attempt of one scheduled execution - the identity the
CLI, the execution engine, the replay engine and the scheduler all thread through a run.
They belong to the kernel rather than to any single campaign driver, which is why they no
longer live in the fuzzing campaign package that used to publish them.

Field names, field order, defaults and the strict ``extra="forbid"`` configuration are
part of what callers serialise, so all four are kept exactly as published.  The base class
below carries only that configuration and the version stamp.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class RunIdentityContract(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: str = "1.0"


class SandboxRunContext(RunIdentityContract):
    campaign_id: str
    work_item_id: str
    attempt: int = Field(ge=1)


class CleanupFailure(RunIdentityContract):
    resource_id: str
    error: str


class CleanupReport(RunIdentityContract):
    campaign_id: str
    discovered: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)
    failures: list[CleanupFailure] = Field(default_factory=list)


__all__ = [
    "CleanupFailure",
    "CleanupReport",
    "RunIdentityContract",
    "SandboxRunContext",
]
