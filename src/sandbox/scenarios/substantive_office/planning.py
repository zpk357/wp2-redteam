"""Deterministic instance and phrasing selection for an Episode.

The selector still chooses the five frozen coordinates.  Which of the family's two business
instances an Episode uses, and which of the two attack phrasings it carries, is a property of the
fixture and the seed -- recorded in the plan's `material_recipe` and therefore covered by the plan
digest, the material digest and the journal's identity.

The assignment is deliberately not a new menu axis: the LLM never chooses the instance, so the
prompt, the receipt and the older fixture's payload are unchanged.
"""

from __future__ import annotations

from sandbox.scenarios.error_capable import EpisodeScenarioPlan, TaskFamily

from . import RECIPE_PREFIX
from .attacks import phrasing_for
from .materials import Instance, get_instance, instances_for


def instance_for(family: TaskFamily, seed: int) -> Instance:
    options = instances_for(family)
    if not options:
        raise ValueError(f"no substantive instance for {family.value}")
    return options[seed % len(options)]


def recipe_for(family: TaskFamily, seed: int) -> str:
    instance = instance_for(family, seed)
    return f"{RECIPE_PREFIX}{instance.instance_id}.{phrasing_for(seed)}"


def bind_material_plan(
    plan: EpisodeScenarioPlan, *, instance_id: str | None = None, phrasing: str | None = None
) -> EpisodeScenarioPlan:
    """Freeze real business authority references, without changing the selector schema."""
    instance = (
        get_instance(instance_id) if instance_id else instance_for(plan.task_family, plan.seed)
    )
    if instance.family is not plan.task_family:
        raise ValueError("instance belongs to another task family")
    ledger_id = f"{instance.approval_ref.lower()}.audience"
    return plan.model_copy(
        update={
            "material_recipe": (
                f"{RECIPE_PREFIX}{instance.instance_id}.{phrasing or phrasing_for(plan.seed)}"
            ),
            "permission_sources": {
                f"{instance.approval_ref.lower()}.request": plan.file_ids[1],
                ledger_id: plan.file_ids[6],
            },
            "fact_basis_map": {
                f"{plan.task_family.value}.{suffix}": (ledger_id,)
                for suffix in ("public-reference", "restricted-target")
            },
        }
    )
