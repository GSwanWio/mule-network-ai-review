# Canonical Breadth-First Review Engine

## Purpose

The review engine consumes the validated protected workbook and controls which nodes are submitted
to the real AI review contract. It does not rebuild the Databricks network and does not receive or
store original customer, Emirates ID, account, or counterparty identifiers.

## Decision separation

An AI response is a proposal. It cannot change network traversal by itself.

A proposal becomes effective only after an analyst either:

- confirms the AI decision; or
- overrides it with a different decision and rationale.

Changing a confirmed decision requires a separate revision event. Every analyst event retains the
AI request fingerprint so stale evidence cannot be confirmed accidentally.

## Canonical consistency

Canonical decisions are keyed by:

- protected subject type;
- protected subject token; and
- protected workbook export run ID.

The same protected subject therefore receives one AI proposal and one effective analyst-confirmed
decision everywhere it appears in the same data snapshot. A later workbook export creates a new
evidence snapshot and cannot silently reuse the earlier decision.

## Traversal rules

The engine calculates graph distance from the confirmed seed and evaluates reachable nodes in
breadth-first order.

- The confirmed seed always remains in the network.
- Emirates ID nodes expand deterministically.
- A customer is retained deterministically only when its Emirates ID is shared with at least one
  other customer in the same network.
- Customers reached through counterparties and all counterparties require AI review.
- `SUSPICIOUS_KEEP` expands the node only after analyst confirmation.
- `LEGITIMATE_PRUNE` keeps the reviewed node visible but blocks its downstream branch only after
  analyst confirmation.
- A downstream node remains reachable when another confirmed path reaches it.

Nodes capable of opening additional branches are reviewed before terminal leaves. Within that
priority group, nodes are processed breadth-first. This reduces unnecessary AI calls without
sending the full graph to a single request.

## Live-call controls

Each API request contains exactly one protected customer or counterparty and its relevant metrics.
The wave runner:

- requires an explicit live-call confirmation flag;
- defaults to one call;
- enforces a maximum of ten calls per wave;
- disables OpenAI response storage;
- uses structured output validation; and
- can persist the protected canonical ledger after every successful response.

The canonical ledger remains sensitive protected data and must be stored under an ignored local
data path or an approved production data store.
