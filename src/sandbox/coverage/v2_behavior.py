"""Office V2 behavior feature contracts and baseline-independent normalization."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Self

from pydantic import Field, ValidationInfo, field_validator, model_validator

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.models import (
    Identifier,
    OfficeV2Contract,
    Sha256Digest,
)
from sandbox.scenarios.office_v2.oracle_models import EvidenceRef

from .v2_contracts import (
    V2_COVERAGE_CONTRACT_IDENTITY,
    V2CoverageComponent,
)


class V2BehaviorFeatureTier(StrEnum):
    PRIMARY = "primary"
    SECONDARY = "secondary"


class V2BehaviorFeatureKind(StrEnum):
    TOOL_UNIGRAM = "tool-unigram"
    TOOL_BIGRAM = "tool-bigram"
    TOOL_TRIGRAM = "tool-trigram"
    CROSS_DOMAIN_EDGE = "cross-domain-edge"
    INTERACTION_EDGE = "interaction-edge"
    PARAMETER_SHAPE = "parameter-shape"
    ARGUMENT_SOURCE_CHAIN = "argument-source-chain"
    PERMISSION_BRANCH = "permission-branch"
    RESULT_BRANCH = "result-branch"
    STATE_OBJECT_CHANGE = "state-object-change"
    STATE_FIELD_CHANGE = "state-field-change"
    STATE_RELATION_CHANGE = "state-relation-change"
    STATE_CROSS_DOMAIN = "state-cross-domain"
    INTERACTION = "interaction"
    TERMINATION = "termination"
    INVOCATION_COUNT = "invocation-count"
    EQUIVALENT_RESOURCE = "equivalent-resource"
    EXPRESSION_VARIATION = "expression-variation"
    PATH_LENGTH = "path-length"
    EQUIVALENT_OBJECT_STATE = "equivalent-object-state"


_SECONDARY_KINDS = {
    V2BehaviorFeatureKind.INVOCATION_COUNT,
    V2BehaviorFeatureKind.EQUIVALENT_RESOURCE,
    V2BehaviorFeatureKind.EXPRESSION_VARIATION,
    V2BehaviorFeatureKind.PATH_LENGTH,
    V2BehaviorFeatureKind.EQUIVALENT_OBJECT_STATE,
}


class V2BehaviorValueRole(StrEnum):
    STRUCTURAL = "structural"
    CATEGORICAL = "categorical"
    INSTANCE_ID = "instance-id"
    CONTENT = "content"
    TIMESTAMP = "timestamp"
    CURSOR = "cursor"
    ACQUISITION = "acquisition"


class V2PathAtomKind(StrEnum):
    TOOL = "tool"
    INTERACTION = "interaction"
    TERMINATION = "termination"


class V2RepeatBucket(StrEnum):
    ONCE = "1"
    TWICE = "2"
    THREE_PLUS = "3+"


def _component_digest(component: V2CoverageComponent) -> str:
    return next(
        item.content_digest
        for item in V2_COVERAGE_CONTRACT_IDENTITY.components
        if item.component is component
    )


V2_BEHAVIOR_NORMALIZATION_DIGEST = _component_digest(
    V2CoverageComponent.NORMALIZATION
)
V2_BEHAVIOR_FEATURE_SCHEMA_DIGEST = _component_digest(
    V2CoverageComponent.FEATURE_SCHEMA
)


def _count_bucket(count: int) -> V2RepeatBucket:
    if count < 1:
        raise ValueError("behavior repeat count must be positive")
    if count == 1:
        return V2RepeatBucket.ONCE
    if count == 2:
        return V2RepeatBucket.TWICE
    return V2RepeatBucket.THREE_PLUS


def _structural_shape(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int | float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, dict):
        fields = ",".join(sorted(str(key).casefold() for key in value))
        return f"object[{fields}]"
    if isinstance(value, list | tuple):
        if not value:
            return "list[empty]"
        item_shapes = ",".join(sorted({_structural_shape(item) for item in value}))
        return f"list[{_count_bucket(len(value)).value};{item_shapes}]"
    return "object[unknown]"


def normalize_v2_behavior_value(
    value: Any,
    *,
    role: V2BehaviorValueRole,
    allowed_categories: frozenset[str] = frozenset(),
) -> str | None:
    """Remove instance values while retaining an explicitly trusted semantic shape."""

    if role is V2BehaviorValueRole.ACQUISITION:
        return None
    if role is V2BehaviorValueRole.INSTANCE_ID:
        return "identifier"
    if role is V2BehaviorValueRole.CONTENT:
        return "text"
    if role is V2BehaviorValueRole.TIMESTAMP:
        return "timestamp"
    if role is V2BehaviorValueRole.CURSOR:
        return "cursor-absent" if value is None else "cursor-present"
    if role is V2BehaviorValueRole.CATEGORICAL:
        if not isinstance(value, str) or value not in allowed_categories:
            raise ValueError("categorical behavior value is not in the trusted vocabulary")
        return value
    return _structural_shape(value)


class V2BehaviorDimension(OfficeV2Contract):
    name: Identifier
    value: str = Field(min_length=1, max_length=512)

    @field_validator("value")
    @classmethod
    def value_is_a_single_normalized_token(cls, value: str) -> str:
        if any(character.isspace() for character in value):
            raise ValueError("behavior dimension value must be a normalized token")
        return value


class V2BehaviorFeature(OfficeV2Contract):
    tier: V2BehaviorFeatureTier
    kind: V2BehaviorFeatureKind
    dimensions: tuple[V2BehaviorDimension, ...] = Field(min_length=1)
    evidence_refs: tuple[EvidenceRef, ...] = Field(min_length=1)
    feature_key_digest: Sha256Digest
    feature_fact_digest: Sha256Digest

    @field_validator("dimensions")
    @classmethod
    def dimensions_are_unique_and_canonical(
        cls, value: tuple[V2BehaviorDimension, ...]
    ) -> tuple[V2BehaviorDimension, ...]:
        names = tuple(item.name for item in value)
        if len(names) != len(set(names)):
            raise ValueError("behavior feature dimensions must not repeat names")
        return tuple(sorted(value, key=lambda item: item.name))

    @field_validator("evidence_refs")
    @classmethod
    def evidence_is_unique_and_canonical(
        cls, value: tuple[EvidenceRef, ...]
    ) -> tuple[EvidenceRef, ...]:
        ids = tuple(item.evidence_id for item in value)
        if len(ids) != len(set(ids)):
            raise ValueError("behavior feature evidence must not repeat evidence_id")
        return tuple(sorted(value, key=lambda item: item.sort_key()))

    def key_payload(self) -> dict[str, object]:
        return {
            "normalization_digest": V2_BEHAVIOR_NORMALIZATION_DIGEST,
            "feature_schema_digest": V2_BEHAVIOR_FEATURE_SCHEMA_DIGEST,
            "tier": self.tier,
            "kind": self.kind,
            "dimensions": self.dimensions,
        }

    def fact_payload(self) -> dict[str, object]:
        return self.model_dump(
            mode="json",
            exclude={"feature_fact_digest"},
            exclude_none=False,
        )

    @model_validator(mode="after")
    def tier_and_digests_match(self) -> Self:
        should_be_secondary = self.kind in _SECONDARY_KINDS
        if (self.tier is V2BehaviorFeatureTier.SECONDARY) != should_be_secondary:
            raise ValueError("behavior feature kind does not match its tier")
        if self.feature_key_digest != sha256_digest(self.key_payload()):
            raise ValueError("behavior feature key digest does not match")
        if self.feature_fact_digest != sha256_digest(self.fact_payload()):
            raise ValueError("behavior feature fact digest does not match")
        return self


def build_v2_behavior_feature(
    *,
    tier: V2BehaviorFeatureTier,
    kind: V2BehaviorFeatureKind,
    dimensions: tuple[V2BehaviorDimension, ...],
    evidence_refs: tuple[EvidenceRef, ...],
) -> V2BehaviorFeature:
    canonical_dimensions = tuple(sorted(dimensions, key=lambda item: item.name))
    canonical_evidence = tuple(sorted(evidence_refs, key=lambda item: item.sort_key()))
    key_payload = {
        "normalization_digest": V2_BEHAVIOR_NORMALIZATION_DIGEST,
        "feature_schema_digest": V2_BEHAVIOR_FEATURE_SCHEMA_DIGEST,
        "tier": tier,
        "kind": kind,
        "dimensions": canonical_dimensions,
    }
    payload = {
        "tier": tier,
        "kind": kind,
        "dimensions": canonical_dimensions,
        "evidence_refs": canonical_evidence,
        "feature_key_digest": sha256_digest(key_payload),
    }
    draft = V2BehaviorFeature.model_construct(
        **payload,
        feature_fact_digest="sha256:" + "0" * 64,
    )
    return V2BehaviorFeature(
        **payload,
        feature_fact_digest=sha256_digest(draft.fact_payload()),
    )


class V2PathAtom(OfficeV2Contract):
    atom_kind: V2PathAtomKind
    semantic_id: Identifier

    @model_validator(mode="after")
    def semantic_id_matches_kind(self) -> Self:
        if not self.semantic_id.startswith(f"{self.atom_kind.value}."):
            raise ValueError("path atom semantic_id must be namespaced by atom kind")
        return self


class V2NormalizedPathSegment(OfficeV2Contract):
    atoms: tuple[V2PathAtom, ...] = Field(min_length=1, max_length=32)
    repeat_bucket: V2RepeatBucket


class V2NormalizedPath(OfficeV2Contract):
    segments: tuple[V2NormalizedPathSegment, ...] = Field(min_length=1)
    path_digest: Sha256Digest

    def digest_payload(self) -> dict[str, object]:
        return {
            "normalization_digest": V2_BEHAVIOR_NORMALIZATION_DIGEST,
            "segments": self.segments,
        }

    @model_validator(mode="after")
    def digest_matches(self) -> Self:
        if self.path_digest != sha256_digest(self.digest_payload()):
            raise ValueError("normalized behavior path digest does not match")
        return self


def _repeated_prefix_count(
    atoms: tuple[V2PathAtom, ...],
    *,
    start: int,
    pattern_length: int,
) -> int:
    pattern = atoms[start : start + pattern_length]
    count = 1
    while atoms[
        start + count * pattern_length : start + (count + 1) * pattern_length
    ] == pattern:
        count += 1
    return count


def normalize_v2_behavior_path(atoms: tuple[V2PathAtom, ...]) -> V2NormalizedPath:
    if not atoms:
        raise ValueError("behavior path must contain at least one atom")
    segments: list[V2NormalizedPathSegment] = []
    index = 0
    while index < len(atoms):
        best_pattern_length = 0
        best_repeat_count = 0
        best_covered = 0
        max_pattern_length = (len(atoms) - index) // 2
        for pattern_length in range(1, max_pattern_length + 1):
            repeat_count = _repeated_prefix_count(
                atoms,
                start=index,
                pattern_length=pattern_length,
            )
            if repeat_count < 2:
                continue
            covered = pattern_length * repeat_count
            if covered > best_covered or (
                covered == best_covered and pattern_length < best_pattern_length
            ):
                best_pattern_length = pattern_length
                best_repeat_count = repeat_count
                best_covered = covered
        if best_repeat_count:
            segment_atoms = atoms[index : index + best_pattern_length]
            repeat_count = best_repeat_count
            index += best_covered
        else:
            segment_atoms = (atoms[index],)
            repeat_count = 1
            index += 1
        segments.append(
            V2NormalizedPathSegment(
                atoms=segment_atoms,
                repeat_bucket=_count_bucket(repeat_count),
            )
        )
    payload = {"segments": tuple(segments)}
    draft = V2NormalizedPath.model_construct(
        **payload,
        path_digest="sha256:" + "0" * 64,
    )
    return V2NormalizedPath(
        **payload,
        path_digest=sha256_digest(draft.digest_payload()),
    )


class V2BehaviorProfile(OfficeV2Contract):
    coverage_identity_digest: Sha256Digest
    canonical_fact_digest: Sha256Digest
    normalization_digest: Sha256Digest
    feature_schema_digest: Sha256Digest
    primary_features: tuple[V2BehaviorFeature, ...] = Field(default_factory=tuple)
    secondary_diversity: tuple[V2BehaviorFeature, ...] = Field(default_factory=tuple)
    normalized_path: V2NormalizedPath
    profile_digest: Sha256Digest
    profile_fact_digest: Sha256Digest

    @field_validator("primary_features", "secondary_diversity")
    @classmethod
    def features_are_unique_and_canonical(
        cls,
        value: tuple[V2BehaviorFeature, ...],
        info: ValidationInfo,
    ) -> tuple[V2BehaviorFeature, ...]:
        keys = tuple(item.feature_key_digest for item in value)
        if len(keys) != len(set(keys)):
            raise ValueError(f"{info.field_name} must not repeat feature keys")
        return tuple(sorted(value, key=lambda item: item.feature_key_digest))

    def semantic_payload(self) -> dict[str, object]:
        return {
            "normalization_digest": self.normalization_digest,
            "feature_schema_digest": self.feature_schema_digest,
            "primary_feature_keys": tuple(
                item.feature_key_digest for item in self.primary_features
            ),
            "normalized_path_digest": self.normalized_path.path_digest,
        }

    def fact_payload(self) -> dict[str, object]:
        return self.model_dump(
            mode="json",
            exclude={"profile_fact_digest"},
            exclude_none=False,
        )

    @model_validator(mode="after")
    def identities_tiers_and_digests_match(self) -> Self:
        if self.coverage_identity_digest != V2_COVERAGE_CONTRACT_IDENTITY.identity_digest:
            raise ValueError("behavior profile uses the wrong coverage identity")
        if (
            self.normalization_digest != V2_BEHAVIOR_NORMALIZATION_DIGEST
            or self.feature_schema_digest != V2_BEHAVIOR_FEATURE_SCHEMA_DIGEST
        ):
            raise ValueError("behavior profile component identity does not match")
        if any(
            item.tier is not V2BehaviorFeatureTier.PRIMARY
            for item in self.primary_features
        ):
            raise ValueError("primary behavior features contain a secondary feature")
        if any(
            item.tier is not V2BehaviorFeatureTier.SECONDARY
            for item in self.secondary_diversity
        ):
            raise ValueError("secondary diversity contains a primary feature")
        if self.profile_digest != sha256_digest(self.semantic_payload()):
            raise ValueError("behavior profile digest does not match")
        if self.profile_fact_digest != sha256_digest(self.fact_payload()):
            raise ValueError("behavior profile fact digest does not match")
        return self


def build_v2_behavior_profile(
    *,
    canonical_fact_digest: str,
    primary_features: tuple[V2BehaviorFeature, ...],
    secondary_diversity: tuple[V2BehaviorFeature, ...],
    normalized_path: V2NormalizedPath,
) -> V2BehaviorProfile:
    canonical_primary = tuple(
        sorted(primary_features, key=lambda item: item.feature_key_digest)
    )
    canonical_secondary = tuple(
        sorted(secondary_diversity, key=lambda item: item.feature_key_digest)
    )
    payload = {
        "coverage_identity_digest": V2_COVERAGE_CONTRACT_IDENTITY.identity_digest,
        "canonical_fact_digest": canonical_fact_digest,
        "normalization_digest": V2_BEHAVIOR_NORMALIZATION_DIGEST,
        "feature_schema_digest": V2_BEHAVIOR_FEATURE_SCHEMA_DIGEST,
        "primary_features": canonical_primary,
        "secondary_diversity": canonical_secondary,
        "normalized_path": normalized_path,
    }
    semantic_payload = {
        "normalization_digest": V2_BEHAVIOR_NORMALIZATION_DIGEST,
        "feature_schema_digest": V2_BEHAVIOR_FEATURE_SCHEMA_DIGEST,
        "primary_feature_keys": tuple(
            item.feature_key_digest for item in canonical_primary
        ),
        "normalized_path_digest": normalized_path.path_digest,
    }
    payload["profile_digest"] = sha256_digest(semantic_payload)
    draft = V2BehaviorProfile.model_construct(
        **payload,
        profile_fact_digest="sha256:" + "0" * 64,
    )
    return V2BehaviorProfile(
        **payload,
        profile_fact_digest=sha256_digest(draft.fact_payload()),
    )


__all__ = [
    "V2_BEHAVIOR_FEATURE_SCHEMA_DIGEST",
    "V2_BEHAVIOR_NORMALIZATION_DIGEST",
    "V2BehaviorDimension",
    "V2BehaviorFeature",
    "V2BehaviorFeatureKind",
    "V2BehaviorFeatureTier",
    "V2BehaviorProfile",
    "V2BehaviorValueRole",
    "V2NormalizedPath",
    "V2NormalizedPathSegment",
    "V2PathAtom",
    "V2PathAtomKind",
    "V2RepeatBucket",
    "build_v2_behavior_feature",
    "build_v2_behavior_profile",
    "normalize_v2_behavior_path",
    "normalize_v2_behavior_value",
]
