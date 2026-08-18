from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path
from typing import Any

DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


class LockVerificationError(RuntimeError):
    pass


def normalize_digest(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise LockVerificationError(f"{field} must be a SHA-256 digest")
    normalized = value.strip().lower()
    if not normalized.startswith("sha256:") and re.fullmatch(r"[0-9a-f]{64}", normalized):
        normalized = f"sha256:{normalized}"
    if not DIGEST_PATTERN.fullmatch(normalized):
        raise LockVerificationError(f"{field} must be a SHA-256 digest")
    return normalized


def verify_image_lock_entry(
    key: str,
    entry: dict[str, Any],
    *,
    observed_reference: str,
    observed_image_id: str,
) -> dict[str, str]:
    reference = entry.get("reference")
    if reference != observed_reference:
        raise LockVerificationError(
            f"{key} reference mismatch: expected {reference!r}, observed {observed_reference!r}"
        )
    archive_config_digest = normalize_digest(
        entry.get("archive_config_digest"),
        field=f"{key}.archive_config_digest",
    )
    source_image_id = normalize_digest(
        entry.get("source_image_id"),
        field=f"{key}.source_image_id",
    )
    observed = normalize_digest(observed_image_id, field=f"{key}.observed_image_id")
    allowed = {archive_config_digest, source_image_id}
    if observed not in allowed:
        raise LockVerificationError(
            f"{key} loaded image ID is not a locked runtime identity: "
            f"observed {observed}, allowed {sorted(allowed)}"
        )
    identity_type = (
        "archive_config_digest"
        if observed == archive_config_digest
        else "source_image_id"
    )
    return {
        "reference": observed_reference,
        "observed_image_id": observed,
        "matched_identity_type": identity_type,
    }


def verify_image_locks(
    lock: dict[str, Any],
    observed: dict[str, dict[str, str]],
) -> dict[str, Any]:
    if lock.get("schema_version") != "2.0":
        raise LockVerificationError("image lock schema_version must be 2.0")
    expected_keys = {"agent", "controller", "ollama"}
    if set(observed) != expected_keys:
        raise LockVerificationError(
            f"observed image keys must be {sorted(expected_keys)}"
        )
    results = {}
    for key in sorted(expected_keys):
        entry = lock.get(key)
        if not isinstance(entry, dict):
            raise LockVerificationError(f"image lock is missing {key}")
        results[key] = verify_image_lock_entry(
            key,
            entry,
            observed_reference=observed[key]["reference"],
            observed_image_id=observed[key]["image_id"],
        )
    return {"schema_version": "1.0", "passed": True, "images": results}


def verify_model_lock(
    lock: dict[str, Any],
    tags: dict[str, Any],
    *,
    expected_ollama_image: str,
) -> dict[str, str | bool]:
    if lock.get("schema_version") != "1.0":
        raise LockVerificationError("model lock schema_version must be 1.0")
    model_name = lock.get("model_name")
    if not isinstance(model_name, str) or not model_name:
        raise LockVerificationError("model lock has no model_name")
    locked_image = lock.get("ollama_image")
    if locked_image != expected_ollama_image:
        raise LockVerificationError(
            "model lock Ollama image does not match deploy configuration"
        )
    expected_digest = normalize_digest(
        lock.get("model_digest"),
        field="model_lock.model_digest",
    )
    models = tags.get("models")
    if not isinstance(models, list):
        raise LockVerificationError("Ollama /api/tags returned no models list")
    matches = [
        item
        for item in models
        if isinstance(item, dict) and item.get("name") == model_name
    ]
    if len(matches) != 1:
        raise LockVerificationError(
            f"expected exactly one Ollama model named {model_name!r}, observed {len(matches)}"
        )
    observed_digest = normalize_digest(
        matches[0].get("digest"),
        field="ollama.model_digest",
    )
    if observed_digest != expected_digest:
        raise LockVerificationError(
            f"model digest mismatch: expected {expected_digest}, observed {observed_digest}"
        )
    return {
        "schema_version": "1.0",
        "passed": True,
        "model_name": model_name,
        "model_digest": observed_digest,
        "ollama_image": locked_image,
    }


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise LockVerificationError(f"{path} must contain a JSON object")
    return payload


def observe_images(lock: dict[str, Any]) -> dict[str, dict[str, str]]:
    import docker

    client = docker.from_env()
    observed: dict[str, dict[str, str]] = {}
    for key in ("agent", "controller", "ollama"):
        entry = lock.get(key)
        if not isinstance(entry, dict) or not isinstance(entry.get("reference"), str):
            raise LockVerificationError(f"image lock is missing {key}.reference")
        reference = entry["reference"]
        image = client.images.get(reference)
        observed[key] = {"reference": reference, "image_id": image.id}
    return observed


def fetch_tags(endpoint: str, timeout: float) -> dict[str, Any]:
    url = endpoint.rstrip("/") + "/api/tags"
    with urllib.request.urlopen(url, timeout=timeout) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise LockVerificationError("Ollama /api/tags response must be an object")
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    images = subparsers.add_parser("images")
    images.add_argument("--lock", type=Path, required=True)
    images.add_argument("--output", type=Path)

    model = subparsers.add_parser("model")
    model.add_argument("--lock", type=Path, required=True)
    model.add_argument("--ollama-image", required=True)
    model.add_argument("--endpoint", default="http://ollama:11434")
    model.add_argument("--timeout", type=float, default=15.0)
    model.add_argument("--output", type=Path)
    return parser


def write_result(result: dict[str, Any], output: Path | None) -> None:
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


def main() -> int:
    args = build_parser().parse_args()
    try:
        lock = load_json(args.lock)
        if args.command == "images":
            result = verify_image_locks(lock, observe_images(lock))
        else:
            result = verify_model_lock(
                lock,
                fetch_tags(args.endpoint, args.timeout),
                expected_ollama_image=args.ollama_image,
            )
        write_result(result, args.output)
        return 0
    except (LockVerificationError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

