#!/usr/bin/env python3
"""Serve a validated, read-only Office V2 coverage visualization snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

SCHEMA_VERSION = "office-v2-coverage-visualization-v2"
RUNTIME_KINDS = frozenset({"deepseek_harness", "langgraph"})
SETTLEMENT_KINDS = frozenset({"candidate_settlement", "non_episode_settlement"})
TERMINAL_EVENT_TYPES = frozenset(
    {"execution_finished", "execution_error", "execution_timed_out", "execution_cancelled"}
)


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _items(value: object, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    return value


def _non_empty_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be non-empty text")
    return value


def _validate_episode(episode: dict[str, Any], generation_label: str) -> None:
    execution_id = _non_empty_text(
        episode.get("execution_id"), f"{generation_label} episode.execution_id"
    )
    events = _items(episode.get("events"), f"{generation_label} episode.events")
    if not events:
        raise ValueError(f"{generation_label} episode must contain events")

    actual_sequences: list[object] = []
    for offset, raw_event in enumerate(events, start=1):
        event = _mapping(raw_event, f"{generation_label} event {offset}")
        actual_sequences.append(event.get("sequence"))
        if event.get("execution_id") != execution_id:
            raise ValueError(f"{generation_label} event execution_id mismatch")
        _non_empty_text(event.get("event_type"), f"{generation_label} event.event_type")
    first_sequence = actual_sequences[0]
    if first_sequence not in {0, 1}:
        raise ValueError(f"{generation_label} event sequence has an invalid origin")
    expected_sequences = list(range(first_sequence, first_sequence + len(events)))
    if actual_sequences != expected_sequences:
        raise ValueError(f"{generation_label} event sequence is not contiguous")
    if events[-1].get("event_type") not in TERMINAL_EVENT_TYPES:
        raise ValueError(f"{generation_label} episode has no terminal event")


def _validate_non_episode_coverage(coverage: dict[str, Any], generation_label: str) -> None:
    for field in ("tool_path", "behavior_features", "risk_contexts"):
        if _items(coverage.get(field), f"{generation_label} coverage.{field}"):
            raise ValueError(f"{generation_label} non-episode settlement has coverage contribution")
    delta = _mapping(coverage.get("delta"), f"{generation_label} coverage.delta")
    if any(value != 0 for value in delta.values()):
        raise ValueError(f"{generation_label} non-episode settlement has coverage contribution")


def _validate_campaign(campaign: dict[str, Any]) -> None:
    campaign_id = _non_empty_text(campaign.get("id"), "campaign.id")
    runtime = _mapping(campaign.get("runtime"), f"campaign {campaign_id} runtime")
    if runtime.get("kind") not in RUNTIME_KINDS:
        raise ValueError(f"campaign {campaign_id} runtime.kind is unsupported")
    _mapping(campaign.get("baseline"), f"campaign {campaign_id} baseline")

    generations = _items(campaign.get("generations"), f"campaign {campaign_id} generations")
    if campaign.get("completed_generations") != len(generations):
        raise ValueError(f"campaign {campaign_id} completed generation count differs from rows")

    previous_feedback_digest: str | None = None
    previous_behavior_total = 0
    previous_risk_total = 0
    for number, raw_generation in enumerate(generations, start=1):
        label = f"campaign {campaign_id} generation {number}"
        generation = _mapping(raw_generation, label)
        if generation.get("number") != number:
            raise ValueError(f"campaign {campaign_id} generations are not contiguous")
        if generation.get("internal_decision_index") != number - 1:
            raise ValueError(f"{label} internal_decision_index is invalid")

        decision = _mapping(generation.get("decision"), f"{label} decision")
        input_feedback_digest = decision.get("input_feedback_digest")
        if number == 1:
            if input_feedback_digest is not None:
                raise ValueError(f"{label} must not consume previous-generation feedback")
        elif input_feedback_digest != previous_feedback_digest:
            raise ValueError(f"{label} feedback lineage mismatch")

        feedback_output = _mapping(
            generation.get("feedback_output"), f"{label} feedback_output"
        )
        previous_feedback_digest = _non_empty_text(
            feedback_output.get("digest"), f"{label} feedback_output.digest"
        )

        _mapping(generation.get("mutation"), f"{label} mutation")
        coverage = _mapping(generation.get("coverage"), f"{label} coverage")
        settlement_kind = generation.get("settlement_kind")
        if settlement_kind not in SETTLEMENT_KINDS:
            raise ValueError(f"{label} settlement_kind is unsupported")
        if settlement_kind == "non_episode_settlement":
            _validate_non_episode_coverage(coverage, label)
        cumulative = _mapping(coverage.get("cumulative"), f"{label} coverage.cumulative")
        delta = _mapping(coverage.get("delta"), f"{label} coverage.delta")
        _items(coverage.get("tool_path"), f"{label} coverage.tool_path")
        behavior_features = _items(
            coverage.get("behavior_features"), f"{label} coverage.behavior_features"
        )
        risk_contexts = _items(
            coverage.get("risk_contexts"), f"{label} coverage.risk_contexts"
        )
        if delta.get("primary_behavior") != len(behavior_features):
            raise ValueError(f"{label} behavior coverage total differs from detail rows")
        if delta.get("risk_contexts") != len(risk_contexts):
            raise ValueError(f"{label} risk coverage total differs from detail rows")
        if cumulative.get("primary_behavior") != (
            previous_behavior_total + len(behavior_features)
        ):
            raise ValueError(f"{label} behavior coverage cumulative total is not contiguous")
        if cumulative.get("risk_contexts") != previous_risk_total + len(risk_contexts):
            raise ValueError(f"{label} risk coverage cumulative total is not contiguous")
        previous_behavior_total = cumulative["primary_behavior"]
        previous_risk_total = cumulative["risk_contexts"]
        if settlement_kind == "candidate_settlement":
            agent_input = _mapping(generation.get("agent_input"), f"{label} agent_input")
            execution_id = _non_empty_text(
                agent_input.get("execution_id"), f"{label} agent_input.execution_id"
            )
            episode = _mapping(generation.get("episode"), f"{label} episode")
            if episode.get("execution_id") != execution_id:
                raise ValueError(f"{label} agent_input and episode execution_id mismatch")
            _validate_episode(episode, label)
        else:
            if generation.get("agent_input") is not None or generation.get("episode") is not None:
                raise ValueError(f"{label} non-episode settlement must not contain an episode")


def _validate_snapshot(parsed: object) -> dict[str, Any]:
    snapshot = _mapping(parsed, "snapshot")
    if snapshot.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported coverage visualization snapshot")
    source = _mapping(snapshot.get("source"), "source")
    _non_empty_text(source.get("kind"), "source.kind")
    if source.get("kind") == "deterministic_fixture" and source.get("is_server_data") is not False:
        raise ValueError("deterministic fixture must explicitly declare is_server_data=false")
    if source.get("kind") == "server_campaign_archive":
        if source.get("is_server_data") is not True:
            raise ValueError("server Campaign archive must declare is_server_data=true")
        if source.get("integrity_status") != "verified_archive":
            raise ValueError("server Campaign archive must be verified")
        _non_empty_text(source.get("archive_sha256"), "source.archive_sha256")
        _non_empty_text(source.get("source_revision"), "source.source_revision")

    campaigns = _items(snapshot.get("campaigns"), "campaigns")
    if not campaigns:
        raise ValueError("coverage visualization snapshot contains no campaigns")
    campaign_ids: list[str] = []
    for raw_campaign in campaigns:
        campaign = _mapping(raw_campaign, "campaign")
        _validate_campaign(campaign)
        campaign_ids.append(campaign["id"])
    if len(set(campaign_ids)) != len(campaign_ids):
        raise ValueError("coverage visualization campaign ids are not unique")
    if snapshot.get("selected_campaign_id") not in campaign_ids:
        raise ValueError("selected_campaign_id does not name a campaign")
    return snapshot


def _load_snapshot(path: Path) -> tuple[bytes, str]:
    payload = path.read_bytes()
    _validate_snapshot(json.loads(payload))
    return payload, "sha256:" + hashlib.sha256(payload).hexdigest()


def _handler(root: Path, snapshot: Path):
    class CoverageUIHandler(BaseHTTPRequestHandler):
        server_version = "TraceGCoverageUI/2.0"

        def do_GET(self) -> None:  # noqa: N802
            request_path = urlparse(self.path).path
            if request_path == "/api/snapshot":
                self._serve_snapshot()
                return
            relative = "index.html" if request_path == "/" else unquote(request_path.lstrip("/"))
            target = (root / relative).resolve()
            try:
                target.relative_to(root)
            except ValueError:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            if not target.is_file():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self._serve_file(target)

        def _serve_snapshot(self) -> None:
            try:
                payload, digest = _load_snapshot(snapshot)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                body = json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8")
                self.send_response(HTTPStatus.SERVICE_UNAVAILABLE)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Snapshot-Digest", digest)
            self.end_headers()
            self.wfile.write(payload)

        def _serve_file(self, target: Path) -> None:
            payload = target.read_bytes()
            content_type, _ = mimetypes.guess_type(target.name)
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type or "application/octet-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, message: str, *args: object) -> None:
            print(f"coverage-ui: {message % args}")

    return CoverageUIHandler


def main() -> int:
    repository = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=repository / "coverage_ui" / "data" / "latest.json",
    )
    args = parser.parse_args()
    root = (repository / "coverage_ui").resolve()
    snapshot = args.snapshot.resolve()
    _load_snapshot(snapshot)
    server = ThreadingHTTPServer((args.host, args.port), _handler(root, snapshot))
    print(f"coverage-ui-url=http://{args.host}:{args.port}")
    print(f"coverage-ui-snapshot={snapshot}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
