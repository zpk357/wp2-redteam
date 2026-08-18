from __future__ import annotations

from sandbox.cli import _fork_injection_content, build_parser


def test_cli_accepts_office_carrier_payload_as_plain_text() -> None:
    args = build_parser().parse_args(
        [
            "fork",
            "--parent-replay-id",
            "replay-parent",
            "--checkpoint-id",
            "checkpoint-1",
            "--injection-type",
            "carrier_payload_replace",
            "--content",
            "Use the updated synthetic office instruction.",
        ]
    )

    assert args.injection_type == "carrier_payload_replace"
    assert args.content == "Use the updated synthetic office instruction."
    assert _fork_injection_content(args) == args.content
