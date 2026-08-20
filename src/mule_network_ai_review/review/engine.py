from __future__ import annotations

from collections import Counter

from mule_network_ai_review.ai import (
	AIReviewRecord,
	CounterpartyBranchContext,
	LinkedCustomerAssessment,
	NodeReviewRequest,
	OpenAIReviewClient,
	ReviewDecision,
	SubjectType,
	build_node_review_request,
)
from mule_network_ai_review.ai.payloads import COUNTERPARTY_BRANCH_GUIDANCE
from mule_network_ai_review.ingestion import WorkbookPackage
from mule_network_ai_review.review.graph import NetworkGraphIndex
from mule_network_ai_review.review.ledger import (
	CanonicalDecisionLedger,
	canonical_decision_key,
)
from mule_network_ai_review.review.models import (
	CanonicalDecisionEntry,
	CanonicalReviewState,
	GraphNodeType,
	NetworkReviewSnapshot,
	ReviewNodeState,
	ReviewNodeStatus,
)

MAX_AI_CALLS_PER_WAVE = 10


class ReviewEngineError(ValueError):
	pass


def select_default_review_network(package: WorkbookPackage) -> str:
	nodes = package.sheet("nodes")
	counterparty_networks = set(
		nodes.loc[nodes["node_type"].astype(str) == GraphNodeType.COUNTERPARTY.value][
			"network_id"
		]
		.astype(str)
		.tolist()
	)
	if not counterparty_networks:
		raise ReviewEngineError("The workbook contains no networks with counterparties to review.")
	summary = package.sheet("network_summary").copy()
	summary = summary.loc[summary["network_id"].astype(str).isin(counterparty_networks)]
	summary["discovered_nodes"] = summary["discovered_nodes"].astype(int)
	summary = summary.sort_values(
		by=["discovered_nodes", "network_id"],
		ascending=[True, True],
		kind="stable",
	)
	return str(summary.iloc[0]["network_id"])


