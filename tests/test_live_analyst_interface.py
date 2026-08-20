import os

import pytest

from mule_network_ai_review.ai import ReviewDecision
from mule_network_ai_review.ai.payloads import build_node_review_request
from mule_network_ai_review.ingestion import load_workbook_package
from mule_network_ai_review.review import (
	GraphNodeType,
	ReviewNodeStatus,
	select_default_review_network,
)
from mule_network_ai_review.ui import (
	AnalystReviewWorkspace,
	analyst_visible_node_ids,
	build_interactive_review_graph,
	build_node_details,
	build_node_display_labels,
	build_review_progress,
	decision_label,
)


def _live_package():
	workbook_path = os.getenv("MULE_NETWORK_WORKBOOK_PATH")
	if not workbook_path:
		pytest.skip("MULE_NETWORK_WORKBOOK_PATH is not configured.")
	return load_workbook_package(workbook_path)


@pytest.mark.live_data
def test_live_workspace_prepares_review_without_writing_a_ledger(tmp_path) -> None:
	package = _live_package()
	ledger_path = tmp_path / "canonical_ledger.json"
	workspace = AnalystReviewWorkspace(package, ledger_path)
	network_id = select_default_review_network(package)
	engine = workspace.engine(network_id)
	snapshot = engine.snapshot()
	visible_node_ids = analyst_visible_node_ids(engine, snapshot)
	rendered = build_interactive_review_graph(engine, snapshot)
	progress = build_review_progress(snapshot, visible_node_ids)

	assert workspace.data_snapshot_id == package.validation_summary.export_run_id
	assert workspace.pending_analyst_entries() == []
	assert not ledger_path.exists()
	assert snapshot.awaiting_ai_count >= 1
	assert snapshot.awaiting_analyst_count == 0
	assert rendered.shown_node_count == rendered.total_node_count
	assert not rendered.truncated
	assert rendered.figure["layout"]["clickmode"] == "event+select"
	assert rendered.figure["layout"]["dragmode"] == "pan"
	assert set(rendered.node_ids) == {item.node_id for item in progress}
	node_trace = rendered.figure["data"][-1]
	rendered_colours = {
		custom_data[0]: colour
		for custom_data, colour in zip(
			node_trace["customdata"],
			node_trace["marker"]["color"],
			strict=True,
		)
	}
	progress_by_node_id = {item.node_id: item for item in progress}
	display_labels = build_node_display_labels(snapshot.nodes)
	identity_nodes = [
		node
		for node in snapshot.nodes
		if node.status == ReviewNodeStatus.IDENTITY_KEEP
		and node.node_id in visible_node_ids
	]
	assert node_trace["mode"] == "markers+text"
	assert len(node_trace["customdata"]) == len(visible_node_ids)
	assert sum(bool(label) for label in node_trace["text"]) == 1
	assert all(item.node_token for item in progress)
	assert all(item.display_label for item in progress)
	assert all("deterministic" not in item.status_label.lower() for item in progress)
	assert all(rendered_colours[node.node_id] == "#d92d20" for node in identity_nodes)
	assert all(
		progress_by_node_id[node.node_id].status_label
		in {
			"Confirmed mule — shared Emirates ID",
			"Emirates ID — connected customers are confirmed mules",
		}
		for node in identity_nodes
	)
	assert all(
		display_labels[node.node_id].startswith(
			"Confirmed mule"
			if node.node_type == GraphNodeType.CUSTOMER
			else "Emirates ID connection"
		)
		for node in identity_nodes
	)
	for node in snapshot.nodes:
		if node.node_type != GraphNodeType.EID or not node.reached:
			continue
		assert node.node_id in visible_node_ids
	identity_customers = []
	identity_customer_network_id = None
	for candidate_network_id in package.sheet("network_summary")["network_id"].astype(str):
		candidate_snapshot = workspace.snapshot(candidate_network_id)
		identity_customers = [
			node
			for node in candidate_snapshot.nodes
			if node.node_type == GraphNodeType.CUSTOMER
			and node.status == ReviewNodeStatus.IDENTITY_KEEP
		]
		if identity_customers:
			identity_customer_network_id = candidate_network_id
			break
	assert identity_customers
	assert identity_customer_network_id is not None
	for customer in identity_customers:
		details = build_node_details(
			package,
			identity_customer_network_id,
			customer,
		)
		assert details.record_type == "Confirmed mule customer"
		assert details.indicators
		assert details.comparisons


def test_decisions_use_plain_analyst_language() -> None:
	assert decision_label(ReviewDecision.SUSPICIOUS_KEEP) == "Needs further investigation"
	assert (
		decision_label(ReviewDecision.LEGITIMATE_PRUNE)
		== "No further investigation needed"
	)


