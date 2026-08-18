"""Versioned initialization boundary for one frozen office episode."""

from __future__ import annotations

import json
from copy import deepcopy
from json import JSONDecodeError
from typing import Any, ClassVar, Literal

from pydantic import Field, ValidationError, model_validator

from sandbox.protocol import normalize_sha256_digest
from sandbox.replay.canonical import canonical_json_bytes
from sandbox.replay.digests import sha256_digest
from sandbox.replay.exceptions import CanonicalizationError
from sandbox.scenarios.injection import MaterializedInjection, materialize_injection
from sandbox.scenarios.models import FrozenContract, Identifier, TestCase

OFFICE_EPISODE_INITIALIZATION_KIND = "trace-g-office-episode-initialization"
OFFICE_EPISODE_INITIALIZATION_VERSION = "1.0"
OFFICE_EPISODE_EXECUTION_BACKEND = "trace_react_v2"
OFFICE_EPISODE_TRACE_SCHEMA_VERSION = "1.2"
OFFICE_EPISODE_STATE_CODEC_VERSION = "2.0"
OFFICE_TOOL_STATE_VERSION = "1.0"
MAX_OFFICE_EPISODE_INITIALIZATION_BYTES = 4 * 1024 * 1024
SHA256_DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"


class OfficeInitializationError(ValueError):
    """Expected rejection at the office episode initialization boundary."""

    error_code: ClassVar[str]


class OfficeInitializationConfigurationError(OfficeInitializationError):
    """The receiver does not support a declared initialization contract version."""

    error_code = "configuration_error"


class OfficeInitializationIntegrityError(OfficeInitializationError):
    """The initialization payload is malformed, incomplete, or inconsistent."""

    error_code = "data_integrity_error"


class OfficeEpisodeInitialization(FrozenContract):
    """Self-contained materialized state handed from the host to one container."""

    schema_version: Literal["1.0"] = OFFICE_EPISODE_INITIALIZATION_VERSION
    kind: Literal["trace-g-office-episode-initialization"] = (
        OFFICE_EPISODE_INITIALIZATION_KIND
    )
    execution_backend: Literal["trace_react_v2"] = OFFICE_EPISODE_EXECUTION_BACKEND
    trace_schema_version: Literal["1.2"] = OFFICE_EPISODE_TRACE_SCHEMA_VERSION
    state_codec_version: Literal["2.0"] = OFFICE_EPISODE_STATE_CODEC_VERSION
    test_case: TestCase
    materialized_injection: MaterializedInjection | None
    initial_state: dict[str, Any]
    initial_state_digest: str = Field(pattern=SHA256_DIGEST_PATTERN)
    envelope_digest: str | None = Field(default=None, pattern=SHA256_DIGEST_PATTERN)

    @model_validator(mode="after")
    def validate_materialization_and_digest(self) -> OfficeEpisodeInitialization:
        self.test_case.assert_integrity()
        expected_injection, expected_state = _expected_materialization(self.test_case)
        if self.materialized_injection != expected_injection:
            raise ValueError("materialized_injection does not match the frozen TestCase")
        if self.initial_state != expected_state:
            raise ValueError("initial_state does not match the frozen TestCase materialization")

        expected_state_digest = sha256_digest(expected_state)
        if self.initial_state_digest != expected_state_digest:
            raise ValueError("initial_state_digest does not match initial_state")

        expected_envelope_digest = self._calculate_envelope_digest()
        if self.envelope_digest is not None and self.envelope_digest != expected_envelope_digest:
            raise ValueError("envelope_digest does not match initialization content")
        object.__setattr__(self, "envelope_digest", expected_envelope_digest)
        return self

    def assert_integrity(self, expected_digest: str | None = None) -> None:
        """Re-derive nested state and digests so mutable nested values cannot drift."""
        self.test_case.assert_integrity()
        expected_injection, expected_state = _expected_materialization(self.test_case)
        if self.materialized_injection != expected_injection:
            raise ValueError("materialized_injection no longer matches the frozen TestCase")
        if self.initial_state != expected_state:
            raise ValueError("initial_state no longer matches the frozen TestCase materialization")
        if self.initial_state_digest != sha256_digest(self.initial_state):
            raise ValueError("initial_state no longer matches initial_state_digest")

        current_digest = self._calculate_envelope_digest()
        if self.envelope_digest != current_digest:
            raise ValueError("initialization content no longer matches envelope_digest")
        if expected_digest is not None:
            normalized = normalize_sha256_digest(expected_digest)
            if normalized != current_digest:
                raise ValueError("initialization does not match the detached expected digest")

    def _calculate_envelope_digest(self) -> str:
        return sha256_digest(self.model_dump(mode="json", exclude={"envelope_digest"}))


