from __future__ import annotations

import argparse
import json
from pathlib import Path

from sandbox.release_candidate import validate_release_candidate


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--require-deployment-ready", action="store_true")
    args = parser.parse_args()
    payload = validate_release_candidate(
        args.manifest,
        require_deployment_ready=args.require_deployment_ready,
    )
    print(
        json.dumps(
            {
                "display_version": payload["release"]["display_version"],
                "deployment_ready": payload["deployment_policy"][
                    "deployment_ready"
                ],
                "release_manifest_digest": payload["release_manifest_digest"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
