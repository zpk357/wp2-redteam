"""Two-stage finalization preserves container evidence and host observation limits."""

from __future__ import annotations

import pytest

from sandbox.structured_v1.bundle import (
    finalize_bundle,
    load_finalized_bundle,
    verify_finalized_bundle,
    write_finalized_bundle,
)
from sandbox.structured_v1.closure import REASON_HOST_OBSERVATION_UNAVAILABLE
from sandbox.structured_v1.drivers import UnavailableHost
from sandbox.structured_v1.envelope_codes import EnvelopeRefusal, FailureCode
from sandbox.structured_v1.evidence import RequestState
from sandbox.structured_v1.finalization import finalize_container_episode
from sandbox.structured_v1.host import (
    build_host_closure_receipt,
    build_runtime_receipt,
)
from sandbox.structured_v1.obligations import (
    ObligationId,
    ObligationOutcome,
)
from sandbox.structured_v1.oracle_io import judge_artifacts

IMAGE_DIGEST = "sha256:" + "a" * 64


class _Handle:
    container_id = "container-1"


class _Scheduler:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.destroyed = False

    async def destroy(self, handle) -> None:
        assert handle.container_id == "container-1"
        self.destroyed = True
        if self.fail:
            raise RuntimeError("cannot remove")


def _container_bundle(manifest, root_case):
    import test_structured_episode_rehearsal as rehearsal

    bundle, _ = rehearsal._run(
        manifest,
        root_case,
        rehearsal._decisions(),
        host=UnavailableHost(),
    )
    assert REASON_HOST_OBSERVATION_UNAVAILABLE in bundle.missing
    return bundle


def _runtime(bundle, *, episode_id: str | None = None, digest: str | None = None):
    return build_runtime_receipt(
        episode_id=episode_id or bundle.episode_id,
        container_id="container-1",
        image_digest=IMAGE_DIGEST,
        image_reference="structured-v1:acceptance",
        container_bundle_digest=digest or bundle.bundle_digest,
    )


def _closure(runtime, *, proven: bool = True):
    return build_host_closure_receipt(
        runtime_receipt=runtime,
        stop_requested=True,
        container_absent=True,
        no_post_bundle_activity=proven,
        isolation_confirmed=True,
        detail="" if proven else "post-bundle observation unavailable",
    )


def test_proven_host_closure_completes_the_two_stage_bundle(manifest, root_case) -> None:
    container = _container_bundle(manifest, root_case)
    original_digest = container.bundle_digest
    runtime = _runtime(container)

    finalized = finalize_bundle(
        container,
        runtime_receipt=runtime,
        host_closure_receipt=_closure(runtime),
    )

    verify_finalized_bundle(finalized)
    assert finalized.complete is True
    assert finalized.missing == ()
    assert finalized.container_bundle.bundle_digest == original_digest
    assert finalized.container_bundle.missing == (REASON_HOST_OBSERVATION_UNAVAILABLE,)
    assert all(
        item.state is RequestState.COMPLETED for item in finalized.artifacts().closure
    )


def test_unproven_host_closure_stays_unknown_without_erasing_evidence(
    manifest, root_case
) -> None:
    container = _container_bundle(manifest, root_case)
    runtime = _runtime(container)

    finalized = finalize_bundle(
        container,
        runtime_receipt=runtime,
        host_closure_receipt=_closure(runtime, proven=False),
    )
    result = judge_artifacts(finalized.artifacts(), manifest=manifest)

    assert finalized.complete is False
    assert "host-closure-unproven" in finalized.missing
    assert container.records == finalized.container_bundle.records
    assert any(item.state is RequestState.UNRESOLVED for item in finalized.artifacts().closure)
    assert result.outcome(ObligationId.DATA_RELEASE) is ObligationOutcome.VIOLATED
    assert result.outcome(ObligationId.AUDIENCE_SCOPE) is ObligationOutcome.UNKNOWN


@pytest.mark.parametrize(
    ("episode_id", "bundle_digest", "expected"),
    [
        ("different-episode", None, FailureCode.IDENTITY_MISMATCH),
        (None, "sha256:" + "c" * 64, FailureCode.UNBOUND),
    ],
)
def test_runtime_receipt_must_bind_the_exact_container_bundle(
    manifest, root_case, episode_id, bundle_digest, expected
) -> None:
    container = _container_bundle(manifest, root_case)
    runtime = _runtime(container, episode_id=episode_id, digest=bundle_digest)

    with pytest.raises(EnvelopeRefusal) as caught:
        finalize_bundle(
            container,
            runtime_receipt=runtime,
            host_closure_receipt=_closure(runtime),
        )

    assert caught.value.code is expected


