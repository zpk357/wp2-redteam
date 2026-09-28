"""The tool catalogue frozen inside the structured runtime image (`SOC-ENV-75`).

The final image digest is deliberately absent: a content-addressed image cannot embed its
own final digest without a self-reference.  The host binds the image that actually ran to
the container, Episode and container bundle in a separate runtime receipt.
"""

from __future__ import annotations

from typing import Any

from sandbox.replay.digests import sha256_digest
from sandbox.structured_v1.envelope_codes import EnvelopeRefusal, FailureCode
from sandbox.structured_v1.models import (
    Identifier,
    Sha256Digest,
    StructuredContract,
)


class ToolingIdentity(StructuredContract):
    """The frozen tool interface the envelope expects to execute."""

    tool_catalogue_digest: Sha256Digest


class ToolContractIdentity(StructuredContract):
    """One tool's name and the digest of its contract."""

    tool_name: Identifier
    contract_digest: Sha256Digest


class ToolCatalogue(StructuredContract):
    """The interface surface the image was built with."""

    tools: tuple[ToolContractIdentity, ...] = ()

    @property
    def catalogue_digest(self) -> str:
        payload: dict[str, Any] = {
            "tools": [
                {"tool_name": item.tool_name, "contract_digest": item.contract_digest}
                for item in sorted(self.tools, key=lambda item: item.tool_name)
            ]
        }
        return sha256_digest(payload)


class ImageManifest(StructuredContract):
    """What the image build froze, excluding self-referential image identity."""

    tool_catalogue: ToolCatalogue


class ToolingFacts(StructuredContract):
    """What the container can observe about the tooling it is running."""

    catalogue: ToolCatalogue
    image_manifest: ImageManifest


class ToolingVerification(StructuredContract):
    """Container-side proof of the frozen tool interface."""

    catalogue_digest: Sha256Digest


def build_office_v2_catalogue() -> ToolCatalogue:
    """The frozen tool surface: one identity per tool, bound to the frozen contract version.

    Shared by the image build and by any host-side rehearsal, so an envelope built for the
    image and the image's own catalogue cannot drift apart.
    """

    from sandbox.scenarios.office_v2 import OFFICE_V2_TOOL_CONTRACT_VERSION
    from sandbox.scenarios.office_v2.tools import OFFICE_V2_TOOL_NAMES

    return ToolCatalogue(
        tools=tuple(
            ToolContractIdentity(
                tool_name=name,
                contract_digest=sha256_digest(
                    {
                        "tool_name": name,
                        "contract_version": OFFICE_V2_TOOL_CONTRACT_VERSION,
                    }
                ),
            )
            for name in OFFICE_V2_TOOL_NAMES
        )
    )


def verify_tooling(claimed: ToolingIdentity, facts: ToolingFacts) -> ToolingVerification:
    """Compare the claimed tool interface with the catalogue frozen in the image."""

    catalogue_digest = facts.catalogue.catalogue_digest
    if claimed.tool_catalogue_digest != catalogue_digest:
        raise EnvelopeRefusal(
            FailureCode.CATALOGUE_MISMATCH,
            f"the envelope claims {claimed.tool_catalogue_digest}, "
            f"the loaded tools are {catalogue_digest}",
        )
    if facts.image_manifest.tool_catalogue.catalogue_digest != catalogue_digest:
        raise EnvelopeRefusal(
            FailureCode.CATALOGUE_MISMATCH,
            "the image manifest was built with a different tool catalogue",
        )
    return ToolingVerification(catalogue_digest=catalogue_digest)
