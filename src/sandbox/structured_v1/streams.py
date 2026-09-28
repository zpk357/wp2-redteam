"""Independent, replayable random streams for the two shared generation modes.

``SS-012`` requires the root distribution and the local edit kernel to keep separate
random streams, and requires both to be replayable: the same campaign seed and
opportunity must rebuild the same draw.  A stream is therefore a digest of its
purpose and coordinates, and the generator is seeded from that digest rather than
from process state.
"""

from __future__ import annotations

import random
from typing import Final

from sandbox.replay.digests import sha256_digest

ROOT_STREAM: Final[str] = "root-distribution"
EDIT_STREAM: Final[str] = "edit-kernel"


def derive_random_state(*parts: str) -> str:
    """A stable random state from its coordinates."""

    return sha256_digest({"stream": list(parts)})


def build_random(random_state: str) -> random.Random:
    """A generator whose sequence depends only on the random state."""

    digest = sha256_digest({"random_state": random_state})
    return random.Random(int(digest.split(":", 1)[1][:16], 16))


def stream_random_state(stream: str, campaign_seed: str, opportunity: int) -> str:
    return derive_random_state(stream, campaign_seed, str(opportunity))


def root_random_state(campaign_seed: str, opportunity: int) -> str:
    """The stream of the shared root distribution."""

    return stream_random_state(ROOT_STREAM, campaign_seed, opportunity)


def edit_random_state(campaign_seed: str, opportunity: int) -> str:
    """The stream of the shared local edit kernel."""

    return stream_random_state(EDIT_STREAM, campaign_seed, opportunity)
