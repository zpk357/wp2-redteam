"""The approved normal control and material-structure input records."""

from sandbox.structured_v1.experiment_inputs import prepare_inputs
from sandbox.structured_v1.operators import diff_cases


def test_prepared_inputs_keep_control_separate_and_record_single_edits(manifest) -> None:
    inputs = prepare_inputs(manifest, count=4)

    assert inputs.normal_control.parent_id is None
    assert inputs.normal_control.operation is None
    assert inputs.normal_control.case.input_digest == inputs.normal_control.rendered.material_digest
    assert len(inputs.candidates) == 4
    assert all(item.parent_id for item in inputs.candidates)
    assert all(item.operation is not None for item in inputs.candidates)
    assert all(item.preserved for item in inputs.candidates)
    assert all(item.rendered_difference for item in inputs.candidates)
    assert all(item.readable_slots == tuple(slot.slot_id for slot in item.rendered.slots)
               for item in inputs.candidates)
    assert all(not diff_cases(inputs.normal_control.case, item.case).is_empty()
               for item in inputs.candidates)
