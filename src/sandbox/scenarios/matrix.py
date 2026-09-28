"""Frozen collections of clean and attacked scenario cases."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.models import FrozenContract, Identifier, TestCase


class TestMatrixSummary(FrozenContract):
    clean_case_count: int = Field(ge=1)
    attack_case_count: int = Field(ge=1)
    task_template_ids: tuple[str, ...] = Field(min_length=1)
    objective_template_ids: tuple[str, ...] = Field(min_length=1)
    carrier_template_ids: tuple[str, ...] = Field(min_length=1)
    payload_digests: tuple[str, ...] = Field(min_length=1)


class TestMatrix(FrozenContract):
    """A deterministic matrix whose cases are ready for later execution."""

    matrix_id: Identifier
    matrix_version: Literal["1.0"] = "1.0"
    clean_cases: tuple[TestCase, ...] = Field(min_length=1)
    attack_cases: tuple[TestCase, ...] = Field(min_length=1)
    content_digest: str | None = None

    @model_validator(mode="after")
    def validate_cases_and_digest(self) -> TestMatrix:
        cases = (*self.clean_cases, *self.attack_cases)
        for case in cases:
            case.assert_integrity()

        if any(case.attack is not None for case in self.clean_cases):
            raise ValueError("clean_cases must not contain attack bindings")
        if any(case.attack is None for case in self.attack_cases):
            raise ValueError("attack_cases must contain attack bindings")

        case_ids = [case.case_id for case in cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("matrix case_id values must be unique")
        clean_task_ids = [case.benign_task.task_id for case in self.clean_cases]
        if len(clean_task_ids) != len(set(clean_task_ids)):
            raise ValueError("clean matrix task_id values must be unique")

        scenario = cases[0].scenario
        if any(case.scenario != scenario for case in cases[1:]):
            raise ValueError("all matrix cases must use the same frozen scenario")

        attack_signatures = [
            (
                case.benign_task.task_id,
                case.attack.objective.objective_id,
                case.attack.carrier.carrier_id,
                sha256_digest(case.attack.payload),
            )
            for case in self.attack_cases
        ]
        if len(attack_signatures) != len(set(attack_signatures)):
            raise ValueError("matrix attack combinations must be unique")

        calculated = sha256_digest(self.model_dump(mode="json", exclude={"content_digest"}))
        if self.content_digest is not None and self.content_digest != calculated:
            raise ValueError("content_digest does not match the frozen TestMatrix content")
        object.__setattr__(self, "content_digest", calculated)
        return self

    def assert_integrity(self) -> None:
        for case in (*self.clean_cases, *self.attack_cases):
            case.assert_integrity()
        current = sha256_digest(self.model_dump(mode="json", exclude={"content_digest"}))
        if current != self.content_digest:
            raise ValueError("frozen TestMatrix content no longer matches content_digest")

    def summary(self) -> TestMatrixSummary:
        attacks = tuple(case.attack for case in self.attack_cases)
        return TestMatrixSummary(
            clean_case_count=len(self.clean_cases),
            attack_case_count=len(self.attack_cases),
            task_template_ids=tuple(
                sorted({case.benign_task.template_id for case in self.clean_cases})
            ),
            objective_template_ids=tuple(
                sorted({attack.objective.template_id for attack in attacks})
            ),
            carrier_template_ids=tuple(
                sorted({attack.carrier.template_id for attack in attacks})
            ),
            payload_digests=tuple(
                sorted({sha256_digest(attack.payload) for attack in attacks})
            ),
        )
