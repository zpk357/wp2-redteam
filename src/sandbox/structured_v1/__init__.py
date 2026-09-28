"""Structured-scenario search space (``SPEC-SOC-SRC-20260921``, route SOC).

This package is the new protocol identity.  The search object is structured
low-trust material held in registered slots rather than a rewritten attack
request, and admissibility is decided by the frozen fixture manifest plus the
bounded grammar of the search contract.

It deliberately shares no target semantics with ``office_v2``: no fixed target
catalogue, no step-shape matching and no per-seed task wording live here.  What
it reuses is neutral machinery only (canonical JSON digests, later the execution
bridge and the campaign store).
"""
