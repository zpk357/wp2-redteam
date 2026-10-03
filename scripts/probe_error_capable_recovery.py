"""Force-kill a real Episode at every recovery boundary and check what resuming produces.

The matrix runs a child process per case, kills it with `SIGKILL` immediately after a named
checkpoint, resumes it, and compares the resumed Episode with an uninterrupted baseline.

What "the same" means here: the same calls in the same order with the same results, the same number
of commits, the same final world digest and the same consumed cursor.  A resumed run must not
re-ask the model for a call whose result is already on disk, and must not add a second commit.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
for _candidate in (_HERE.parent, _HERE.parents[1] / "src", _HERE.parents[1] / "agent_image"):
    if str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))

from sandbox.scenarios.error_capable_agent import tool_writes_state  # noqa: E402
from sandbox.scenarios.error_capable_journal import (  # noqa: E402
    JournalCorruptError,
    JournalIdentityError,
    JournalStore,
)

CHILD = str(_HERE.parent / "run_error_capable_resumable.py")
EPISODE_ID = "probe.summary_delivery.0"
#: Every boundary the recovery contract names.  `settled` is included because a kill after settling
#: must not let a resume execute anything at all.
KILL_PHASES = ("awaiting-model", "model-returned", "before-call", "after-call", "settled")


def _child(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, CHILD, *args],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        check=False,
    )


def _summary(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def run_matrix(workdir: Path, kill_counts: int) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []

    baseline_root = workdir / "baseline"
    baseline_out = baseline_root / "summary.json"
    baseline_root.mkdir(parents=True, exist_ok=True)
    done = _child(["--root", str(baseline_root), "--output", str(baseline_out)])
    if done.returncode != 0:
        raise SystemExit(f"baseline failed: {done.stderr[-2000:]}")
    baseline = _summary(baseline_out)
    accepted = [call for call in baseline["calls"] if call[2]]

    print(f"baseline: steps={baseline['steps']} commits={baseline['commits']} "
          f"issued={baseline['issued']} stop={baseline['stop_reason']}")

    for phase in KILL_PHASES:
        for count in range(1, kill_counts + 1):
            case = f"{phase}#{count}"
            root = workdir / f"kill-{phase}-{count}"
            root.mkdir(parents=True, exist_ok=True)
            killed_out = root / "killed.json"
            killed = _child(
                [
                    "--root", str(root),
                    "--kill-at", phase,
                    "--kill-count", str(count),
                    "--output", str(killed_out),
                ]
            )
            if killed.returncode == 0 and not killed_out.exists():
                results.append({"case": case, "verdict": "SKIPPED",
                                "note": "phase occurred fewer times than the requested count"})
                continue
            if killed.returncode == 0:
                results.append({"case": case, "verdict": "SKIPPED",
                                "note": "phase did not occur"})
                continue
            if killed_out.exists():
                results.append({"case": case, "verdict": "FAIL",
                                "note": "the killed run still wrote its final summary"})
                continue

            # Taken now, before anything resumes: the resume writes to the same log, so reading it
            # later would mix the interrupted run's checkpoints with the resumed run's.
            log_at_kill = json.loads((root / "pending-log.json").read_text(encoding="utf-8"))

            resumed_out = root / "resumed.json"
            resumed = _child(
                ["--root", str(root), "--resume", "--output", str(resumed_out)]
            )
            if resumed.returncode != 0:
                results.append({"case": case, "verdict": "FAIL",
                                "note": f"resume failed: {resumed.stderr[-400:]}"})
                continue
            got = _summary(resumed_out)

            # What the killed checkpoint said was still to run, taken from the child's own record of
            # the checkpoint it wrote rather than guessed from the baseline's call list.
            at_kill = next(
                (
                    item
                    for item in reversed(log_at_kill)
                    if item["phase"] == phase and item["occurrence"] == count
                ),
                None,
            )
            if at_kill is None:
                results.append({"case": case, "verdict": "FAIL",
                                "note": f"the killed run left no record of a {phase}#{count} checkpoint"})
                continue
            pending = list(at_kill["pending"])
            pending_side_effecting = bool(pending) and tool_writes_state(pending[0])

            if pending_side_effecting and not at_kill["settled"]:
                # A call that may have committed was in flight.  The resumed run must stop and say
                # so rather than match the baseline, and the world must not move past the checkpoint
                # it resumed from.
                ok = (
                    got["stop_reason"] == "recovery-uncertain-commit"
                    and bool(got["unresolved"])
                    and got["world_history"] == at_kill["transactions"]
                )
                results.append(
                    {
                        "case": case,
                        "verdict": "PASS" if ok else "FAIL",
                        "note": "a call that may have committed was in flight: refused and recorded as"
                        " unresolved, with nothing re-sent",
                        "pending": pending,
                        "stop_reason": got["stop_reason"],
                        "transactions_at_kill": at_kill["transactions"],
                        "transactions_after_resume": got["world_history"],
                        "unresolved": got["unresolved"],
                    }
                )
            else:
                differences = [
                    key
                    for key in ("calls", "commits", "final_state_digest", "issued",
                                "stop_reason", "world_history", "settled")
                    if got[key] != baseline[key]
                ]
                results.append(
                    {
                        "case": case,
                        "verdict": "PASS" if not differences else "FAIL",
                        "note": "resumed Episode matches the uninterrupted baseline"
                        if not differences
                        else f"differs on {differences}",
                        "got": {key: got[key] for key in differences},
                        "want": {key: baseline[key] for key in differences},
                    }
                )

            # A second resume must add nothing at all.
            twice_out = root / "resumed-twice.json"
            twice = _child(["--root", str(root), "--resume", "--output", str(twice_out)])
            if twice.returncode != 0:
                results.append({"case": f"{case}/twice", "verdict": "FAIL",
                                "note": twice.stderr[-300:]})
            else:
                again = _summary(twice_out)
                stable = again == got
                results.append(
                    {
                        "case": f"{case}/twice",
                        "verdict": "PASS" if stable else "FAIL",
                        "note": "a second resume changed nothing" if stable
                        else "a second resume changed the Episode",
                    }
                )
    return results


def run_refusals(workdir: Path) -> list[dict[str, object]]:
    """A corrupted checkpoint, a moved plan and a second live writer must all be refused."""

    results: list[dict[str, object]] = []
    root = workdir / "refusals"
    root.mkdir(parents=True, exist_ok=True)
    out = root / "summary.json"
    done = _child(["--root", str(root), "--output", str(out)])
    if done.returncode != 0:
        raise SystemExit(f"refusal baseline failed: {done.stderr[-2000:]}")

    store = JournalStore(root, EPISODE_ID)
    good = store.read()

    # Corruption: a truncated file must be refused, not interpreted.
    store.path.write_text(store.path.read_text(encoding="utf-8")[:-40], encoding="utf-8")
    try:
        store.read()
        results.append({"case": "corrupt-checkpoint", "verdict": "FAIL", "note": "accepted"})
    except JournalCorruptError as error:
        results.append({"case": "corrupt-checkpoint", "verdict": "PASS", "note": str(error)[:160]})

    # A tampered field with a stale digest must be refused too.
    store.write(good)
    payload = json.loads(store.path.read_text(encoding="utf-8"))
    payload["issued"] = payload["issued"] + 1
    store.path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    try:
        store.read()
        results.append({"case": "tampered-checkpoint", "verdict": "FAIL", "note": "accepted"})
    except JournalCorruptError as error:
        results.append({"case": "tampered-checkpoint", "verdict": "PASS", "note": str(error)[:160]})

    # A checkpoint that describes a different plan must name the fields that differ.
    store.write(good)
    moved = _child(
        ["--root", str(root), "--resume", "--path-index", "1", "--output", str(root / "moved.json")]
    )
    refused = "does not describe this Episode" in moved.stderr or "moved" in moved.stderr
    results.append(
        {
            "case": "mismatched-plan",
            "verdict": "PASS" if moved.returncode != 0 and refused else "FAIL",
            "note": moved.stderr.strip().splitlines()[-1][:200] if moved.stderr else "no error",
        }
    )

    # A second live writer must be refused.  The lock is written by this process, which is alive.
    store.claim()
    try:
        JournalStore(root, EPISODE_ID).claim()
        results.append({"case": "duplicate-writer-same-pid", "verdict": "PASS",
                        "note": "re-entrant claim by the owning process is allowed by design"})
    except Exception as error:  # noqa: BLE001
        results.append({"case": "duplicate-writer-same-pid", "verdict": "FAIL", "note": str(error)})
    store.release()
    # A live process other than this one, spawned here.  The parent process would not do: the driver
    # is launched detached, so by the time this runs its parent has usually exited, and a liveness
    # check that correctly said "gone" would look like a failure.
    holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    foreign = JournalStore(root, EPISODE_ID)
    foreign.lock_path.write_text(f"{holder.pid}\n", encoding="utf-8")
    try:
        foreign.claim()
        results.append({"case": "duplicate-writer-live-pid", "verdict": "FAIL",
                        "note": "a live foreign writer was not refused"})
    except Exception as error:  # noqa: BLE001
        results.append({"case": "duplicate-writer-live-pid", "verdict": "PASS",
                        "note": str(error)[:160]})
    finally:
        holder.kill()
        holder.wait(timeout=10)
        foreign.lock_path.unlink(missing_ok=True)
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--kill-counts", type=int, default=3)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    args.workdir.mkdir(parents=True, exist_ok=True)
    results = run_matrix(args.workdir, args.kill_counts) + run_refusals(args.workdir)

    passed = sum(1 for item in results if item["verdict"] == "PASS")
    skipped = sum(1 for item in results if item["verdict"] == "SKIPPED")
    failed = [item for item in results if item["verdict"] == "FAIL"]
    for item in results:
        print(f"  [{item['verdict']:>7}] {item['case']:<28} {item.get('note', '')}")
    print(f"\nPASS {passed}  SKIPPED {skipped}  FAIL {len(failed)}")

    payload = {"results": results, "passed": passed, "skipped": skipped, "failed": len(failed)}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8"
        )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
