"""The one event the host and the container agree announces a finished Episode.

This module imports nothing, on purpose.  The host waits for this pair and the container
emits it, so both sides need the same name -- but the container is an image that ships the
scenario package and not the host's tooling, and importing the name from the runner would
have pulled the Docker client into the image along with it.  A shared constant is supposed
to remove a coupling, not add one.
"""

from __future__ import annotations

#: A container that finished for another reason reports `execution_error`,
#: `execution_timed_out` or `execution_cancelled` from `runtime` instead.  The host matches
#: on both halves of this pair, so a failure cannot be read as an Episode result.
FINISHED_EVENT_TYPE = "execution_finished"
FINISHED_EVENT_SOURCE = "error_capable"

__all__ = ["FINISHED_EVENT_SOURCE", "FINISHED_EVENT_TYPE"]
