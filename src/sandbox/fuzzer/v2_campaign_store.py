"""Minimal SQLite WAL store for recoverable Office V2 campaign state."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from sandbox.mutation.v2_preparation import MutationPreparation
from sandbox.mutation.v2_provider import MutationProviderAttempt
from sandbox.replay.digests import sha256_digest

from .v2_campaign_state import V2CampaignStateSnapshot
from .v2_feedback import FindingRecord, NextGenerationFeedback
from .v2_identity import (
    V2CampaignIdentityLock,
    require_v2_campaign_identity_lock,
)
from .v2_loop_contracts import (
    ExecutionHandoff,
    MutationBudgetReservation,
    NonEpisodeSettlement,
    PreparationCostSettlement,
)
from .v2_orchestrator import GenerationClosureReceipt, GenerationDecision
from .v2_scheduler import GenerationAllocation
from .v2_work import (
    AttemptDisposition,
    AttemptReceipt,
    CandidateSettlement,
    CandidateWork,
    CandidateWorkState,
    retry_allowed,
)


class V2CampaignStoreError(RuntimeError):
    """Campaign state cannot be safely persisted or recovered."""


class V2CampaignStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        if str(path) == ":memory:":
            self._db = sqlite3.connect(":memory:")
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(self.path)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._create_schema()

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> V2CampaignStore:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _create_schema(self) -> None:
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS campaign (
              campaign_id TEXT PRIMARY KEY,
              identity_json TEXT NOT NULL,
              identity_digest TEXT NOT NULL,
              lifecycle_json TEXT NOT NULL,
              current_snapshot_digest TEXT NOT NULL,
              current_state_digest TEXT,
              generation_index INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS coverage_snapshot (
              snapshot_digest TEXT PRIMARY KEY,
              snapshot_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS campaign_state_snapshot (
              state_digest TEXT PRIMARY KEY,
              state_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS candidate_work (
              work_id TEXT PRIMARY KEY,
              campaign_id TEXT NOT NULL REFERENCES campaign(campaign_id),
              work_digest TEXT NOT NULL,
              work_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS generation_allocation (
              generation_allocation_id TEXT PRIMARY KEY,
              campaign_id TEXT NOT NULL REFERENCES campaign(campaign_id),
              allocation_digest TEXT NOT NULL,
              allocation_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS attempt_receipt (
              attempt_id TEXT PRIMARY KEY,
              work_id TEXT NOT NULL REFERENCES candidate_work(work_id),
              receipt_digest TEXT NOT NULL,
              receipt_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS settlement (
              settlement_id TEXT PRIMARY KEY,
              work_id TEXT NOT NULL UNIQUE REFERENCES candidate_work(work_id),
              settlement_digest TEXT NOT NULL,
              settlement_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS mutation_preparation (
              preparation_id TEXT PRIMARY KEY,
              campaign_id TEXT NOT NULL REFERENCES campaign(campaign_id),
              preparation_digest TEXT NOT NULL,
              preparation_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS mutation_provider_attempt (
              provider_attempt_id TEXT PRIMARY KEY,
              preparation_id TEXT NOT NULL REFERENCES mutation_preparation(preparation_id),
              attempt_digest TEXT NOT NULL,
              attempt_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS mutation_budget_reservation (
              reservation_id TEXT PRIMARY KEY,
              campaign_id TEXT NOT NULL REFERENCES campaign(campaign_id),
              generation_allocation_id TEXT NOT NULL,
              reservation_digest TEXT NOT NULL,
              reservation_json TEXT NOT NULL,
              settled_by TEXT UNIQUE
            );
            CREATE TABLE IF NOT EXISTS preparation_cost_settlement (
              settlement_id TEXT PRIMARY KEY,
              reservation_id TEXT NOT NULL UNIQUE
                REFERENCES mutation_budget_reservation(reservation_id),
              settlement_digest TEXT NOT NULL,
              settlement_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS execution_handoff (
              handoff_id TEXT PRIMARY KEY,
              campaign_id TEXT NOT NULL REFERENCES campaign(campaign_id),
              preparation_id TEXT NOT NULL UNIQUE REFERENCES mutation_preparation(preparation_id),
              work_id TEXT NOT NULL UNIQUE REFERENCES candidate_work(work_id),
              handoff_digest TEXT NOT NULL,
              handoff_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS non_episode_settlement (
              settlement_id TEXT PRIMARY KEY,
              campaign_id TEXT NOT NULL REFERENCES campaign(campaign_id),
              generation_allocation_id TEXT NOT NULL UNIQUE,
              settlement_digest TEXT NOT NULL,
              settlement_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS finding (
              finding_key TEXT PRIMARY KEY,
              campaign_id TEXT NOT NULL REFERENCES campaign(campaign_id),
              finding_digest TEXT NOT NULL,
              finding_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS generation_feedback (
              feedback_digest TEXT PRIMARY KEY,
              campaign_id TEXT NOT NULL REFERENCES campaign(campaign_id),
              generation_index INTEGER NOT NULL,
              feedback_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS generation_decision (
              decision_digest TEXT PRIMARY KEY,
              campaign_id TEXT NOT NULL REFERENCES campaign(campaign_id),
              generation_index INTEGER NOT NULL,
              decision_json TEXT NOT NULL,
              UNIQUE(campaign_id, generation_index)
            );
            CREATE TABLE IF NOT EXISTS generation_closure (
              closure_digest TEXT PRIMARY KEY,
              campaign_id TEXT NOT NULL REFERENCES campaign(campaign_id),
              generation_index INTEGER NOT NULL,
              closure_json TEXT NOT NULL,
              UNIQUE(campaign_id, generation_index)
            );
            CREATE TABLE IF NOT EXISTS audit_event (
              sequence INTEGER PRIMARY KEY AUTOINCREMENT,
              campaign_id TEXT NOT NULL,
              event_kind TEXT NOT NULL,
              detail_json TEXT NOT NULL
            );
            """
        )
        columns = {
            row[1] for row in self._db.execute("PRAGMA table_info(campaign)").fetchall()
        }
        if "current_state_digest" not in columns:
            self._db.execute("ALTER TABLE campaign ADD COLUMN current_state_digest TEXT")
        self._db.commit()

    @staticmethod
    def _json(model) -> str:
        return model.model_dump_json(exclude_none=False)

    def create_campaign(
        self,
        *,
        campaign_id: str,
        identity: V2CampaignIdentityLock | Mapping[str, Any],
        initial_state: V2CampaignStateSnapshot,
    ) -> None:
        identity = require_v2_campaign_identity_lock(identity)
        with self._db:
            self._insert_state(initial_state)
            existing = self._db.execute(
                "SELECT identity_digest FROM campaign WHERE campaign_id=?",
                (campaign_id,),
            ).fetchone()
            if existing is not None:
                if existing["identity_digest"] != identity.identity_digest:
                    raise V2CampaignStoreError("campaign identity changed")
                return
            self._db.execute(
                "INSERT INTO campaign "
                "(campaign_id, identity_json, identity_digest, lifecycle_json, "
                "current_snapshot_digest, current_state_digest, generation_index) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    campaign_id,
                    self._json(identity),
                    identity.identity_digest,
                    self._json(initial_state.lifecycle),
                    initial_state.coverage.snapshot_digest,
                    initial_state.state_digest,
                    initial_state.lifecycle.counters.generation_index,
                ),
            )
            self._audit(campaign_id, "campaign-created", {"identity": identity.identity_digest})

    def campaign_exists(self, campaign_id: str) -> bool:
        return (
            self._db.execute(
                "SELECT 1 FROM campaign WHERE campaign_id=?", (campaign_id,)
            ).fetchone()
            is not None
        )

    def commit_generation(
        self,
        *,
        decision: GenerationDecision,
        next_state: V2CampaignStateSnapshot,
        closure: GenerationClosureReceipt,
        feedback: NextGenerationFeedback,
    ) -> None:
        """Atomically checkpoint a driver-computed generation."""

        current = self.load_state(decision.campaign_id)
        if decision.input_state_digest != current.state_digest:
            raise V2CampaignStoreError("generation decision does not match current state")
        if closure.generation_index != decision.generation_index:
            raise V2CampaignStoreError("generation closure index differs")
        if closure.resulting_state_digest != next_state.state_digest:
            raise V2CampaignStoreError("generation closure does not produce next state")
        if feedback.generation_index != next_state.lifecycle.counters.generation_index:
            raise V2CampaignStoreError("generation feedback does not match next state")
        with self._db:
            self._point_campaign_to_state(decision.campaign_id, next_state)
            self.put_generation_checkpoint(closure=closure, feedback=feedback)

    def commit_scripted_generation(
        self,
        *,
        decision: GenerationDecision,
        next_state: V2CampaignStateSnapshot,
        closure: GenerationClosureReceipt,
        feedback: NextGenerationFeedback,
    ) -> None:
        """Backward-compatible name for the Stage 5 scripted runner."""

        self.commit_generation(
            decision=decision,
            next_state=next_state,
            closure=closure,
            feedback=feedback,
        )

    def load_identity(self, campaign_id: str) -> V2CampaignIdentityLock:
        row = self._require_campaign(campaign_id)
        identity = require_v2_campaign_identity_lock(json.loads(row["identity_json"]))
        if row["identity_digest"] != identity.identity_digest:
            raise V2CampaignStoreError("stored campaign identity digest differs")
        return identity

    def load_lifecycle(self, campaign_id: str):
        return self.load_state(campaign_id).lifecycle

    def _require_campaign(self, campaign_id: str) -> sqlite3.Row:
        row = self._db.execute(
            "SELECT * FROM campaign WHERE campaign_id=?", (campaign_id,)
        ).fetchone()
        if row is None:
            raise V2CampaignStoreError("campaign does not exist")
        return row

    def _insert_snapshot(self, snapshot) -> None:
        existing = self._db.execute(
            "SELECT snapshot_json FROM coverage_snapshot WHERE snapshot_digest=?",
            (snapshot.snapshot_digest,),
        ).fetchone()
        payload = self._json(snapshot)
        if existing is not None and existing["snapshot_json"] != payload:
            raise V2CampaignStoreError("coverage snapshot digest collision")
        self._db.execute(
            "INSERT OR IGNORE INTO coverage_snapshot VALUES (?, ?)",
            (snapshot.snapshot_digest, payload),
        )

    def _insert_state(self, state: V2CampaignStateSnapshot) -> None:
        self._insert_snapshot(state.coverage)
        payload = self._json(state)
        existing = self._db.execute(
            "SELECT state_json FROM campaign_state_snapshot WHERE state_digest=?",
            (state.state_digest,),
        ).fetchone()
        if existing is not None and existing["state_json"] != payload:
            raise V2CampaignStoreError("campaign state digest collision")
        self._db.execute(
            "INSERT OR IGNORE INTO campaign_state_snapshot VALUES (?, ?)",
            (state.state_digest, payload),
        )

    def _point_campaign_to_state(
        self, campaign_id: str, state: V2CampaignStateSnapshot
    ) -> None:
        self._insert_state(state)
        self._db.execute(
            "UPDATE campaign SET lifecycle_json=?, current_snapshot_digest=?, "
            "current_state_digest=?, generation_index=? WHERE campaign_id=?",
            (
                self._json(state.lifecycle),
                state.coverage.snapshot_digest,
                state.state_digest,
                state.lifecycle.counters.generation_index,
                campaign_id,
            ),
        )

    def load_state(self, campaign_id: str) -> V2CampaignStateSnapshot:
        campaign = self._require_campaign(campaign_id)
        state_digest = campaign["current_state_digest"]
        if state_digest is None:
            raise V2CampaignStoreError("campaign has no complete state snapshot")
        row = self._db.execute(
            "SELECT state_json FROM campaign_state_snapshot WHERE state_digest=?",
            (state_digest,),
        ).fetchone()
        if row is None:
            raise V2CampaignStoreError("current campaign state snapshot is missing")
        state = V2CampaignStateSnapshot.model_validate_json(row["state_json"])
        if state.state_digest != state_digest:
            raise V2CampaignStoreError("campaign state pointer digest differs")
        if state.coverage.snapshot_digest != campaign["current_snapshot_digest"]:
            raise V2CampaignStoreError("campaign coverage pointer differs from state")
        if self._json(state.lifecycle) != campaign["lifecycle_json"]:
            raise V2CampaignStoreError("campaign lifecycle pointer differs from state")
        return state

    def put_work(self, work: CandidateWork) -> None:
        self._require_campaign(work.campaign_id)
        allocation = self._db.execute(
            "SELECT allocation_digest FROM generation_allocation "
            "WHERE generation_allocation_id=? AND campaign_id=?",
            (work.generation_allocation_id, work.campaign_id),
        ).fetchone()
        if (
            allocation is None
            or allocation["allocation_digest"] != work.generation_allocation_digest
        ):
            raise V2CampaignStoreError("candidate work requires persisted allocation")
        payload = self._json(work)
        with self._db:
            row = self._db.execute(
                "SELECT work_digest, work_json FROM candidate_work WHERE work_id=?",
                (work.work_id,),
            ).fetchone()
            if row is not None:
                if row["work_digest"] != work.work_digest or row["work_json"] != payload:
                    raise V2CampaignStoreError("candidate work is immutable; use transition_work")
                return
            self._db.execute(
                "INSERT INTO candidate_work VALUES (?, ?, ?, ?)",
                (work.work_id, work.campaign_id, work.work_digest, payload),
            )
            self._audit(work.campaign_id, "work-created", {"work_id": work.work_id})

    def put_allocation(
        self, *, campaign_id: str, allocation: GenerationAllocation
    ) -> None:
        self._require_campaign(campaign_id)
        payload = self._json(allocation)
        with self._db:
            row = self._db.execute(
                "SELECT allocation_digest, allocation_json FROM generation_allocation "
                "WHERE generation_allocation_id=?",
                (allocation.generation_allocation_id,),
            ).fetchone()
            if row is not None:
                if (
                    row["allocation_digest"] != allocation.allocation_digest
                    or row["allocation_json"] != payload
                ):
                    raise V2CampaignStoreError("generation allocation is immutable")
                return
            self._db.execute(
                "INSERT INTO generation_allocation VALUES (?, ?, ?, ?)",
                (
                    allocation.generation_allocation_id,
                    campaign_id,
                    allocation.allocation_digest,
                    payload,
                ),
            )
            self._audit(
                campaign_id,
                "allocation-persisted",
                {"allocation_id": allocation.generation_allocation_id},
            )

    def put_scheduled_work(
        self,
        *,
        campaign_id: str,
        allocation: GenerationAllocation,
        work: CandidateWork,
        reserved_state: V2CampaignStateSnapshot,
    ) -> None:
        current = self.load_state(campaign_id)
        if (
            allocation.coverage_snapshot_digest != current.coverage.snapshot_digest
            or allocation.corpus_digest != current.corpus.snapshot_digest
            or allocation.frontier_digest != current.frontiers.snapshot_digest
        ):
            raise V2CampaignStoreError("allocation does not use current campaign state")
        if (
            work.campaign_id != campaign_id
            or work.generation_allocation_id != allocation.generation_allocation_id
            or work.generation_allocation_digest != allocation.allocation_digest
            or work.baseline_snapshot_digest != current.coverage.snapshot_digest
        ):
            raise V2CampaignStoreError("candidate work does not close over allocation")
        if any(
            before != after
            for before, after in (
                (current.coverage, reserved_state.coverage),
                (current.corpus, reserved_state.corpus),
                (current.frontiers, reserved_state.frontiers),
                (current.exposure_ledger, reserved_state.exposure_ledger),
                (current.lifecycle, reserved_state.lifecycle),
            )
        ):
            raise V2CampaignStoreError("scheduling may only reserve campaign budget")
        if (
            reserved_state.budget.reserved_episodes
            != current.budget.reserved_episodes + 1
        ):
            raise V2CampaignStoreError("scheduled work requires one Episode reservation")
        allocation_payload = self._json(allocation)
        work_payload = self._json(work)
        with self._db:
            existing = self._db.execute(
                "SELECT allocation_digest, allocation_json FROM generation_allocation "
                "WHERE generation_allocation_id=?",
                (allocation.generation_allocation_id,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["allocation_digest"] != allocation.allocation_digest
                    or existing["allocation_json"] != allocation_payload
                ):
                    raise V2CampaignStoreError("generation allocation is immutable")
            else:
                self._db.execute(
                    "INSERT INTO generation_allocation VALUES (?, ?, ?, ?)",
                    (
                        allocation.generation_allocation_id,
                        campaign_id,
                        allocation.allocation_digest,
                        allocation_payload,
                    ),
                )
            existing_work = self._db.execute(
                "SELECT work_digest, work_json FROM candidate_work WHERE work_id=?",
                (work.work_id,),
            ).fetchone()
            if existing_work is not None:
                if (
                    existing_work["work_digest"] != work.work_digest
                    or existing_work["work_json"] != work_payload
                ):
                    raise V2CampaignStoreError("candidate work is immutable")
            else:
                self._db.execute(
                    "INSERT INTO candidate_work VALUES (?, ?, ?, ?)",
                    (work.work_id, campaign_id, work.work_digest, work_payload),
                )
            self._insert_state(reserved_state)
            self._db.execute(
                "UPDATE campaign SET lifecycle_json=?, current_snapshot_digest=?, "
                "current_state_digest=? WHERE campaign_id=?",
                (
                    self._json(reserved_state.lifecycle),
                    reserved_state.coverage.snapshot_digest,
                    reserved_state.state_digest,
                    campaign_id,
                ),
            )
            self._audit(
                campaign_id,
                "scheduled-work-persisted",
                {
                    "allocation_id": allocation.generation_allocation_id,
                    "work_id": work.work_id,
                    "state_digest": reserved_state.state_digest,
                },
            )

    def load_allocation(self, allocation_id: str) -> GenerationAllocation:
        row = self._db.execute(
            "SELECT allocation_json FROM generation_allocation "
            "WHERE generation_allocation_id=?",
            (allocation_id,),
        ).fetchone()
        if row is None:
            raise V2CampaignStoreError("generation allocation does not exist")
        return GenerationAllocation.model_validate_json(row["allocation_json"])

    def load_work(self, work_id: str) -> CandidateWork:
        row = self._db.execute(
            "SELECT work_json FROM candidate_work WHERE work_id=?", (work_id,)
        ).fetchone()
        if row is None:
            raise V2CampaignStoreError("candidate work does not exist")
        return CandidateWork.model_validate_json(row["work_json"])

    def transition_work(
        self,
        work_id: str,
        *,
        state: CandidateWorkState,
        sealed_execution_record_id: str | None = None,
    ) -> CandidateWork:
        current = self.load_work(work_id)
        allowed = {
            CandidateWorkState.ALLOCATED: {
                CandidateWorkState.EXECUTING,
                CandidateWorkState.FAILED,
            },
            CandidateWorkState.EXECUTING: {
                CandidateWorkState.SEALED,
                CandidateWorkState.AMBIGUOUS,
                CandidateWorkState.FAILED,
            },
            CandidateWorkState.SEALED: {CandidateWorkState.COMMITTED},
        }
        if state is current.state:
            return current
        if state not in allowed.get(current.state, set()):
            raise V2CampaignStoreError("invalid candidate work transition")
        draft = current.model_copy(
            update={
                "state": state,
                "sealed_execution_record_id": sealed_execution_record_id,
                "attempt_ids": tuple(
                    item.attempt_id for item in self.receipts_for_work(work_id)
                ),
                "work_digest": "sha256:" + "0" * 64,
            }
        )
        updated = CandidateWork.model_validate(
            {
                **draft.model_dump(mode="python", exclude={"work_digest"}),
                "work_digest": sha256_digest(draft.digest_payload()),
            }
        )
        campaign_id = current.campaign_id
        with self._db:
            self._db.execute(
                "UPDATE candidate_work SET work_digest=?, work_json=? WHERE work_id=?",
                (updated.work_digest, self._json(updated), work_id),
            )
            self._audit(campaign_id, "work-transition", {"work_id": work_id, "state": state})
        return updated

    def seal_attempt(self, receipt: AttemptReceipt) -> None:
        work = self.load_work(receipt.work_id)
        if work.state is not CandidateWorkState.EXECUTING:
            raise V2CampaignStoreError("attempt requires executing work")
        payload = self._json(receipt)
        with self._db:
            row = self._db.execute(
                "SELECT receipt_digest, receipt_json FROM attempt_receipt WHERE attempt_id=?",
                (receipt.attempt_id,),
            ).fetchone()
            if row is not None:
                if (
                    row["receipt_digest"] != receipt.receipt_digest
                    or row["receipt_json"] != payload
                ):
                    raise V2CampaignStoreError("attempt receipt is immutable")
                return
            expected_number = len(self.receipts_for_work(receipt.work_id)) + 1
            if receipt.attempt_number != expected_number or expected_number > work.max_attempts:
                raise V2CampaignStoreError("attempt sequence exceeds frozen bounds")
            self._db.execute(
                "INSERT INTO attempt_receipt VALUES (?, ?, ?, ?)",
                (receipt.attempt_id, receipt.work_id, receipt.receipt_digest, payload),
            )
            self._audit(work.campaign_id, "attempt-sealed", {"attempt_id": receipt.attempt_id})

    def receipts_for_work(self, work_id: str) -> tuple[AttemptReceipt, ...]:
        rows = self._db.execute(
            "SELECT receipt_json FROM attempt_receipt WHERE work_id=? ORDER BY rowid",
            (work_id,),
        ).fetchall()
        return tuple(AttemptReceipt.model_validate_json(item["receipt_json"]) for item in rows)

    def commit_settlement(
        self,
        *,
        campaign_id: str,
        settlement: CandidateSettlement,
        next_state: V2CampaignStateSnapshot,
        feedback: NextGenerationFeedback | None = None,
        finding: FindingRecord | None = None,
        closure: GenerationClosureReceipt | None = None,
    ) -> bool:
        expected_state_digests = {
            "next_coverage_snapshot_digest": next_state.coverage.snapshot_digest,
            "corpus_snapshot_digest": next_state.corpus.snapshot_digest,
            "frontier_snapshot_digest": next_state.frontiers.snapshot_digest,
            "exposure_ledger_digest": next_state.exposure_ledger.ledger_digest,
            "budget_digest": next_state.budget.budget_digest,
            "lifecycle_digest": next_state.lifecycle_digest,
            "next_campaign_state_digest": next_state.state_digest,
        }
        for field_name, expected in expected_state_digests.items():
            if getattr(settlement, field_name) != expected:
                raise V2CampaignStoreError(f"settlement {field_name} differs from state")
        if closure is not None and (
            closure.campaign_id != campaign_id
            or closure.generation_index
            != next_state.lifecycle.counters.generation_index - 1
            or closure.settlement_id != settlement.settlement_id
            or closure.settlement_digest != settlement.settlement_digest
            or closure.resulting_state_digest != next_state.state_digest
        ):
            raise V2CampaignStoreError("generation closure does not match settlement")
        work = self.load_work(settlement.work_id)
        receipt_ids = tuple(item.attempt_id for item in self.receipts_for_work(work.work_id))
        if receipt_ids != settlement.attempt_receipt_ids:
            raise V2CampaignStoreError("settlement attempt receipt lineage differs")
        if work.sealed_execution_record_id != settlement.execution_record_id:
            raise V2CampaignStoreError("settlement execution lineage differs")
        if work.campaign_id != campaign_id or work.state is not CandidateWorkState.SEALED:
            existing = self._db.execute(
                "SELECT settlement_digest FROM settlement WHERE settlement_id=?",
                (settlement.settlement_id,),
            ).fetchone()
            if (
                existing is not None
                and existing["settlement_digest"] == settlement.settlement_digest
            ):
                return False
            raise V2CampaignStoreError("settlement requires sealed work")
        with self._db:
            existing = self._db.execute(
                "SELECT settlement_digest FROM settlement WHERE settlement_id=?",
                (settlement.settlement_id,),
            ).fetchone()
            if existing is not None:
                if existing["settlement_digest"] != settlement.settlement_digest:
                    raise V2CampaignStoreError("settlement id has different content")
                return False
            self._insert_state(next_state)
            self._db.execute(
                "INSERT INTO settlement VALUES (?, ?, ?, ?)",
                (
                    settlement.settlement_id,
                    settlement.work_id,
                    settlement.settlement_digest,
                    self._json(settlement),
                ),
            )
            if feedback is not None:
                if feedback.campaign_id != campaign_id:
                    raise V2CampaignStoreError("feedback belongs to another Campaign")
                self._insert_generation_feedback(feedback)
            if closure is not None:
                self._insert_generation_closure(closure)
            if finding is not None:
                if finding.campaign_id != campaign_id:
                    raise V2CampaignStoreError("finding belongs to another Campaign")
                existing_finding = self._db.execute(
                    "SELECT finding_json FROM finding WHERE finding_key=?",
                    (finding.finding_key,),
                ).fetchone()
                if existing_finding is None:
                    self._db.execute(
                        "INSERT INTO finding VALUES (?, ?, ?, ?)",
                        (
                            finding.finding_key,
                            finding.campaign_id,
                            finding.finding_digest,
                            self._json(finding),
                        ),
                    )
                elif existing_finding["finding_json"] != self._json(finding):
                    raise V2CampaignStoreError("finding identity already has other content")
            draft = work.model_copy(
                update={
                    "state": CandidateWorkState.COMMITTED,
                    "work_digest": "sha256:" + "0" * 64,
                }
            )
            committed = CandidateWork.model_validate(
                {
                    **draft.model_dump(mode="python", exclude={"work_digest"}),
                    "work_digest": sha256_digest(draft.digest_payload()),
                }
            )
            self._db.execute(
                "UPDATE candidate_work SET work_digest=?, work_json=? WHERE work_id=?",
                (committed.work_digest, self._json(committed), work.work_id),
            )
            self._db.execute(
                "UPDATE campaign SET lifecycle_json=?, current_snapshot_digest=?, "
                "current_state_digest=?, generation_index=? "
                "WHERE campaign_id=?",
                (
                    self._json(next_state.lifecycle),
                    next_state.coverage.snapshot_digest,
                    next_state.state_digest,
                    next_state.lifecycle.counters.generation_index,
                    campaign_id,
                ),
            )
            self._audit(campaign_id, "settlement-committed", {"work_id": work.work_id})
        return True

    def recover(self, campaign_id: str) -> dict[str, tuple[str, ...]]:
        self.load_identity(campaign_id)
        self.load_state(campaign_id)
        rows = self._db.execute(
            "SELECT work_id FROM candidate_work WHERE campaign_id=?", (campaign_id,)
        ).fetchall()
        resumable: list[str] = []
        ambiguous: list[str] = []
        sealed: list[str] = []
        for row in rows:
            work = self.load_work(row["work_id"])
            allocation = self.load_allocation(work.generation_allocation_id)
            if allocation.allocation_digest != work.generation_allocation_digest:
                raise V2CampaignStoreError("candidate work allocation digest differs")
            receipts = self.receipts_for_work(work.work_id)
            if work.state is CandidateWorkState.EXECUTING:
                if not receipts or receipts[-1].disposition in {
                    AttemptDisposition.AMBIGUOUS,
                    AttemptDisposition.UNKNOWN_FAILURE,
                    AttemptDisposition.SUCCEEDED,
                }:
                    work = self.transition_work(
                        work.work_id, state=CandidateWorkState.AMBIGUOUS
                    )
                    ambiguous.append(work.work_id)
                elif retry_allowed(work=work, receipts=receipts):
                    resumable.append(work.work_id)
                else:
                    self.transition_work(work.work_id, state=CandidateWorkState.FAILED)
            elif work.state is CandidateWorkState.SEALED:
                sealed.append(work.work_id)
            elif work.state is CandidateWorkState.AMBIGUOUS:
                ambiguous.append(work.work_id)
        return {
            "resumable": tuple(sorted(resumable)),
            "ambiguous": tuple(sorted(ambiguous)),
            "sealed_uncommitted": tuple(sorted(sealed)),
        }

    def generation_index(self, campaign_id: str) -> int:
        return int(self._require_campaign(campaign_id)["generation_index"])

    def put_mutation_preparation(self, preparation: MutationPreparation) -> None:
        self._require_campaign(preparation.campaign_id)
        payload = self._json(preparation)
        with self._db:
            existing = self._db.execute(
                "SELECT preparation_digest, preparation_json "
                "FROM mutation_preparation WHERE preparation_id=?",
                (preparation.preparation_id,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["preparation_digest"] != preparation.preparation_digest
                    or existing["preparation_json"] != payload
                ):
                    raise V2CampaignStoreError("mutation preparation is immutable")
                return
            self._db.execute(
                "INSERT INTO mutation_preparation VALUES (?, ?, ?, ?)",
                (
                    preparation.preparation_id,
                    preparation.campaign_id,
                    preparation.preparation_digest,
                    payload,
                ),
            )
            for attempt in preparation.provider_attempts:
                self._insert_mutation_provider_attempt(preparation.preparation_id, attempt)
            self._audit(
                preparation.campaign_id,
                "mutation-preparation-sealed",
                {"preparation_id": preparation.preparation_id, "state": preparation.state},
            )

    def _insert_mutation_provider_attempt(
        self, preparation_id: str, attempt: MutationProviderAttempt
    ) -> None:
        self._db.execute(
            "INSERT INTO mutation_provider_attempt VALUES (?, ?, ?, ?)",
            (
                attempt.provider_attempt_id,
                preparation_id,
                attempt.attempt_digest,
                self._json(attempt),
            ),
        )

    def load_mutation_preparation(self, preparation_id: str) -> MutationPreparation:
        row = self._db.execute(
            "SELECT preparation_json FROM mutation_preparation WHERE preparation_id=?",
            (preparation_id,),
        ).fetchone()
        if row is None:
            raise V2CampaignStoreError("unknown mutation preparation")
        return MutationPreparation.model_validate_json(row["preparation_json"])

    def reserve_mutation(
        self,
        *,
        reservation: MutationBudgetReservation,
        next_state: V2CampaignStateSnapshot,
    ) -> bool:
        """Persist the maximum MutationPlan budget before Provider execution."""
        current = self.load_state(reservation.campaign_id)
        if next_state.coverage != current.coverage:
            raise V2CampaignStoreError("mutation reservation cannot change Coverage")
        payload = self._json(reservation)
        with self._db:
            existing = self._db.execute(
                "SELECT reservation_digest, reservation_json "
                "FROM mutation_budget_reservation WHERE reservation_id=?",
                (reservation.reservation_id,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["reservation_digest"] != reservation.reservation_digest
                    or existing["reservation_json"] != payload
                ):
                    raise V2CampaignStoreError("mutation reservation is immutable")
                return False
            if self.load_state(reservation.campaign_id).state_digest != current.state_digest:
                raise V2CampaignStoreError("campaign state changed during reservation")
            self._db.execute(
                "INSERT INTO mutation_budget_reservation "
                "(reservation_id, campaign_id, generation_allocation_id, "
                "reservation_digest, reservation_json, settled_by) "
                "VALUES (?, ?, ?, ?, ?, NULL)",
                (
                    reservation.reservation_id,
                    reservation.campaign_id,
                    reservation.generation_allocation_id,
                    reservation.reservation_digest,
                    payload,
                ),
            )
            self._point_campaign_to_state(reservation.campaign_id, next_state)
            self._audit(
                reservation.campaign_id,
                "mutation-budget-reserved",
                {"reservation_id": reservation.reservation_id},
            )
        return True

    def settle_preparation_cost(
        self,
        *,
        settlement: PreparationCostSettlement,
        next_state: V2CampaignStateSnapshot,
    ) -> bool:
        current = self.load_state(settlement.campaign_id)
        if (
            next_state.coverage != current.coverage
            or next_state.corpus != current.corpus
            or next_state.frontiers != current.frontiers
            or next_state.exposure_ledger != current.exposure_ledger
        ):
            raise V2CampaignStoreError(
                "preparation cost settlement cannot change test facts"
            )
        payload = self._json(settlement)
        with self._db:
            existing = self._db.execute(
                "SELECT settlement_digest, settlement_json "
                "FROM preparation_cost_settlement WHERE settlement_id=?",
                (settlement.settlement_id,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["settlement_digest"] != settlement.settlement_digest
                    or existing["settlement_json"] != payload
                ):
                    raise V2CampaignStoreError(
                        "preparation cost settlement is immutable"
                    )
                return False
            reservation = self._db.execute(
                "SELECT settled_by FROM mutation_budget_reservation "
                "WHERE reservation_id=? AND campaign_id=?",
                (settlement.reservation_id, settlement.campaign_id),
            ).fetchone()
            if reservation is None:
                raise V2CampaignStoreError("mutation reservation is missing")
            if reservation["settled_by"] is not None:
                raise V2CampaignStoreError("mutation reservation is already settled")
            preparation = self.load_mutation_preparation(settlement.preparation_id)
            if preparation.preparation_digest != settlement.preparation_digest:
                raise V2CampaignStoreError("preparation settlement lineage differs")
            self._db.execute(
                "INSERT INTO preparation_cost_settlement VALUES (?, ?, ?, ?)",
                (
                    settlement.settlement_id,
                    settlement.reservation_id,
                    settlement.settlement_digest,
                    payload,
                ),
            )
            self._db.execute(
                "UPDATE mutation_budget_reservation SET settled_by=? "
                "WHERE reservation_id=?",
                (settlement.settlement_id, settlement.reservation_id),
            )
            self._point_campaign_to_state(settlement.campaign_id, next_state)
            self._audit(
                settlement.campaign_id,
                "preparation-cost-settled",
                {"settlement_id": settlement.settlement_id},
            )
        return True

    def put_execution_handoff(
        self,
        *,
        handoff: ExecutionHandoff,
        work: CandidateWork,
        next_state: V2CampaignStateSnapshot,
    ) -> bool:
        if work.campaign_id != handoff.campaign_id:
            raise V2CampaignStoreError("handoff and Work use different Campaigns")
        if work.generation_allocation_digest != handoff.generation_allocation_digest:
            raise V2CampaignStoreError("handoff and Work allocation differ")
        if work.comparison_context_digest != handoff.comparison_context_digest:
            raise V2CampaignStoreError("handoff and Work comparison context differ")
        if work.baseline_snapshot_digest != handoff.baseline_snapshot_digest:
            raise V2CampaignStoreError("handoff and Work baseline differ")
        payload = self._json(handoff)
        with self._db:
            existing = self._db.execute(
                "SELECT handoff_digest, handoff_json, work_id "
                "FROM execution_handoff WHERE handoff_id=?",
                (handoff.handoff_id,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["handoff_digest"] != handoff.handoff_digest
                    or existing["handoff_json"] != payload
                    or existing["work_id"] != work.work_id
                ):
                    raise V2CampaignStoreError("execution handoff is immutable")
                return False
            self.put_work(work)
            self._db.execute(
                "INSERT INTO execution_handoff VALUES (?, ?, ?, ?, ?, ?)",
                (
                    handoff.handoff_id,
                    handoff.campaign_id,
                    handoff.preparation_id,
                    work.work_id,
                    handoff.handoff_digest,
                    payload,
                ),
            )
            self._point_campaign_to_state(handoff.campaign_id, next_state)
            self._audit(
                handoff.campaign_id,
                "execution-handoff-created",
                {"handoff_id": handoff.handoff_id, "work_id": work.work_id},
            )
        return True

    def commit_non_episode_settlement(
        self,
        *,
        settlement: NonEpisodeSettlement,
        next_state: V2CampaignStateSnapshot,
        feedback: NextGenerationFeedback | None = None,
        closure: GenerationClosureReceipt | None = None,
    ) -> bool:
        current = self.load_state(settlement.campaign_id)
        if settlement.next_state_digest != next_state.state_digest:
            raise V2CampaignStoreError("non-Episode settlement state differs")
        if (
            current.coverage != next_state.coverage
            or current.corpus != next_state.corpus
            or current.frontiers != next_state.frontiers
            or current.exposure_ledger != next_state.exposure_ledger
        ):
            raise V2CampaignStoreError("non-Episode settlement changed test facts")
        if feedback is not None and (
            feedback.campaign_id != settlement.campaign_id
            or feedback.generation_index
            != next_state.lifecycle.counters.generation_index
        ):
            raise V2CampaignStoreError("non-Episode feedback lineage differs")
        if closure is not None and (
            closure.campaign_id != settlement.campaign_id
            or closure.generation_index
            != next_state.lifecycle.counters.generation_index - 1
            or closure.settlement_id != settlement.settlement_id
            or closure.settlement_digest != settlement.settlement_digest
            or closure.resulting_state_digest != next_state.state_digest
        ):
            raise V2CampaignStoreError("non-Episode closure lineage differs")
        payload = self._json(settlement)
        with self._db:
            existing = self._db.execute(
                "SELECT settlement_digest, settlement_json "
                "FROM non_episode_settlement WHERE settlement_id=?",
                (settlement.settlement_id,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["settlement_digest"] != settlement.settlement_digest
                    or existing["settlement_json"] != payload
                ):
                    raise V2CampaignStoreError("non-Episode settlement is immutable")
                return False
            self._db.execute(
                "INSERT INTO non_episode_settlement VALUES (?, ?, ?, ?, ?)",
                (
                    settlement.settlement_id,
                    settlement.campaign_id,
                    settlement.generation_allocation_id,
                    settlement.settlement_digest,
                    payload,
                ),
            )
            if feedback is not None:
                self._insert_generation_feedback(feedback)
            if closure is not None:
                self._insert_generation_closure(closure)
            self._point_campaign_to_state(settlement.campaign_id, next_state)
            self._audit(
                settlement.campaign_id,
                "non-episode-settled",
                {"settlement_id": settlement.settlement_id},
            )
        return True

    def put_generation_decision(self, decision: GenerationDecision) -> bool:
        self._require_campaign(decision.campaign_id)
        payload = self._json(decision)
        with self._db:
            existing = self._db.execute(
                "SELECT decision_digest, decision_json FROM generation_decision "
                "WHERE campaign_id=? AND generation_index=?",
                (decision.campaign_id, decision.generation_index),
            ).fetchone()
            if existing is not None:
                if (
                    existing["decision_digest"] != decision.decision_digest
                    or existing["decision_json"] != payload
                ):
                    raise V2CampaignStoreError("generation decision is immutable")
                return False
            self._db.execute(
                "INSERT INTO generation_decision VALUES (?, ?, ?, ?)",
                (
                    decision.decision_digest,
                    decision.campaign_id,
                    decision.generation_index,
                    payload,
                ),
            )
        return True

    def load_latest_generation_decision(
        self, campaign_id: str
    ) -> GenerationDecision | None:
        row = self._db.execute(
            "SELECT decision_json FROM generation_decision WHERE campaign_id=? "
            "ORDER BY generation_index DESC LIMIT 1",
            (campaign_id,),
        ).fetchone()
        return (
            GenerationDecision.model_validate_json(row["decision_json"])
            if row is not None
            else None
        )

    def put_generation_closure(self, closure: GenerationClosureReceipt) -> bool:
        self._require_campaign(closure.campaign_id)
        payload = self._json(closure)
        with self._db:
            existing = self._db.execute(
                "SELECT closure_digest, closure_json FROM generation_closure "
                "WHERE campaign_id=? AND generation_index=?",
                (closure.campaign_id, closure.generation_index),
            ).fetchone()
            if existing is not None:
                if (
                    existing["closure_digest"] != closure.closure_digest
                    or existing["closure_json"] != payload
                ):
                    raise V2CampaignStoreError("generation closure is immutable")
                return False
            self._db.execute(
                "INSERT INTO generation_closure VALUES (?, ?, ?, ?)",
                (
                    closure.closure_digest,
                    closure.campaign_id,
                    closure.generation_index,
                    payload,
                ),
            )
        return True

    def put_generation_checkpoint(
        self,
        *,
        closure: GenerationClosureReceipt,
        feedback: NextGenerationFeedback,
    ) -> bool:
        """Persist post-settlement lineage; current state must already match it."""
        state = self.load_state(closure.campaign_id)
        if closure.resulting_state_digest != state.state_digest:
            raise V2CampaignStoreError("generation closure is not current state")
        if feedback.campaign_id != closure.campaign_id:
            raise V2CampaignStoreError("generation feedback belongs to another Campaign")
        if feedback.generation_index != state.lifecycle.counters.generation_index:
            raise V2CampaignStoreError("generation feedback does not match current index")
        with self._db:
            inserted = self.put_generation_closure(closure)
            existing = self._db.execute(
                "SELECT feedback_json FROM generation_feedback WHERE feedback_digest=?",
                (feedback.feedback_digest,),
            ).fetchone()
            payload = self._json(feedback)
            if existing is not None and existing["feedback_json"] != payload:
                raise V2CampaignStoreError("generation feedback is immutable")
            self._db.execute(
                "INSERT OR IGNORE INTO generation_feedback VALUES (?, ?, ?, ?)",
                (
                    feedback.feedback_digest,
                    feedback.campaign_id,
                    feedback.generation_index,
                    payload,
                ),
            )
        return inserted

    def load_latest_generation_closure(
        self, campaign_id: str
    ) -> GenerationClosureReceipt | None:
        row = self._db.execute(
            "SELECT closure_json FROM generation_closure WHERE campaign_id=? "
            "ORDER BY generation_index DESC LIMIT 1",
            (campaign_id,),
        ).fetchone()
        return (
            GenerationClosureReceipt.model_validate_json(row["closure_json"])
            if row is not None
            else None
        )

    def load_latest_feedback(self, campaign_id: str) -> NextGenerationFeedback | None:
        row = self._db.execute(
            "SELECT feedback_json FROM generation_feedback WHERE campaign_id=? "
            "ORDER BY generation_index DESC LIMIT 1",
            (campaign_id,),
        ).fetchone()
        return (
            NextGenerationFeedback.model_validate_json(row["feedback_json"])
            if row is not None
            else None
        )

    def journal_mode(self) -> str:
        return str(self._db.execute("PRAGMA journal_mode").fetchone()[0]).lower()

    def _insert_generation_feedback(self, feedback: NextGenerationFeedback) -> bool:
        payload = self._json(feedback)
        existing = self._db.execute(
            "SELECT feedback_digest, feedback_json FROM generation_feedback "
            "WHERE campaign_id=? AND generation_index=?",
            (feedback.campaign_id, feedback.generation_index),
        ).fetchone()
        if existing is not None:
            if (
                existing["feedback_digest"] != feedback.feedback_digest
                or existing["feedback_json"] != payload
            ):
                raise V2CampaignStoreError("generation feedback is immutable")
            return False
        self._db.execute(
            "INSERT INTO generation_feedback VALUES (?, ?, ?, ?)",
            (
                feedback.feedback_digest,
                feedback.campaign_id,
                feedback.generation_index,
                payload,
            ),
        )
        return True

    def _insert_generation_closure(self, closure: GenerationClosureReceipt) -> bool:
        payload = self._json(closure)
        existing = self._db.execute(
            "SELECT closure_digest, closure_json FROM generation_closure "
            "WHERE campaign_id=? AND generation_index=?",
            (closure.campaign_id, closure.generation_index),
        ).fetchone()
        if existing is not None:
            if (
                existing["closure_digest"] != closure.closure_digest
                or existing["closure_json"] != payload
            ):
                raise V2CampaignStoreError("generation closure is immutable")
            return False
        self._db.execute(
            "INSERT INTO generation_closure VALUES (?, ?, ?, ?)",
            (
                closure.closure_digest,
                closure.campaign_id,
                closure.generation_index,
                payload,
            ),
        )
        return True

    def _audit(self, campaign_id: str, event_kind: str, detail: Mapping[str, Any]) -> None:
        self._db.execute(
            "INSERT INTO audit_event(campaign_id, event_kind, detail_json) VALUES (?, ?, ?)",
            (campaign_id, event_kind, json.dumps(detail, sort_keys=True)),
        )


__all__ = ["V2CampaignStore", "V2CampaignStoreError"]
