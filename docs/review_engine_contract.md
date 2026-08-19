# Canonical Breadth-First Review Engine

## Purpose

The review engine consumes the validated protected workbook and controls which nodes are submitted
to the real AI review contract. It does not rebuild the Databricks network and does not receive or
store original customer, Emirates ID, account, or counterparty identifiers.

## Decision separation

An AI response is a provisional traversal decision. It controls whether deterministic discovery
continues beyond that node during the bounded AI run, but it is not a final analyst-approved
classification.

The decision becomes final only after an analyst either:

- confirms the AI decision; or
- overrides it with a different decision and rationale.

Changing a confirmed decision requires a separate revision event. Every analyst event retains the
AI request fingerprint so stale evidence cannot be confirmed accidentally.

## Canonical consistency

Canonical decisions are keyed by:

- protected subject type;
- protected subject token;
- protected workbook export run ID; and
- AI branch-gate policy version.

The same protected subject therefore receives one AI decision and one effective analyst-confirmed
outcome everywhere it appears in the same data snapshot. A later workbook export creates a new
evidence snapshot and cannot silently reuse the earlier decision.

## Traversal rules

The engine directs every relationship using the deterministic discovery layers already present in
the protected workbook. A customer at layer `L` opens an Emirates ID or counterparty at layer
`L + 1`; an Emirates ID or counterparty at layer `L` opens a customer at layer `L`. The engine does
not infer branch direction from an undirected shortest path.

- The confirmed seed always remains in the network.
- Emirates ID nodes expand deterministically.
- A customer is retained deterministically only when its Emirates ID is shared with at least one
  other customer in the same network.
- Customers reached through counterparties and all counterparties require AI review.
- AI `SUSPICIOUS_KEEP` provisionally expands the node during the autonomous run.
- AI `LEGITIMATE_PRUNE` keeps the reviewed node visible and provisionally blocks its downstream
  branch.
- Analyst confirmation preserves that outcome.
- An analyst override recalculates traversal. An override from legitimate to suspicious opens
  only the newly reachable branch for a continuation AI run.
- A downstream node remains reachable when another expanding path reaches it.

All unresolved nodes at the shallowest reached graph depth are processed before any deeper node.
Nodes capable of opening additional branches are ordered first only within the same breadth-first
depth. Each AI request receives one subject and its relevant metrics rather than the full graph.

The branch-gate policy is binary. `SUSPICIOUS_KEEP` requires affirmative material risk evidence
beyond mere membership in a mule-seeded network. `LEGITIMATE_PRUNE` stops the branch when material
suspicious evidence is absent or outweighed. Sparse data lower confidence but do not automatically
open a branch.

## Counterparty domain policy

Counterparty rail is resolved before an AI request is created. The relationship that introduced
the counterparty is authoritative: a local payment selects local metrics and an international
payment selects international metrics. A SWIFT-account key resolves an otherwise beneficiary-only
counterparty as international. An IBAN beneficiary without payment evidence remains `UNRESOLVED`
and receives no rail-specific transaction metrics. Conflicting local and international source
relationships fail closed.

The request contains a versioned domain context and exactly one of the local or international
metric families. It never supplies both. For an unresolved rail, it supplies neither and instructs
the model not to infer a rail. The response is rejected if it introduces a rail that conflicts with
the authoritative context. These stable invariants are deterministic policy controls rather than
semantic retrieval because they must not vary with document similarity or model interpretation.

## Live-call controls

Each API request contains exactly one protected customer or counterparty and its relevant metrics.
The wave runner:

- requires an explicit live-call confirmation flag;
- defaults to one call;
- enforces a maximum of ten calls per wave;
- disables OpenAI response storage;
- uses structured output validation; and
- can persist the protected canonical ledger after every successful response.

The analyst interface processes requests until convergence or its explicit per-run limit of 50
calls. Independent nodes in the same breadth-first depth may use up to three concurrent calls;
deeper nodes cannot become eligible until the current wave is recorded. Each valid response is
saved before the next wave, so a failed or limited run can continue without repeating completed
decisions. The structured-output allowance is intentionally large enough for the decision
contract, and incomplete responses fail safely without publishing a partial decision.

The canonical ledger remains sensitive protected data and must be stored under an ignored local
data path or an approved production data store.
