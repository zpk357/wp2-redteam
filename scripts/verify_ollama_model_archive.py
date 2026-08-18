#!/usr/bin/env python3
"""Verify the content-addressed closure of an offline Ollama model archive."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tarfile
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

DIGEST_PATTERN = re.compile(r"^sha256:([0-9a-f]{64})$")
BLOB_NAME_PATTERN = re.compile(r"^blobs/sha256-([0-9a-f]{64})$")


class ModelArchiveVerificationError(RuntimeError):
    """The archive cannot deterministically restore the locked Ollama model."""


def _normalized_member_name(raw_name: str) -> str:
    name = raw_name.replace("\\", "/")
    while name.startswith("./"):
        name = name[2:]
    path = PurePosixPath(name)
    if not name or path.is_absolute() or ".." in path.parts:
        raise ModelArchiveVerificationError(f"unsafe archive member: {raw_name}")
    return str(path)


def _read_member(archive: tarfile.TarFile, member: tarfile.TarInfo) -> bytes:
    source = archive.extractfile(member)
    if source is None:
        raise ModelArchiveVerificationError(f"unable to read archive member: {member.name}")
    with source:
        return source.read()


def _sha256_stream(source: BinaryIO) -> str:
    digest = hashlib.sha256()
    while chunk := source.read(1024 * 1024):
        digest.update(chunk)
    return digest.hexdigest()


def _manifest_path(model_name: str) -> str:
    if not model_name or model_name.count(":") > 1:
        raise ModelArchiveVerificationError("model lock has an invalid model_name")
    repository, separator, tag = model_name.rpartition(":")
    if not separator:
        repository, tag = model_name, "latest"
    repository_parts = repository.split("/")
    if any(not part or part in {".", ".."} for part in repository_parts) or not tag:
        raise ModelArchiveVerificationError("model lock has an invalid model_name")
    if len(repository_parts) == 1:
        repository_parts.insert(0, "library")
    return "/".join(("manifests", "registry.ollama.ai", *repository_parts, tag))


def _descriptor_digest(descriptor: object, *, label: str) -> tuple[str, int | None]:
    if not isinstance(descriptor, dict):
        raise ModelArchiveVerificationError(f"manifest {label} descriptor is invalid")
    raw_digest = descriptor.get("digest")
    match = DIGEST_PATTERN.fullmatch(str(raw_digest))
    if match is None:
        raise ModelArchiveVerificationError(f"manifest {label} digest is invalid")
    size = descriptor.get("size")
    if size is not None and (not isinstance(size, int) or isinstance(size, bool) or size < 0):
        raise ModelArchiveVerificationError(f"manifest {label} size is invalid")
    return match.group(1), size


def verify_model_archive(archive_path: Path, lock: dict[str, Any]) -> dict[str, Any]:
    if lock.get("schema_version") != "1.0":
        raise ModelArchiveVerificationError("model lock schema_version must be 1.0")
    model_name = lock.get("model_name")
    if not isinstance(model_name, str):
        raise ModelArchiveVerificationError("model lock has no model_name")
    manifest_digest = lock.get("model_digest")
    manifest_match = DIGEST_PATTERN.fullmatch(str(manifest_digest))
    if manifest_match is None:
        raise ModelArchiveVerificationError("model lock has no valid model_digest")

    try:
        archive = tarfile.open(archive_path, mode="r:*")  # noqa: SIM115
    except (OSError, tarfile.TarError) as exc:
        raise ModelArchiveVerificationError(
            f"unable to open model archive: {archive_path}"
        ) from exc

    with archive:
        members: dict[str, tarfile.TarInfo] = {}
        for member in archive.getmembers():
            name = _normalized_member_name(member.name)
            if member.isdir():
                continue
            if not member.isfile():
                raise ModelArchiveVerificationError(
                    f"model archive member is not a regular file: {member.name}"
                )
            if name in members:
                raise ModelArchiveVerificationError(f"duplicate archive member: {name}")
            members[name] = member

        expected_manifest_path = _manifest_path(model_name)
        manifest_member = members.get(expected_manifest_path)
        if manifest_member is None:
            raise ModelArchiveVerificationError(
                f"model archive is missing manifest: {expected_manifest_path}"
            )
        manifest_raw = _read_member(archive, manifest_member)
        observed_manifest_digest = hashlib.sha256(manifest_raw).hexdigest()
        if observed_manifest_digest != manifest_match.group(1):
            raise ModelArchiveVerificationError(
                "model manifest digest does not match the locked model_digest"
            )
        try:
            manifest = json.loads(manifest_raw)
        except (UnicodeError, ValueError) as exc:
            raise ModelArchiveVerificationError("model manifest is invalid JSON") from exc
        if not isinstance(manifest, dict):
            raise ModelArchiveVerificationError("model manifest must be a JSON object")

        descriptors = [("config", manifest.get("config"))]
        layers = manifest.get("layers")
        if not isinstance(layers, list) or not layers:
            raise ModelArchiveVerificationError("model manifest has no layers")
        descriptors.extend((f"layer[{index}]", layer) for index, layer in enumerate(layers))

        blob_hashes: dict[str, str] = {}
        for name, member in members.items():
            match = BLOB_NAME_PATTERN.fullmatch(name)
            if match is None:
                continue
            source = archive.extractfile(member)
            if source is None:
                raise ModelArchiveVerificationError(f"unable to read archive blob: {name}")
            with source:
                observed = _sha256_stream(source)
            if observed != match.group(1):
                raise ModelArchiveVerificationError(
                    f"model blob content digest does not match its filename: {name}"
                )
            blob_hashes[match.group(1)] = observed

        referenced: set[str] = set()
        descriptor_locks: list[dict[str, object]] = []
        config_blob_digest: str | None = None
        for label, descriptor in descriptors:
            digest, declared_size = _descriptor_digest(descriptor, label=label)
            blob_name = f"blobs/sha256-{digest}"
            member = members.get(blob_name)
            if member is None:
                raise ModelArchiveVerificationError(
                    f"model archive is missing manifest-referenced blob: {blob_name}"
                )
            if declared_size is not None and member.size != declared_size:
                raise ModelArchiveVerificationError(
                    f"model blob size differs from manifest {label}: {blob_name}"
                )
            if digest not in blob_hashes:
                raise ModelArchiveVerificationError(
                    f"model archive blob was not content-verified: {blob_name}"
                )
            referenced.add(digest)
            if label == "config":
                config_blob_digest = digest
            descriptor_locks.append(
                {
                    "label": label,
                    "digest": f"sha256:{digest}",
                    "size": member.size,
                    "media_type": descriptor.get("mediaType"),
                }
            )

        if config_blob_digest is None:
            raise ModelArchiveVerificationError("model config descriptor is missing")
        config_member = members[f"blobs/sha256-{config_blob_digest}"]
        try:
            config_payload = json.loads(_read_member(archive, config_member))
        except (UnicodeError, ValueError) as exc:
            raise ModelArchiveVerificationError("model config blob is invalid JSON") from exc
        if not isinstance(config_payload, dict):
            raise ModelArchiveVerificationError("model config blob must be a JSON object")

        chat_template = config_payload.get("template") or config_payload.get("chat_template")
        renderer = config_payload.get("renderer")
        parser = config_payload.get("parser")
        requires = config_payload.get("requires")
        chat_template_digest: str | None = None
        if isinstance(chat_template, str) and chat_template:
            chat_protocol_kind = "template"
            chat_protocol_digest = "sha256:" + hashlib.sha256(
                chat_template.encode("utf-8")
            ).hexdigest()
            chat_template_digest = chat_protocol_digest
        elif all(
            isinstance(value, str) and value
            for value in (renderer, parser, requires)
        ):
            chat_protocol_kind = "renderer_parser"
            protocol_identity = {
                "parser": parser,
                "renderer": renderer,
                "requires": requires,
            }
            chat_protocol_digest = "sha256:" + hashlib.sha256(
                json.dumps(
                    protocol_identity,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
        else:
            raise ModelArchiveVerificationError(
                "model config has neither a chat template nor a complete renderer protocol"
            )

        archive_sha256 = lock.get("archive_sha256")
        if archive_sha256 is not None:
            archive_match = DIGEST_PATTERN.fullmatch(str(archive_sha256))
            if archive_match is None:
                raise ModelArchiveVerificationError("model lock archive_sha256 is invalid")
            with archive_path.open("rb") as source:
                observed_archive_sha256 = _sha256_stream(source)
            if observed_archive_sha256 != archive_match.group(1):
                raise ModelArchiveVerificationError("model archive SHA-256 differs from lock")
        archive_bytes = lock.get("archive_bytes")
        if archive_bytes is not None and archive_bytes != archive_path.stat().st_size:
            raise ModelArchiveVerificationError("model archive byte size differs from lock")

        return {
            "schema_version": "1.0",
            "passed": True,
            "model_name": model_name,
            "model_digest": manifest_digest,
            "manifest_path": expected_manifest_path,
            "verified_blob_count": len(blob_hashes),
            "referenced_blob_count": len(referenced),
            "descriptors": descriptor_locks,
            "chat_protocol_kind": chat_protocol_kind,
            "chat_protocol_digest": chat_protocol_digest,
            "chat_template_digest": chat_template_digest,
            "renderer": renderer,
            "parser": parser,
            "requires": requires,
            "archive_sha256": archive_sha256,
            "archive_bytes": archive_path.stat().st_size,
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        lock = json.loads(args.lock.read_text(encoding="utf-8"))
        if not isinstance(lock, dict):
            raise ModelArchiveVerificationError("model lock must be a JSON object")
        result = verify_model_archive(args.archive, lock)
        rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered, encoding="utf-8")
        print(rendered, end="")
        return 0
    except (ModelArchiveVerificationError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
