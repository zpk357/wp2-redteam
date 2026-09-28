"""G1-freezing acceptance: candidate preconditions and host-owned runtime identity.

The candidate set must be deterministic and must satisfy the probe's precondition - at
least two no-injection roots and at least two parents with at least two children - so the
"material changed the behaviour" criterion can actually run. The host reader must bind
the actual container id and immutable image id to one Episode and its exported bundle.
The bundle export must survive a write/reload round trip and re-verify.
"""

from __future__ import annotations

import pytest

from sandbox.structured_v1.bundle import load_bundle, verify_bundle, write_bundle
from sandbox.structured_v1.candidates import (
    CandidateKind,
    freeze_g1_candidates,
)
from sandbox.structured_v1.envelope_codes import EnvelopeRefusal, FailureCode
from sandbox.structured_v1.host import (
    build_host_closure_receipt,
    query_runtime_receipt,
    runtime_receipt_from_inspect,
)

DIGEST = "sha256:" + "a" * 64


def test_the_candidate_set_satisfies_the_g1_preconditions(manifest) -> None:
    frozen = freeze_g1_candidates(manifest)

    assert frozen.verify_invariants() == ()
    assert len(frozen.roots()) == 10
    assert len(frozen.edited()) == 10
    assert len(frozen.no_injection_roots()) >= 2
    parents_with_two = [
        parent for parent, children in frozen.parents().items() if len(children) >= 2
    ]
    assert len(parents_with_two) >= 2
    # every edited candidate descends from one of the roots
    root_ids = {item.candidate_id for item in frozen.roots()}
    assert all(item.parent_id in root_ids for item in frozen.edited())
    assert {item.kind for item in frozen.candidates} == {
        CandidateKind.ROOT,
        CandidateKind.EDITED,
    }


def test_the_candidate_set_is_deterministic(manifest) -> None:
    first = freeze_g1_candidates(manifest)
    second = freeze_g1_candidates(manifest)

    assert [item.material_digest for item in first.candidates] == [
        item.material_digest for item in second.candidates
    ]


def test_the_host_reader_binds_container_image_episode_and_bundle() -> None:
    record = runtime_receipt_from_inspect(
        {
            "Id": "container-1",
            "Image": DIGEST,
            "Config": {"Image": "registry/structured:acceptance"},
        },
        episode_id="episode-1",
        container_bundle_digest="sha256:" + "b" * 64,
    )

    assert record.image_digest == DIGEST
    assert record.container_id == "container-1"
    assert record.episode_id == "episode-1"
    assert record.container_bundle_digest == "sha256:" + "b" * 64
    assert record.image_reference == "registry/structured:acceptance"
    assert record.source == "container-runtime"


def test_the_host_reader_refuses_a_tag_only() -> None:
    with pytest.raises(EnvelopeRefusal) as caught:
        runtime_receipt_from_inspect(
            {
                "Id": "container-1",
                "Image": "registry/structured:latest",
                "Config": {"Image": "registry/structured:latest"},
            },
            episode_id="episode-1",
            container_bundle_digest="sha256:" + "b" * 64,
        )

    assert caught.value.code is FailureCode.IMAGE_MISMATCH


def test_the_host_reader_refuses_when_the_runtime_cannot_be_asked() -> None:
    def broken(container_id: str) -> dict[str, object]:
        raise RuntimeError("no such container")

    with pytest.raises(EnvelopeRefusal) as caught:
        query_runtime_receipt(
            "container-1",
            episode_id="episode-1",
            container_bundle_digest="sha256:" + "b" * 64,
            inspect=broken,
        )

    assert caught.value.code is FailureCode.PAYLOAD_MISSING


def test_the_host_reader_rejects_a_different_container_instance() -> None:
    def wrong_container(container_id: str) -> dict[str, object]:
        return {"Id": "container-2", "Image": DIGEST, "Config": {"Image": "tag"}}

    with pytest.raises(EnvelopeRefusal) as caught:
        query_runtime_receipt(
            "container-1",
            episode_id="episode-1",
            container_bundle_digest="sha256:" + "b" * 64,
            inspect=wrong_container,
        )

    assert caught.value.code is FailureCode.IDENTITY_MISMATCH


def test_the_closure_receipt_is_bound_to_the_runtime_receipt() -> None:
    runtime = runtime_receipt_from_inspect(
        {"Id": "container-1", "Image": DIGEST, "Config": {"Image": "tag"}},
        episode_id="episode-1",
        container_bundle_digest="sha256:" + "b" * 64,
    )

    closure = build_host_closure_receipt(
        runtime_receipt=runtime,
        stop_requested=True,
        container_absent=True,
        no_post_bundle_activity=False,
        isolation_confirmed=True,
        detail="post-bundle activity observation unavailable",
    )

    assert closure.runtime_receipt_digest == runtime.receipt_digest
    assert closure.container_id == runtime.container_id
    assert closure.proven is False


def test_the_bundle_exports_and_reloads(manifest, root_case, tmp_path) -> None:
    import test_structured_episode_rehearsal as rehearsal

    bundle, _ = rehearsal._run(manifest, root_case, rehearsal._decisions())
    path = write_bundle(bundle, tmp_path / "bundle.json")

    reloaded = load_bundle(path)

    verify_bundle(reloaded, known_sources=frozenset({("f-overview", "current_version.content")}))
    assert reloaded.bundle_digest == bundle.bundle_digest
    assert reloaded.episode_id == bundle.episode_id