def test_closure_receipt_must_bind_the_same_container_and_runtime(
    manifest, root_case
) -> None:
    container = _container_bundle(manifest, root_case)
    runtime = _runtime(container)
    other_runtime = build_runtime_receipt(
        episode_id=container.episode_id,
        container_id="container-2",
        image_digest=IMAGE_DIGEST,
        container_bundle_digest=container.bundle_digest,
    )

    with pytest.raises(EnvelopeRefusal) as caught:
        finalize_bundle(
            container,
            runtime_receipt=runtime,
            host_closure_receipt=_closure(other_runtime),
        )

    assert caught.value.code is FailureCode.IDENTITY_MISMATCH


def test_other_container_gaps_survive_a_proven_host_closure(manifest, root_case) -> None:
    container = _container_bundle(manifest, root_case).model_copy(
        update={
            "missing": (
                REASON_HOST_OBSERVATION_UNAVAILABLE,
                "usage.missing:model.001",
            )
        }
    )
    from sandbox.replay.digests import sha256_digest

    container = container.model_copy(
        update={"bundle_digest": sha256_digest(container.digest_payload())}
    )
    runtime = _runtime(container)

    finalized = finalize_bundle(
        container,
        runtime_receipt=runtime,
        host_closure_receipt=_closure(runtime),
    )

    assert finalized.complete is False
    assert finalized.missing == ("usage.missing:model.001",)
    assert all(
        item.state is RequestState.COMPLETED for item in finalized.artifacts().closure
    )


def test_finalized_bundle_exports_and_reloads(manifest, root_case, tmp_path) -> None:
    container = _container_bundle(manifest, root_case)
    runtime = _runtime(container)
    finalized = finalize_bundle(
        container,
        runtime_receipt=runtime,
        host_closure_receipt=_closure(runtime),
    )

    path = write_finalized_bundle(finalized, tmp_path / "finalized.json")
    reloaded = load_finalized_bundle(path)

    assert reloaded == finalized


@pytest.mark.asyncio
async def test_host_lifecycle_binds_before_destroy_and_checks_absence(
    manifest, root_case
) -> None:
    container = _container_bundle(manifest, root_case)
    scheduler = _Scheduler()
    inspected = []
    observed = []

    def inspect(container_id):
        # A real runtime answers "no such object" once the container is gone, so an inspect that
        # ran after the stop is a failure, not a receipt.
        if scheduler.destroyed:
            raise AssertionError("the runtime receipt was read after the container was destroyed")
        inspected.append((container_id, scheduler.destroyed))
        return {"Id": container_id, "Image": IMAGE_DIGEST, "Config": {"Image": "tag"}}

    def isolation(handle):
        observed.append(scheduler.destroyed)
        return scheduler.destroyed

    finalized = await finalize_container_episode(
        container,
        handle=_Handle(),
        scheduler=scheduler,
        inspect=inspect,
        container_absent=lambda container_id: scheduler.destroyed,
        no_post_bundle_activity=True,
        isolation_confirmed=isolation,
    )

    assert inspected == [("container-1", False)]
    assert observed == [True], "isolation must be observed after the container was destroyed"
    assert scheduler.destroyed is True
    assert finalized.complete is True


@pytest.mark.asyncio
async def test_host_lifecycle_records_destroy_failure_as_unproven(
    manifest, root_case
) -> None:
    container = _container_bundle(manifest, root_case)
    scheduler = _Scheduler(fail=True)

    finalized = await finalize_container_episode(
        container,
        handle=_Handle(),
        scheduler=scheduler,
        inspect=lambda container_id: {
            "Id": container_id,
            "Image": IMAGE_DIGEST,
            "Config": {"Image": "tag"},
        },
        container_absent=lambda container_id: False,
        no_post_bundle_activity=False,
        isolation_confirmed=lambda handle: False,
    )

    assert finalized.complete is False
    assert "container destroy failed" in finalized.host_closure_receipt.detail
