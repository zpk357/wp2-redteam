"""Workflow search layer: chains, candidates, diagnosis, dispatch.

This package holds the new layer described in ``SPEC.md`` §9.1 and revised in
§14.  It does **not** execute an Agent and does **not** judge a run -- the
sandbox runtime and the committed-state Oracle keep those jobs.

The first thing built here is the opposite of a search: :mod:`constraints`
recovers the security constraints a run was actually judged against, so that
the per-case Oracle can later be replaced by one evaluator over declared data.
"""

from sandbox.toolchain.constraints import (
    AudienceGrant,
    ConstraintSet,
    PolicyRule,
    load_world_constraints,
)

__all__ = [
    "AudienceGrant",
    "ConstraintSet",
    "PolicyRule",
    "load_world_constraints",
]
