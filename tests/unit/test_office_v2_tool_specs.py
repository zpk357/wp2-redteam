from __future__ import annotations

import hashlib
import json

from sandbox.scenarios.office_v2 import OFFICE_V2_TOOL_CONTRACT_VERSION
from sandbox.scenarios.office_v2.tools import (
    OFFICE_V2_EXCLUDED_TOOL_NAMES,
    OFFICE_V2_TOOL_NAMES,
    office_v2_tool_definitions,
)
from sandbox.tool_contracts import (
    OFFICE_V2_PUBLIC_TOOL_CONTRACT,
    OFFICE_V2_PUBLIC_TOOL_CONTRACT_DIGEST,
    OFFICE_V2_TOOL_SPEC_BY_NAME,
    OFFICE_V2_TOOL_SPECS,
    TOOL_SPECS,
    ToolEffect,
)

FROZEN_V1_PUBLIC_CONTRACT_DIGEST = (
    "sha256:b9beec69a03e4b5081acd369d54a1421a69ab96dc2feb4de573456c441a4e9e1"
)
FROZEN_V2_PUBLIC_CONTRACT_DIGEST = (
    "sha256:fe9fdcad58adb09859c92ceb5200901962da81a80a3941753a2edaff47365750"
)


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def test_v2_specs_are_exactly_backed_by_the_frozen_handler_catalog() -> None:
    definitions = office_v2_tool_definitions()

    assert tuple(spec.name for spec in OFFICE_V2_TOOL_SPECS) == OFFICE_V2_TOOL_NAMES
    assert tuple(OFFICE_V2_TOOL_SPEC_BY_NAME) == OFFICE_V2_TOOL_NAMES
    assert len(OFFICE_V2_TOOL_SPECS) == len(OFFICE_V2_TOOL_SPEC_BY_NAME) == 17
    for spec in OFFICE_V2_TOOL_SPECS:
        definition = definitions[spec.name]
        assert spec.definition is definition
        assert spec.arguments_model is definition.arguments_model
        assert spec.required_capability == definition.capability_id
        assert callable(definition.prepare)
        assert callable(definition.execute)


def test_v2_public_contract_is_complete_stable_and_excludes_legacy_tools() -> None:
    public_names = tuple(item["name"] for item in OFFICE_V2_PUBLIC_TOOL_CONTRACT)

    assert public_names == OFFICE_V2_TOOL_NAMES
    assert set(public_names).isdisjoint(OFFICE_V2_EXCLUDED_TOOL_NAMES)
    assert all(
        item["version"] == OFFICE_V2_TOOL_CONTRACT_VERSION
        for item in OFFICE_V2_PUBLIC_TOOL_CONTRACT
    )
    assert all(
        item["arguments_schema"]["type"] == "object"
        for item in OFFICE_V2_PUBLIC_TOOL_CONTRACT
    )
    actual_digest = _digest(OFFICE_V2_PUBLIC_TOOL_CONTRACT)
    assert actual_digest == OFFICE_V2_PUBLIC_TOOL_CONTRACT_DIGEST
    assert actual_digest == FROZEN_V2_PUBLIC_CONTRACT_DIGEST


def test_v2_effect_metadata_agrees_with_handler_state_semantics() -> None:
    for spec in OFFICE_V2_TOOL_SPECS:
        if spec.effect == ToolEffect.READ_ONLY:
            assert spec.definition.writes_state is False
        else:
            assert spec.definition.writes_state is True


def test_v2_descriptions_are_business_facing_and_case_independent() -> None:
    forbidden = ("synthetic", "matrix", "attack", "apollo", "jordan", "maya")

    for spec in OFFICE_V2_TOOL_SPECS:
        lowered = spec.description.lower()
        assert not any(token in lowered for token in forbidden)


def test_v1_public_contract_and_registry_identity_are_unchanged() -> None:
    assert tuple(spec.name for spec in TOOL_SPECS) == (
        "read_file",
        "write_file",
        "run_command",
        "call_internal_api",
        "list_directory",
        "search_files",
        "read_environment",
        "list_processes",
        "query_database",
        "send_email",
        "http_request",
        "retrieve_secret",
    )
    assert _digest([spec.public_contract() for spec in TOOL_SPECS]) == (
        FROZEN_V1_PUBLIC_CONTRACT_DIGEST
    )
