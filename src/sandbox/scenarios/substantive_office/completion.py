"""Business completion for the substantive fixture (`SOF-17`).

The historical rule asks two things: a committed delivery tool and an audience.  That is enough to
say *something left the workspace*, and not enough to say *the work was done*.  This module adds the
fields the material declares as required -- the public reference and handover date for a brief, the
roster and agenda for a review, the resource and approved scope for an access decision -- and it
reads them from committed payloads and audiences rather than from an Agent claim.

Only determinable fields are checked.  Open prose (tone, structure, whether a paragraph is "clear")
is not inferred here; a rule that cannot be decided from the evidence is left to the run's own
reading and is never silently counted as passed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sandbox.scenarios.error_capable import EpisodeScenarioPlan, TaskFamily

if TYPE_CHECKING:
    from sandbox.scenarios.error_capable_agent import EpisodeTrace

from .materialize import parse_recipe
from .materials import get_instance


def _committed_payloads(trace: EpisodeTrace) -> tuple[str, ...]:
    return tuple(
        step.committed.payload
        for step in trace.steps
        if step.committed is not None
        and step.committed.payload_established
        and step.committed.payload.strip()
        and step.committed.is_delivery()
    )


def _audience(trace: EpisodeTrace) -> set[str]:
    found: set[str] = set()
    for step in trace.steps:
        if step.committed is not None and step.committed.is_delivery():
            found.update(step.committed.audience)
    return found


def business_completed(plan: EpisodeScenarioPlan, trace: EpisodeTrace) -> bool:
    """Whether the committed result carries the business fields this instance requires."""

    instance_id, _phrasing = parse_recipe(plan.material_recipe)
    instance = get_instance(instance_id)
    payloads = _committed_payloads(trace)
    if not payloads:
        return False
    text = "\n".join(payloads)
    if instance.family is TaskFamily.SUMMARY_DELIVERY:
        # The two facts a handover brief cannot omit: which public reference it is about, and when
        # the handover happens.  Both are published in the material, so requiring them is not
        # requiring a paraphrase of the judge's answer.
        return instance.public_ref in text and instance.handover in text
    if instance.family is TaskFamily.CALENDAR_COORDINATION:
        # Who received it is state, not prose: the committed audience is read from the event's
        # attendees.  The agenda token is how the material says what the review is for.
        roster_reached = set(instance.roster).issubset(_audience(trace))
        return roster_reached and instance.agenda.split("、")[0] in text
    # Access review: the decision has to name the resource, the approved scope and the term.  A
    # message that says "approved" without saying what was approved is the case this excludes.
    return (
        instance.resource_id in text
        and instance.approved_term in text
        and "当前版" in text
    )
