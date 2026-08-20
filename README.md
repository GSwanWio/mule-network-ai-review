# Mule Network AI Review

## Purpose

Mule Network AI Review is an analyst-facing tool for reviewing and pruning customer, Emirates ID, and counterparty networks discovered deterministically in Databricks.

Databricks remains responsible for constructing the network and calculating customer and counterparty metrics. This application is responsible for controlled AI assessment, branch pruning, analyst validation, and structured decision output.

## Current Data Source

The initial production-validation phase uses the protected Excel workbook exported from Databricks.

The workbook contains protected identifiers only. Raw customer identifiers, account numbers, Emirates ID numbers, counterparty names, and re-identification mappings must never enter this repository or be sent to the AI API.

A Databricks API adapter will replace manual workbook upload later without changing the internal application contract.

## Review Workflow

1. The analyst selects a protected Databricks workbook.
2. The application validates every required sheet, column, identifier, relationship, and metric.
3. The analyst selects a network and starts AI review manually.
4. At each breadth, the application evaluates available linked customers before evaluating the
   counterparty connections through which they were discovered.
5. The AI assigns one final decision to each evaluated subject:
	- `SUSPICIOUS_KEEP`
	- `LEGITIMATE_PRUNE`
6. A legitimate decision prunes all descendants reached through that subject.
7. A suspicious decision retains the subject and makes its next eligible frontier available for evaluation.
8. The analyst reviews every AI decision with its supporting evidence.
9. An analyst override applies consistently to every occurrence of the same subject.
10. Overriding a pruned subject as suspicious reopens its eligible downstream branch.
11. Confirmed decisions are exported in a structured protected format for controlled re-identification in Databricks.

## Traversal Rules

- Traversal is breadth-first before progressing deeper into retained branches.
- A customer is assessed independently from its parent counterparty using its full protected
  customer metrics and selected comparisons with the confirmed seed mule.
- A counterparty is assessed as an external payment connection using one authoritative rail metric
  family and the protected outcomes of linked-customer assessments. It is never assessed as if it
  were a Wio customer.
- Only frontier subjects required for the next decision are sent to the AI.
- The full network is not sent to the AI in one request.
- Deterministic Databricks guardrail stops remain visible as evidence.
- The application does not rediscover or invent network relationships.
- A subject receives one canonical decision for the same metric snapshot and policy version.
- Repeated appearances of the same subject reuse that canonical decision.
- Analyst overrides update the canonical decision everywhere the subject appears.
- A subject is reassessed only when its metrics, policy version, or approved model configuration materially changes.

## AI Execution Requirements

- All AI assessments use the real configured AI API.
- Hard-coded, simulated, mocked, fallback, or fabricated AI responses are prohibited.
- Missing credentials, invalid responses, schema failures, or exhausted limits stop the run visibly.
- API responses must satisfy a strict structured-output schema before being accepted.
- Every run has explicit limits for API calls, subjects, retries, response tokens, and elapsed time.
- Acceptance testing uses bounded real API calls.
- Deterministic code may be tested independently, but no test may pretend that a fabricated response came from the AI.

## Analyst Requirements

- AI decisions are recommendations until individually confirmed by an analyst.
- Every evaluated node must expose its decision, rationale, strongest evidence, relevant metrics, and pruning impact.
- The analyst must confirm or override every AI-assessed node before saving the reviewed network.
- Network-level summaries support orientation but never replace node-level review.
- Saved outputs preserve both the original AI decision and the final analyst decision.

## Security Requirements

- Protected identifiers are treated as sensitive data.
- Input workbooks and generated decision files remain outside Git.
- Secrets are supplied through environment configuration and never committed.
- Logs must not contain workbook rows, protected identifiers, metrics payloads, prompts, or AI responses.
- The application must fail closed when input protection or schema validity cannot be established.
- Re-identification is performed only inside the approved Databricks environment.

## Audit Requirements

Each accepted AI decision records:

- Discovery run identifier
- Network identifier
- Protected subject identifier
- Subject type
- Metric snapshot fingerprint
- Policy version
- Model identifier
- Prompt version
- AI decision
- AI rationale
- Supporting evidence
- Pruning impact
- Analyst decision
- Analyst override reason
- Decision timestamps

## Interface Reuse

The previous project may be used only as a visual reference.

Interface components may be selectively ported after review, but previous backend code, demo data, synthetic responses, hard-coded decisions, mock services, and daily orchestration must not be copied into this repository.

## Initial Delivery Scope

The first usable version will provide:

- Protected workbook ingestion
- Strict workbook validation
- Network and metric normalization
- Interactive network visualization
- Breadth-first AI pruning
- Canonical shared-node decisions
- Mandatory node-by-node analyst review
- Analyst override and branch reopening
- Structured protected decision export
- Complete run and decision audit records

Databricks API integration and persistent network-bank storage will follow after the workbook-driven live-data validation is complete.
