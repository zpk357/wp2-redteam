#!/usr/bin/env python3
"""Create or verify the frozen Stage 6 Controller source identity."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sandbox.fuzzer.v2_stage6_identity import (
    Stage6ModelLock,
    Stage6RepairApplicationReceipt,
    Stage6RepairPlanLock,
)
from sandbox.fuzzer.v2_stage6_source_identity import (
    verify_source_tree_identity,
    write_source_tree_identity,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("--root", type=Path, required=True)
    create.add_argument("--source-revision", required=True)
    create.add_argument("--output", type=Path, required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--root", type=Path, required=True)
    verify.add_argument("--identity", type=Path, required=True)
    chain = commands.add_parser("chain")
    chain.add_argument("--model-lock", type=Path, required=True)
    chain.add_argument("--repair-plan", type=Path, required=True)
    chain.add_argument("--receipt", type=Path, required=True)
    chain.add_argument("--stage-record", type=Path, required=True)
    chain.add_argument("--root-stage", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "create":
        write_source_tree_identity(
            root=args.root,
            source_revision=args.source_revision,
            output=args.output,
        )
    elif args.command == "verify":
        verify_source_tree_identity(root=args.root, identity=args.identity)
    else:
        plan = Stage6RepairPlanLock.model_validate_json(args.repair_plan.read_bytes())
        receipt = Stage6RepairApplicationReceipt.model_validate_json(
            args.receipt.read_bytes()
        )
        lock = Stage6ModelLock.model_validate_json(args.model_lock.read_bytes())
        installed = json.loads(args.stage_record.read_text(encoding="utf-8"))
        root_stage = json.loads(args.root_stage.read_text(encoding="utf-8"))
        if installed != root_stage:
            raise ValueError("Stage 6 root and project stage records differ")
        if (
            installed.get("status") != "ready"
            or installed.get("source_revision") != plan.source_revision
            or installed.get("repair_lock_digest") != plan.lock_digest
            or receipt.repair_lock_digest != plan.lock_digest
            or receipt.active_model_lock_digest != lock.lock_digest
        ):
            raise ValueError("installed Stage 6 identity chain differs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