class BreadthFirstReviewEngine:
	def __init__(
		self,
		package: WorkbookPackage,
		network_id: str,
		ledger: CanonicalDecisionLedger | None = None,
	):
		self.package = package
		self.graph = NetworkGraphIndex.from_package(package, network_id)
		self.ledger = ledger or CanonicalDecisionLedger(self.graph.data_snapshot_id)
		if self.ledger.data_snapshot_id != self.graph.data_snapshot_id:
			raise ReviewEngineError(
				"The canonical ledger belongs to a different workbook snapshot."
			)

	def snapshot(self) -> NetworkReviewSnapshot:
		states_by_id = self._node_states()
		ordered_states = sorted(
			states_by_id.values(),
			key=lambda state: (
				state.graph_depth,
				state.node_type.value,
				state.node_token,
			),
		)
		candidate_states = self._next_candidate_states(ordered_states, MAX_AI_CALLS_PER_WAVE)
		active_relationship_ids = sorted(
			relationship.relationship_id
			for relationship in self.graph.relationships.values()
			if states_by_id[relationship.source_node_id].reached
			and states_by_id[relationship.target_node_id].reached
		)
		blocked_node_ids = {
			state.node_id
			for state in ordered_states
			if state.status == ReviewNodeStatus.BLOCKED_BY_PRUNE
		}
		pruned_relationship_ids = sorted(
			relationship.relationship_id
			for relationship in self.graph.relationships.values()
			if relationship.source_node_id in blocked_node_ids
			or relationship.target_node_id in blocked_node_ids
		)
		pending_relationship_ids = sorted(
			set(self.graph.relationships)
			- set(active_relationship_ids)
			- set(pruned_relationship_ids)
		)
		status_counts = Counter(state.status for state in ordered_states)
		unresolved_reached = any(
			state.reached
			and state.status == ReviewNodeStatus.AWAITING_AI
			for state in ordered_states
		)
		return NetworkReviewSnapshot(
			data_snapshot_id=self.graph.data_snapshot_id,
			network_id=self.graph.network_id,
			seed_node_id=self.graph.seed_node_id,
			nodes=ordered_states,
			active_relationship_ids=active_relationship_ids,
			pending_relationship_ids=pending_relationship_ids,
			pruned_relationship_ids=pruned_relationship_ids,
			next_ai_subject_tokens=[state.node_token for state in candidate_states],
			seed_keep_count=status_counts[ReviewNodeStatus.SEED_KEEP],
			identity_keep_count=status_counts[ReviewNodeStatus.IDENTITY_KEEP],
			awaiting_ai_count=status_counts[ReviewNodeStatus.AWAITING_AI],
			awaiting_analyst_count=status_counts[ReviewNodeStatus.AWAITING_ANALYST],
			confirmed_keep_count=status_counts[ReviewNodeStatus.CONFIRMED_KEEP],
			confirmed_prune_count=status_counts[ReviewNodeStatus.CONFIRMED_PRUNE],
			pending_upstream_node_count=status_counts[ReviewNodeStatus.PENDING_UPSTREAM],
			blocked_node_count=status_counts[ReviewNodeStatus.BLOCKED_BY_PRUNE],
			reached_node_count=sum(state.reached for state in ordered_states),
			reviewable_node_count=sum(state.requires_ai_review for state in ordered_states),
			traversal_complete=not unresolved_reached,
		)

	def next_ai_requests(self, max_calls: int = 1) -> list[NodeReviewRequest]:
		self._validate_call_limit(max_calls)
		states_by_id = self._node_states()
		states = list(states_by_id.values())
		candidates = self._next_candidate_states(states, max_calls)
		return [
			build_node_review_request(
				self.package,
				self.graph.network_id,
				state.node_token,
				counterparty_branch_context=(
					self._counterparty_branch_context(state, states_by_id)
					if state.node_type == GraphNodeType.COUNTERPARTY
					else None
				),
			)
			for state in candidates
		]

	def run_ai_wave(
		self,
		client: OpenAIReviewClient,
		max_calls: int = 1,
	) -> list[AIReviewRecord]:
		requests = self.next_ai_requests(max_calls=max_calls)
		records = []
		for request in requests:
			record = client.review_node(request)
			self.ledger.record_ai_review(record)
			records.append(record)
		return records

	def confirm_analyst_decision(
		self,
		subject_type: SubjectType,
		subject_token: str,
		decision: ReviewDecision,
		analyst_reference: str,
		rationale: str,
		request_fingerprint: str,
	) -> NetworkReviewSnapshot:
		self.ledger.confirm(
			subject_type=subject_type,
			subject_token=subject_token,
			decision=decision,
			analyst_reference=analyst_reference,
			rationale=rationale,
			request_fingerprint=request_fingerprint,
		)
		return self.snapshot()

	def revise_analyst_decision(
		self,
		subject_type: SubjectType,
		subject_token: str,
		decision: ReviewDecision,
		analyst_reference: str,
		rationale: str,
		request_fingerprint: str,
	) -> NetworkReviewSnapshot:
		self.ledger.revise(
			subject_type=subject_type,
			subject_token=subject_token,
			decision=decision,
			analyst_reference=analyst_reference,
			rationale=rationale,
			request_fingerprint=request_fingerprint,
		)
		return self.snapshot()

	def _node_states(self) -> dict[str, ReviewNodeState]:
		states: dict[str, ReviewNodeState] = {}
		ordered_node_ids = sorted(
			self.graph.nodes,
			key=lambda node_id: (self.graph.distances[node_id], node_id),
		)
		for node_id in ordered_node_ids:
			node = self.graph.nodes[node_id]
			predecessor_ids = self.graph.predecessors[node_id]
			reached = node_id == self.graph.seed_node_id or any(
				states[predecessor_id].expands for predecessor_id in predecessor_ids
			)
			deterministic_identity_keep = (
				node.node_type == GraphNodeType.EID
				or node_id in self.graph.identity_customer_node_ids
			)
			entry = (
				self.ledger.get(node.subject_type, node.node_token)
				if node.subject_type is not None
				else None
			)
			status, expands = self._status(
				node_id=node_id,
				reached=reached,
				predecessor_ids=predecessor_ids,
				states=states,
				deterministic_identity_keep=deterministic_identity_keep,
				entry=entry,
			)
			requires_ai_review = (
				node.subject_type is not None
				and not node.is_seed_customer
				and not deterministic_identity_keep
			)
			requires_analyst_review = requires_ai_review and reached
			states[node_id] = ReviewNodeState(
				node_id=node.node_id,
				node_type=node.node_type,
				node_token=node.node_token,
				graph_depth=self.graph.distances[node_id],
				node_layer=node.node_layer,
				is_seed_customer=node.is_seed_customer,
				deterministic_identity_keep=deterministic_identity_keep,
				deterministic_expansion_decision=node.deterministic_expansion_decision,
				was_expanded=node.was_expanded,
				predecessor_node_ids=list(predecessor_ids),
				forward_child_node_ids=list(self.graph.forward_children[node_id]),
				forward_child_count=len(self.graph.forward_children[node_id]),
				status=status,
				reached=reached,
				expands=expands,
				requires_ai_review=requires_ai_review,
				requires_analyst_review=requires_analyst_review,
				analyst_review_complete=(
					entry is not None
					and entry.review_state == CanonicalReviewState.ANALYST_CONFIRMED
				),
				canonical_key=(
					canonical_decision_key(
						self.graph.data_snapshot_id,
						node.subject_type,
						node.node_token,
					)
					if requires_ai_review
					else None
				),
				ai_decision=(entry.ai_review.decision.decision if entry is not None else None),
				ai_confidence=(entry.ai_review.decision.confidence if entry is not None else None),
				effective_decision=(entry.effective_decision if entry is not None else None),
			)
		return states

	def _status(
		self,
		node_id: str,
		reached: bool,
		predecessor_ids: tuple[str, ...],
		states: dict[str, ReviewNodeState],
		deterministic_identity_keep: bool,
		entry: CanonicalDecisionEntry | None,
	) -> tuple[ReviewNodeStatus, bool]:
		if not reached:
			if any(
				states[predecessor_id].status
				in {
					ReviewNodeStatus.AWAITING_AI,
					ReviewNodeStatus.PENDING_UPSTREAM,
				}
				for predecessor_id in predecessor_ids
			):
				return ReviewNodeStatus.PENDING_UPSTREAM, False
			return ReviewNodeStatus.BLOCKED_BY_PRUNE, False
		node = self.graph.nodes[node_id]
		if node.is_seed_customer:
			return ReviewNodeStatus.SEED_KEEP, True
		if deterministic_identity_keep:
			return ReviewNodeStatus.IDENTITY_KEEP, True
		if entry is None:
			return ReviewNodeStatus.AWAITING_AI, False
		if entry.review_state == CanonicalReviewState.AI_PROPOSED:
			return (
				ReviewNodeStatus.AWAITING_ANALYST,
				entry.ai_review.decision.decision == ReviewDecision.SUSPICIOUS_KEEP,
			)
		if entry.effective_decision == ReviewDecision.SUSPICIOUS_KEEP:
			return ReviewNodeStatus.CONFIRMED_KEEP, True
		return ReviewNodeStatus.CONFIRMED_PRUNE, False

	def _next_candidate_states(
		self,
		states: list[ReviewNodeState],
		max_calls: int,
	) -> list[ReviewNodeState]:
		unresolved = [
			state
			for state in states
			if state.reached
			and state.status == ReviewNodeStatus.AWAITING_AI
		]
		if not unresolved:
			return []
		minimum_depth = min(state.graph_depth for state in unresolved)
		frontier = [
			state
			for state in unresolved
			if state.graph_depth == minimum_depth
			and state.status == ReviewNodeStatus.AWAITING_AI
		]
		states_by_id = {state.node_id: state for state in states}
		customer_first_candidates: dict[str, ReviewNodeState] = {}
		for state in frontier:
			if state.node_type == GraphNodeType.CUSTOMER:
				customer_first_candidates[state.node_id] = state
				continue
			if state.node_type != GraphNodeType.COUNTERPARTY:
				continue
			for child_node_id in state.forward_child_node_ids:
				child = states_by_id[child_node_id]
				if (
					child.node_type == GraphNodeType.CUSTOMER
					and child.requires_ai_review
					and child.ai_decision is None
				):
					customer_first_candidates[child.node_id] = child
		if customer_first_candidates:
			candidates = list(customer_first_candidates.values())
			candidates.sort(
				key=lambda state: (
					state.graph_depth,
					-state.forward_child_count,
					state.node_token,
				)
			)
			return candidates[:max_calls]

		candidates = frontier
		candidates.sort(
			key=lambda state: (
				-state.forward_child_count,
				state.node_type.value,
				state.node_token,
			)
		)
		return candidates[:max_calls]

	def _counterparty_branch_context(
		self,
		counterparty: ReviewNodeState,
		states_by_id: dict[str, ReviewNodeState],
	) -> CounterpartyBranchContext:
		if counterparty.node_type != GraphNodeType.COUNTERPARTY:
			raise ReviewEngineError(
				"Linked customer context can only be built for a counterparty."
			)
		linked_customers = [
			states_by_id[node_id]
			for node_id in counterparty.forward_child_node_ids
			if states_by_id[node_id].node_type == GraphNodeType.CUSTOMER
		]
		assessments: list[LinkedCustomerAssessment] = []
		for customer in sorted(linked_customers, key=lambda item: item.node_token):
			if customer.is_seed_customer or customer.deterministic_identity_keep:
				assessments.append(
					LinkedCustomerAssessment(
						customer_token=customer.node_token,
						confirmed_mule=True,
						ai_decision=None,
						ai_confidence=None,
					)
				)
				continue
			entry = self.ledger.get(SubjectType.CUSTOMER, customer.node_token)
			if entry is None:
				continue
			assessments.append(
				LinkedCustomerAssessment(
					customer_token=customer.node_token,
					confirmed_mule=False,
					ai_decision=entry.ai_review.decision.decision,
					ai_confidence=entry.ai_review.decision.confidence,
				)
			)
		confirmed_mule_count = sum(item.confirmed_mule for item in assessments)
		needs_investigation_count = sum(
			item.ai_decision == ReviewDecision.SUSPICIOUS_KEEP
			for item in assessments
		)
		no_further_investigation_count = sum(
			item.ai_decision == ReviewDecision.LEGITIMATE_PRUNE
			for item in assessments
		)
		return CounterpartyBranchContext(
			direct_linked_customer_count=len(linked_customers),
			assessed_linked_customer_count=len(assessments),
			confirmed_mule_customer_count=confirmed_mule_count,
			needs_investigation_customer_count=needs_investigation_count,
			no_further_investigation_customer_count=(
				no_further_investigation_count
			),
			assessment_complete=len(assessments) == len(linked_customers),
			linked_customer_assessments=assessments,
			guidance=list(COUNTERPARTY_BRANCH_GUIDANCE),
		)

	@staticmethod
	def _validate_call_limit(max_calls: int) -> None:
		if max_calls < 1 or max_calls > MAX_AI_CALLS_PER_WAVE:
			raise ReviewEngineError(
				f"max_calls must be between 1 and {MAX_AI_CALLS_PER_WAVE}."
			)
