import os

import pytest

from mule_network_ai_review.ai import CounterpartyRail, SubjectType
from mule_network_ai_review.ingestion import load_workbook_package
from mule_network_ai_review.review import (
	BreadthFirstReviewEngine,
	CanonicalDecisionLedger,
	GraphNodeType,
	ReviewNodeStatus,
	select_default_review_network,
)


def _live_package():
	workbook_path = os.getenv("MULE_NETWORK_WORKBOOK_PATH")
	if not workbook_path:
		pytest.skip("MULE_NETWORK_WORKBOOK_PATH is not configured.")
	return load_workbook_package(workbook_path)


@pytest.mark.live_data
def test_live_workbook_produces_protected_breadth_first_review_plan() -> None:
	package = _live_package()
	network_id = select_default_review_network(package)
	engine = BreadthFirstReviewEngine(package, network_id)
	snapshot = engine.snapshot()
	requests = engine.next_ai_requests(max_calls=1)

	assert snapshot.data_snapshot_id == package.validation_summary.export_run_id
	assert snapshot.network_id == network_id
	assert snapshot.seed_keep_count == 1
	assert snapshot.identity_keep_count >= 1
	assert snapshot.awaiting_ai_count >= 1
	assert snapshot.awaiting_analyst_count == 0
	assert snapshot.pending_upstream_node_count == 0
	assert snapshot.blocked_node_count == 0
	assert not snapshot.traversal_complete
	assert len(requests) == 1
	assert requests[0].subject.subject_type == SubjectType.COUNTERPARTY
	assert requests[0].subject.subject_token.startswith("CP_")
	assert requests[0].counterparty_domain is not None
	assert requests[0].counterparty_branch_context is not None
	if requests[0].counterparty_domain.rail == CounterpartyRail.LOCAL:
		assert requests[0].counterparty_local_metrics
		assert requests[0].counterparty_international_metrics is None
	elif requests[0].counterparty_domain.rail == CounterpartyRail.INTERNATIONAL:
		assert requests[0].counterparty_local_metrics is None
		assert requests[0].counterparty_international_metrics
	else:
		assert requests[0].counterparty_local_metrics is None
		assert requests[0].counterparty_international_metrics is None
	assert not any(
		node.status == ReviewNodeStatus.AWAITING_ANALYST for node in snapshot.nodes
	)
	identity_mule_nodes = [
		node
		for node in snapshot.nodes
		if node.status == ReviewNodeStatus.IDENTITY_KEEP
	]
	assert identity_mule_nodes
	assert all(node.deterministic_identity_keep for node in identity_mule_nodes)
	assert all(node.expands for node in identity_mule_nodes)
	assert all(not node.requires_ai_review for node in identity_mule_nodes)
	assert all(not node.requires_analyst_review for node in identity_mule_nodes)


@pytest.mark.live_data
def test_shared_counterparty_has_one_canonical_key_across_networks() -> None:
	package = _live_package()
	nodes = package.sheet("nodes")
	counterparties = nodes.loc[
		(nodes["node_type"].astype(str) == "COUNTERPARTY")
		& nodes["counterparty_token"].notna(),
		["network_id", "counterparty_token"],
	].drop_duplicates()
	shared_counts = counterparties.groupby("counterparty_token")["network_id"].nunique()
	shared_tokens = sorted(shared_counts.loc[shared_counts > 1].index.astype(str).tolist())

	assert shared_tokens
	shared_token = shared_tokens[0]
	shared_networks = sorted(
		counterparties.loc[
			counterparties["counterparty_token"].astype(str) == shared_token,
			"network_id",
		]
		.astype(str)
		.tolist()
	)
	ledger = CanonicalDecisionLedger(package.validation_summary.export_run_id)
	canonical_keys = set()
	for network_id in shared_networks:
		snapshot = BreadthFirstReviewEngine(package, network_id, ledger).snapshot()
		matching_nodes = [
			node for node in snapshot.nodes if node.node_token == shared_token
		]
		assert len(matching_nodes) == 1
		assert matching_nodes[0].canonical_key
		canonical_keys.add(matching_nodes[0].canonical_key)

	assert len(canonical_keys) == 1


@pytest.mark.live_data
def test_live_relationships_follow_deterministic_discovery_direction() -> None:
	package = _live_package()
	network_ids = sorted(
		package.sheet("network_summary")["network_id"].astype(str).tolist()
	)
	for network_id in network_ids:
		engine = BreadthFirstReviewEngine(package, network_id)
		for relationship in engine.graph.relationships.values():
			source = engine.graph.nodes[relationship.source_node_id]
			target = engine.graph.nodes[relationship.target_node_id]
			if target.node_layer == source.node_layer + 1:
				parent_node_id = source.node_id
				child_node_id = target.node_id
			else:
				assert target.node_layer == source.node_layer
				parent_node_id = target.node_id
				child_node_id = source.node_id
			assert parent_node_id in engine.graph.predecessors[child_node_id]
			assert child_node_id in engine.graph.forward_children[parent_node_id]
			assert (
				engine.graph.distances[parent_node_id]
				< engine.graph.distances[child_node_id]
			)


@pytest.mark.live_data
def test_large_network_assesses_linked_customers_before_counterparties() -> None:
	package = _live_package()
	summary = package.sheet("network_summary").copy()
	summary["discovered_nodes"] = summary["discovered_nodes"].astype(int)
	largest_network_id = str(
		summary.sort_values("discovered_nodes", ascending=False).iloc[0]["network_id"]
	)
	engine = BreadthFirstReviewEngine(package, largest_network_id)
	snapshot = engine.snapshot()
	candidate_tokens = set(snapshot.next_ai_subject_tokens)
	candidates = [
		node for node in snapshot.nodes if node.node_token in candidate_tokens
	]

	assert candidates
	assert all(node.node_type == GraphNodeType.CUSTOMER for node in candidates)
	assert all(node.ai_decision is None for node in candidates)
	assert all(not node.reached for node in candidates)
	assert len({node.graph_depth for node in candidates}) == 1
	minimum_unresolved_depth = min(
		node.graph_depth
		for node in snapshot.nodes
		if node.reached and node.status == ReviewNodeStatus.AWAITING_AI
	)
	assert candidates[0].graph_depth == minimum_unresolved_depth + 1
	states_by_id = {node.node_id: node for node in snapshot.nodes}
	assert all(
		all(
			states_by_id[predecessor_id].node_type == GraphNodeType.COUNTERPARTY
			and states_by_id[predecessor_id].status == ReviewNodeStatus.AWAITING_AI
			for predecessor_id in candidate.predecessor_node_ids
		)
		for candidate in candidates
	)
	requests = engine.next_ai_requests(max_calls=10)
	assert all(request.subject.subject_type == SubjectType.CUSTOMER for request in requests)
	assert all(request.customer_metrics for request in requests)
	assert all(request.customer_seed_comparison for request in requests)
	assert all(request.counterparty_branch_context is None for request in requests)
	for request in requests:
		assert "customer_token" not in request.customer_metrics
		comparison_context = request.customer_seed_comparison
		assert comparison_context is not None
		assert comparison_context.seed_customer_token == request.seed_customer_token
		for comparison in comparison_context.comparisons.values():
			assert comparison.absolute_difference == pytest.approx(
				comparison.customer_value - comparison.seed_value,
				abs=1e-5,
			)
	assert snapshot.pending_upstream_node_count > 0
	assert snapshot.blocked_node_count == 0
