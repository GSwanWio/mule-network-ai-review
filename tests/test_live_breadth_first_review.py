import os

import pytest

from mule_network_ai_review.ai import SubjectType
from mule_network_ai_review.ingestion import load_workbook_package
from mule_network_ai_review.review import (
	BreadthFirstReviewEngine,
	CanonicalDecisionLedger,
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
	assert requests[0].counterparty_local_metrics
	assert requests[0].counterparty_international_metrics
	assert not any(
		node.status == ReviewNodeStatus.AWAITING_ANALYST for node in snapshot.nodes
	)


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
def test_large_network_prioritizes_expansion_nodes_before_terminal_leaves() -> None:
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
	assert all(node.status == ReviewNodeStatus.AWAITING_AI for node in candidates)
	assert all(node.forward_child_count > 0 for node in candidates)
	assert len({node.graph_depth for node in candidates}) == 1
	assert snapshot.pending_upstream_node_count > 0
	assert snapshot.blocked_node_count == 0
