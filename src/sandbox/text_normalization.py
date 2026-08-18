"""Dependency-neutral text normalization shared by mutation pipelines."""

from __future__ import annotations

import re
import unicodedata

from sandbox.replay.digests import sha256_digest


def normalize_prompt(prompt: str) -> str:
    text = unicodedata.normalize("NFKC", prompt).replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    normalized: list[str] = []
    in_code_block = False
    previous_blank = False
    for line in lines:
        if line.strip().startswith("```"):
            in_code_block = not in_code_block
        current = line if in_code_block else re.sub(r"[\t ]+", " ", line)
        blank = not current.strip()
        if blank and previous_blank:
            continue
        normalized.append(current)
        previous_blank = blank
    return "\n".join(normalized).strip()


def normalized_prompt_digest(prompt: str) -> str:
    return sha256_digest(normalize_prompt(prompt))
