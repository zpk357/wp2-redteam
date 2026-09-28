"""Deterministic clean-case materialization from the frozen task catalog."""

from __future__ import annotations

from typing import Self

from pydantic import Field, field_validator, model_validator

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2 import OFFICE_V2_TASK_CATALOG_VERSION
from sandbox.scenarios.office_v2.canonical_world import (
    CanonicalOfficeWorld,
    OfficeWorldState,
    load_canonical_world,
)
from sandbox.scenarios.office_v2.models import (
    AccessRight,
    ActionKind,
    ActorContext,
    BranchCondition,
    ClarificationGate,
    ClarificationRequest,
    GrantTemplate,
    Identifier,
    InteractionContract,
    IssuerAuthentication,
    OfficeV2Contract,
    PredicateField,
    PredicateOperator,
    PrincipalKind,
    QuestionKind,
    ResolvedBinding,
    ResourcePredicate,
    ResourceQuery,
    ResourceRef,
    ResourceRelationConstraint,
    ResponseMatch,
    Sha256Digest,
    TaskContract,
    TaskDelegation,
    TaskFact,
    TaskGoal,
    TaskGoalGraph,
    UserResponseRule,
    canonicalize_identifiers,
)
from sandbox.scenarios.office_v2.resolution import (
    resolve_clarification_selection,
    resolve_resource_query,
)
from sandbox.scenarios.office_v2.task_catalog import (
    MAIL_SEND,
    TASK_BLUEPRINT_BY_ID,
    TASK_BLUEPRINT_CATALOG_DIGEST,
    TASK_BLUEPRINTS,
    BlueprintQuery,
    TaskBlueprint,
)

CASE_MATERIALIZATION_VERSION = "office-v2-clean-case-v1"


class CleanCaseSeed(OfficeV2Contract):
    case_id: Identifier
    blueprint_id: Identifier
    project: Identifier
    actor_id: Identifier
    issuer_principal_id: Identifier


class CleanCaseMaterialization(OfficeV2Contract):
    case_id: Identifier
    case_version: Identifier = CASE_MATERIALIZATION_VERSION
    catalog_version: Identifier = OFFICE_V2_TASK_CATALOG_VERSION
    catalog_digest: Sha256Digest
    blueprint_id: Identifier
    blueprint_digest: Sha256Digest
    base_world_version: str
    base_world_digest: Sha256Digest
    actor: ActorContext
    task: TaskContract
    resolved_bindings: tuple[ResolvedBinding, ...] = Field(min_length=1)
    case_digest: Sha256Digest

    @field_validator("resolved_bindings")
    @classmethod
    def bindings_are_canonical(
        cls, value: tuple[ResolvedBinding, ...]
    ) -> tuple[ResolvedBinding, ...]:
        query_ids = tuple(item.query_id for item in value)
        canonicalize_identifiers(query_ids, field_name="case binding query ids")
        return tuple(sorted(value, key=lambda item: item.query_id))

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"case_digest"}, exclude_none=False)

    @model_validator(mode="after")
    def digest_and_references_match(self) -> Self:
        if self.case_digest != sha256_digest(self.digest_payload()):
            raise ValueError("case_digest does not match materialized case")
        if self.actor.actor_id != self.task.actor_id:
            raise ValueError("case actor and task actor must match")
        query_ids = {item.query_id for item in self.task.resource_queries}
        if {item.query_id for item in self.resolved_bindings} != query_ids:
            raise ValueError("case must freeze exactly one binding per task query")
        return self


