"""Smoke-probe the real generator before a full comparison run.

The generator's reaction to the targeted prompt and the eight angles cannot be checked offline:
every automated test injects a transport double, and the very existence of
``ProviderFailureClass.REFUSAL`` says the model is allowed to decline.  So before spending a full
16-opportunity x 2-arm run, this calls the provider directly a few times and reports what came
back: the version asked, whether the model refused, the item count, token counters and the text.

It is read-only: no opportunity is consumed, no data root is touched and no Episode runs.

Usage:
    python scripts/smoke_attack_prompt.py                       # every registered angle, once
    python scripts/smoke_attack_prompt.py --techniques T1,T6    # only the named angles
    python scripts/smoke_attack_prompt.py --attempts 2          # two draws per angle
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO_ROOT / "src"), str(REPO_ROOT / "agent_image")]

from sandbox.scenarios.structured_v1.fixtures import load_fixture  # noqa: E402
from sandbox.structured_v1.projection import build_public_projection  # noqa: E402
from sandbox.structured_v1.provider import (  # noqa: E402
    ATTACK_TECHNIQUE_IDS,
    ProviderRequestKind,
    ProviderTextRequest,
)
from sandbox.structured_v1.text_provider import (  # noqa: E402
    ATTACK_TECHNIQUE_ANGLES,
    HttpJsonTextProvider,
    TextProviderOptions,
)

DIRECTIONS = ("data-release", "audience-scope")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", default="summary-delivery-e2-completion")
    parser.add_argument("--endpoint", default="http://127.0.0.1:11434")
    parser.add_argument("--model", default="qwen3.5:27b-q4_K_M")
    parser.add_argument("--direction", default="data-release", choices=DIRECTIONS)
    parser.add_argument("--techniques", default=",".join(ATTACK_TECHNIQUE_IDS))
    parser.add_argument("--attempts", type=int, default=1)
    parser.add_argument("--node-id", default="s2-1")
    args = parser.parse_args()

    techniques = [item.strip() for item in args.techniques.split(",") if item.strip()]
    fixture = load_fixture(args.fixture)
    provider = HttpJsonTextProvider(
        TextProviderOptions(
            model_name=args.model,
            endpoint=args.endpoint,
            timeout_seconds=120,
        )
    )
    print(f"fixture={args.fixture} provider_version={provider.provider_version}")
    print(f"manifest_digest={fixture.manifest.manifest_digest}")

    projection = build_public_projection(manifest=fixture.manifest, parent=None)
    refusals = 0
    empty = 0
    for technique in techniques:
        for attempt in range(1, args.attempts + 1):
            request = ProviderTextRequest(
                request_id=f"smoke-{technique.lower()}-{attempt}",
                kind=ProviderRequestKind.GENERATION,
                projection=projection,
                node_ids=(args.node_id,),
                obligation_direction=args.direction,
                attack_technique=technique,
            )
            try:
                response = provider.complete(request)
            except Exception as error:  # noqa: BLE001 - the probe reports whatever happened
                print(f"[{technique}#{attempt}] EXCEPTION {type(error).__name__}: {error}")
                continue
            refused = response.refusal is not None
            refusals += int(refused)
            empty += int(not response.items)
            known = technique in ATTACK_TECHNIQUE_ANGLES
            print(f"[{technique}#{attempt}] angle_registered={known} refusal={refused} "
                  f"items={len(response.items)} in_tokens={response.input_tokens} "
                  f"out_tokens={response.output_tokens}")
            if refused:
                print(f"    refusal text: {response.refusal}")
            for item in response.items:
                print(f"    {item.node_id}: {item.text[:400]}")

    print(f"summary: probes={len(techniques) * args.attempts} refusals={refusals} empty={empty}")


if __name__ == "__main__":
    main()
