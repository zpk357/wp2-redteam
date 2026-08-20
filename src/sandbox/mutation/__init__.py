"""Coverage-guided semantic mutation."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sandbox.mutation.config import MutationConfig
    from sandbox.mutation.feedback import MutationFeedbackBuilder
    from sandbox.mutation.models import (
        MutationBatch,
        MutationCandidate,
        MutationFeedback,
        MutationHistorySnapshot,
        MutationOperatorSpec,
        MutationProviderKind,
        MutationSeed,
        RiskGap,
        to_test_case,
    )
    from sandbox.mutation.mutator import SemanticMutator
    from sandbox.mutation.operators import MutationOperatorRegistryLoader
    from sandbox.mutation.store import MutationStore

_EXPORTS = {
    "MutationConfig": ("sandbox.mutation.config", "MutationConfig"),
    "MutationFeedbackBuilder": (
        "sandbox.mutation.feedback",
        "MutationFeedbackBuilder",
    ),
    "MutationBatch": ("sandbox.mutation.models", "MutationBatch"),
    "MutationCandidate": ("sandbox.mutation.models", "MutationCandidate"),
    "MutationFeedback": ("sandbox.mutation.models", "MutationFeedback"),
    "MutationHistorySnapshot": (
        "sandbox.mutation.models",
        "MutationHistorySnapshot",
    ),
    "MutationOperatorSpec": ("sandbox.mutation.models", "MutationOperatorSpec"),
    "MutationProviderKind": ("sandbox.mutation.models", "MutationProviderKind"),
    "MutationSeed": ("sandbox.mutation.models", "MutationSeed"),
    "RiskGap": ("sandbox.mutation.models", "RiskGap"),
    "to_test_case": ("sandbox.mutation.models", "to_test_case"),
    "SemanticMutator": ("sandbox.mutation.mutator", "SemanticMutator"),
    "MutationOperatorRegistryLoader": (
        "sandbox.mutation.operators",
        "MutationOperatorRegistryLoader",
    ),
    "MutationStore": ("sandbox.mutation.store", "MutationStore"),
}


def __getattr__(name: str) -> Any:
    try:
        module_name, attribute_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value

__all__ = [
    "MutationBatch",
    "MutationCandidate",
    "MutationConfig",
    "MutationFeedback",
    "MutationFeedbackBuilder",
    "MutationHistorySnapshot",
    "MutationOperatorRegistryLoader",
    "MutationOperatorSpec",
    "MutationProviderKind",
    "MutationSeed",
    "MutationStore",
    "RiskGap",
    "SemanticMutator",
    "to_test_case",
]
