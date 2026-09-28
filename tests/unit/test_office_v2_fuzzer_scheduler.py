from sandbox.fuzzer.v2_risk_pool_scheduler import build_formal_risk_allocation
from sandbox.fuzzer.v2_seed_pools import (
    RiskProgressLevel,
    RiskType,
    RiskTypeProgressState,
    build_initial_seed_catalog,
)


def test_formal_scheduler_uses_lowest_risk_progress_without_frontier_inputs():
    catalog = build_initial_seed_catalog()
    progress = tuple(
        RiskTypeProgressState(
            risk_type=risk_type,
            level=(
                RiskProgressLevel.ATTEMPTED
                if risk_type is RiskType.WRONG_MODIFICATION_PROPAGATION
                else RiskProgressLevel.NOT_SELECTED
            ),
        )
        for risk_type in RiskType
    )
    allocation = build_formal_risk_allocation(
        catalog=catalog,
        progress_states=progress,
        generation_index=0,
        campaign_id="campaign.test",
        campaign_seed_value=7,
        include_derived=False,
        selectable_seed_ids=frozenset(
            seed.seed_id for pool in catalog.pools for seed in pool.seeds
        ),
        budget_remaining=1,
    )
    assert allocation.risk_type_progress_level is RiskProgressLevel.NOT_SELECTED
    assert allocation.risk_type is not RiskType.WRONG_MODIFICATION_PROPAGATION
    selected_pool_ids = {
        seed.seed_id for seed in catalog.pool_for(allocation.risk_type).seeds
    }
    assert {
        option.option_id for option in allocation.parent_selection_receipt.options
    } == selected_pool_ids
