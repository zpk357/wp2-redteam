"""Guards for the packages this repository has retired.

Each retired module must stay **unimportable**.  Asserting on a name that used to live inside
it would not be enough: an emptied directory is still importable as a namespace package, so a
guard that only looked for a missing symbol would pass for the wrong reason.  Importability is
the property the deletion has to produce, and it is the property checked here -- which is also
why every retirement commit removes the package directory itself rather than only its files.

`sandbox.mutation` is deliberately **not** in this list: the second retirement kept a seven
module subset of it (`v2_brief` and `v2_contracts` plus the five contracts they depend on),
because the Office V2 mutator image imports them at run time.
"""

from __future__ import annotations

import importlib

import pytest

RETIRED_MODULES = (
    # Batch one: the old structured-scenario fixtures and their registry.
    "sandbox.scenarios.structured_v1",
    # Batch two: the structured-scenario package itself.
    "sandbox.structured_v1",
    # Batch three: the fuzzing campaign package.
    "sandbox.fuzzer",
)


@pytest.mark.parametrize("module", RETIRED_MODULES)
def test_retired_package_is_not_importable(module: str) -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module)
