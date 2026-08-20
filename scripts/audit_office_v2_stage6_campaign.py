#!/usr/bin/env python3
"""Validate and archive one Office V2 Stage 6 server Campaign."""

from __future__ import annotations

import argparse
from pathlib import Path

from sandbox.fuzzer.v2_stage6_evidence import (
    build_stage6_evidence_archive,
    verify_stage6_evidence_archive,
    write_stage6_milestone,
    write_two_generation_gate,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    gate = subparsers.add_parser("two-generation-gate")
    gate.add_argument("--db", type=Path, required=True)
    gate.add_argument("--campaign-id", required=True)
    gate.add_argument("--output", type=Path, required=True)

    milestone = subparsers.add_parser("milestone-gate")
    milestone.add_argument("--db", type=Path, required=True)
    milestone.add_argument("--campaign-id", required=True)
    milestone.add_argument("--target-generation", type=int, required=True)
    milestone.add_argument("--output", type=Path, required=True)

    archive = subparsers.add_parser("archive")
    archive.add_argument("--campaign-id", required=True)
    archive.add_argument("--outcome", choices=("success", "failure"), required=True)
    archive.add_argument("--campaign-root", type=Path, required=True)
    archive.add_argument("--result-root", type=Path, required=True)
    archive.add_argument("--model-lock", type=Path, required=True)
    archive.add_argument("--bootstrap", type=Path, required=True)
    archive.add_argument("--preflight", type=Path, required=True)
    archive.add_argument("--repair-receipt", type=Path, required=True)
    archive.add_argument("--server-host", type=Path, required=True)
    archive.add_argument("--gpu-residency", type=Path, required=True)
    archive.add_argument("--output", type=Path, required=True)

    verify = subparsers.add_parser("verify-archive")
    verify.add_argument("--archive", type=Path, required=True)

    args = parser.parse_args()
    if args.command == "two-generation-gate":
        report = write_two_generation_gate(
            db_path=args.db, campaign_id=args.campaign_id, output=args.output
        )
        return 0 if report["passed"] else 1
    if args.command == "milestone-gate":
        report = write_stage6_milestone(
            db_path=args.db,
            campaign_id=args.campaign_id,
            target_generation=args.target_generation,
            output=args.output,
        )
        return 0 if report["passed"] else 1
    if args.command == "archive":
        build_stage6_evidence_archive(
            campaign_id=args.campaign_id,
            outcome=args.outcome,
            campaign_root=args.campaign_root,
            result_root=args.result_root,
            model_lock=args.model_lock,
            bootstrap=args.bootstrap,
            preflight=args.preflight,
            repair_receipt=args.repair_receipt,
            server_host=args.server_host,
            gpu_residency=args.gpu_residency,
            output=args.output,
        )
    else:
        verify_stage6_evidence_archive(args.archive)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