class OfficeExecutedAction(FrozenContract):
    capability_id: Identifier
    arguments: dict[str, Any]


class OfficeToolRuntimeState(FrozenContract):
    schema_version: Literal["1.0"] = OFFICE_TOOL_STATE_VERSION
    initialization: OfficeEpisodeInitialization
    actions: tuple[OfficeExecutedAction, ...] = Field(default_factory=tuple)
    records_digest: str = Field(pattern=SHA256_DIGEST_PATTERN)
    final_state_digest: str = Field(pattern=SHA256_DIGEST_PATTERN)


def build_office_episode_initialization(case: TestCase) -> OfficeEpisodeInitialization:
    """Build one deterministic host-side initialization envelope."""
    case.assert_integrity()
    materialized_injection, initial_state = _expected_materialization(case)
    return OfficeEpisodeInitialization(
        test_case=case,
        materialized_injection=materialized_injection,
        initial_state=initial_state,
        initial_state_digest=sha256_digest(initial_state),
    )


def dump_office_episode_initialization(
    initialization: OfficeEpisodeInitialization,
) -> bytes:
    """Serialize an initialization envelope as bounded canonical JSON bytes."""
    initialization.assert_integrity()
    payload = canonical_json_bytes(initialization)
    _require_size_within_limit(len(payload))
    return payload


def load_office_episode_initialization(
    payload: bytes | str | dict[str, Any],
    *,
    expected_digest: str | None = None,
) -> OfficeEpisodeInitialization:
    """Strictly parse and re-derive one initialization envelope."""
    raw = _decode_payload(payload)
    _require_supported_contract(raw)
    try:
        initialization = OfficeEpisodeInitialization.model_validate(raw)
    except ValidationError as exc:
        raise OfficeInitializationIntegrityError(
            "office episode initialization failed schema or integrity validation"
        ) from exc
    try:
        initialization.assert_integrity(expected_digest)
    except ValueError as exc:
        raise OfficeInitializationIntegrityError(str(exc)) from exc
    return initialization


def _expected_materialization(
    case: TestCase,
) -> tuple[MaterializedInjection | None, dict[str, Any]]:
    if case.attack is None:
        return None, deepcopy(case.scenario.initial_state)
    injection = materialize_injection(case)
    return injection, deepcopy(injection.initial_state)


def _decode_payload(payload: bytes | str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(payload, bytes):
        _require_size_within_limit(len(payload))
        serialized: bytes | str = payload
    elif isinstance(payload, str):
        _require_size_within_limit(len(payload.encode("utf-8")))
        serialized = payload
    elif isinstance(payload, dict):
        try:
            _require_size_within_limit(len(canonical_json_bytes(payload)))
        except CanonicalizationError as exc:
            raise OfficeInitializationIntegrityError(
                "office episode initialization is not canonical JSON data"
            ) from exc
        return deepcopy(payload)
    else:
        raise OfficeInitializationIntegrityError(
            "office episode initialization must be bytes, text, or an object"
        )

    try:
        decoded = json.loads(serialized)
    except (JSONDecodeError, UnicodeDecodeError) as exc:
        raise OfficeInitializationIntegrityError(
            "office episode initialization is not valid UTF-8 JSON"
        ) from exc
    if not isinstance(decoded, dict):
        raise OfficeInitializationIntegrityError(
            "office episode initialization JSON must contain one object"
        )
    return decoded


def _require_size_within_limit(size: int) -> None:
    if size > MAX_OFFICE_EPISODE_INITIALIZATION_BYTES:
        raise OfficeInitializationIntegrityError(
            "office episode initialization exceeds the maximum serialized size"
        )


def _require_supported_contract(raw: dict[str, Any]) -> None:
    supported = {
        "kind": OFFICE_EPISODE_INITIALIZATION_KIND,
        "schema_version": OFFICE_EPISODE_INITIALIZATION_VERSION,
        "execution_backend": OFFICE_EPISODE_EXECUTION_BACKEND,
        "trace_schema_version": OFFICE_EPISODE_TRACE_SCHEMA_VERSION,
        "state_codec_version": OFFICE_EPISODE_STATE_CODEC_VERSION,
    }
    missing = sorted(field for field in supported if field not in raw)
    if missing:
        raise OfficeInitializationIntegrityError(
            f"office episode initialization is missing contract fields: {missing}"
        )
    incompatible = {
        field: raw[field]
        for field, expected in supported.items()
        if raw[field] != expected
    }
    if incompatible:
        raise OfficeInitializationConfigurationError(
            f"unsupported office episode initialization contract: {incompatible}"
        )