class CleanCaseCatalog(OfficeV2Contract):
    catalog_version: Identifier = OFFICE_V2_TASK_CATALOG_VERSION
    blueprint_catalog_digest: Sha256Digest
    world_digest: Sha256Digest
    cases: tuple[CleanCaseMaterialization, ...] = Field(min_length=1)
    catalog_digest: Sha256Digest

    @field_validator("cases")
    @classmethod
    def cases_are_canonical(
        cls, value: tuple[CleanCaseMaterialization, ...]
    ) -> tuple[CleanCaseMaterialization, ...]:
        case_ids = tuple(item.case_id for item in value)
        canonicalize_identifiers(case_ids, field_name="clean case ids")
        return tuple(sorted(value, key=lambda item: item.case_id))

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"catalog_digest"}, exclude_none=False)

    @model_validator(mode="after")
    def digest_matches(self) -> Self:
        if self.catalog_digest != sha256_digest(self.digest_payload()):
            raise ValueError("clean case catalog digest does not match payload")
        return self


_PROJECT_ACTORS = {
    "apollo": "user.maya.chen",
    "borealis": "user.priya.shah",
    "cedar": "user.elena.rossi",
    "delta": "user.rachel.stone",
    "evergreen": "user.hana.sato",
}

_CASE_PROJECTS = {
    "t1-review-from-approved-brief": ("apollo", "borealis", "cedar"),
    "t2-reschedule-from-current-roster": ("delta", "evergreen", "apollo"),
    "t3-cancel-superseded-review": ("borealis", "cedar"),
    "t4-build-drive-brief": ("delta", "evergreen"),
    "t5-approved-internal-distribution": ("apollo", "borealis"),
    "t6-maintain-project-access": ("cedar", "delta"),
    "t7-archive-obsolete-draft": ("borealis", "apollo"),
    "t8-reconcile-attachment-set": ("borealis", "cedar"),
    "t9-meeting-follow-up-package": ("delta", "borealis", "apollo"),
    "t10-workspace-to-drive-handoff": ("borealis", "cedar", "delta"),
}


def _seed_catalog() -> tuple[CleanCaseSeed, ...]:
    seeds: list[CleanCaseSeed] = []
    for blueprint_id, projects in _CASE_PROJECTS.items():
        short_id = blueprint_id.split("-", 1)[0]
        for project in projects:
            actor_id = _PROJECT_ACTORS[project]
            seeds.append(
                CleanCaseSeed(
                    case_id=f"clean.{short_id}.{project}",
                    blueprint_id=blueprint_id,
                    project=project,
                    actor_id=actor_id,
                    issuer_principal_id=actor_id,
                )
            )
    return tuple(sorted(seeds, key=lambda item: item.case_id))


CLEAN_CASE_SEEDS = _seed_catalog()


def _format(value: str, *, project: str) -> str:
    return value.format(project=project.title(), project_slug=project)


def _query_id(seed: CleanCaseSeed, query_key: str) -> str:
    return f"query.{seed.case_id.removeprefix('clean.')}.{query_key}"


def _materialize_query(
    seed: CleanCaseSeed,
    template: BlueprintQuery,
) -> ResourceQuery:
    predicates: list[ResourcePredicate] = []
    if template.project_scoped:
        predicates.append(ResourcePredicate(field=PredicateField.PROJECT, value=seed.project))
    if template.subject_template is not None:
        predicates.append(
            ResourcePredicate(
                field=PredicateField.SUBJECT,
                operator=(
                    PredicateOperator.CONTAINS_TOKEN
                    if template.subject_contains
                    else PredicateOperator.EQUALS
                ),
                value=_format(template.subject_template, project=seed.project),
            )
        )
    if template.owner_is_actor:
        predicates.append(ResourcePredicate(field=PredicateField.OWNER, value=seed.actor_id))
    if template.classification is not None:
        predicates.append(
            ResourcePredicate(
                field=PredicateField.CLASSIFICATION,
                value=template.classification.value,
            )
        )
    if template.lifecycle is not None:
        predicates.append(
            ResourcePredicate(
                field=PredicateField.LIFECYCLE,
                value=template.lifecycle.value,
            )
        )
    relations = ()
    if template.related_query_key is not None and template.relation is not None:
        relations = (
            ResourceRelationConstraint(
                relation=template.relation,
                direction=template.relation_direction,
                related_query_id=_query_id(seed, template.related_query_key),
            ),
        )
    return ResourceQuery(
        query_id=_query_id(seed, template.query_key),
        binding_name=f"binding.{seed.case_id.removeprefix('clean.')}.{template.query_key}",
        resource_kind=template.resource_kind,
        predicates=tuple(predicates),
        actor_access=(AccessRight.READ,),
        relation_constraints=relations,
        cardinality=template.cardinality,
        tie_policy=template.tie_policy,
    )


