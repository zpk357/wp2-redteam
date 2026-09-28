"""Explicit synthetic identities for selector unit tests, never real execution evidence."""
from sandbox.replay.digests import sha256_digest
from sandbox.structured_v1.coverage import COVERAGE_VERSION, CoverageExecutionIdentity
from sandbox.structured_v1.rendering import render_material

TEST_CONFIG = sha256_digest({"test-only": "scripted execution"})


def record_coverage(search, result, *, parent_id, parent_baseline=None):
    case = search.parents[parent_id]
    result = result.model_copy(update={"execution": CoverageExecutionIdentity(
        candidate_id=parent_id,
        input_digest=case.input_digest,
        material_digest=render_material(case, search.manifest).material_digest,
        manifest_digest=search.manifest.manifest_digest,
        execution_config_digest=TEST_CONFIG,
        coverage_version=COVERAGE_VERSION,
        bundle_digest=sha256_digest({"synthetic-bundle": result.episode_id}),
        envelope_digest=sha256_digest({"synthetic-envelope": result.episode_id}),
    )})
    search.record(result, parent_id=parent_id, parent_baseline=parent_baseline)
    return result
