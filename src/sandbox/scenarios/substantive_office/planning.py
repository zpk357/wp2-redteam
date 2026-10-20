"""Deterministic instance and phrasing selection for an Episode.

The selector still chooses the five frozen coordinates.  Which of the family's two business
instances an Episode uses, and which of the two attack phrasings it carries, is a property of the
fixture and the seed -- recorded in the plan's `material_recipe` and therefore covered by the plan
digest, the material digest and the journal's identity.

The assignment is deliberately not a new menu axis: the LLM never chooses the instance, so the
prompt, the receipt and the older fixture's payload are unchanged.
"""

from __future__ import annotations

from sandbox.scenarios.error_capable import TaskFamily

from . import RECIPE_PREFIX
from .attacks import phrasing_for
from .materials import Instance, instances_for


def instance_for(family: TaskFamily, seed: int) -> Instance:
    options = instances_for(family)
    if not options:
        raise ValueError(f"no substantive instance for {family.value}")
    return options[seed % len(options)]


def recipe_for(family: TaskFamily, seed: int) -> str:
    instance = instance_for(family, seed)
    return f"{RECIPE_PREFIX}{instance.instance_id}.{phrasing_for(seed)}"
