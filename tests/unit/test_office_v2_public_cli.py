from __future__ import annotations

import pytest

from sandbox.cli import _validate_agent_runtime_image, build_parser


def test_public_cli_exposes_only_v2_scenario_and_replay_commands() -> None:
    parser = build_parser()

    listed = parser.parse_args(["scenario", "list"])
    assert listed.command == "scenario"
    assert listed.scenario_command == "list"

    replay = parser.parse_args(["replay", "--replay-id", "replay-1"])
    assert replay.command == "replay"


@pytest.mark.parametrize(
    "legacy_command",
    ["list-cases", "run", "record", "coverage", "mutate", "campaign"],
)
def test_public_cli_rejects_legacy_production_commands(legacy_command: str) -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args([legacy_command])


def test_scenario_run_requires_locked_model_identity_and_image() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["scenario", "run", "--case", "clean.t2.delta"])

    args = parser.parse_args(
        [
            "scenario",
            "run",
            "--case",
            "clean.t2.delta",
            "--image",
            "trace-g-agent-qwen:locked",
            "--model-name",
            "qwen3:8b",
            "--model-digest",
            "sha256:" + "a" * 64,
        ]
    )
    assert args.case_id == "clean.t2.delta"
    assert args.model_name == "qwen3:8b"
    assert args.agent_runtime == "langgraph"

    harness = parser.parse_args(
        [
            "scenario",
            "run",
            "--case",
            "clean.t2.delta",
            "--image",
            "trace-g-deepseek-harness:h4",
            "--agent-runtime",
            "deepseek_harness",
            "--model-name",
            "fixture",
            "--model-digest",
            "sha256:" + "b" * 64,
        ]
    )
    assert harness.agent_runtime == "deepseek_harness"


def test_harness_image_identity_is_rejected_before_scheduling() -> None:
    class Images:
        @staticmethod
        def get(_image):
            return type(
                "Image",
                (),
                {
                    "attrs": {
                        "Config": {
                            "Labels": {
                                "org.trace-g.agent-runtime": "deepseek_harness",
                                "org.trace-g.runtime": "deepseek-harness-h3-v1",
                            }
                        }
                    }
                },
            )()

    client = type("Client", (), {"images": Images()})()
    with pytest.raises(SystemExit, match="does not match"):
        _validate_agent_runtime_image(
            client,
            "trace-g-deepseek-harness:stale",
            "deepseek_harness",
        )
