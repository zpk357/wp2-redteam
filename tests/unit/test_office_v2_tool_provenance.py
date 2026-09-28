"""Resource citations versus observed evidence (VE-02 / VE-04).

The Agent may cite a drive file at the granularity it was shown: tool output
evidence for a file carries the resolved version, while a later call may cite the
file without a version. Inference and verification must judge both ends by the
same rule, or a real "read then use" chain silently loses its source.

Nothing here calls a model or Docker; the fixtures only stand in for the tool
results the runtime would have produced.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.models import ResourceRef
from sandbox.scenarios.office_v2.tools.contracts import (
    ArgumentSource,
    ArgumentSourceMode,
    OfficeToolInvocation,
    OutputEvidence,
)
from sandbox.scenarios.office_v2.tools.provenance import (
    EvidenceLedger,
    ProvenanceError,
    infer_exact_argument_sources,
)

DIGEST = "sha256:" + "0" * 64
FILE_ID = "drive.apollo.restricted-roadmap"
VERSION_2 = "version.apollo.restricted-roadmap.2"
VERSION_1 = "version.apollo.restricted-roadmap.1"


@dataclass(frozen=True)
class ObservedResult:
    """Stands in for an OfficeToolResult: inference reads only its evidence."""

    output_evidence: tuple[OutputEvidence, ...]


def evidence(
    *,
    evidence_id: str,
    sequence: int,
    resource: dict[str, Any] | None,
    value: str = "payload",
) -> OutputEvidence:
    return OutputEvidence(
        evidence_id=evidence_id,
        invocation_id=f"invocation.{sequence}",
        invocation_sequence=sequence,
        field_path=("content",),
        resource_ref=ResourceRef.model_validate(resource) if resource else None,
        value_digest=sha256_digest(value),
    )


def file_citation(**overrides: Any) -> dict[str, Any]:
    citation: dict[str, Any] = {"kind": "drive_file", "resource_id": FILE_ID}
    citation.update(overrides)
    return citation


def test_file_level_citation_reuses_versioned_observed_evidence() -> None:
    observed = (
        ObservedResult(
            (
                evidence(
                    evidence_id="evidence.read.file",
                    sequence=0,
                    resource={
                        "kind": "drive_file",
                        "resource_id": FILE_ID,
                        "version_id": VERSION_2,
                    },
                ),
            )
        ),
    )

    sources = infer_exact_argument_sources(
        {"related_refs": [file_citation()]},
        observed,
    )

    assert [source.argument_path for source in sources] == [("related_refs", "0")]
    assert sources[0].mode is ArgumentSourceMode.RESOURCE_REFERENCE
    assert sources[0].source_evidence_ids == ("evidence.read.file",)


def test_matching_versioned_citation_links_to_that_version() -> None:
    observed = (
        ObservedResult(
            (
                evidence(
                    evidence_id="evidence.read.file",
                    sequence=0,
                    resource={
                        "kind": "drive_file",
                        "resource_id": FILE_ID,
                        "version_id": VERSION_2,
                    },
                ),
            )
        ),
    )

    sources = infer_exact_argument_sources(
        {"related_refs": [file_citation(version_id=VERSION_2)]},
        observed,
    )

    assert [source.source_evidence_ids for source in sources] == [
        ("evidence.read.file",)
    ]


def test_citation_of_another_version_does_not_link() -> None:
    observed = (
        ObservedResult(
            (
                evidence(
                    evidence_id="evidence.read.file",
                    sequence=0,
                    resource={
                        "kind": "drive_file",
                        "resource_id": FILE_ID,
                        "version_id": VERSION_2,
                    },
                ),
            )
        ),
    )

    sources = infer_exact_argument_sources(
        {"related_refs": [file_citation(version_id=VERSION_1)]},
        observed,
    )

    assert sources == ()


def test_citation_of_an_unobserved_resource_does_not_link() -> None:
    observed = (
        ObservedResult(
            (
                evidence(
                    evidence_id="evidence.read.file",
                    sequence=0,
                    resource={
                        "kind": "drive_file",
                        "resource_id": FILE_ID,
                        "version_id": VERSION_2,
                    },
                ),
            )
        ),
    )

    sources = infer_exact_argument_sources(
        {"related_refs": [file_citation(resource_id="drive.apollo.other")]},
        observed,
    )

    assert sources == ()


def invocation_with_file_citation(*, sequence: int) -> OfficeToolInvocation:
    arguments = {"related_refs": [file_citation()]}
    return OfficeToolInvocation(
        invocation_id=f"invocation.{sequence}",
        sequence=sequence,
        tool_name="send_email",
        actor_id="user.maya.chen",
        task_id="task.t1.apollo",
        logical_time=sequence + 1,
        arguments=arguments,
        arguments_digest=sha256_digest(arguments),
        argument_sources=(
            ArgumentSource(
                argument_path=("related_refs", "0"),
                source_evidence_ids=("evidence.read.file",),
                mode=ArgumentSourceMode.RESOURCE_REFERENCE,
            ),
        ),
        before_state_digest=DIGEST,
    )


def test_ledger_verifies_a_file_level_citation_of_prior_evidence() -> None:
    ledger = EvidenceLedger()
    ledger.add(
        (
            evidence(
                evidence_id="evidence.read.file",
                sequence=0,
                resource={
                    "kind": "drive_file",
                    "resource_id": FILE_ID,
                    "version_id": VERSION_2,
                },
            ),
        )
    )

    ledger.verify_sources(invocation_with_file_citation(sequence=1))


def test_ledger_refuses_evidence_from_the_same_or_a_later_invocation() -> None:
    ledger = EvidenceLedger()
    ledger.add(
        (
            evidence(
                evidence_id="evidence.read.file",
                sequence=1,
                resource={
                    "kind": "drive_file",
                    "resource_id": FILE_ID,
                    "version_id": VERSION_2,
                },
            ),
        )
    )

    with pytest.raises(ProvenanceError) as error:
        ledger.verify_sources(invocation_with_file_citation(sequence=1))

    assert error.value.code.value == "argument_source_mismatch"
