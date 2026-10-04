"""One Episode, run or resumed, optionally killing itself at a named checkpoint.

This is the child process of the recovery matrix.  It exists as a separate process because the
recovery contract is about a process that dies: an exception would unwind cleanly and prove nothing,
so the only honest failure to inject here is `SIGKILL` at the moment a checkpoint has just been
written.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
for _candidate in (_HERE.parent, _HERE.parents[1] / "src", _HERE.parents[1] / "agent_image"):
    if str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))

from probe_error_capable_agent import _plan_for  # noqa: E402

from sandbox.scenarios.error_capable import TASK_FAMILY_SPECS  # noqa: E402
from sandbox.scenarios.error_capable_agent import (  # noqa: E402
    DiscoveryScriptedAgent,
    run_agent_episode,
)
from sandbox.scenarios.error_capable_identity import ModelIdentity  # noqa: E402
from sandbox.scenarios.error_capable_journal import JournalStore  # noqa: E402
from sandbox.scenarios.error_capable_registry import load_error_capable_fixture  # noqa: E402


class KillingJournalStore(JournalStore):
    """A store that dies of `SIGKILL` right after the nth checkpoint of a named phase."""

    def __init__(
        self, root: Path | str, episode_id: str, *, kill_at: str | None, kill_count: int
    ) -> None:
        super().__init__(root, episode_id)
        self.kill_at = kill_at
        self.kill_count = kill_count
        self.matched = 0
        self.observed: list[dict[str, object]] = []

    def write(self, journal):  # noqa: ANN001, ANN201 - mirrors the base signature
        if journal.phase.value == self.kill_at:
            self.matched += 1
        self.observed.append(
            {
                "phase": journal.phase.value,
                "occurrence": self.matched if journal.phase.value == self.kill_at else None,
                # What this checkpoint actually said was still to run.  Recorded here rather than
                # inferred later by the driver, because the driver's guess about which call was in
                # flight is exactly the kind of second-hand reasoning this test exists to avoid.
                "pending": [item.tool_name for item in journal.pending],
                "pending_accepted": [item.accepted for item in journal.pending],
                "issued": journal.issued,
                "settled": journal.settled,
                "transactions": len(journal.world_history),
            }
        )
        # Flushed before the journal itself, so the record survives the kill the same way the
        # checkpoint does.
        (self.root / "pending-log.json").write_text(
            json.dumps(self.observed, indent=2), encoding="utf-8"
        )
        path = super().write(journal)
        if self.kill_at and journal.phase.value == self.kill_at and self.matched >= self.kill_count:
            # After the write: the kill destroys everything after this boundary.
            os.kill(os.getpid(), signal.SIGKILL)
        return path


def _summarise(trace, store: JournalStore) -> dict[str, object]:  # noqa: ANN001
    """Everything two runs must agree on, read from the trace and the settled checkpoint."""

    journal = store.read()
    return {
        "episode_id": trace.episode_id,
        "stop_reason": trace.stop_reason,
        "unresolved": list(trace.unresolved),
        "turns": len(trace.turns),
        "steps": len(trace.steps),
        "issued": journal.issued,
        "calls": [
            [
                step.request.tool_name,
                None if step.result is None else step.result.status.value,
                step.request.accepted,
            ]
            for step in trace.steps
        ],
        "commits": sum(
            1
            for step in trace.steps
            if step.result is not None and step.result.state_transition is not None
        ),
        "final_state_digest": journal.world_state.canonical_digest(),
        "world_history": len(journal.world_history),
        "settled": journal.settled,
        "trace_digest": trace.trace_digest,
    }


async def _run(args: argparse.Namespace) -> dict[str, object]:
    from sandbox.scenarios.error_capable_registry import ERROR_CAPABLE_FIXTURE_ID

    fixture = load_error_capable_fixture(ERROR_CAPABLE_FIXTURE_ID)
    family_index = next(
        index
        for index, spec in enumerate(TASK_FAMILY_SPECS)
        if spec.task_family.value == args.task_family
    )
    plan, material = _plan_for(family_index, args.path_index, args.episode_id)
    adapter = DiscoveryScriptedAgent()
    identity = ModelIdentity.capture(
        provider_id="local",
        raw_model_label="scripted-agent",
        provider_version=adapter.version,
    )
    store = KillingJournalStore(
        args.root, args.episode_id, kill_at=args.kill_at, kill_count=args.kill_count
    )
    store.claim()
    try:
        trace = await run_agent_episode(
            fixture=fixture,
            plan=plan,
            material=material,
            adapter=adapter,
            model_identity=identity,
            seed=args.seed,
            max_tool_requests=args.max_tool_requests,
            journal=store,
            resume=args.resume,
        )
    finally:
        store.release()
    return _summarise(trace, store)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--episode-id", default="probe.summary_delivery.0")
    parser.add_argument("--task-family", default="summary_delivery")
    parser.add_argument("--path-index", type=int, default=0)
    parser.add_argument("--seed", type=int, default=20260930)
    parser.add_argument("--max-tool-requests", type=int, default=24)
    parser.add_argument(
        "--kill-at", default=None, help="phase to die after, or omitted to run to the end"
    )
    parser.add_argument("--kill-count", type=int, default=1)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    summary = asyncio.run(_run(args))
    encoded = json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(
        json.dumps(
            {
                key: summary[key]
                for key in ("stop_reason", "steps", "commits", "issued", "settled", "unresolved")
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
