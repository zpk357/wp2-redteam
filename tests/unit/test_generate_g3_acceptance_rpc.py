from __future__ import annotations

from scripts.generate_g3_acceptance_rpc import execution_request, formal_case, rpc_payload


def test_acceptance_cases_lock_the_formal_in_container_agent() -> None:
    clean = formal_case("clean")
    injected = formal_case("injected")

    assert clean.agent.provider == "ollama"
    assert clean.agent.endpoint == "http://127.0.0.1:11434"
    assert injected.attack is not None
    assert injected.attack.carrier.carrier_type == "email_body"
    assert clean.content_digest != injected.content_digest


def test_submit_rpc_contains_case_but_no_action_plan() -> None:
    payload = rpc_payload("submit", "g3-clean", "clean")
    request = execution_request("clean", "g3-clean")

    assert payload["method"] == "execution.submit"
    assert payload["params"] == request.model_dump(mode="json")
    assert "action_plan" not in payload["params"]
    assert payload["params"]["model"]["endpoint"] == "http://127.0.0.1:11434"
