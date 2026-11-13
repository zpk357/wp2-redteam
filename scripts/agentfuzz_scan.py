"""Scan a declared tool set for sinks, dependencies and candidate chains.

Reads only.  No model, no container, no Episode.  The output is the chain pool
that later stages test, together with every edge rationale needed to audit it.

    python scripts/agentfuzz_scan.py
    python scripts/agentfuzz_scan.py --max-length 4 --out runs/my-scan
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

from agentfuzz.analysis import build_graph, extract_chains, extract_toolset, scan_sinks
from agentfuzz.contracts import dumps

REPO_ROOT = Path(__file__).resolve().parents[1]


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--toolset",
        default="agentfuzz.toolset",
        help="module that declares TOOL_METADATA (default: agentfuzz.toolset)",
    )
    parser.add_argument("--min-length", type=int, default=2)
    parser.add_argument("--max-length", type=int, default=5)
    parser.add_argument("--max-chains", type=int, default=256)
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="output directory (default: runs/agentfuzz-scan/<utc stamp>)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    module = __import__(args.toolset, fromlist=["TOOL_METADATA"])

    extraction = extract_toolset(module)
    tools = tuple(contract.name for contract in extraction.contracts)
    sinks = scan_sinks(extraction.source_path, tools)
    graph = build_graph(extraction.contracts)
    pool = extract_chains(
        graph,
        sinks,
        min_length=args.min_length,
        max_length=args.max_length,
        max_chains=args.max_chains,
    )

    out_dir = args.out or _default_out_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "toolset": {
            "module": extraction.module_name,
            "source_path": extraction.source_path,
            "tools": [contract.to_dict() for contract in extraction.contracts],
        },
        "sinks": [sink.to_dict() for sink in sinks],
        "graph": graph.to_dict(),
        "chains": pool.to_dict(),
    }
    (out_dir / "scan.json").write_text(dumps(payload) + "\n", encoding="utf-8")

    _print_report(extraction.contracts, sinks, graph, pool, out_dir)
    return 0 if pool.chains else 1


def _default_out_dir() -> Path:
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    return REPO_ROOT / "runs" / "agentfuzz-scan" / stamp


def _print_report(contracts, sinks, graph, pool, out_dir: Path) -> None:
    print(f"toolset   {len(contracts)} tools")
    print(f"sinks     {len(sinks)} tools")
    for sink in sinks:
        types = ", ".join(kind.value for kind in sink.sink_types)
        apis = ", ".join(sorted({site.api for site in sink.sites}))
        print(f"  {sink.tool:22s} {types:6s} via {apis}")

    summary = graph.summary()
    print(
        f"graph     {summary['edges']} edges "
        f"({summary['direct_edges']} direct, {summary['indirect_edges']} indirect)"
    )

    chain_summary = pool.summary()
    print(
        f"chains    {chain_summary['chains']} "
        f"(lengths {chain_summary['by_length']}, "
        f"sinks {chain_summary['by_sink_type']}, "
        f"carrying persisted artifact {chain_summary['chains_carrying_a_persisted_artifact']})"
    )
    for chain in pool.chains:
        types = ",".join(kind.value for kind in chain.sink_types)
        print(f"  [{chain.length}] {chain.identifier()}  -> {types}")

    print(f"written   {out_dir / 'scan.json'}")


if __name__ == "__main__":
    sys.exit(main())
