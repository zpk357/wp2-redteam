from __future__ import annotations

import json

from app.office_v2_mutator_worker import _response_schema


def test_v2_worker_uses_ollama_compatible_nested_schema() -> None:
    schema_text = json.dumps(_response_schema())
    assert '"maxLength": 8192' not in schema_text
    assert '"maxLength": 1024' not in schema_text
