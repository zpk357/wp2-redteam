"""Materialise one substantive-office Episode from its frozen plan.

The plan carries its selection in `material_recipe` (fixture/version/instance/phrasing), so nothing
in the plan model had to grow a coordinate for this fixture and the historical fixture's
serialisation is unchanged.  The documents come from `materials`; the carrier text is substituted
into the sentence the clean document already holds at that position, which keeps CSV, ICS and mail
formats intact.
"""

from __future__ import annotations

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.error_capable import (
    ATTACK_SPECS,
    CarrierBinding,
    ContentFact,
    EpisodeScenarioPlan,
    MaterializedScenario,
    PermissionBasis,
    ScenarioFile,
    TaskFamily,
)
from sandbox.scenarios.error_capable_world import (
    CARRIER_SLOTS,
    DECISION_DIMENSIONS,
    file_path,
)

from . import RECIPE_PREFIX
from .attacks import attack_text
from .materials import TITLES, build_documents, get_instance

_RECIPIENTS: dict[TaskFamily, tuple[str, ...]] = {
    TaskFamily.SUMMARY_DELIVERY: ("partner.contact",),
    TaskFamily.CALENDAR_COORDINATION: ("user.maya.chen",),
    TaskFamily.ACCESS_REVIEW: ("user.maya.chen",),
}


def parse_recipe(recipe: str) -> tuple[str, str]:
    """Return (instance_id, phrasing) from a substantive recipe, or raise."""

    from .materials import INSTANCES

    if not recipe.startswith(RECIPE_PREFIX):
        raise ValueError("not a substantive-office material recipe")
    for instance in INSTANCES:
        prefix = f"{RECIPE_PREFIX}{instance.instance_id}."
        if recipe.startswith(prefix):
            phrasing = recipe[len(prefix) :]
            if not phrasing:
                raise ValueError("substantive recipe must name an attack phrasing")
            return instance.instance_id, phrasing
    raise ValueError("substantive recipe names an unregistered instance")


def materialize_substantive(plan: EpisodeScenarioPlan) -> MaterializedScenario:
    instance_id, phrasing = parse_recipe(plan.material_recipe)
    instance = get_instance(instance_id)
    if instance.family is not plan.task_family:
        raise ValueError("material instance belongs to another task family")
    ids = plan.file_ids
    paths = [file_path(plan, value) for value in ids]
    documents = build_documents(instance, paths)
    if len(documents) < len(ids):
        raise ValueError("material does not cover the selected layout")

    bindings = tuple(
        CarrierBinding(
            attack_mode=spec.attack_mode,
            carrier_id=carrier,
            file_id=ids[slot],
            field=field,
            decision_dimension=DECISION_DIMENSIONS[spec.attack_mode],
            expected_stage=spec.decision_stage,
        )
        for spec in ATTACK_SPECS
        for carrier in CARRIER_SLOTS
        for slot, field in (CARRIER_SLOTS[carrier],)
    )
    selected = next(
        item
        for item in bindings
        if item.attack_mode is plan.attack_mode and item.carrier_id == plan.attack_carrier
    )
    payload = attack_text(
        instance,
        mode=plan.attack_mode,
        phrasing=phrasing,
        index_path=paths[1],
        ledger_path=paths[6],
        worksheet_path=paths[5],
    )

    files: list[ScenarioFile] = []
    for index, (file_id, role, (body, snippet)) in enumerate(
        zip(ids, plan.file_roles, documents[: len(ids)], strict=True)
    ):
        fields: dict[str, str] = {}
        rendered = body
        for carrier, (slot, field) in CARRIER_SLOTS.items():
            if slot != index:
                continue
            if not snippet:
                raise ValueError(f"carrier slot {carrier} has no business sentence to carry text")
            value = payload if file_id == selected.file_id else snippet
            fields[field] = value
            if file_id == selected.file_id and carrier == selected.carrier_id:
                if snippet not in body:
                    raise ValueError("carrier sentence is missing from the clean document")
                rendered = body.replace(snippet, payload)
        files.append(
            ScenarioFile(
                file_id=file_id,
                role=role,
                title=TITLES[index],
                body=rendered,
                fields=fields,
                discovery_terms=("work", "request") if index < 2 else ("record",),
            )
        )
    source = next(item for item in files if item.file_id == selected.file_id)
    if payload not in source.body:
        raise ValueError("the selected carrier text did not reach the rendered body")

    bases = (
        PermissionBasis(
            basis_id=f"{plan.task_family.value}.work-order",
            source_file_id=ids[1],
            discovery_terms=("work", "index"),
        ),
        PermissionBasis(
            basis_id=f"{plan.task_family.value}.audience-ledger",
            source_file_id=ids[6],
            discovery_terms=("audience", "evidence"),
            fact_ids=(
                f"{plan.task_family.value}.public-reference",
                f"{plan.task_family.value}.restricted-target",
            ),
        ),
    )
    facts = (
        ContentFact(
            fact_id=f"{plan.task_family.value}.public-reference",
            canonical_value=instance.public_ref,
            restricted=False,
            permission_basis_ids=(f"{plan.task_family.value}.audience-ledger",),
        ),
        ContentFact(
            fact_id=f"{plan.task_family.value}.restricted-target",
            canonical_value=instance.restricted_ref,
            readable_by=(instance.reviewer,),
            permission_basis_ids=(f"{plan.task_family.value}.audience-ledger",),
        ),
    )
    values = {
        "plan": plan,
        "files": tuple(files),
        "task_file_id": plan.task_file_id,
        "attack_carrier_file_id": selected.file_id,
        "facts": facts,
        "authorized_recipients": _RECIPIENTS[plan.task_family],
        "permission_bases": bases,
        "carrier_bindings": bindings,
    }
    placeholder = MaterializedScenario.model_construct(
        **values, materialization_digest="sha256:" + "0" * 64
    )
    material = MaterializedScenario(
        **values, materialization_digest=sha256_digest(placeholder.digest_payload())
    )
    if len(material.files) != len(documents[: len(ids)]):
        raise ValueError("material file count does not match the plan")
    return material
