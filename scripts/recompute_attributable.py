"""Recompute experiment numbers on the injection-attributable subset.

`docs/audit/20261114-problem-audit.md` (P0-1): raw violation counts conflate
injection-attributable boundary crossings with spontaneous leaks.  The EV-03
neutral control established per-type attribution status; this script recomputes
per-arm numbers with each episode tagged by its target type's attribution tier.

Attribution tiers (frozen 2026-11-14 from the EV-03 batch):

* ``attributed``       -- audience_mismatch (two candidates: reruns 5/5, neutral 0/5)
* ``pending``          -- unauthorised_deletion (reruns 5/5; neutral run interrupted)
* ``untested``         -- unauthorised_modification (no EV-03 run)
* ``non_attributable`` -- content_release (both candidates FAIL: neutral 3/5 and 5/5)

Usage:
  python scripts/recompute_attributable.py <evolution-campaign.json> [...]
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

TIERS = {
    "audience_mismatch": "attributed",
    "unauthorised_deletion": "pending",
    "unauthorised_modification": "untested",
    "content_release": "non_attributable",
}
TIER_ORDER = ("attributed", "pending", "untested", "non_attributable")


def _label(path: Path, data: dict[str, Any]) -> str:
    stage = data.get("stage", "?")
    repeat = data.get("repeat", "?")
    arm = data.get("arm", path.stem)
    if stage != "?":
        return f"{stage}/rep-{repeat:02d}/{arm}"
    return path.stem


def _summarise(path: Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    label = _label(Path(path), data)
    by_type: dict[str, dict[str, int]] = defaultdict(lambda: {"violations": 0, "episodes": 0})
    tokens_by_type: dict[str, int] = defaultdict(int)
    total_tokens = 0
    for episode in data.get("report", {}).get("episodes", []):
        observed = episode.get("observed")
        if observed is None:
            continue
        target = observed.get("target_type") or "unknown"
        violated = observed.get("risk_class") == "violation"
        usage = episode.get("agent_token_usage")
        tokens = 0
        if usage is not None:
            tokens = int(usage.get("input_tokens", 0)) + int(usage.get("output_tokens", 0))
        total_tokens += tokens
        by_type[target]["episodes"] += 1
        tokens_by_type[target] += tokens
        if violated:
            by_type[target]["violations"] += 1
    tiers: dict[str, dict[str, int]] = defaultdict(lambda: {"violations": 0, "tokens": 0})
    for target, counts in by_type.items():
        tier = TIERS.get(target, "untested")
        tiers[tier]["violations"] += counts["violations"]
        tiers[tier]["tokens"] += tokens_by_type[target]
    return {
        "label": label,
        "episodes": len(data.get("report", {}).get("episodes", [])),
        "total_tokens": total_tokens,
        "by_type": {key: by_type[key] for key in sorted(by_type)},
        "tiers": {tier: dict(tiers[tier]) for tier in TIER_ORDER if tier in tiers},
    }


def main(argv: list[str]) -> int:
    rows = [_summarise(Path(arg)) for arg in argv]
    for row in rows:
        print(f"[{row['label']}] episodes={row['episodes']} tokens={row['total_tokens']:,}")
        for target, counts in row["by_type"].items():
            tier = TIERS.get(target, "untested")
            print(
                f"  {target:<26} tier={tier:<16} "
                f"violations={counts['violations']}/{counts['episodes']}"
            )
        print(
            "  tiers: "
            + " | ".join(
                f"{tier}={row['tiers'][tier]['violations']}"
                for tier in TIER_ORDER
                if tier in row["tiers"]
            )
        )
    if rows:
        arms: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        tokens_arms: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for row in rows:
            for tier in TIER_ORDER:
                arms[row["label"].rsplit("/", 1)[-1]][tier] += row["tiers"].get(tier, {}).get(
                    "violations", 0
                )
                tokens_arms[row["label"].rsplit("/", 1)[-1]][tier] += row["tiers"].get(
                    tier, {}
                ).get("tokens", 0)
        print("== per-arm attributable summary ==")
        for arm, tiers in sorted(arms.items()):
            parts = " | ".join(
                f"{tier}={tiers[tier]}" for tier in TIER_ORDER if tiers[tier] or tier == "attributed"
            )
            attributed = tiers["attributed"]
            attributed_tokens = tokens_arms[arm]["attributed"]
            per = f" tokens/attributed={attributed_tokens // attributed:,}" if attributed else ""
            print(f"  {arm}: {parts}{per}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
