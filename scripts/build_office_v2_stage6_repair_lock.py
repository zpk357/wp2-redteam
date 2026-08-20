#!/usr/bin/env python3
"""Plan or seal the offline Office V2 Stage 6 repair layer."""

from __future__ import annotations

import argparse
import hashlib
import subprocess
from pathlib import Path

from sandbox.fuzzer.v2_stage6_identity import (
    Stage6AppliedRoleIdentity,
    Stage6ModelLock,
    Stage6RepairFileIdentity,
    Stage6RepairPlanLock,
    Stage6Role,
    seal_repair_application_receipt,
    seal_repair_plan_lock,
    seal_repair_role_plan,
    seal_role_identity,
    seal_stage6_model_lock,
)
from sandbox.replay.digests import sha256_digest

ROLE_SPECS = (
    {
        "role": "agent",
        "base_image_reference": (
            "trace-g-office-v2-agent-qwen:step6-baseline-20260819"
        ),
        "final_image_reference": "trace-g-office-v2-agent-qwen:step6-repair-core-v3",
        "dockerfile": "agent_image/Dockerfile.qwen-agent-repair",
        "copied_files": (
            "agent_image/app/agent_qwen_bootstrap.py",
            "agent_image/app/replay/replay_adapter.py",
        ),
    },
    {
        "role": "mutator",
        "base_image_reference": (
            "trace-g-office-v2-mutator-qwen:step6-baseline-20260819"
        ),
        "final_image_reference": (
            "trace-g-office-v2-mutator-qwen:step6-repair-core-v3"
        ),
        "dockerfile": "agent_image/Dockerfile.qwen-mutator-repair",
        "copied_files": (
            "agent_image/app/agent_qwen_bootstrap.py",
            "agent_image/app/office_v2_mutator_worker.py",
            "src/sandbox/ollama_schema.py",
            "src/sandbox/mutation/__init__.py",
            "src/sandbox/coverage/__init__.py",
        ),
    },
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _bytes_sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _image_id(reference: str) -> str:
    result = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", reference],
        check=True,
        capture_output=True,
        text=True,
    )
    value = result.stdout.strip().lower()
    if not value.startswith("sha256:") or len(value) != 71:
        raise ValueError(f"invalid Docker image ID for {reference}")
    return value


def _git_blob(repository: Path, revision: str, path: str) -> bytes:
    return subprocess.run(
        ["git", "show", f"{revision}:{path}"],
        cwd=repository,
        check=True,
        capture_output=True,
    ).stdout


