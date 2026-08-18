#!/usr/bin/env python3
"""Print the shared office Agent system-prompt identity for image builds."""

from __future__ import annotations

import ast
import hashlib
import json
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROMPT_SOURCE = ROOT / "src" / "sandbox" / "agent_prompts.py"


def string_constant(name: str) -> str:
    tree = ast.parse(PROMPT_SOURCE.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name) and target.id == name:
            value = ast.literal_eval(node.value)
            if isinstance(value, str):
                return value
    raise RuntimeError(f"missing string constant {name} in {PROMPT_SOURCE}")


def canonical_string_digest(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value)
    payload = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def main() -> None:
    prompt = string_constant("OFFICE_AGENT_SYSTEM_PROMPT")
    version = string_constant("OFFICE_AGENT_SYSTEM_PROMPT_VERSION")
    mutator_prompt = string_constant("OFFICE_MUTATOR_SYSTEM_PROMPT")
    mutator_version = string_constant("OFFICE_MUTATOR_SYSTEM_PROMPT_VERSION")
    print(
        json.dumps(
            {
                "version": version,
                "digest": canonical_string_digest(prompt),
                "mutator_version": mutator_version,
                "mutator_digest": canonical_string_digest(mutator_prompt),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
