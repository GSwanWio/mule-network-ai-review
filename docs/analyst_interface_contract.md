# Analyst Interface Contract

## Purpose

The analyst interface is the controlled operational surface for reviewing protected mule
networks. It uses the validated workbook, canonical breadth-first review engine, real OpenAI
node assessments, and the protected canonical ledger already established by the application.

## Review sequence

1. The analyst uploads the protected Databricks workbook.
2. The application validates the complete workbook contract before displaying any network.
3. The analyst selects a network and starts a bounded AI network-review run.
4. AI assesses reached nodes breadth-first, expands suspicious branches, and prunes legitimate
   branches without waiting for analyst input between nodes.
5. The run stops at convergence or at its explicit API-call safety limit. A limited run can be
   continued without repeating canonical decisions.
6. After convergence, the application displays the resulting graph and every AI decision.
7. The analyst selects a reached node directly from the graph or review-progress panel.
8. The analyst reviews each reason, evidence set, counter-evidence set, and data limitation.
9. The analyst confirms or overrides every AI decision and supplies an audit rationale.
10. An override that reopens a branch returns the network to AI traversal for only the newly
   reachable nodes. The updated graph is presented again after that continuation stops.

## Mandatory controls

- The interface never produces simulated, default, or hard-coded AI decisions.
- Each Run action has a visible hard limit of no more than 50 OpenAI requests.
- Independent nodes at the same breadth-first layer may use up to three concurrent requests.
- Every successful AI response is saved atomically before a deeper layer begins.
- Incomplete structured responses fail safely without discarding valid decisions completed in the
  same run.
- AI decisions provisionally control traversal during a run: suspicious decisions expand and
  legitimate decisions prune.
- Analyst controls and the resulting graph remain unavailable until AI traversal stops.
- The graph is the primary node selector; the interface does not use a separate AI-decision
  dropdown.
- A scrollable reached-node panel shows deterministic context, pending analyst decisions, and
  completed analyst decisions. Completed review items display a green check.
- Recording a decision keeps the selected network stable and advances to the next pending node
  when one remains.
- The analyst must provide an approved opaque reference, rationale, and evidence attestation.
- A protected subject has one canonical decision per workbook snapshot, including when it is
  shared across networks.
- Analyst-confirmed suspicious decisions retain the node and its downstream branch.
- Analyst-confirmed legitimate decisions prune the node and block downstream nodes reached only
  through it.
- Alternate suspicious paths remain available when a node has more than one predecessor.
- Ledger writes are atomic and fail if the ledger belongs to another workbook snapshot or review
  policy version.

## Data protection

Only protected tokens from the validated workbook may be displayed or sent to OpenAI. The
workbook and canonical ledger remain sensitive and must stay in an approved environment. Raw
customer identifiers, Emirates ID numbers, account numbers, counterparty names, and the
re-identification mapping must never enter the repository, interface, ledger, or AI request.

## Graph presentation

The graph is generated locally without external network or visualization services. It supports
click selection, hover detail, pan, and zoom. Suspicious paths are red, legitimate pruned paths are
green, identity links are teal, and unresolved states remain neutral or amber. Only the seed and
selected node carry persistent labels so dense networks remain readable. The resulting graph
contains reached nodes and legitimate boundary nodes only. Downstream nodes blocked by a legitimate
decision remain counted for audit but are intentionally hidden from the rendered result. Large
reached networks use an explicitly labelled review-focused view.
