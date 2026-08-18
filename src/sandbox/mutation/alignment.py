"""Independent static checks for provider-declared mutation semantics."""

from __future__ import annotations

import re

from sandbox.mutation.models import (
    RawMutationCandidate,
    StaticSemanticAlignment,
    StaticSemanticStatus,
)
from sandbox.mutation.operators import MutationOperatorRegistryIndex


class StaticSemanticVerifier:
    version = "static-semantic-v1"

    def __init__(self, registry: MutationOperatorRegistryIndex) -> None:
        self.registry = registry
        self.taxonomy = registry.taxonomy

    def verify(self, raw: RawMutationCandidate) -> StaticSemanticAlignment:
        operator = self.registry.get(raw.operator_id)
        operator_evidence = [
            f"operator_pattern:{index}"
            for index, pattern in enumerate(operator.semantic_patterns)
            if re.search(pattern, raw.prompt) is not None
        ]
        supported_risks: list[str] = []
        risk_evidence: list[str] = []
        lowered = raw.prompt.casefold()
        for category_id in sorted(set(raw.target_risks)):
            category = self.taxonomy.get(category_id)
            matched = next(
                (
                    index
                    for index, keyword in enumerate(category.keywords)
                    if keyword.casefold() in lowered
                ),
                None,
            )
            if matched is not None:
                supported_risks.append(category_id)
                risk_evidence.append(f"risk_keyword:{category_id}:{matched}")
        operator_evidenced = bool(operator_evidence)
        if operator_evidenced and set(supported_risks) == set(raw.target_risks):
            status = StaticSemanticStatus.SUPPORTED
        elif operator_evidenced or supported_risks:
            status = StaticSemanticStatus.PARTIAL
        else:
            status = StaticSemanticStatus.NOT_EVIDENCED
        return StaticSemanticAlignment(
            verifier_version=self.version,
            operator_evidenced=operator_evidenced,
            supported_target_risks=supported_risks,
            evidence=[*operator_evidence, *risk_evidence],
            status=status,
        )