def _plan(args: argparse.Namespace) -> int:
    repository = args.repository.resolve()
    base_lock = Stage6ModelLock.model_validate_json(args.base_model_lock.read_bytes())
    if args.model_digest != base_lock.manifest_digest:
        raise ValueError("repair model digest differs from the validated base lock")
    if args.controller_image != base_lock.controller_image_reference:
        raise ValueError("repair controller reference differs from the validated base lock")
    roles = []
    for spec in ROLE_SPECS:
        dockerfile = str(spec["dockerfile"])
        copied_files = tuple(str(item) for item in spec["copied_files"])
        roles.append(
            seal_repair_role_plan(
                role=Stage6Role(str(spec["role"])),
                base_image_reference=spec["base_image_reference"],
                base_image_id=_image_id(str(spec["base_image_reference"])),
                final_image_reference=spec["final_image_reference"],
                dockerfile=dockerfile,
                dockerfile_sha256=_bytes_sha256(
                    _git_blob(repository, args.revision, dockerfile)
                ),
                copied_files=tuple(
                    Stage6RepairFileIdentity(
                        path=path,
                        sha256=_bytes_sha256(
                            _git_blob(repository, args.revision, path)
                        ),
                    )
                    for path in copied_files
                ),
            )
        )
    lock = seal_repair_plan_lock(
        source_revision=args.revision,
        source_archive_sha256=_sha256(args.source_archive),
        source_archive_bytes=args.source_archive.stat().st_size,
        model_digest=args.model_digest,
        base_model_lock_digest=base_lock.lock_digest,
        controller_image_reference=args.controller_image,
        controller_image_id=_image_id(args.controller_image),
        roles=tuple(roles),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(lock.model_dump_json(indent=2) + "\n", encoding="utf-8")
    print(lock.lock_digest)
    return 0


def _seal(args: argparse.Namespace) -> int:
    plan = Stage6RepairPlanLock.model_validate_json(args.repair_lock.read_bytes())
    base_lock = Stage6ModelLock.model_validate_json(args.base_model_lock.read_bytes())
    if plan.base_model_lock_digest != base_lock.lock_digest:
        raise ValueError("repair plan and base model lock differ")
    if plan.model_digest != base_lock.manifest_digest:
        raise ValueError("repair plan and model manifest differ")
    if _image_id(plan.controller_image_reference) != plan.controller_image_id:
        raise ValueError("controller image ID differs from repair plan")

    base_roles = {item.role: item for item in base_lock.roles}
    active_roles = []
    applied_roles = []
    for role_plan in plan.roles:
        if _image_id(role_plan.base_image_reference) != role_plan.base_image_id:
            raise ValueError(f"{role_plan.role.value} base image ID differs")
        final_image_id = _image_id(role_plan.final_image_reference)
        build_receipt_digest = sha256_digest(
            {
                "repair_lock_digest": plan.lock_digest,
                "role_plan_digest": role_plan.role_plan_digest,
                "image_reference": role_plan.final_image_reference,
                "image_id": final_image_id,
            }
        )
        base_role = base_roles[role_plan.role]
        values = base_role.model_dump(
            exclude={
                "image_reference",
                "image_id",
                "image_archive_sha256",
                "image_build_receipt_digest",
                "inference",
                "role_digest",
            }
        )
        active_roles.append(
            seal_role_identity(
                **values,
                image_reference=role_plan.final_image_reference,
                image_id=final_image_id,
                image_build_receipt_digest=build_receipt_digest,
                inference=base_role.inference,
            )
        )
        applied_roles.append(
            Stage6AppliedRoleIdentity(
                role=role_plan.role,
                image_reference=role_plan.final_image_reference,
                image_id=final_image_id,
                image_build_receipt_digest=build_receipt_digest,
            )
        )
    values = base_lock.model_dump(
        exclude={
            "schema_version",
            "plan_digest",
            "campaign_identity_digest",
            "roles",
            "lock_digest",
        }
    )
    active_lock = seal_stage6_model_lock(**values, roles=tuple(active_roles))
    receipt = seal_repair_application_receipt(
        repair_lock_digest=plan.lock_digest,
        active_model_lock_digest=active_lock.lock_digest,
        roles=tuple(applied_roles),
    )
    args.model_lock_output.parent.mkdir(parents=True, exist_ok=True)
    args.model_lock_output.write_text(
        active_lock.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    args.receipt_output.parent.mkdir(parents=True, exist_ok=True)
    args.receipt_output.write_text(
        receipt.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    print(active_lock.lock_digest)
    print(receipt.receipt_digest)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan")
    plan.add_argument("--repository", type=Path, required=True)
    plan.add_argument("--revision", required=True)
    plan.add_argument("--source-archive", type=Path, required=True)
    plan.add_argument("--model-digest", required=True)
    plan.add_argument("--controller-image", required=True)
    plan.add_argument("--base-model-lock", type=Path, required=True)
    plan.add_argument("--output", type=Path, required=True)
    plan.set_defaults(handler=_plan)

    seal = commands.add_parser("seal")
    seal.add_argument("--repair-lock", type=Path, required=True)
    seal.add_argument("--base-model-lock", type=Path, required=True)
    seal.add_argument("--model-lock-output", type=Path, required=True)
    seal.add_argument("--receipt-output", type=Path, required=True)
    seal.set_defaults(handler=_seal)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
