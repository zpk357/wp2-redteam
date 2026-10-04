"""Reproduce the exploratory 80-episode grouping; never execute or rescore an episode."""

import argparse
import difflib
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

from sandbox.structured_v1.exposure import carried_content_digest, return_content_digest


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def analyze(root):
    hashes = {}

    def read(path):
        raw = path.read_bytes()
        hashes[str(path.relative_to(root))] = hashlib.sha256(raw).hexdigest()
        return json.loads(raw)

    historical = read(root / "g1-analysis.json")
    parent_of = {
        child: parent
        for parent, info in historical["parents"].items()
        for child in info["children"]
    }
    episodes, by_candidate, groups = [], defaultdict(list), defaultdict(list)
    for path in sorted((root / "finalized").glob("*.final.json")):
        final = read(path)
        bundle = final["container_bundle"]
        candidate, repeat = bundle["episode_id"].rsplit("-k", 1)
        old = read(root / "records-derived" / (bundle["episode_id"] + ".json"))
        assert old["final_bundle_digest"] == final["final_bundle_digest"]
        assert final["complete"] and not final["missing"] and old["bundle_ok"]
        decisions = bundle["model_decisions"]
        positions = {d["action_request_id"]: i + 1 for i, d in enumerate(decisions)}
        records = {r["transaction_id"]: r for r in bundle["records"]}
        returns = {r["tool_call_id"]: r for r in bundle["tool_returns"]}
        slots = {s["slot_id"]: "\n".join(s["contents"]) for s in bundle["material"]["slots"]}
        observations = []
        for exposure in bundle["exposures"]:
            slot = exposure["material"]["slot_id"]
            content = slots[slot]
            assert carried_content_digest(content) == exposure["material"]["carried_content_digest"]
            assert return_content_digest(content) == exposure["return_digest"]
            assert exposure["return_digest"] == returns[exposure["tool_call_id"]]["content_digest"]
            assert exposure["material"]["material_digest"] == bundle["material"]["material_digest"]
            position = positions[records[exposure["tool_call_id"]]["action_request_id"]]
            observations.append(
                {
                    "slot": slot,
                    "source": exposure["source_object_id"],
                    "field": exposure["source_field"],
                    "position": position,
                    "content": content,
                    "return_digest": exposure["return_digest"],
                }
            )
        assert len(observations) == 1 and observations[0]["position"] == 7
        assert observations[0]["slot"] == "s2" and len(decisions) == 8
        action = {"tool": decisions[7]["tool_name"], "arguments": decisions[7]["arguments"]}
        deliveries = [
            e
            for r in bundle["records"]
            for e in r["effects"]
            if e["committed"]
            and not e["blocked"]
            and any(a["principal"] == "partner.contact" and a["readable"] for a in e["audience"])
        ]
        delivered = bool(deliveries)
        assert delivered == old["required_delivery"]
        assert not delivered or (old["legitimate_task"] and old["delivery_clean"])
        row = {
            "episode": bundle["episode_id"],
            "candidate": candidate,
            "repeat": int(repeat),
            "observations": observations,
            "slots": slots,
            "inputs": [d["input_digest"] for d in decisions],
            "last_action": action,
            "delivered": delivered,
            "stop_reason": bundle["stop_reason"],
        }
        episodes.append(row)
        by_candidate[candidate].append(row)
        # Group actual consumed content, independent of candidate ID, material hash or result.
        groups[canonical([(e["source"], e["field"], e["content"]) for e in observations])].append(
            row
        )
    assert len(episodes) == 80 and len(by_candidate) == 16
    for rows in by_candidate.values():
        assert sorted(r["repeat"] for r in rows) == [1, 2, 3, 4, 5]
        assert len({canonical((r["slots"], r["inputs"], r["last_action"])) for r in rows}) == 1
    output_groups, group_for = [], {}
    for number, rows in enumerate(groups.values(), 1):
        gid = f"G{number:02}"
        candidates = sorted({r["candidate"] for r in rows})
        group_for.update(dict.fromkeys(candidates, gid))
        output_groups.append(
            {
                "group": gid,
                "candidates": candidates,
                "candidate_count": len(candidates),
                "episodes": len(rows),
                "observations": rows[0]["observations"],
                "distinct_next_inputs": len({r["inputs"][7] for r in rows}),
                "distinct_next_actions": len({canonical(r["last_action"]) for r in rows}),
                "next_action": rows[0]["last_action"],
                "delivery_episodes": sum(r["delivered"] for r in rows),
            }
        )
    pairs = []
    for child, parent in sorted(parent_of.items()):
        a, b = by_candidate[parent][0], by_candidate[child][0]
        changed = sorted(s for s in a["slots"] if a["slots"][s] != b["slots"][s])
        consumed = {e["slot"] for e in b["observations"]}
        visible_change = sorted(consumed.intersection(changed))
        same_content = group_for[parent] == group_for[child]
        assert bool(visible_change) != same_content
        pairs.append(
            {
                "parent": parent,
                "child": child,
                "changed_slots": changed,
                "changed_slots_consumed": visible_change,
                "classification": "changed-content-read"
                if visible_change
                else "only-unread-change",
                "same_observed_content": same_content,
                "parent_group": group_for[parent],
                "child_group": group_for[child],
                "same_next_input": a["inputs"][7] == b["inputs"][7],
                "same_next_action": a["last_action"] == b["last_action"],
                "parent_action": a["last_action"],
                "child_action": b["last_action"],
                "parent_delivery_episodes": sum(r["delivered"] for r in by_candidate[parent]),
                "child_delivery_episodes": sum(r["delivered"] for r in by_candidate[child]),
                "slot_diffs": {
                    s: list(
                        difflib.unified_diff(
                            a["slots"][s].splitlines(),
                            b["slots"][s].splitlines(),
                            fromfile=parent,
                            tofile=child,
                            n=1,
                            lineterm="",
                        )
                    )
                    for s in changed
                },
            }
        )
    # Input archives are read-only; verify every source remains byte-identical.
    assert all(
        hashlib.sha256((root / name).read_bytes()).hexdigest() == h for name, h in hashes.items()
    )
    return {
        "status": "EXPLORATORY_ONLY_NOT_G1_VERDICT",
        "source_directory": str(root),
        "grouping": (
            "Exact reconstructed bound return content plus source/field; "
            "verified against both recorded hashes. Full payload not reconstructed."
        ),
        "delivery_basis": (
            "Original committed nonblocked audience effect, cross-checked with archived "
            "required_delivery/legitimate_task/delivery_clean; no new Oracle or MBR."
        ),
        "episodes": len(episodes),
        "candidates": len(by_candidate),
        "content_groups": len(groups),
        "classification_counts": dict(Counter(p["classification"] for p in pairs)),
        "model_input_cardinality": [len({r["inputs"][i] for r in episodes}) for i in range(8)],
        "groups": output_groups,
        "parent_child_pairs": pairs,
        "source_sha256": hashes,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(args.source.resolve()):
        parser.error("output must be outside the source archive")
    result = analyze(args.source)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print(
        {
            k: result[k]
            for k in ("episodes", "candidates", "content_groups", "classification_counts")
        }
    )