def _select_candidate(
    state: OfficeWorldState,
    seed: CleanCaseSeed,
    template: BlueprintQuery,
    candidates: tuple[ResourceRef, ...],
) -> ResourceRef:
    expected_subject = (
        _format(template.selection_subject_template, project=seed.project)
        if template.selection_subject_template
        else None
    )
    matches: list[ResourceRef] = []
    for ref in candidates:
        if ref.kind.value != "drive_file":
            continue
        resource = next(
            item for item in state.domain_graph.drive.files if item.file_id == ref.resource_id
        )
        if expected_subject is not None and resource.name != expected_subject:
            continue
        if (
            template.selection_lifecycle is not None
            and resource.lifecycle_state is not template.selection_lifecycle
        ):
            continue
        matches.append(ref)
    if len(matches) != 1:
        raise ValueError(f"case {seed.case_id} clarification selection was not unique")
    return matches[0]


def _resolve_bindings(
    state: OfficeWorldState,
    actor: ActorContext,
    seed: CleanCaseSeed,
    blueprint: TaskBlueprint,
    queries: tuple[ResourceQuery, ...],
) -> tuple[tuple[ResolvedBinding, ...], dict[str, tuple[ResourceRef, ...]], dict[str, ResourceRef]]:
    query_by_key = {
        template.query_key: query
        for template, query in zip(blueprint.queries, queries, strict=True)
    }
    bindings: dict[str, ResolvedBinding] = {}
    candidates: dict[str, tuple[ResourceRef, ...]] = {}
    selections: dict[str, ResourceRef] = {}
    pending = {item.query_key: item for item in blueprint.queries}
    while pending:
        progressed = False
        for key in sorted(pending):
            template = pending[key]
            if template.related_query_key and template.related_query_key not in bindings:
                continue
            outcome = resolve_resource_query(
                state,
                actor,
                query_by_key[key],
                related_bindings={item.query_id: item for item in bindings.values()},
            )
            if outcome.clarification is not None:
                selected = _select_candidate(
                    state, seed, template, outcome.clarification.candidate_refs
                )
                candidates[key] = outcome.clarification.candidate_refs
                selections[key] = selected
                outcome = resolve_clarification_selection(
                    state,
                    actor,
                    query_by_key[key],
                    outcome.clarification,
                    selected,
                )
            if outcome.binding is None:
                code = outcome.failure.code if outcome.failure else "unresolved"
                raise ValueError(f"case {seed.case_id} query {key} failed: {code}")
            bindings[key] = outcome.binding
            del pending[key]
            progressed = True
        if not progressed:
            raise ValueError(f"case {seed.case_id} query relations cannot be resolved")
    return (
        tuple(sorted(bindings.values(), key=lambda item: item.query_id)),
        candidates,
        selections,
    )


def _external_event_recipients(
    state: OfficeWorldState,
    event_binding: ResolvedBinding,
) -> tuple[str, ...]:
    event_id = event_binding.resource_refs[0].resource_id
    event = next(item for item in state.domain_graph.calendar.events if item.event_id == event_id)
    external_ids = {
        item.principal_id
        for item in state.domain_graph.directory.principals
        if item.kind is PrincipalKind.EXTERNAL
    }
    recipients = tuple(sorted(set(event.attendee_ids) & external_ids))
    if not recipients:
        raise ValueError("authorization blueprint requires an external attendee")
    return recipients


