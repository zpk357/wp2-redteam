"""P6 acceptance: the container request, and the assets the image rebuilds from its own freeze.

Two rules are under test. A request that is not this protocol's is refused by identity, so
a payload cannot be routed half-way into the old execution path. A world rebuilt from the
freeze manifest must hash to the digest its locator claims, so the container cannot be
handed a different world wearing the right name.
"""

from __future__ import annotations

import pytest

from sandbox.scenarios.office_v2.canonical_world import (
    CanonicalOfficeWorld,
    OfficeWorldState,
    build_canonical_world,
)
from sandbox.scenarios.office_v2.models import (
    IdentityDirectory,
    OfficeDomainGraph,
    Organization,
    Principal,
    PrincipalKind,
)
from sandbox.structured_v1.assets import FixtureBaseMapping, FrozenWorldAsset
from sandbox.structured_v1.container import (
    REQUEST_KIND,
    StructuredCaseRequest,
    parse_structured_case_request,
)
from sandbox.structured_v1.envelope_codes import EnvelopeRefusal, FailureCode
from sandbox.structured_v1.freeze import (
    FreezeManifest,
    FrozenWorldSnapshot,
    load_assets,
)
from sandbox.structured_v1.tool_catalogue import (
    ImageManifest,
    ToolCatalogue,
    ToolContractIdentity,
    ToolingFacts,
    ToolingIdentity,
    verify_tooling,
)

DIGEST = {name: "sha256:" + name * 64 for name in "abcde"}


def _envelope_payload(manifest, root_case) -> dict[str, object]:
    """A real envelope, built by the rehearsal's own harness."""

    import test_structured_episode_rehearsal as rehearsal

    envelope, _, _, _ = rehearsal._prepared(manifest, root_case)
    return envelope.model_dump(mode="json")


def _world(name: str = "maya.chen") -> CanonicalOfficeWorld:
    organization = Organization(
        organization_id="org.example",
        name="Example Corporation",
        primary_domain="example.test",
    )
    graph = OfficeDomainGraph(
        directory=IdentityDirectory(
            organization=organization,
            principals=(
                Principal(
                    principal_id=name,
                    kind=PrincipalKind.USER,
                    display_name="Example Person",
                    email=f"{name.split('.')[0]}@example.test",
                    organization_id="org.example",
                ),
            ),
        )
    )
    return build_canonical_world(OfficeWorldState(domain_graph=graph))


def _catalogue() -> ToolCatalogue:
    return ToolCatalogue(
        tools=(ToolContractIdentity(tool_name="mail.send", contract_digest=DIGEST["a"]),)
    )


def _image_manifest() -> ImageManifest:
    return ImageManifest(tool_catalogue=_catalogue())


def _request_payload(envelope_payload, *, kind: str = REQUEST_KIND):
    return {
        "request_id": "request-0001",
        "kind": kind,
        "envelope": envelope_payload,
    }


# --- the request -------------------------------------------------------------------


def test_a_request_of_another_kind_is_refused(manifest, root_case) -> None:
    payload = _request_payload(_envelope_payload(manifest, root_case))
    payload["kind"] = "office-v2-execution"

    with pytest.raises(EnvelopeRefusal) as caught:
        parse_structured_case_request(payload)

    assert caught.value.code is FailureCode.IDENTITY_MISMATCH


def test_a_malformed_request_is_refused(manifest, root_case) -> None:
    payload = _request_payload(_envelope_payload(manifest, root_case))
    payload.pop("envelope")

    with pytest.raises(EnvelopeRefusal) as caught:
        parse_structured_case_request(payload)

    assert caught.value.code is FailureCode.PAYLOAD_MISSING


def test_a_valid_request_parses_without_host_identity(manifest, root_case) -> None:
    payload = _request_payload(_envelope_payload(manifest, root_case))
    request = parse_structured_case_request(payload)

    assert isinstance(request, StructuredCaseRequest)
    assert request.kind == REQUEST_KIND
    assert request.envelope.manifest.fixture_id == manifest.fixture_id


# --- the freeze manifest -----------------------------------------------------------


def _freeze(base: CanonicalOfficeWorld, **overrides) -> FreezeManifest:
    asset = FrozenWorldAsset(world=base)
    fields: dict[str, object] = {
        "image_manifest": _image_manifest(),
        "mapping": (
            FixtureBaseMapping(
                fixture_id="summary-delivery-a",
                fixture_version="v1",
                manifest_digest=DIGEST["c"],
                base_locator=asset.locator,
                base_world_digest=base.world_digest,
                overlay_digest=DIGEST["d"],
            ),
        ),
        "worlds": (
            FrozenWorldSnapshot(
                locator=asset.locator,
                world_digest=base.world_digest,
                state=base.state.model_dump(mode="json", exclude_none=False),
            ),
        ),
    }
    fields.update(overrides)
    return FreezeManifest(**fields)  # type: ignore[arg-type]


def test_the_image_rebuilds_its_worlds() -> None:
    base = _world()
    assets, tooling = load_assets(_freeze(base))

    assert assets.locators() == (FrozenWorldAsset(world=base).locator,)
    assert assets.resolve(FrozenWorldAsset(world=base).locator).world_digest == (
        base.world_digest
    )
    assert assets.mapping_for("summary-delivery-a") is not None
    verification = verify_tooling(_claimed_tooling(tooling), tooling)
    assert verification.catalogue_digest == tooling.catalogue.catalogue_digest


def _claimed_tooling(tooling: ToolingFacts) -> ToolingIdentity:
    return ToolingIdentity(
        tool_catalogue_digest=tooling.catalogue.catalogue_digest,
    )


def test_a_world_that_does_not_rebuild_to_its_digest_is_refused() -> None:
    base = _world()
    other = _world("sam.lee")
    manifest = _freeze(
        base,
        worlds=(
            FrozenWorldSnapshot(
                locator=FrozenWorldAsset(world=base).locator,
                world_digest=base.world_digest,
                state=other.state.model_dump(mode="json", exclude_none=False),
            ),
        ),
    )

    with pytest.raises(EnvelopeRefusal) as caught:
        load_assets(manifest)

    assert caught.value.code is FailureCode.BASE_WORLD_MISMATCH


def test_a_locator_that_is_not_the_worlds_own_digest_is_refused() -> None:
    base = _world()
    manifest = _freeze(
        base,
        worlds=(
            FrozenWorldSnapshot(
                locator="frozen-world/" + "9" * 64,
                world_digest=base.world_digest,
                state=base.state.model_dump(mode="json", exclude_none=False),
            ),
        ),
    )

    with pytest.raises(EnvelopeRefusal) as caught:
        load_assets(manifest)

    assert caught.value.code is FailureCode.LOCATOR_OUTSIDE_STORE


def test_the_freeze_manifest_has_no_image_self_identity() -> None:
    payload = _freeze(_world()).model_dump(mode="json")

    assert set(payload["image_manifest"]) == {"tool_catalogue"}
