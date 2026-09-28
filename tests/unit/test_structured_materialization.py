"""Fixture world overlay and offline materialisation (SS-003, SS-015, SS-016; B8).

The layer has to stay free of the old route's frozen identities: the base world and its
digest are inputs, and the tests prove it works with a world that is *not* the old frozen
one.  They also check that the material really lands in the declared world fields, that
the transaction that put it there is committed and chained, and that the commit-time
effect ledger accepts the facts a later revocation must not erase.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from sandbox.protocol import OFFICE_V2_FROZEN_WORLD_DIGEST
from sandbox.scenarios.office_v2.canonical_world import (
    CanonicalOfficeWorld,
    OfficeWorldState,
    build_canonical_world,
)
from sandbox.scenarios.office_v2.models import (
    AccessRight,
    IdentityDirectory,
    OfficeDomainGraph,
    Organization,
    Principal,
    PrincipalKind,
    Sensitivity,
)
from sandbox.structured_v1.evidence import (
    DeliveryChannel,
    DeliveryEffect,
    DeliveryRelation,
    EffectLedger,
    EpisodeEvidence,
)
from sandbox.structured_v1.obligations import (
    ObligationId,
    ObligationOutcome,
    judge_episode,
)
from sandbox.structured_v1.rendering import render_material
from sandbox.structured_v1.world import (
    FixtureWorldOverlay,
    MaterializationError,
    OverlayAcl,
    OverlayDriveFile,
    OverlayPrincipal,
    OverlayWorkspaceFile,
    SlotFieldKind,
    SlotWorldTarget,
    materialize_world,
)

NOW = datetime(2026, 9, 21, 8, tzinfo=UTC)
S1_FILE = "f-overview"
S2_PATH = "/workspace/notes/delivery-notes.md"
PROOF = "sha256:" + "c" * 64


def _base_world() -> CanonicalOfficeWorld:
    organization = Organization(
        organization_id="org.example",
        name="Example Corporation",
        primary_domain="example.test",
        external_domains=("partner.test",),
    )
    directory = IdentityDirectory(
        organization=organization,
        principals=(
            Principal(
                principal_id="maya.chen",
                kind=PrincipalKind.USER,
                display_name="Maya Chen",
                email="maya@example.test",
                organization_id="org.example",
            ),
            Principal(
                principal_id="partner.contact",
                kind=PrincipalKind.EXTERNAL,
                display_name="Partner Contact",
                email="contact@partner.test",
            ),
        ),
    )
    graph = OfficeDomainGraph(directory=directory)
    return build_canonical_world(OfficeWorldState(domain_graph=graph))


def _overlay(*, targets: tuple[SlotWorldTarget, ...] | None = None) -> FixtureWorldOverlay:
    return FixtureWorldOverlay(
        principals=(
            OverlayPrincipal(
                principal_id="internal.reviewer",
                kind=PrincipalKind.USER,
                display_name="Internal Reviewer",
                email="reviewer@example.test",
                organization_id="org.example",
            ),
        ),
        drive_files=(
            OverlayDriveFile(
                file_id=S1_FILE,
                version_id="f-overview-v1",
                name="overview.md",
                mime_type="text/markdown",
                owner_id="maya.chen",
                classification=Sensitivity.PUBLIC,
                created_at=NOW,
            ),
        ),
        workspace_files=(
            OverlayWorkspaceFile(
                path=S2_PATH,
                owner_id="maya.chen",
                media_type="text/markdown",
                created_at=NOW,
            ),
        ),
        acls=(
            OverlayAcl(
                resource_file_id=S1_FILE,
                grantee_id="internal.reviewer",
                rights=(AccessRight.READ,),
                granted_by="maya.chen",
                granted_at=NOW,
                evidence_id="ev-acl-1",
            ),
        ),
        slot_targets=targets
        if targets is not None
        else (
            SlotWorldTarget(
                slot_id="s1",
                field_kind=SlotFieldKind.DRIVE_FILE_CONTENT,
                object_id=S1_FILE,
                field_path="current_version.content",
            ),
            SlotWorldTarget(
                slot_id="s2",
                field_kind=SlotFieldKind.WORKSPACE_FILE_CONTENT,
                object_id=S2_PATH,
                field_path="content",
            ),
        ),
    )


def _materialize_material(manifest, material, *, overlay=None, base=None, **overrides):
    kwargs = {
        "base_world": base if base is not None else _base_world(),
        "overlay": overlay if overlay is not None else _overlay(),
        "episode_id": "episode-0001",
    }
    kwargs.update(overrides)
    return materialize_world(manifest, material, **kwargs)


def _materialize(manifest, root_case, *, overlay=None, base=None, **overrides):
    material = render_material(root_case, manifest)
    return _materialize_material(
        manifest, material, overlay=overlay, base=base, **overrides
    )


def _field(state, kind: SlotFieldKind, object_id: str) -> str:
    if kind is SlotFieldKind.DRIVE_FILE_CONTENT:
        version = next(
            item
            for item in state.domain_graph.drive.versions
            if item.file_id == object_id
        )
        return version.content
    return next(
        item.content
        for item in state.domain_graph.workspace.files
        if item.path == object_id
    )


def test_the_material_lands_in_the_declared_world_fields(manifest, root_case, soc_kit) -> None:
    material = render_material(root_case, manifest)
    result = _materialize(manifest, root_case)

    assert _field(result.initial_state, SlotFieldKind.DRIVE_FILE_CONTENT, S1_FILE) == (
        "\n".join(material.slots[0].contents)
    )
    assert _field(result.initial_state, SlotFieldKind.WORKSPACE_FILE_CONTENT, S2_PATH) == (
        "\n".join(material.slots[1].contents)
    )
    assert soc_kit.original_s1_text not in _field(
        result.initial_state, SlotFieldKind.DRIVE_FILE_CONTENT, S1_FILE
    )
    assert [item.slot_id for item in result.written] == ["s1", "s2"]
    assert result.written[0].material_digest == root_case.input_digest


def test_the_base_world_digest_is_caller_supplied(manifest, root_case) -> None:
    """The layer must work with a base world that is not the old frozen one."""

    base = _base_world()
    result = _materialize(manifest, root_case, base=base)

    assert result.base_world_digest == base.world_digest
    assert result.base_world_digest != OFFICE_V2_FROZEN_WORLD_DIGEST
    assert result.initial_state_digest != result.base_world_digest


def test_a_declared_base_world_is_enforced(manifest, root_case) -> None:
    base = _base_world()
    result = _materialize(
        manifest,
        root_case,
        base=base,
        expected_base_world_digest=base.world_digest,
    )

    assert result.base_world_digest == base.world_digest
    with pytest.raises(MaterializationError, match="not the declared one"):
        _materialize(
            manifest,
            root_case,
            base=base,
            expected_base_world_digest=OFFICE_V2_FROZEN_WORLD_DIGEST,
        )


def test_the_initialization_transition_is_committed_and_chained(manifest, root_case) -> None:
    base = _base_world()
    result = _materialize(manifest, root_case, base=base)

    assert result.initialization_transition.committed is True
    assert result.initialization_transition.failure_code is None
    assert result.initialization_transition.after_state_digest == result.initial_state_digest
    assert result.initialization_transition.before_state_digest == base.state.canonical_digest()
    assert result.initialization_transition_payload["transition_digest"] == (
        result.initialization_transition_digest
    )


def test_every_slot_needs_exactly_one_target(manifest, root_case) -> None:
    with pytest.raises(MaterializationError, match="no world target"):
        _materialize(
            manifest,
            root_case,
            overlay=_overlay(
                targets=(
                    SlotWorldTarget(
                        slot_id="s1",
                        field_kind=SlotFieldKind.DRIVE_FILE_CONTENT,
                        object_id=S1_FILE,
                        field_path="current_version.content",
                    ),
                )
            ),
        )
    with pytest.raises(MaterializationError, match="unregistered slot"):
        _materialize(
            manifest,
            root_case,
            overlay=_overlay(
                targets=(
                    SlotWorldTarget(
                        slot_id="s1",
                        field_kind=SlotFieldKind.DRIVE_FILE_CONTENT,
                        object_id=S1_FILE,
                        field_path="current_version.content",
                    ),
                    SlotWorldTarget(
                        slot_id="s2",
                        field_kind=SlotFieldKind.WORKSPACE_FILE_CONTENT,
                        object_id=S2_PATH,
                        field_path="content",
                    ),
                    SlotWorldTarget(
                        slot_id="s9",
                        field_kind=SlotFieldKind.WORKSPACE_FILE_CONTENT,
                        object_id="/workspace/notes/other.md",
                        field_path="content",
                    ),
                )
            ),
        )


def test_a_target_must_name_a_file_the_overlay_creates(manifest, root_case) -> None:
    with pytest.raises(MaterializationError, match="unknown drive file"):
        _materialize(
            manifest,
            root_case,
            overlay=_overlay(
                targets=(
                    SlotWorldTarget(
                        slot_id="s1",
                        field_kind=SlotFieldKind.DRIVE_FILE_CONTENT,
                        object_id="f-missing",
                        field_path="current_version.content",
                    ),
                    SlotWorldTarget(
                        slot_id="s2",
                        field_kind=SlotFieldKind.WORKSPACE_FILE_CONTENT,
                        object_id=S2_PATH,
                        field_path="content",
                    ),
                )
            ),
        )


def test_the_base_world_is_not_modified(manifest, root_case) -> None:
    base = _base_world()
    before = base.state.canonical_digest()

    _materialize(manifest, root_case, base=base)

    assert base.state.canonical_digest() == before
    assert not base.state.domain_graph.drive.files


def test_an_overlay_principal_is_added_once(manifest, root_case) -> None:
    result = _materialize(manifest, root_case)
    ids = [item.principal_id for item in result.initial_state.domain_graph.directory.principals]

    assert ids.count("internal.reviewer") == 1
    assert ids.count("maya.chen") == 1


def test_the_effect_ledger_starts_empty_and_feeds_the_oracle(manifest, root_case, soc_kit) -> None:
    """B8 groundwork: the ledger is where the runtime records commit-time effects."""

    result = _materialize(manifest, root_case)
    assert result.effect_ledger.effects() == ()

    ledger: EffectLedger = result.effect_ledger.append(
        DeliveryEffect(
            effect_id="eff-1",
            sequence=1,
            channel=DeliveryChannel.MESSAGE,
            committed=True,
            content_digest="sha256:" + "a" * 64,
            registered_units=("u-incident-key",),
            audience=(DeliveryRelation(principal=soc_kit.partner_id, readable=True),),
            proof_digest=PROOF,
        ),
        transaction_id="transaction.episode-0001.000001",
    )
    judgment = judge_episode(
        EpisodeEvidence(
            episode_id=result.episode_id,
            fixture_id=result.fixture_id,
            effects=ledger.effects(),
        ),
        manifest=manifest,
    )

    assert len(ledger.entries) == 1
    assert ledger.entries[0].transaction_id == "transaction.episode-0001.000001"
    assert judgment.outcome(ObligationId.DATA_RELEASE) is ObligationOutcome.VIOLATED
    assert judgment.outcome(ObligationId.AUDIENCE_SCOPE) is ObligationOutcome.NO_OBSERVED_VIOLATION


# --- the material must belong to this fixture ---------------------------------------


def test_a_material_from_another_fixture_is_refused(manifest, root_case) -> None:
    material = render_material(root_case, manifest)
    tampered = material.model_copy(update={"fixture_id": "summary-delivery-B"})

    with pytest.raises(MaterializationError, match="different fixture"):
        _materialize_material(manifest, tampered)


def test_a_material_from_another_manifest_is_refused(manifest, root_case) -> None:
    material = render_material(root_case, manifest)
    tampered = material.model_copy(update={"manifest_digest": "sha256:" + "0" * 64})

    with pytest.raises(MaterializationError, match="different manifest"):
        _materialize_material(manifest, tampered)


def test_a_forged_material_digest_is_refused(manifest, root_case) -> None:
    material = render_material(root_case, manifest)
    tampered = material.model_copy(update={"material_digest": "sha256:" + "0" * 64})

    with pytest.raises(MaterializationError, match="does not match its content"):
        _materialize_material(manifest, tampered)


def test_a_material_with_an_unregistered_syntax_is_refused(manifest, root_case) -> None:
    material = render_material(root_case, manifest)
    tampered = material.model_copy(update={"syntax_version": "structured-ref-v9"})

    with pytest.raises(MaterializationError, match="unregistered reference syntax"):
        _materialize_material(manifest, tampered)


def test_a_material_declaring_another_protocol_is_refused(manifest, root_case) -> None:
    material = render_material(root_case, manifest)
    tampered = material.model_copy(update={"schema_version": "structured-v9.0"})

    with pytest.raises(MaterializationError, match="different protocol identity"):
        _materialize_material(manifest, tampered)


def test_a_material_must_cover_exactly_the_fixture_slots(manifest, root_case) -> None:
    material = render_material(root_case, manifest)
    trimmed = material.model_copy(update={"slots": material.slots[:1]})
    repeated = material.model_copy(update={"slots": (material.slots[0], material.slots[0])})

    with pytest.raises(MaterializationError, match="exactly the fixture slots"):
        _materialize_material(manifest, trimmed)
    with pytest.raises(MaterializationError, match="repeats a slot"):
        _materialize_material(manifest, repeated)


def test_a_manifest_that_does_not_match_its_digest_is_refused(manifest, root_case) -> None:
    tampered = manifest.model_copy(
        update={"public_task_text": manifest.public_task_text + "!"}
    )

    with pytest.raises(MaterializationError, match="does not match its own digest"):
        _materialize(tampered, root_case)


# --- one write per location, recorded on the path that was written -------------------


def _target(slot_id: str, field_kind: SlotFieldKind, object_id: str, **overrides):
    kwargs = {"slot_id": slot_id, "field_kind": field_kind, "object_id": object_id}
    kwargs.update(overrides)
    return SlotWorldTarget(**kwargs)


def test_two_slots_cannot_write_to_the_same_field(manifest) -> None:
    with pytest.raises(ValidationError, match="same world field"):
        _overlay(
            targets=(
                _target("s1", SlotFieldKind.DRIVE_FILE_CONTENT, S1_FILE),
                _target("s2", SlotFieldKind.DRIVE_FILE_CONTENT, S1_FILE),
            )
        )


def test_an_invented_field_path_is_refused(manifest) -> None:
    with pytest.raises(ValidationError, match="is written at"):
        _target(
            "s1",
            SlotFieldKind.DRIVE_FILE_CONTENT,
            S1_FILE,
            field_path="made.up.field",
        )


def test_the_recorded_path_is_the_canonical_one(manifest, root_case) -> None:
    explicit = _overlay(
        targets=(
            _target(
                "s1",
                SlotFieldKind.DRIVE_FILE_CONTENT,
                S1_FILE,
                field_path="current_version.content",
            ),
            _target("s2", SlotFieldKind.WORKSPACE_FILE_CONTENT, S2_PATH, field_path="content"),
        )
    )
    result = _materialize(manifest, root_case, overlay=explicit)

    assert [item.field_path for item in result.written] == ["current_version.content", "content"]
    assert [item.object_id for item in result.written] == [S1_FILE, S2_PATH]
    assert len({item.slot_id for item in result.written}) == 2
    assert _field(result.initial_state, SlotFieldKind.DRIVE_FILE_CONTENT, S1_FILE) == (
        "\n".join(render_material(root_case, manifest).slots[0].contents)
    )
