from __future__ import annotations

from pathlib import Path

from app.adapter.trace_react_adapter import TraceReactAdapter
from app.protocol import ExecutionRequest

from sandbox.coverage.input import CoverageInputResolver
from sandbox.coverage.store import CoverageStore
from sandbox.coverage.taxonomy import RiskTaxonomyLoader
from sandbox.engine.case_source import TemplateCaseSource


async def test_all_week_one_templates_flow_into_cumulative_coverage(tmp_path: Path) -> None:
    source = TemplateCaseSource()
    trajectory_root = tmp_path / "trajectories"
    trajectory_root.mkdir()
    resolver = CoverageInputResolver(
        trajectory_root=trajectory_root,
        manifest_root=tmp_path / "replays",
        artifact_root=tmp_path / "artifacts",
        case_source=source,
    )
    taxonomy = RiskTaxonomyLoader(Path("config/risk-taxonomy.yaml")).load()
    cumulative_counts: list[int] = []
    results = {}

    with CoverageStore(
        tmp_path / "coverage",
        "week3-integration",
        taxonomy,
        auto_snapshot_interval=0,
    ) as store:
        template_ids = tuple(
            template_id
            for template_id in source.template_ids
            if template_id != "loop-timeout-001"
        )
        for index, template_id in enumerate(template_ids):
            case = source.generate(template_id, seed=42)
            if template_id in {
                "trace-workspace-clean-001",
                "trace-workspace-injected-001",
            }:
                case = case.model_copy(
                    update={
                        "metadata": {
                            **case.metadata,
                            "scenario_control": (
                                "safe"
                                if template_id == "trace-workspace-clean-001"
                                else "vulnerable"
                            ),
                        }
                    }
                )
            request = ExecutionRequest(
                execution_id=f"exec-coverage-{index}",
                case_id=case.case_id,
                prompt=case.prompt,
                scenario_id=case.scenario_id,
                metadata=case.metadata,
                agent_version="trace-react-v2",
                max_steps=8,
            )
            events = [event async for event in TraceReactAdapter().execute(request)]
            path = trajectory_root / f"{request.execution_id}.jsonl"
            path.write_text(
                "".join(event.model_dump_json() + "\n" for event in events),
                encoding="utf-8",
            )
            result = store.evaluate(resolver.from_trajectory_path(path))
            results[template_id] = result
            cumulative_counts.append(result.cumulative_behavior_count)
            assert result.behavior_features_total > 0

        assert cumulative_counts == sorted(cumulative_counts)
        assert all(
            hit.depth < 2 for hit in results["benign-control-001"].risk_hits
        )
        assert any(
            hit.category_id == "unauthorized_file_read" and hit.depth == 2
            for hit in results["path-absolute-001"].risk_hits
        )
        assert any(
            hit.category_id == "unauthorized_network_access" and hit.depth == 2
            for hit in results["network-egress-001"].risk_hits
        )
        assert store.snapshot().total_trajectories == len(template_ids)
