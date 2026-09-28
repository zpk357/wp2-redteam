from __future__ import annotations

import ast
from pathlib import Path


def _calls(path: Path) -> tuple[tuple[str, str], ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports = {
        alias.asname or alias.name: (node.module or "", alias.name)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    return tuple(sorted(imports[name] for name in called if name in imports))


def test_both_agent_runtimes_use_the_shared_office_v2_surface() -> None:
    expected = (
        "app.office_v2_runtime_surface",
        "build_office_v2_runtime_surface",
    )
    adapters = (
        Path("agent_image/app/adapter/langgraph_react_runtime.py"),
        Path("agent_image/app/adapter/deepseek_harness_adapter.py"),
    )
    for adapter in adapters:
        source = adapter.read_text(encoding="utf-8")
        assert "build_office_v2_runtime_surface" in source
        assert expected in _calls(adapter)


def test_shared_surface_keeps_argument_provenance_enabled() -> None:
    source = Path("agent_image/app/office_v2_session.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    surface_calls = tuple(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "OfficeV2AgentSessionSurface"
    )
    assert len(surface_calls) == 1
    keywords = {item.arg: item.value for item in surface_calls[0].keywords}
    resolver = keywords["argument_source_resolver"]
    assert isinstance(resolver, ast.Attribute)
    assert resolver.attr == "_resolve_argument_sources"
