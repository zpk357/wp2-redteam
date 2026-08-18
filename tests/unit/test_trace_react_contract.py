from __future__ import annotations

import pytest
from app.agent.react_contract import ReactMessage, ReactToolCall, ReactTurn
from pydantic import ValidationError


def test_react_turn_does_not_allow_model_owned_loop_or_final_fields() -> None:
    turn = ReactTurn(
        assistant_text="I will inspect the workspace.",
        tool_calls=[ReactToolCall(name="list_directory", arguments={"path": "/workspace"})],
    )

    assert turn.tool_calls[0].name == "list_directory"
    with pytest.raises(ValidationError):
        ReactTurn.model_validate({"assistant_text": "done", "continue_loop": False})
    with pytest.raises(ValidationError):
        ReactTurn.model_validate({"assistant_text": "done", "final_answer": "done"})


def test_tool_message_requires_call_identity() -> None:
    message = ReactMessage(
        role="tool",
        name="read_file",
        call_id="call-1",
        content={"allowed": True, "output": "hello"},
    )

    assert message.call_id == "call-1"
    with pytest.raises(ValidationError, match="require call_id and name"):
        ReactMessage(role="tool", content="missing identity")


def test_empty_model_turn_is_rejected() -> None:
    with pytest.raises(ValidationError, match="assistant text or at least one tool call"):
        ReactTurn()
