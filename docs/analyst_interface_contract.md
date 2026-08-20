# Analyst Interface Contract

## Purpose

The analyst interface is the controlled operational surface for reviewing protected mule
networks. It uses the validated workbook, canonical breadth-first review engine, real OpenAI
node assessments, and the protected canonical ledger already established by the application.

## Review sequence

1. The analyst chooses the approved network-review file.
2. The application validates the complete workbook contract before displaying any network.
3. The analyst uses an explicitly labelled network selector and starts the assessment.
4. At each breadth, AI assesses available linked customers before assessing the counterparty
   connections through which they were found. It follows connections needing investigation and
   stops connections that do not, without waiting for analyst input between records.
5. The run stops at convergence or at its explicit API-call safety limit. A limited run can be
   continued without repeating canonical decisions.
6. After convergence, the application displays the resulting graph and every AI decision.
7. The analyst selects a reached node directly from the graph or review-progress panel.
8. The interface displays a compact record overview using the selected customer profile or the
   authoritative payment-rail-specific counterparty metrics. Every non-seed customer overview,
   including a confirmed mule found through a shared Emirates ID, shows selected comparisons with
   the confirmed seed mule.
9. The analyst reviews why each recommendation was made, what supports it, what lowers concern,
   and what information limitations remain.
10. The analyst chooses either `Needs further investigation` or
   `No further investigation needed` and supplies an audit rationale.
11. An override that reopens a branch returns the network to AI traversal for only the newly
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
- A scrollable checklist uses numbered, analyst-friendly record labels rather than exposing raw
  protected tokens as the primary label. Completed review items display a green check.
- The confirmed seed and every customer reached through its shared Emirates ID are presented as
  confirmed mules. They remain in the network automatically and never appear as neutral identity
  records requiring an AI or analyst decision.
- Selecting a node displays a protected record overview before the recommendation. Customer
  overviews include available profile, segment, account status, risk rating, tenure, and recent
  activity. Non-seed customer overviews also show selected values beside the confirmed seed values
  and clearly label the comparison as context rather than proof. Counterparty overviews include
  exactly one authoritative payment rail, connection context, recent activity, mule exposure,
  linked-customer counts, and recency.
- Every Emirates ID connection remains visible in the analyst graph and is shown in red. Selecting
  it displays every connected confirmed-mule customer's profile and activity summary in the same
  panel, including when the only connected customer is the seed. Each non-seed customer also shows
  its selected comparison with the confirmed seed mule. The Emirates ID itself is described as the
  connection, not as a customer record requiring a decision.
- A counterparty overview must never combine local and international metric families. The same
  authoritative rail policy used for the AI request controls the analyst view.
- Backend terms including traversal, convergence, deterministic expansion, graph depth, prune,
  and branch gate are not shown in the normal analyst journey.
- Internal decisions are translated consistently: `SUSPICIOUS_KEEP` is shown as
  `Needs further investigation`; `LEGITIMATE_PRUNE` is shown as
  `No further investigation needed`.
- Workbook validation, API configuration, ledger paths, snapshots, and raw network identifiers are
  hidden from the normal review journey. Technical error detail is available only in a collapsed
  support section.
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
The protected workbook does not contain customer or counterparty names. The interface therefore
uses protected record labels and never invents or infers a name. Any later internal name display
requires a separately authorised lookup that remains outside the AI request and repository.

## Graph presentation

The graph is generated locally without external network or visualization services. It supports
click selection, hover detail, pan, and zoom. Confirmed mules and records that appear suspicious
are red, records that appear legitimate are green, and records waiting for analyst review are
outlined in amber. Shared Emirates ID paths are red because they identify confirmed mule customers,
not neutral connections. Emirates IDs connected to only one customer remain visible so the analyst
can inspect the complete identity path. Only the seed and selected node carry persistent labels so
dense networks remain readable. The resulting graph contains reached nodes and legitimate boundary
nodes only.
Downstream nodes blocked by a legitimate decision remain counted for audit but are intentionally
hidden from the rendered result. Large reached networks use an explicitly labelled review-focused
view.