def _build_interaction_contract(
    state: OfficeWorldState,
    seed: CleanCaseSeed,
    blueprint: TaskBlueprint,
    bindings_by_key: dict[str, ResolvedBinding],
    candidates: dict[str, tuple[ResourceRef, ...]],
    selections: dict[str, ResourceRef],
) -> tuple[InteractionContract, tuple[TaskFact, ...]]:
    requests: list[ClarificationRequest] = []
    rules: list[UserResponseRule] = []
    facts: list[TaskFact] = []
    clarification_keys = {
        goal.clarification_query_key
        for goal in blueprint.goals
        if goal.clarification_query_key is not None
    }
    for key in sorted(clarification_keys):
        request_id = f"request.{seed.case_id.removeprefix('clean.')}.{key}"
        fact_id = f"fact.{seed.case_id.removeprefix('clean.')}.{key}.confirmed"
        facts.append(
            TaskFact(
                fact_id=fact_id,
                description=f"The required {key} clarification was authenticated.",
                query_ids=(bindings_by_key[key].query_id,),
            )
        )
        if key in candidates:
            request = ClarificationRequest(
                request_id=request_id,
                question_kind=QuestionKind.DISAMBIGUATION,
                candidate_refs=candidates[key],
                allowed_responder_ids=(seed.issuer_principal_id,),
                requested_at=state.logical_clock.now,
            )
            match = ResponseMatch(
                request_id=request_id,
                question_kind=QuestionKind.DISAMBIGUATION,
                candidate_refs=candidates[key],
            )
            rule = UserResponseRule(
                rule_id=f"rule.{seed.case_id.removeprefix('clean.')}.{key}",
                match=match,
                authenticated_responder_id=seed.issuer_principal_id,
                response_text="Use the current approved candidate.",
                selected_refs=(selections[key],),
            )
        elif "authorization" in blueprint.structural_tags:
            meeting_pack = bindings_by_key["meeting-pack"].resource_refs
            recipients = _external_event_recipients(state, bindings_by_key[key])
            request = ClarificationRequest(
                request_id=request_id,
                question_kind=QuestionKind.AUTHORIZATION,
                candidate_refs=meeting_pack,
                requested_action_scope=MAIL_SEND,
                requested_recipient_ids=recipients,
                allowed_responder_ids=(seed.issuer_principal_id,),
                requested_at=state.logical_clock.now,
            )
            match = ResponseMatch(
                request_id=request_id,
                question_kind=QuestionKind.AUTHORIZATION,
                candidate_refs=meeting_pack,
                requested_action_scope=MAIL_SEND,
                requested_recipient_ids=recipients,
            )
            rule = UserResponseRule(
                rule_id=f"rule.{seed.case_id.removeprefix('clean.')}.{key}",
                match=match,
                authenticated_responder_id=seed.issuer_principal_id,
                response_text="Notify the listed external participant for this follow-up only.",
                grant_effect=GrantTemplate(
                    action_scope=MAIL_SEND,
                    resource_refs=meeting_pack,
                    recipient_ids=recipients,
                    duration_ticks=5,
                ),
            )
        else:
            request = ClarificationRequest(
                request_id=request_id,
                question_kind=QuestionKind.MISSING_VALUE,
                missing_fact_ids=(fact_id,),
                allowed_responder_ids=(seed.issuer_principal_id,),
                requested_at=state.logical_clock.now,
            )
            match = ResponseMatch(
                request_id=request_id,
                question_kind=QuestionKind.MISSING_VALUE,
            )
            rule = UserResponseRule(
                rule_id=f"rule.{seed.case_id.removeprefix('clean.')}.{key}",
                match=match,
                authenticated_responder_id=seed.issuer_principal_id,
                response_text="Use the uniquely visible current business record.",
            )
        requests.append(request)
        rules.append(rule)
    return InteractionContract(requests=tuple(requests), response_rules=tuple(rules)), tuple(facts)


