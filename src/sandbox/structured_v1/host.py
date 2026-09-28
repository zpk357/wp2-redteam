"""Host-owned runtime and closure receipts for structured episodes (P6/R10/R12)."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from typing import Any, Literal

from pydantic import model_validator

from sandbox.replay.digests import sha256_digest
from sandbox.structured_v1.envelope_codes import EnvelopeRefusal, FailureCode
from sandbox.structured_v1.models import Identifier, Sha256Digest, StructuredContract

PLACEHOLDER_DIGEST = "sha256:" + "0" * 64


class RuntimeReceipt(StructuredContract):
    """Host binding of a container instance to an Episode and container evidence."""

    receipt_version: Literal["structured-runtime-receipt-v1"] = (
        "structured-runtime-receipt-v1"
    )
    episode_id: Identifier
    container_id: Identifier
    image_digest: Sha256Digest
    image_reference: str = ""
    container_bundle_digest: Sha256Digest
    #: Where the observation came from: a container runtime, or a substitute used offline.
    source: Literal["container-runtime", "test-substitute"] = "container-runtime"
    receipt_digest: Sha256Digest

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(
            mode="json", exclude={"receipt_digest"}, exclude_none=False
        )

    @model_validator(mode="after")
    def digest_matches(self) -> RuntimeReceipt:
        if self.receipt_digest != sha256_digest(self.digest_payload()):
            raise ValueError("runtime receipt digest does not match its content")
        return self


class HostClosureReceipt(StructuredContract):
    """Host observation made after retrieving the container evidence."""

    receipt_version: Literal["structured-host-closure-v1"] = (
        "structured-host-closure-v1"
    )
    episode_id: Identifier
    container_id: Identifier
    runtime_receipt_digest: Sha256Digest
    stop_requested: bool
    container_absent: bool
    no_post_bundle_activity: bool
    isolation_confirmed: bool
    #: Where the observation came from: a container runtime, or a substitute used offline.
    source: Literal["container-runtime", "test-substitute"] = "container-runtime"
    detail: str = ""
    receipt_digest: Sha256Digest

    @property
    def proven(self) -> bool:
        return all(
            (
                self.stop_requested,
                self.container_absent,
                self.no_post_bundle_activity,
                self.isolation_confirmed,
            )
        )

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(
            mode="json", exclude={"receipt_digest"}, exclude_none=False
        )

    @model_validator(mode="after")
    def digest_matches(self) -> HostClosureReceipt:
        if self.receipt_digest != sha256_digest(self.digest_payload()):
            raise ValueError("host closure receipt digest does not match its content")
        return self


def build_runtime_receipt(
    *,
    episode_id: str,
    container_id: str,
    image_digest: str,
    container_bundle_digest: str,
    image_reference: str = "",
    source: str = "container-runtime",
) -> RuntimeReceipt:
    fields = {
        "episode_id": episode_id,
        "container_id": container_id,
        "image_digest": image_digest,
        "image_reference": image_reference,
        "container_bundle_digest": container_bundle_digest,
        "source": source,
        "receipt_digest": PLACEHOLDER_DIGEST,
    }
    draft = RuntimeReceipt.model_construct(**fields)
    return RuntimeReceipt(**{**fields, "receipt_digest": sha256_digest(draft.digest_payload())})


def build_host_closure_receipt(
    *,
    runtime_receipt: RuntimeReceipt,
    stop_requested: bool,
    container_absent: bool,
    no_post_bundle_activity: bool,
    isolation_confirmed: bool,
    detail: str = "",
    source: str = "container-runtime",
) -> HostClosureReceipt:
    fields = {
        "episode_id": runtime_receipt.episode_id,
        "container_id": runtime_receipt.container_id,
        "runtime_receipt_digest": runtime_receipt.receipt_digest,
        "stop_requested": stop_requested,
        "container_absent": container_absent,
        "no_post_bundle_activity": no_post_bundle_activity,
        "isolation_confirmed": isolation_confirmed,
        "detail": detail,
        "source": source,
        "receipt_digest": PLACEHOLDER_DIGEST,
    }
    draft = HostClosureReceipt.model_construct(**fields)
    return HostClosureReceipt(
        **{**fields, "receipt_digest": sha256_digest(draft.digest_payload())}
    )


def _docker_inspect(container_id: str) -> dict[str, Any]:
    completed = subprocess.run(
        ["docker", "inspect", container_id],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise EnvelopeRefusal(
            FailureCode.PAYLOAD_MISSING,
            f"the runtime could not inspect {container_id!r}: "
            f"{completed.stderr.strip()[:200]}",
        )
    try:
        payload = json.loads(completed.stdout)
    except ValueError as error:
        raise EnvelopeRefusal(
            FailureCode.PAYLOAD_MISSING, "the runtime returned invalid inspect JSON"
        ) from error
    if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
        raise EnvelopeRefusal(
            FailureCode.PAYLOAD_MISSING, "the runtime returned an unexpected inspect payload"
        )
    return payload[0]


def runtime_receipt_from_inspect(
    runtime_answer: dict[str, Any],
    *,
    episode_id: str,
    container_bundle_digest: str,
    source: str = "container-runtime",
) -> RuntimeReceipt:
    container_id = runtime_answer.get("Id")
    image_digest = runtime_answer.get("Image")
    config = runtime_answer.get("Config") or {}
    image_reference = config.get("Image", "") if isinstance(config, dict) else ""
    if not isinstance(container_id, str) or not container_id:
        raise EnvelopeRefusal(FailureCode.PAYLOAD_MISSING, "inspect has no container id")
    if not isinstance(image_digest, str) or not image_digest.startswith("sha256:"):
        raise EnvelopeRefusal(
            FailureCode.IMAGE_MISMATCH, "inspect has no immutable image id"
        )
    return build_runtime_receipt(
        episode_id=episode_id,
        container_id=container_id,
        image_digest=image_digest,
        image_reference=str(image_reference),
        container_bundle_digest=container_bundle_digest,
        source=source,
    )


def query_runtime_receipt(
    container_id: str,
    *,
    episode_id: str,
    container_bundle_digest: str,
    inspect: Callable[[str], dict[str, Any]] | None = None,
    source: str = "container-runtime",
) -> RuntimeReceipt:
    ask = inspect or _docker_inspect
    try:
        answer = ask(container_id)
    except EnvelopeRefusal:
        raise
    except Exception as error:
        raise EnvelopeRefusal(
            FailureCode.PAYLOAD_MISSING,
            f"the runtime could not be asked about {container_id!r}: {error}",
        ) from error
    receipt = runtime_receipt_from_inspect(
        answer,
        episode_id=episode_id,
        container_bundle_digest=container_bundle_digest,
        source=source,
    )
    if receipt.container_id != container_id:
        raise EnvelopeRefusal(
            FailureCode.IDENTITY_MISMATCH,
            "the inspected container id differs from the requested container",
        )
    return receipt
