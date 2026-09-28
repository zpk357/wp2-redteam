"""Office V2 mutation contracts only.

The former YAML-driven Mutation CLI and its lazy legacy exports were removed
so new code cannot accidentally select the retired mutation pipeline.
"""

from .v2_brief import MinimalFactBrief, MutationCandidateResponse

__all__ = ["MinimalFactBrief", "MutationCandidateResponse"]
