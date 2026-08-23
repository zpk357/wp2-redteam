from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from app.adapter.base import AdapterConfigurationError
from app.adapter.deepseek_harness_adapter import DeepSeekHarnessAdapter
from app.agent.react_contract import (
    REQUEST_CLARIFICATION_TOOL_SPEC,
    SUBMIT_TOOL_SPEC,
)

from sandbox.scenarios.office_v2.agent_api import office_v2_model_tool_specs
from tests.harness_support import harness_request


def test_adapter_rejects_a_model_outside_the_h4_lock() -> None:
    request = harness_request()
    assert request.model is not None
    assert request.office_v2_execution is not None
    wrong_model = request.model.model_copy(update={"model_name": "other"})
    wrong_envelope = request.office_v2_execution.model_copy(
        update={"model_identity": wrong_model}
    )
    wrong = request.model_copy(
        update={"model": wrong_model, "office_v2_execution": wrong_envelope}
    )

    async def consume() -> None:
        async for _ in DeepSeekHarnessAdapter().execute(wrong):
            pass

    with pytest.raises(AdapterConfigurationError) as raised:
        asyncio.run(consume())
    assert raised.value.error_code == "harness_model_identity_mismatch"


def test_h4_mapping_is_mechanically_derived_from_the_frozen_catalog() -> None:
    request = harness_request()
    assert request.office_v2_execution is not None
    specs = (
        *office_v2_model_tool_specs(),
        REQUEST_CLARIFICATION_TOOL_SPEC,
        SUBMIT_TOOL_SPEC,
    )
    manifest = DeepSeekHarnessAdapter.tool_mapping_manifest(
        request.office_v2_execution.tool_catalog_digest,
        specs,
    )

    assert len(specs) == 19
    assert [item["canonical_name"] for item in manifest["mappings"]] == [
        *(spec.name for spec in office_v2_model_tool_specs()),
        "request_clarification",
        "submit",
    ]
    assert len({item["transport_name"] for item in manifest["mappings"]}) == 19
    assert manifest["mapping_digest"].startswith("sha256:")


def test_deterministic_model_contains_no_frozen_resource_or_case_ids() -> None:
    module = (
        Path(__file__).resolve().parents[2]
        / "agent_variants"
        / "deepseek_harness"
        / "runtime"
        / "deterministic_model.mjs"
    )
    text = module.read_text(encoding="utf-8")
    request = harness_request()
    assert request.case_id not in text
    assert request.office_v2_execution is not None
    for binding in request.office_v2_execution.scenario_case_payload["resolved_bindings"]:
        for resource in binding["resource_refs"]:
            assert resource["resource_id"] not in text


def test_bootstrap_mapping_round_trips_as_canonical_json() -> None:
    request = harness_request()
    assert request.office_v2_execution is not None
    specs = (
        *office_v2_model_tool_specs(),
        REQUEST_CLARIFICATION_TOOL_SPEC,
        SUBMIT_TOOL_SPEC,
    )
    manifest = DeepSeekHarnessAdapter.tool_mapping_manifest(
        request.office_v2_execution.tool_catalog_digest,
        specs,
    )
    assert json.loads(json.dumps(manifest)) == manifest
