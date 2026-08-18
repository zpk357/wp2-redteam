"""Strict parsing and normalization for one Office V2 mutation candidate."""

from __future__ import annotations

import json
import unicodedata
from typing import Self

from pydantic import Field, field_validator, model_validator

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.models import OfficeV2Contract, Sha256Digest

from .v2_brief import MutationCandidateResponse
from .v2_contracts import MutationPlan


def normalize_generated_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).replace("\r\n", "\n")
    return "\n".join(line.rstrip() for line in normalized.strip().split("\n"))


class CandidateTextDiff(OfficeV2Contract):
    payload_slot_id: str
    parent_content_digest: Sha256Digest
    generated_content_digest: Sha256Digest
    changed: bool


class ParsedMutationCandidate(OfficeV2Contract):
    mutation_plan_digest: Sha256Digest
    slot_values: tuple[tuple[str, str], ...] = Field(min_length=1)
    text_diffs: tuple[CandidateTextDiff, ...] = Field(min_length=1)
    structural_objective_preserved: bool = True
    semantic_preservation: str = "unverified"
    lexical_heuristic: str = "audit-only"
    candidate_digest: Sha256Digest

    @field_validator("slot_values")
    @classmethod
    def slots_are_canonical(
        cls, value: tuple[tuple[str, str], ...]
    ) -> tuple[tuple[str, str], ...]:
        ids = tuple(item[0] for item in value)
        if len(ids) != len(set(ids)):
            raise ValueError("parsed candidate repeats a slot")
        return tuple(sorted(value))

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"candidate_digest"}, exclude_none=False)

    @model_validator(mode="after")
    def digest_matches(self) -> Self:
        if self.semantic_preservation != "unverified":
            raise ValueError("host cannot claim semantic preservation without Judge scope")
        if self.candidate_digest != sha256_digest(self.digest_payload()):
            raise ValueError("parsed candidate digest does not match")
        return self


def parse_candidate(
    *, plan: MutationPlan, raw_json: str, parent_text_by_slot: dict[str, str]
) -> ParsedMutationCandidate:
    raw = json.loads(raw_json)
    response = MutationCandidateResponse.model_validate(raw)
    values = tuple(
        (item.payload_slot_id, normalize_generated_text(item.generated_content))
        for item in response.slot_values
    )
    diffs = tuple(
        CandidateTextDiff(
            payload_slot_id=slot_id,
            parent_content_digest=sha256_digest(
                {"content": parent_text_by_slot.get(slot_id, "")}
            ),
            generated_content_digest=sha256_digest({"content": content}),
            changed=content != normalize_generated_text(parent_text_by_slot.get(slot_id, "")),
        )
        for slot_id, content in values
    )
    payload = {
        "mutation_plan_digest": plan.plan_digest,
        "slot_values": values,
        "text_diffs": diffs,
    }
    draft = ParsedMutationCandidate.model_construct(
        **payload, candidate_digest="sha256:" + "0" * 64
    )
    return ParsedMutationCandidate(
        **payload, candidate_digest=sha256_digest(draft.digest_payload())
    )


__all__ = [
    "CandidateTextDiff",
    "ParsedMutationCandidate",
    "normalize_generated_text",
    "parse_candidate",
]
