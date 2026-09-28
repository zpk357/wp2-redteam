"""Verify old archives and fixture source, without extracting or rewriting evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tarfile
from pathlib import Path

from sandbox.scenarios.structured_v1.fixtures import load_fixture
from sandbox.structured_v1.bundle import FinalizedEpisodeBundle, verify_finalized_bundle
from sandbox.structured_v1.freeze import build_freeze_manifest
from sandbox.structured_v1.tool_catalogue import ImageManifest, build_office_v2_catalogue


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", action="append", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve() in {p.resolve() for p in args.archive}:
        parser.error("output cannot overwrite an archive")
    rows = []
    for archive in args.archive:
        before = hashlib.sha256(archive.read_bytes()).hexdigest()
        count = 0
        with tarfile.open(archive) as tar:
            for member in tar.getmembers():
                if "/finalized/" in member.name and member.name.endswith(".json"):
                    raw = json.load(tar.extractfile(member))
                    bundle = FinalizedEpisodeBundle.model_validate(raw)
                    verify_finalized_bundle(bundle)
                    if bundle.model_dump(mode="json") != raw:
                        raise ValueError(f"old payload changed: {member.name}")
                    count += 1
        if count == 0 or before != hashlib.sha256(archive.read_bytes()).hexdigest():
            raise ValueError("archive is empty or changed")
        rows.append({"archive": str(archive), "sha256": before,
                     "finalized_verified": count, "payload_unchanged": True})
    for name in ("c", "d"):
        path = f"src/sandbox/scenarios/structured_v1/fixtures/summary_delivery_{name}.py"
        old = subprocess.check_output(["git", "show", f"1703471:{path}"])
        if old != Path(path).read_bytes().replace(b"\r\n", b"\n"):
            raise ValueError(f"old fixture changed: {path}")
    fixture = load_fixture("summary-delivery-e")
    freeze = build_freeze_manifest(
        image_manifest=ImageManifest(tool_catalogue=build_office_v2_catalogue()),
        worlds=(fixture.base_world,), mapping=(fixture.mapping,),
    )
    result = {
        "history": rows, "old_fixture_code_unchanged": True,
        "fixture_id": fixture.manifest.fixture_id,
        "manifest_digest": fixture.manifest.manifest_digest,
        "overlay_digest": fixture.mapping.overlay_digest,
        "freeze_digest": freeze.canonical_digest(),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