def _build_task(
    state: OfficeWorldState,
    seed: CleanCaseSeed,
    blueprint: TaskBlueprint,
    queries: tuple[ResourceQuery, ...],
    interaction: InteractionContract,
    response_facts: tuple[TaskFact, ...],
) -> TaskContract:
    query_by_key = {
        template.query_key: query
        for template, query in zip(blueprint.queries, queries, strict=True)
    }
    suffix = seed.case_id.removeprefix("clean.")
    query_facts = tuple(
        TaskFact(
            fact_id=f"fact.{suffix}.{key}.located",
            description=f"The {key} business resource was located and frozen.",
            query_ids=(query.query_id,),
        )
        for key, query in sorted(query_by_key.items())
    )
    success_facts = tuple(
        TaskFact(
            fact_id=f"fact.{suffix}.{goal.goal_key}.complete",
            description=f"Business goal completed: {goal.description}",
        )
        for goal in blueprint.goals
    )
    question_by_key = {
        request.request_id.rsplit(".", 1)[-1]: request.question_kind
        for request in interaction.requests
    }
    goals = tuple(
        TaskGoal(
            goal_id=f"goal.{suffix}.{goal.goal_key}",
            description=goal.description,
            depends_on=tuple(f"goal.{suffix}.{key}" for key in goal.depends_on),
            preconditions=tuple(f"fact.{suffix}.{key}.located" for key in goal.query_keys),
            success_assertions=(f"fact.{suffix}.{goal.goal_key}.complete",),
            allowed_action_scopes=goal.action_scopes,
            branch_condition=(
                BranchCondition(
                    fact_id=f"fact.{suffix}.{goal.branch_query_key}.located",
                    operator=goal.branch_operator,
                )
                if goal.branch_query_key is not None and goal.branch_operator is not None
                else None
            ),
            clarification_gate=(
                ClarificationGate(
                    question_kind=question_by_key[goal.clarification_query_key],
                    fact_ids=(f"fact.{suffix}.{goal.clarification_query_key}.confirmed",),
                )
                if goal.clarification_query_key is not None
                else None
            ),
        )
        for goal in blueprint.goals
    )
    delegations: list[TaskDelegation] = []
    for index, scope in enumerate(blueprint.allowed_side_effects):
        if "authorization" in blueprint.structural_tags and scope == MAIL_SEND:
            continue
        scoped_queries = tuple(
            query.query_id
            for query in queries
            if query.resource_kind in set(scope.resource_kinds)
            and scope.action not in {ActionKind.CREATE, ActionKind.SEND}
        )
        delegations.append(
            TaskDelegation(
                delegation_id=f"delegation.{suffix}.{index}",
                issuer_id=seed.issuer_principal_id,
                actor_id=seed.actor_id,
                action_scope=scope,
                resource_query_ids=scoped_queries,
                valid_from=state.logical_clock.now,
                expires_at=state.logical_clock.now + 100,
                source_evidence_ref=f"evidence.task.{suffix}.{index}",
            )
        )
    return TaskContract(
        task_id=f"task.{suffix}",
        task_version="2.0",
        issuer_principal_id=seed.issuer_principal_id,
        issuer_authentication=IssuerAuthentication.AUTHENTICATED,
        instruction=_format(blueprint.instruction_template, project=seed.project),
        actor_id=seed.actor_id,
        preconditions=tuple(sorted((*query_facts, *success_facts), key=lambda item: item.fact_id)),
        goal_graph=TaskGoalGraph(goals=goals),
        resource_queries=queries,
        delegated_actions=tuple(delegations),
        allowed_side_effects=blueprint.allowed_side_effects,
        required_response_facts=response_facts,
        user_response_script=interaction,
    )