@pytest.mark.live_data
def test_live_selected_node_details_are_protected_and_rail_specific(tmp_path) -> None:
	package = _live_package()
	workspace = AnalystReviewWorkspace(package, tmp_path / "canonical_ledger.json")
	network_ids = sorted(
		package.sheet("network_summary")["network_id"].astype(str).tolist()
	)
	details_by_type = {}
	checked_counterparties = 0
	for network_id in network_ids:
		engine = workspace.engine(network_id)
		snapshot = engine.snapshot()
		visible_node_ids = analyst_visible_node_ids(engine, snapshot)
		for node in snapshot.nodes:
			if node.node_id not in visible_node_ids:
				continue
			details = build_node_details(package, network_id, node)
			details_by_type.setdefault(node.node_type, details)
			all_text = " ".join(
				[
					details.record_type,
					details.description,
					*(item.label for item in details.facts + details.indicators),
					*(item.value for item in details.facts + details.indicators),
				]
			).lower()
			assert "counterparty name" not in all_text
			assert "account_token" not in all_text
			assert "counterparty_key_token" not in all_text
			if node.node_type == GraphNodeType.COUNTERPARTY and checked_counterparties < 20:
				request = build_node_review_request(
					package,
					network_id,
					node.node_token,
				)
				assert request.counterparty_domain is not None
				expected_family = request.counterparty_domain.supplied_metric_family
				assert details.metric_family == (
					expected_family if expected_family != "NONE" else None
				)
				assert not (
					"Local payments" in all_text
					and "International payments" in all_text
				)
				checked_counterparties += 1
		if len(details_by_type) == 3 and checked_counterparties >= 20:
			break

	assert set(details_by_type) == {
		GraphNodeType.CUSTOMER,
		GraphNodeType.EID,
		GraphNodeType.COUNTERPARTY,
	}
	assert details_by_type[GraphNodeType.CUSTOMER].metric_family == "CUSTOMER"
	assert (
		details_by_type[GraphNodeType.EID].record_type
		== "Emirates ID connection"
	)
	assert len(details_by_type[GraphNodeType.EID].connected_customers) >= 1
	assert all(
		customer.indicators
		for customer in details_by_type[GraphNodeType.EID].connected_customers
	)
	assert details_by_type[GraphNodeType.EID].metric_family is None
	assert checked_counterparties >= 1


@pytest.mark.live_data
def test_live_identity_view_keeps_every_eid_and_summarises_its_customers(
	tmp_path,
) -> None:
	package = _live_package()
	workspace = AnalystReviewWorkspace(package, tmp_path / "canonical_ledger.json")
	identity_details = []
	singleton_identity_count = 0
	shared_identity_count = 0
	for network_id in sorted(
		package.sheet("network_summary")["network_id"].astype(str).tolist()
	):
		engine = workspace.engine(network_id)
		snapshot = engine.snapshot()
		visible_node_ids = analyst_visible_node_ids(engine, snapshot)
		states_by_id = {node.node_id: node for node in snapshot.nodes}
		for node_id, graph_node in engine.graph.nodes.items():
			if graph_node.node_type != GraphNodeType.EID:
				continue
			connected_customer_count = sum(
				engine.graph.nodes[neighbour_id].node_type == GraphNodeType.CUSTOMER
				for neighbour_id in engine.graph.adjacency[node_id]
			)
			if not states_by_id[node_id].reached:
				assert node_id not in visible_node_ids
				continue
			assert node_id in visible_node_ids
			if connected_customer_count == 1:
				singleton_identity_count += 1
			else:
				shared_identity_count += 1
			details = build_node_details(
				package,
				network_id,
				states_by_id[node_id],
			)
			assert len(details.connected_customers) == connected_customer_count
			identity_details.append(details)

	assert singleton_identity_count >= 1
	assert shared_identity_count >= 1
	assert identity_details
	for details in identity_details:
		assert details.record_type == "Emirates ID connection"
		assert len(details.connected_customers) >= 1
		assert all(customer.facts for customer in details.connected_customers)
		assert all(customer.indicators for customer in details.connected_customers)
	assert any(
		customer.comparisons
		for details in identity_details
		for customer in details.connected_customers
	)


@pytest.mark.live_data
def test_large_live_network_uses_an_explicit_bounded_graph_view(tmp_path) -> None:
	package = _live_package()
	summary = package.sheet("network_summary").copy()
	summary["discovered_nodes"] = summary["discovered_nodes"].astype(int)
	network_id = str(
		summary.sort_values("discovered_nodes", ascending=False).iloc[0]["network_id"]
	)
	workspace = AnalystReviewWorkspace(package, tmp_path / "canonical_ledger.json")
	engine = workspace.engine(network_id)
	rendered = build_interactive_review_graph(engine, max_nodes=80)

	assert rendered.total_node_count > 80
	assert rendered.shown_node_count <= 80
	assert rendered.truncated
	assert len(rendered.node_ids) == rendered.shown_node_count
	assert rendered.figure["layout"]["height"] <= 700
	assert rendered.figure["layout"]["showlegend"] is False
