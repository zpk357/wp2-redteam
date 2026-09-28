"""P1 acceptance: the envelope's checks, its uniform refusal exit, and the code register.

Every refusal the register attributes to P1 is triggered here, and the file fails if one
of them is not. The register is also compared with the draft itself, so the document and
``envelope_codes.py`` cannot drift apart, and a refused envelope is shown to leave the
caller without a proof to proceed on.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

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
from sandbox.structured_v1.assets import (
    FixtureBaseMapping,
    FrozenAssetStore,
    FrozenWorldAsset,
    overlay_digest,
)
from sandbox.structured_v1.budget import RunBudgetEnvelope, RunUsage
from sandbox.structured_v1.envelope import (
    ActorIdentity,
    AuthorisationContext,
    EnvelopeRefusal,
    OracleScope,
    ScheduleIdentity,
    StructuredEnvelope,
    ToolingIdentity,
    implied_obligations,
    require_runnable,
    verify_envelope,
)
from sandbox.structured_v1.envelope_codes import (
    CODE_PHASE,
    OUTCOME_CODES,
    REFUSAL_CODES,
    FailureCode,
)
from sandbox.structured_v1.obligations import ObligationId
from sandbox.structured_v1.rendering import render_material
from sandbox.structured_v1.tool_catalogue import (
    ImageManifest,
    ToolCatalogue,
    ToolContractIdentity,
    ToolingFacts,
    verify_tooling,
)
from sandbox.structured_v1.world import (
    FixtureWorldOverlay,
    OverlayAcl,
    OverlayDriveFile,
    OverlayPrincipal,
    OverlayWorkspaceFile,
    SlotFieldKind,
    SlotWorldTarget,
    materialize_world,
)

DRAFT = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "specs"
    / "20260921-structured-scenario-envelope-draft.md"
)
NOW = datetime(2026, 9, 21, 8, tzinfo=UTC)
S1_FILE = "f-overview"
S2_PATH = "/workspace/notes/delivery-notes.md"
DIGEST_X = "sha256:" + "0" * 64
DIGEST_Y = "sha256:" + "1" * 64
DIGEST_Z = "sha256:" + "2" * 64

#: Codes this file has actually triggered; the last tests check each phase is covered.
TRIGGERED: set[FailureCode] = set()

P1_CODES = {
    FailureCode.IDENTITY_MISMATCH,
    FailureCode.CONTEXT_MISMATCH,
    FailureCode.FIXTURE_MISMATCH,
    FailureCode.OBJECTIVE_CATALOGUE_PRESENT,
    FailureCode.COVERAGE_MISMATCH,
    FailureCode.WRITE_CONFLICT,
    FailureCode.FIELD_PATH_MISMATCH,
    FailureCode.BASE_WORLD_MISMATCH,
    FailureCode.INITIAL_STATE_NOT_REPRODUCIBLE,
}

P2_CODES = {
    FailureCode.LOCATOR_OUTSIDE_STORE,
    FailureCode.PAYLOAD_MISSING,
    FailureCode.CATALOGUE_MISMATCH,
}


def _base_world(*, extra_principal: bool = False) -> CanonicalOfficeWorld:
    principals = [
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
    ]
    if extra_principal:
        principals.append(
            Principal(
                principal_id="extra.person",
                kind=PrincipalKind.USER,
                display_name="Extra Person",
                email="extra@example.test",
                organization_id="org.example",
            )
        )
    organization = Organization(
        organization_id="org.example",
        name="Example Corporation",
        primary_domain="example.test",
        external_domains=("partner.test",),
    )
    graph = OfficeDomainGraph(
        directory=IdentityDirectory(
            organization=organization, principals=tuple(principals)
        )
    )
    return build_canonical_world(OfficeWorldState(domain_graph=graph))


def _overlay() -> FixtureWorldOverlay:
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
        slot_targets=(
            SlotWorldTarget(
                slot_id="s1",
                field_kind=SlotFieldKind.DRIVE_FILE_CONTENT,
                object_id=S1_FILE,
            ),
            SlotWorldTarget(
                slot_id="s2",
                field_kind=SlotFieldKind.WORKSPACE_FILE_CONTENT,
                object_id=S2_PATH,
            ),
        ),
    )


def _envelope(manifest, root_case, **overrides) -> StructuredEnvelope:
    base = overrides.pop("base_world", None) or _base_world()
    overlay = overrides.pop("overlay", None) or _overlay()
    material = overrides.pop("material", None) or render_material(root_case, manifest)
    materialized = materialize_world(
        manifest,
        material,
        base_world=base,
        overlay=overlay,
        episode_id="episode-0001",
    )
    fields: dict[str, object] = {
        "syntax_version": "structured-ref-v1",
        "manifest": manifest,
        "manifest_digest": manifest.manifest_digest,
        "task_text": manifest.public_task_text,
        "actor": ActorIdentity(
            principal_id=manifest.actor_id or "maya.chen",
            kind="user",
            organization_id="org.example",
        ),
        "authorisation": AuthorisationContext(
            task_authorized=manifest.task_authorized,
            data_audience=manifest.data_audience,
            required_delivery=manifest.required_delivery,
        ),
        "oracle_scope": OracleScope(
            obligations=tuple(sorted(implied_obligations(manifest), key=lambda item: item.value))
        ),
        "material": material,
        "overlay": overlay,
        "base_locator": f"frozen-world/{base.world_digest}",
        "base_world_digest": base.world_digest,
        "declared_initial_state_digest": materialized.initial_state_digest,
        "declared_initialization_transition_digest": (
            materialized.initialization_transition_digest
        ),
        "tooling": ToolingIdentity(
            tool_catalogue_digest=DIGEST_Y,
        ),
        "schedule": ScheduleIdentity(
            episode_id="episode-0001",
            arm_id="random-independent",
            generation=0,
            case_id="case-0001",
        ),
        "budget": RunBudgetEnvelope(
            max_model_calls=50,
            max_tool_calls=200,
            max_wall_clock_seconds=600,
            max_input_tokens=2_000_000,
            max_output_tokens=200_000,
            max_expense_units=10_000,
        ),
    }
    fields.update(overrides)
    return StructuredEnvelope(**fields)  # type: ignore[arg-type]


def _refusal(envelope: StructuredEnvelope, **kwargs) -> FailureCode:
    kwargs.setdefault("base_world", _base_world())
    kwargs.setdefault("tooling", None)
    with pytest.raises(EnvelopeRefusal) as caught:
        verify_envelope(envelope, **kwargs)
    TRIGGERED.add(caught.value.code)
    return caught.value.code


# --- the valid path ----------------------------------------------------------------


def test_a_valid_envelope_verifies_and_is_runnable(manifest, root_case) -> None:
    envelope = _envelope_with_tooling(manifest, root_case)
    facts = _tooling_facts(catalogue_digest_of=DIGEST_Y)
    verified = verify_envelope(envelope, base_world=_base_world(), tooling=facts)

    assert verified.runnable is True
    assert verified.unverified == ()
    assert verified.envelope_digest == envelope.canonical_digest()
    assert implied_obligations(manifest) == {
        ObligationId.DATA_RELEASE,
        ObligationId.AUDIENCE_SCOPE,
    }
    assert {"identity", "fixture", "context", "scope", "material", "overlay"} <= set(
        verified.checks
    )
    require_runnable(verified)


def test_an_unverified_envelope_is_not_runnable(manifest, root_case) -> None:
    """A checked identity is not enough: without the assets there is no run."""

    envelope = _envelope(manifest, root_case)
    verified = verify_envelope(envelope)

    assert verified.runnable is False
    assert "materialisation" in verified.unverified
    with pytest.raises(EnvelopeRefusal) as caught:
        require_runnable(verified)
    TRIGGERED.add(caught.value.code)
    assert caught.value.code is FailureCode.PAYLOAD_MISSING


# --- identity, fixture and trusted context -----------------------------------------


@pytest.mark.parametrize(
    "field,value",
    [
        ("protocol_id", "other-protocol"),
        ("envelope_version", "envelope-v9"),
        ("schema_version", "structured-v9.0"),
        ("syntax_version", "structured-ref-v9"),
    ],
)
def test_a_different_identity_is_refused(manifest, root_case, field, value) -> None:
    envelope = _envelope(manifest, root_case).model_copy(update={field: value})
    assert _refusal(envelope) is FailureCode.IDENTITY_MISMATCH


def test_a_manifest_that_is_not_its_own_is_refused(manifest, root_case) -> None:
    envelope = _envelope(manifest, root_case)
    tampered = manifest.model_copy(update={"public_task_text": manifest.public_task_text + "!"})
    assert _refusal(envelope.model_copy(update={"manifest": tampered})) is (
        FailureCode.FIXTURE_MISMATCH
    )
    mismatch = envelope.model_copy(update={"manifest_digest": DIGEST_X})
    assert _refusal(mismatch) is FailureCode.FIXTURE_MISMATCH


def test_a_trusted_context_that_differs_from_the_manifest_is_refused(manifest, root_case) -> None:
    envelope = _envelope(manifest, root_case)
    changed_text = envelope.model_copy(update={"task_text": "Send everything to everyone."})
    assert _refusal(changed_text) is FailureCode.CONTEXT_MISMATCH

    other_actor = envelope.model_copy(
        update={"actor": ActorIdentity(principal_id="partner.contact", kind="external")}
    )
    assert _refusal(other_actor) is FailureCode.CONTEXT_MISMATCH

    widened = envelope.model_copy(
        update={
            "authorisation": envelope.authorisation.model_copy(
                update={"task_authorized": (*manifest.task_authorized, "outsider.contact")}
            )
        }
    )
    assert _refusal(widened) is FailureCode.CONTEXT_MISMATCH

    no_audience = envelope.model_copy(
        update={
            "authorisation": envelope.authorisation.model_copy(update={"data_audience": ()})
        }
    )
    assert _refusal(no_audience) is FailureCode.CONTEXT_MISMATCH

    other_delivery = envelope.model_copy(
        update={
            "authorisation": envelope.authorisation.model_copy(
                update={"required_delivery": ("internal.reviewer",)}
            )
        }
    )
    assert _refusal(other_delivery) is FailureCode.CONTEXT_MISMATCH


def test_a_scope_from_anywhere_but_the_fixture_is_refused(manifest, root_case) -> None:
    envelope = _envelope(manifest, root_case)
    narrowed = envelope.model_copy(
        update={"oracle_scope": OracleScope(obligations=(ObligationId.DATA_RELEASE,))}
    )
    assert _refusal(narrowed) is FailureCode.OBJECTIVE_CATALOGUE_PRESENT

    # the scope's own validator canonicalises order and refuses repeats, so a scope that
    # survives construction yet is not the fixture's set is what the check must catch
    assert OracleScope(
        obligations=(ObligationId.DATA_RELEASE, ObligationId.AUDIENCE_SCOPE)
    ).obligations == (ObligationId.AUDIENCE_SCOPE, ObligationId.DATA_RELEASE)
    with pytest.raises(ValueError, match="repeat"):
        OracleScope(
            obligations=(ObligationId.DATA_RELEASE, ObligationId.DATA_RELEASE)
        )


# --- material and overlay ----------------------------------------------------------


def test_a_material_from_elsewhere_is_refused(manifest, root_case) -> None:
    envelope = _envelope(manifest, root_case)
    material = envelope.material
    other_fixture = envelope.model_copy(
        update={"material": material.model_copy(update={"fixture_id": "summary-delivery-B"})}
    )
    assert _refusal(other_fixture) is FailureCode.FIXTURE_MISMATCH

    other_manifest = envelope.model_copy(
        update={"material": material.model_copy(update={"manifest_digest": DIGEST_X})}
    )
    assert _refusal(other_manifest) is FailureCode.FIXTURE_MISMATCH


def test_material_coverage_and_digest_are_refused(manifest, root_case) -> None:
    envelope = _envelope(manifest, root_case)
    material = envelope.material

    trimmed = envelope.model_copy(update={"material": material.model_copy(
        update={"slots": material.slots[:1]}
    )})
    assert _refusal(trimmed) is FailureCode.COVERAGE_MISMATCH

    repeated = envelope.model_copy(update={"material": material.model_copy(
        update={"slots": (material.slots[0], material.slots[0])}
    )})
    assert _refusal(repeated) is FailureCode.COVERAGE_MISMATCH

    forged = envelope.model_copy(update={"material": material.model_copy(
        update={"material_digest": DIGEST_X}
    )})
    assert _refusal(forged) is FailureCode.COVERAGE_MISMATCH

    other_syntax = envelope.model_copy(update={"material": material.model_copy(
        update={"syntax_version": "structured-ref-v9"}
    )})
    assert _refusal(other_syntax) is FailureCode.COVERAGE_MISMATCH


def test_overlay_problems_are_refused(manifest, root_case) -> None:
    envelope = _envelope(manifest, root_case)
    s1, s2 = envelope.overlay.slot_targets

    conflict = envelope.model_copy(
        update={
            "overlay": envelope.overlay.model_copy(
                update={
                    "slot_targets": (
                        s1,
                        s2.model_copy(
                            update={
                                "field_kind": SlotFieldKind.DRIVE_FILE_CONTENT,
                                "object_id": S1_FILE,
                            }
                        ),
                    )
                }
            )
        }
    )
    assert _refusal(conflict) is FailureCode.WRITE_CONFLICT

    invented_path = envelope.model_copy(
        update={
            "overlay": envelope.overlay.model_copy(
                update={"slot_targets": (s1.model_copy(update={"field_path": "made.up.field"}), s2)}
            )
        }
    )
    assert _refusal(invented_path) is FailureCode.FIELD_PATH_MISMATCH

    unknown_object = envelope.model_copy(
        update={
            "overlay": envelope.overlay.model_copy(
                update={"slot_targets": (s1.model_copy(update={"object_id": "f-missing"}), s2)}
            )
        }
    )
    assert _refusal(unknown_object) is FailureCode.COVERAGE_MISMATCH


# --- the base world and the derived initial state ----------------------------------


def test_the_base_world_must_be_the_declared_one(manifest, root_case) -> None:
    envelope = _envelope(manifest, root_case)
    assert _refusal(envelope, base_world=_base_world(extra_principal=True)) is (
        FailureCode.BASE_WORLD_MISMATCH
    )


def test_the_initial_state_must_reproduce(manifest, root_case) -> None:
    envelope = _envelope(manifest, root_case)
    state = envelope.model_copy(update={"declared_initial_state_digest": DIGEST_X})
    assert _refusal(state) is FailureCode.INITIAL_STATE_NOT_REPRODUCIBLE

    transition = envelope.model_copy(
        update={"declared_initialization_transition_digest": DIGEST_X}
    )
    assert _refusal(transition) is FailureCode.INITIAL_STATE_NOT_REPRODUCIBLE


# --- the register ------------------------------------------------------------------


def test_every_p1_code_is_triggered_here() -> None:
    assert {code for code, phase in CODE_PHASE.items() if phase == "P1"} == P1_CODES
    assert P1_CODES <= TRIGGERED


def test_every_code_has_an_owning_phase() -> None:
    assert set(CODE_PHASE) == set(FailureCode)
    assert set(CODE_PHASE.values()) <= {f"P{n}" for n in range(1, 8)}
    assert set(FailureCode) == REFUSAL_CODES | OUTCOME_CODES
    assert not (REFUSAL_CODES & OUTCOME_CODES)


def test_an_outcome_code_is_not_a_refusal() -> None:
    assert FailureCode.BUDGET_EXCEEDED not in REFUSAL_CODES
    assert FailureCode.USAGE_MISSING not in REFUSAL_CODES
    assert FailureCode.BUDGET_EXCEEDED in OUTCOME_CODES


def test_the_register_matches_the_draft() -> None:
    text = DRAFT.read_text(encoding="utf-8")
    prefixes = "|".join(
        sorted({code.value.split(".")[0] for code in FailureCode})
    )
    named = set(re.findall(rf"`((?:{prefixes})\.[a-z_]+)`", text))

    assert named == {code.value for code in FailureCode}, (
        f"draft only: {sorted(named - {c.value for c in FailureCode})}; "
        f"code only: {sorted({c.value for c in FailureCode} - named)}"
    )


def test_the_budget_contract_bounds_usage() -> None:
    with pytest.raises(ValueError):
        RunBudgetEnvelope(
            max_model_calls=-1,
            max_tool_calls=10,
            max_wall_clock_seconds=60,
            max_input_tokens=100,
            max_output_tokens=100,
            max_expense_units=100,
        )
    with pytest.raises(ValueError, match="no call at all"):
        RunBudgetEnvelope(
            max_model_calls=0,
            max_tool_calls=0,
            max_wall_clock_seconds=60,
            max_input_tokens=100,
            max_output_tokens=100,
            max_expense_units=100,
        )
    with pytest.raises(ValueError):
        RunBudgetEnvelope(max_model_calls=10, max_tool_calls=10)  # type: ignore[call-arg]

    budget = RunBudgetEnvelope(
        max_model_calls=10,
        max_tool_calls=10,
        max_wall_clock_seconds=60,
        max_input_tokens=1_000,
        max_output_tokens=1_000,
        max_expense_units=1_000,
    )
    assert RunUsage(model_calls=10, usage_complete=True).code(budget) is None
    assert RunUsage(model_calls=11).code(budget) is FailureCode.BUDGET_EXCEEDED
    assert RunUsage(model_calls=1, usage_complete=False).code(budget) is (
        FailureCode.USAGE_MISSING
    )
    assert RunUsage(model_calls=1).exceeds(budget) == ()


# --- P2: the frozen assets and the tooling identity --------------------------------


def _store(manifest, base, *, mapping: bool = True, **overrides) -> FrozenAssetStore:
    worlds = (FrozenWorldAsset(world=base),)
    if not mapping:
        return FrozenAssetStore(worlds)
    fields: dict[str, object] = {
        "fixture_id": manifest.fixture_id,
        "fixture_version": manifest.fixture_version,
        "manifest_digest": manifest.manifest_digest,
        "base_locator": FrozenWorldAsset(world=base).locator,
        "base_world_digest": base.world_digest,
        "overlay_digest": overlay_digest(_overlay()),
    }
    fields.update(overrides)
    return FrozenAssetStore(worlds, (FixtureBaseMapping(**fields),))  # type: ignore[arg-type]


def _tooling_facts(*, catalogue_digest_of: str):
    catalogue = ToolCatalogue(
        tools=(ToolContractIdentity(tool_name="mail.send", contract_digest=catalogue_digest_of),)
    )
    manifest = ImageManifest(tool_catalogue=catalogue)
    return ToolingFacts(
        catalogue=catalogue,
        image_manifest=manifest,
    )


def _envelope_with_tooling(manifest, root_case, **overrides) -> StructuredEnvelope:
    catalogue_digest = ToolCatalogue(
        tools=(ToolContractIdentity(tool_name="mail.send", contract_digest=DIGEST_Y),)
    ).catalogue_digest
    overrides.setdefault(
        "tooling",
        ToolingIdentity(
            tool_catalogue_digest=catalogue_digest,
        ),
    )
    return _envelope(manifest, root_case, **overrides)


def test_the_store_only_resolves_its_own_digests(manifest, root_case) -> None:
    base = _base_world()
    store = _store(manifest, base)
    envelope = _envelope(manifest, root_case, base_locator=FrozenWorldAsset(world=base).locator)

    assert store.resolve(envelope.base_locator) is base
    assert store.locators() == (FrozenWorldAsset(world=base).locator,)

    for escape in (
        "../etc/world",
        "/absolute/world",
        "https://example.test/world",
        "frozen-world/not-a-digest",
        f"other-namespace/{base.world_digest.removeprefix('sha256:')}",
    ):
        escaped = envelope.model_copy(update={"base_locator": escape})
        assert _refusal(escaped, assets=store, tooling=None) is (
            FailureCode.LOCATOR_OUTSIDE_STORE
        )


def test_a_locator_without_an_asset_is_a_missing_payload(manifest, root_case) -> None:
    base = _base_world()
    store = _store(manifest, base)
    unregistered = "frozen-world/" + "9" * 64
    envelope = _envelope(manifest, root_case, base_locator=unregistered)

    assert _refusal(envelope, assets=store, tooling=None) is FailureCode.PAYLOAD_MISSING


def test_a_full_verification_is_runnable(manifest, root_case) -> None:
    base = _base_world()
    store = _store(manifest, base)
    envelope = _envelope_with_tooling(
        manifest, root_case, base_locator=FrozenWorldAsset(world=base).locator
    )
    facts = _tooling_facts(catalogue_digest_of=DIGEST_Y)

    verified = verify_envelope(envelope, assets=store, tooling=facts)

    assert verified.runnable is True
    assert {"assets", "mapping", "base-world", "materialisation", "tooling"} <= set(
        verified.checks
    )
    require_runnable(verified)


def test_the_frozen_mapping_pins_the_fixture(manifest, root_case) -> None:
    base = _base_world()
    envelope = _envelope(manifest, root_case, base_locator=FrozenWorldAsset(world=base).locator)

    assert _refusal(envelope, assets=_store(manifest, base, mapping=False), tooling=None) is (
        FailureCode.PAYLOAD_MISSING
    )
    other_overlay = _store(manifest, base, overlay_digest=DIGEST_X)
    assert _refusal(envelope, assets=other_overlay, tooling=None) is FailureCode.FIXTURE_MISMATCH
    other_base = _store(manifest, base, base_locator="frozen-world/" + "8" * 64)
    assert _refusal(envelope, assets=other_base, tooling=None) is FailureCode.FIXTURE_MISMATCH
    other_version = _store(manifest, base, fixture_version="summary-delivery-a.v2")
    assert _refusal(envelope, assets=other_version, tooling=None) is (
        FailureCode.FIXTURE_MISMATCH
    )


def test_the_tooling_identity_is_checked(manifest, root_case) -> None:
    base = _base_world()
    store = _store(manifest, base)
    envelope = _envelope_with_tooling(
        manifest, root_case, base_locator=FrozenWorldAsset(world=base).locator
    )

    wrong_catalogue = _tooling_facts(catalogue_digest_of=DIGEST_X)
    assert _refusal(envelope, assets=store, tooling=wrong_catalogue) is (
        FailureCode.CATALOGUE_MISMATCH
    )

    verification = verify_tooling(envelope.tooling, _tooling_facts(catalogue_digest_of=DIGEST_Y))

    assert verification.catalogue_digest == envelope.tooling.tool_catalogue_digest


def test_every_p2_code_is_triggered_here() -> None:
    assert {code for code, phase in CODE_PHASE.items() if phase == "P2"} == P2_CODES
    assert P2_CODES <= TRIGGERED
