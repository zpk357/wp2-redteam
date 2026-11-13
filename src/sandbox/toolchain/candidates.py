"""Candidate lineage under a mutation guard.

``SPEC.md`` TC-04 splits a scenario into two parts that must never mix: the part
the mutator may edit (low-trust material, payload, layout, carrier position) and
the part it must not (the task, the authorisation table, tool permissions, the
protected facts, target resource identities).  ``TASK.md`` TASK-04 measures one
number about that split: how many forbidden edits take effect.  The answer is
supposed to be **zero**, and the only way to prove zero is to try.

The guard is a whitelist, not a blacklist.  Editing ``payload`` works because it
is on the list; editing ``task_text`` fails because it is not, and so does any
key nobody registered -- a mutator that invents a slot has still written to the
scenario.  ``FROZEN_KEYS`` exists only so the rejection can name the rule it
broke; it is not the boundary itself.

A candidate carries the lineage TC-04 asks for: parent, target dependency,
carrier position, edits, generator identity, feedback digest and cost.  Cost is
charged whether or not the candidate is admissible -- ``TASK.md``: "生成与校验
成本仍入账" -- so a rejected candidate is a recorded expense, not an erased one.

Nothing here decides whether a candidate is *effective*.  This module only
answers what it is allowed to change.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

#: Slots a mutator may edit during the search phase.  ``SPEC.md`` §14.3: the
#: search phase may only touch registered low-trust material, payloads, layouts.
EDITABLE_SLOTS = frozenset(
    {"low_trust_material", "payload", "layout", "carrier_position"}
)

#: The names a scenario freezes.  Used to *explain* a rejection, not to allow one:
#: the whitelist above is the rule.
FROZEN_KEYS = frozenset(
    {
        "task_text",
        "authorized_recipients",
        "policy_rules",
        "protected_facts",
        "target_resources",
    }
)


class MutationRefused(ValueError):
    """A mutation the guard would not let near the scene."""


@dataclass(frozen=True)
class Edit:
    """One slot's new value."""

    slot: str
    value: str


@dataclass(frozen=True)
class FrozenState:
    """What the guard hashes before and after a proposal.

    The fields mirror ``FROZEN_KEYS`` so a caller can build this from a real
    scenario; ``digest`` is what candidates carry so a later reader can tell
    whether the scene has moved under a lineage.
    """

    task_text: str = ""
    authorized_recipients: tuple[str, ...] = ()
    policy_rules: tuple[str, ...] = ()
    protected_facts: tuple[str, ...] = ()
    target_resources: tuple[str, ...] = ()

    def digest(self) -> str:
        payload = {
            "task_text": self.task_text,
            "authorized_recipients": list(self.authorized_recipients),
            "policy_rules": list(self.policy_rules),
            "protected_facts": list(self.protected_facts),
            "target_resources": list(self.target_resources),
        }
        blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return "sha256:" + hashlib.sha256(blob).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_text": self.task_text,
            "authorized_recipients": list(self.authorized_recipients),
            "policy_rules": list(self.policy_rules),
            "protected_facts": list(self.protected_facts),
            "target_resources": list(self.target_resources),
            "digest": self.digest(),
        }


@dataclass(frozen=True)
class Candidate:
    """One proposed scene edit, with the lineage TC-04 requires.

    ``admissible`` is the guard's answer, not the search's.  A rejected candidate
    still carries its edits and its cost so the ledger can show the attempt.
    """

    candidate_id: str
    parent_id: str | None
    target_dependency: str | None
    carrier_position: str | None
    edits: tuple[Edit, ...]
    #: Why this candidate exists.  TC-04 lists it as lineage, not as a log line.
    reason: str
    generator: str
    feedback_digest: str
    cost: int
    frozen_digest: str
    admissible: bool
    rejection: str | None = None

    def edit_map(self) -> dict[str, str]:
        return {edit.slot: edit.value for edit in self.edits}

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "parent_id": self.parent_id,
            "target_dependency": self.target_dependency,
            "carrier_position": self.carrier_position,
            "edits": [{"slot": edit.slot, "value": edit.value} for edit in self.edits],
            "reason": self.reason,
            "generator": self.generator,
            "feedback_digest": self.feedback_digest,
            "cost": self.cost,
            "frozen_digest": self.frozen_digest,
            "admissible": self.admissible,
            "rejection": self.rejection,
        }


@dataclass
class GuardedEvolution:
    """The ledger: every proposal, admissible or not, with its cost counted."""

    frozen: FrozenState
    candidates: list[Candidate] = field(default_factory=list)
    _next: int = 0

    def propose(
        self,
        *,
        edits: Sequence[Edit],
        reason: str,
        generator: str,
        parent: Candidate | None = None,
        target_dependency: str | None = None,
        carrier_position: str | None = None,
        feedback_digest: str = "",
        cost: int = 1,
    ) -> Candidate:
        """Record a proposal against the guard's verdict.

        ``reason`` is part of the lineage, not a debug note: TC-04 requires it and
        the ledger rejects a proposal that omits it.
        """

        if not reason.strip():
            raise MutationRefused("a candidate must state why it exists")
        candidate_id = f"cand.{self._next:03d}"
        self._next += 1

        rejection = self._check(edits)
        candidate = Candidate(
            candidate_id=candidate_id,
            parent_id=parent.candidate_id if parent else None,
            target_dependency=target_dependency,
            carrier_position=carrier_position,
            edits=tuple(edits),
            reason=reason,
            generator=generator,
            feedback_digest=feedback_digest,
            cost=cost,
            frozen_digest=self.frozen.digest(),
            admissible=rejection is None,
            rejection=rejection,
        )
        self.candidates.append(candidate)
        return candidate

    def _check(self, edits: Sequence[Edit]) -> str | None:
        for edit in edits:
            if edit.slot in FROZEN_KEYS:
                return f"frozen-field:{edit.slot}"
            if edit.slot not in EDITABLE_SLOTS:
                return f"unregistered-slot:{edit.slot}"
        return None

    def summary(self) -> dict[str, Any]:
        known = {item.candidate_id for item in self.candidates}
        admissible = [item for item in self.candidates if item.admissible]
        forbidden = [
            item
            for item in self.candidates
            if not item.admissible and item.rejection
            and (
                item.rejection.startswith("frozen-field:")
                or item.rejection.startswith("unregistered-slot:")
            )
        ]
        edit_attempts = sum(len(item.edits) for item in self.candidates)
        return {
            "frozen_digest": self.frozen.digest(),
            "candidates": len(self.candidates),
            "admissible": len(admissible),
            "refused": len(self.candidates) - len(admissible),
            "forbidden_edit_attempts": sum(len(item.edits) for item in forbidden),
            # The TASK-04 number: forbidden mutations that took effect.  A
            # rejected proposal changed nothing, so this counts admissions.
            "forbidden_edits_effective": sum(
                1 for item in admissible if any(edit.slot in FROZEN_KEYS for edit in item.edits)
            ),
            "edit_attempts": edit_attempts,
            "total_cost": sum(item.cost for item in self.candidates),
            # Every candidate either starts a line (parent None) or points at a
            # candidate that exists in this ledger; a dangling parent is a broken
            # lineage, and so is a candidate with no recorded reason.
            "lineage_complete": all(
                (item.parent_id is None or item.parent_id in known)
                and bool(item.reason.strip())
                for item in self.candidates
            ),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.summary(),
            "frozen": self.frozen.to_dict(),
            "candidates": [item.to_dict() for item in self.candidates],
        }
