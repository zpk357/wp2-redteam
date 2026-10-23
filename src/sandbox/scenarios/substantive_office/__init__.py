"""The fixture: six business instances on the Office V2 protocol.

The material lives entirely in this package: two instances per task family, their documents, the
judge-side declarations and the attack library.  It is the only fixture the runner loads.
"""

from __future__ import annotations

SUBSTANTIVE_FIXTURE_ID = "substantive-office-01"
SUBSTANTIVE_FIXTURE_VERSION = "1.3.0"
#: The plan's free-form `material_recipe` names the fixture, the version, the instance and the
#: attack phrasing.  It is the only plan field that carries the selection, so the older fixture's
#: serialisation is untouched.
RECIPE_PREFIX = f"{SUBSTANTIVE_FIXTURE_ID}.{SUBSTANTIVE_FIXTURE_VERSION}."
MATERIAL_RECIPE = f"{SUBSTANTIVE_FIXTURE_ID}-material-v1"

__all__ = [
    "MATERIAL_RECIPE",
    "RECIPE_PREFIX",
    "SUBSTANTIVE_FIXTURE_ID",
    "SUBSTANTIVE_FIXTURE_VERSION",
]
