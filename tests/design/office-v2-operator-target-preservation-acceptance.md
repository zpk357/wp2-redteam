# Operator Target-Preservation Acceptance Design

This document defines the Stage 4 acceptance boundary for the formal twelve
family operator directory. It is an acceptance design, not permission to add
new risk objectives or change the frozen seed directory.

## Directory Contract

Every formal family must expose at least four concrete variants. Every variant
must carry:

- a unique `variant_id` and a replayable digest;
- a concrete wording instruction;
- non-empty risk applicability;
- explicit constraints preserving the selected seed's attack target and all
  frozen facts;
- explicit prohibitions against changing the requested action or inventing
  authorization.

The formal catalog digest is part of the mutation identity lock and is included
in the operator sampling receipt.

## Host-Frozen Dimensions

Before the LLM request, the host freezes and records:

```text
attack_target
risk_type
requested_action
actor
task
resource bindings
authorization branch
delegation facts
scenario/world identity
```

The operator may change only expression dimensions such as tone, urgency,
role framing, justification, directness, ordering, salience, or social
framing. It must not add a second action, move the request to another
resource, change the actor/task, or turn an untrusted claim into an actual
permission.

## Required Tests

Add or extend unit tests to cover:

1. all 12 families are present and each has at least four concrete variants;
2. every variant has non-empty applicability, constraints, forbidden
   transformations, and a digest that replays identically;
3. malformed variants missing preservation or prohibition constraints are
   rejected at model construction;
4. sampling the same campaign, generation, state, parent and seed produces the
   same family, variant and selection receipt;
5. changing only the campaign seed changes the sampling receipt without
   changing the frozen target;
6. a generated candidate that changes wording but preserves target/action/
   resource/authorization passes the host validation layers;
7. a candidate that changes the requested action is rejected;
8. a candidate that changes the resource or actor is rejected;
9. a candidate that adds a second step or permission grant is rejected;
10. a candidate that turns an authority claim into a real authorization is
    rejected;
11. a candidate that mentions the target in text but has no valid structured
    candidate slot is rejected;
12. rejected or paused candidates never reach Episode execution or seed
    promotion.

## Strategy and Runtime Parity

Both `coverage_guided` and `random_independent` must use the same target
preservation checks. The random strategy differs only in feedback and
promotion: it may not consume cross-generation feedback or add a derived seed.
Both strategies must preserve the same operator receipt and host-frozen
dimensions in their Episode evidence.

Both LangGraph and DeepSeek Harness runtimes must receive only the final
validated text. Operator family, variant and constraint metadata remain host
audit data and are not presented as a new attack objective to the tested Agent.

## Completion Gate

Stage 4 is complete only when the catalog, selection receipt, provider prompt,
candidate parser, validation result and materialized Episode all agree on the
same frozen target and preserved dimensions. Textual similarity or keyword
presence is insufficient evidence of target preservation.