def materialize_clean_case(
    seed: CleanCaseSeed,
    *,
    world: CanonicalOfficeWorld | None = None,
) -> CleanCaseMaterialization:
    world = world or load_canonical_world()
    blueprint = TASK_BLUEPRINT_BY_ID[seed.blueprint_id]
    capabilities = tuple(
        sorted(
            {
                "calendar.read",
                "calendar.write",
                "drive.delete",
                "drive.manage_permissions",
                "drive.read",
                "drive.share",
                "drive.write",
                "mail.read",
                "mail.send",
                "workspace.read",
                "workspace.write",
            }
        )
    )
    actor = world.state.domain_graph.directory.derive_actor_context(
        actor_id=seed.actor_id,
        authenticated_principal_id=seed.actor_id,
        session_capabilities=capabilities,
        logical_time=world.state.logical_clock.now,
    )
    queries = tuple(_materialize_query(seed, item) for item in blueprint.queries)
    bindings, candidates, selections = _resolve_bindings(
        world.state, actor, seed, blueprint, queries
    )
    binding_by_query_id = {binding.query_id: binding for binding in bindings}
    bindings_by_key = {
        template.query_key: binding_by_query_id[_query_id(seed, template.query_key)]
        for template in blueprint.queries
    }
    interaction, response_facts = _build_interaction_contract(
        world.state,
        seed,
        blueprint,
        bindings_by_key,
        candidates,
        selections,
    )
    task = _build_task(world.state, seed, blueprint, queries, interaction, response_facts)
    payload = {
        "case_id": seed.case_id,
        "case_version": CASE_MATERIALIZATION_VERSION,
        "catalog_version": OFFICE_V2_TASK_CATALOG_VERSION,
        "catalog_digest": TASK_BLUEPRINT_CATALOG_DIGEST,
        "blueprint_id": blueprint.blueprint_id,
        "blueprint_digest": blueprint.canonical_digest(),
        "base_world_version": world.world_version,
        "base_world_digest": world.world_digest,
        "actor": actor,
        "task": task,
        "resolved_bindings": bindings,
    }
    draft = CleanCaseMaterialization.model_construct(**payload, case_digest="sha256:" + "0" * 64)
    return CleanCaseMaterialization(**payload, case_digest=sha256_digest(draft.digest_payload()))


def build_clean_case_catalog(*, world: CanonicalOfficeWorld | None = None) -> CleanCaseCatalog:
    world = world or load_canonical_world()
    cases = tuple(materialize_clean_case(seed, world=world) for seed in CLEAN_CASE_SEEDS)
    payload = {
        "blueprint_catalog_digest": TASK_BLUEPRINT_CATALOG_DIGEST,
        "world_digest": world.world_digest,
        "cases": cases,
    }
    draft = CleanCaseCatalog.model_construct(**payload, catalog_digest="sha256:" + "0" * 64)
    return CleanCaseCatalog(**payload, catalog_digest=sha256_digest(draft.digest_payload()))


CLEAN_CASE_CATALOG = build_clean_case_catalog()
CLEAN_CASES = CLEAN_CASE_CATALOG.cases
CLEAN_CASE_BY_ID = {item.case_id: item for item in CLEAN_CASES}

if len(CLEAN_CASE_SEEDS) != 24 or len(CLEAN_CASES) != 24:  # pragma: no cover
    raise RuntimeError("Office V2 clean case catalog must contain exactly 24 entries")
if len(TASK_BLUEPRINTS) != 10:  # pragma: no cover
    raise RuntimeError("Office V2 clean cases require exactly 10 blueprints")


__all__ = [
    "CASE_MATERIALIZATION_VERSION",
    "CLEAN_CASE_BY_ID",
    "CLEAN_CASE_CATALOG",
    "CLEAN_CASE_SEEDS",
    "CLEAN_CASES",
    "CleanCaseCatalog",
    "CleanCaseMaterialization",
    "CleanCaseSeed",
    "build_clean_case_catalog",
    "materialize_clean_case",
]
