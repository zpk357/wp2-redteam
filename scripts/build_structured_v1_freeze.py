"""Build the structured-scenario freeze manifest (the image's `/opt/structured-v1/...` file).

This is the build-time step that freezes what the structured-scenario container owns:
the frozen base world(s), the ``fixture -> base/overlay`` mapping, and the tool catalogue
plus the image manifest. The container then rebuilds every world from this manifest and
refuses any world that does not hash back to its own digest.

Routine choices fixed here (not left to the caller):

* a tool's ``contract_digest`` is ``sha256({"tool_name", "contract_version"})`` over the
  frozen ``OFFICE_V2_TOOL_CONTRACT_VERSION`` - the interface identity the catalogue binds;
* the ``fixture -> base/overlay`` mapping is loaded from a JSON file when the fixtures are
  frozen (``--mapping``); until then it is empty and the container refuses any fixture that
  has no frozen mapping.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sandbox.scenarios.office_v2.canonical_world import (
    CanonicalOfficeWorld,
    load_canonical_world,
)
from sandbox.scenarios.structured_v1.fixtures import load_fixture
from sandbox.structured_v1.assets import FixtureBaseMapping
from sandbox.structured_v1.freeze import build_freeze_manifest, write_freeze_manifest
from sandbox.structured_v1.tool_catalogue import (
    ImageManifest,
    ToolCatalogue,
    build_office_v2_catalogue,
)


def build_catalogue() -> ToolCatalogue:
    """The frozen tool surface: one identity per tool, bound to the frozen contract version."""

    return build_office_v2_catalogue()


def build(
    *,
    output: Path,
    mapping_path: Path | None = None,
    fixture_id: str | None = None,
) -> Path:
    """Freeze the tool catalogue, the base world(s) and the `fixture -> base/overlay` mapping.

    Exactly one mapping source may be given: ``fixture_id`` takes the mapping from the
    fixture asset itself (so the frozen container and the fixture code cannot drift apart),
    while ``mapping_path`` loads an explicit JSON list.
    """

    if fixture_id is not None and mapping_path is not None:
        raise SystemExit("give either --fixture or --mapping, not both")
    catalogue = build_catalogue()
    image_manifest = ImageManifest(tool_catalogue=catalogue)
    mapping: tuple[FixtureBaseMapping, ...] = ()
    worlds: tuple[CanonicalOfficeWorld, ...] = ()
    if fixture_id is not None:
        fixture = load_fixture(fixture_id)
        mapping = (fixture.mapping,)
        worlds = (fixture.base_world,)
    else:
        if mapping_path is not None:
            mapping = tuple(
                FixtureBaseMapping.model_validate(item)
                for item in json.loads(mapping_path.read_text(encoding="utf-8"))
            )
        worlds = (load_canonical_world(),)
    manifest = build_freeze_manifest(
        image_manifest=image_manifest,
        worlds=worlds,
        mapping=mapping,
    )
    return write_freeze_manifest(manifest, output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("freeze-manifest.json"))
    parser.add_argument("--mapping", type=Path, default=None)
    parser.add_argument(
        "--fixture",
        default=None,
        help="freeze the mapping recorded by a fixture asset (e.g. summary-delivery-a)",
    )
    args = parser.parse_args()
    built = build(
        output=args.output,
        mapping_path=args.mapping,
        fixture_id=args.fixture,
    )
    print(f"wrote {built}")
