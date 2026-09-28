"""Small, non-release preflight for exploratory Campaigns."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def run_preflight(
    *,
    agent_image: str,
    mutator_image: str,
    model_name: str,
    ollama_mode: str,
    ollama_endpoint: str,
    db: Path,
    data_root: Path,
    client: Any | None = None,
) -> dict[str, object]:
    errors: list[str] = []
    warnings: list[str] = []
    images: dict[str, object] = {}
    data_root.mkdir(parents=True, exist_ok=True)
    db.parent.mkdir(parents=True, exist_ok=True)
    try:
        import docker

        client = client or docker.from_env()
        client.ping()
        for name, image_ref in (("agent", agent_image), ("mutator", mutator_image)):
            try:
                image = client.images.get(image_ref)
                config = image.attrs.get("Config", {})
                images[name] = {
                    "reference": image_ref,
                    "entrypoint": config.get("Entrypoint"),
                    "cmd": config.get("Cmd"),
                    "healthcheck": config.get("Healthcheck"),
                }
            except Exception as exc:
                errors.append(f"{name} image is unavailable: {exc}")
    except Exception as exc:
        errors.append(f"Docker is unavailable: {exc}")

    model: dict[str, object] = {"mode": ollama_mode, "name": model_name}
    if ollama_mode == "host":
        model["endpoint"] = ollama_endpoint
        try:
            with urllib.request.urlopen(
                urllib.request.Request(ollama_endpoint.rstrip("/") + "/api/tags"),
                timeout=5,
            ) as response:
                payload = json.load(response)
            names = {
                item.get("name")
                for item in payload.get("models", [])
                if isinstance(item, dict)
            }
            model["available_models"] = sorted(name for name in names if isinstance(name, str))
            if model_name not in names:
                errors.append(f"host Ollama does not expose model {model_name!r}")
        except (OSError, urllib.error.URLError, ValueError, TypeError) as exc:
            errors.append(f"host Ollama is unavailable at {ollama_endpoint}: {exc}")
    elif ollama_mode == "embedded":
        warnings.append(
            "embedded mode requires the selected images to contain Ollama and the model"
        )
    else:
        errors.append(f"unknown Ollama mode: {ollama_mode}")

    return {
        "ready": not errors,
        "errors": errors,
        "warnings": warnings,
        "images": images,
        "model": model,
        "paths": {"db": str(db), "data_root": str(data_root)},
    }


__all__ = ["run_preflight"]
