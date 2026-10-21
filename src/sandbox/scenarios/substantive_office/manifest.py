"""Content, authority and resource inputs covered by the SOF fixture freeze (SOF-18)."""

from dataclasses import asdict
from pathlib import Path

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.error_capable import AttackMode

from . import attacks, materials
from .world import resource_documents


def content_manifest() -> dict[str, object]:
    paths = tuple(f"/workspace/sof/slot-{index:02d}" for index in range(len(materials.TITLES)))
    return {
        "completion_contract": "sof-business-fields-v2",
        # Includes resource versions/ACL construction, rendering and matching rules. Normalize
        # newlines so the same checkout has the same identity on Windows and Linux.
        "implementation": {
            name: sha256_digest(Path(__file__).with_name(name).read_text(encoding="utf-8"))
            for name in (
                "world.py",
                "materials.py",
                "attacks.py",
                "materialize.py",
                "planning.py",
                "completion.py",
                "manifest.py",
            )
        },
        "titles": materials.TITLES,
        "instances": [
            {
                "data": asdict(instance),
                "documents": [
                    (materials.format_document(slot, body), snippet)
                    for slot, (body, snippet) in enumerate(
                        materials.build_documents(instance, paths)
                    )
                ],
                "resources": resource_documents(instance),
                "completion_fields": instance.completion_fields,
                "authorized_recipients": instance.delivery_recipients,
                "restricted_readers": instance.restricted_readers,
                "attacks": {
                    mode.value: {
                        phrasing: attacks.attack_text(
                            instance,
                            mode=mode,
                            phrasing=phrasing,
                            index_path=paths[1],
                            ledger_path=paths[6],
                            worksheet_path=paths[5],
                        )
                        for phrasing in attacks.PHRASING_IDS
                    }
                    for mode in AttackMode
                },
            }
            for instance in materials.INSTANCES
        ],
    }
