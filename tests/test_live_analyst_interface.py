import os

import pytest

from mule_network_ai_review.ai import ReviewDecision
from mule_network_ai_review.ingestion import load_workbook_package
from mule_network_ai_review.review import select_default_review_network
from mule_network_ai_review.ui import (
	AnalystReviewWorkspace,
	build_interactive_review_graph,
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
	rendered = build_interactive_review_graph(engine, snapshot)
	progress = build_review_progress(snapshot)

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
	assert node_trace["mode"] == "markers+text"
	assert len(node_trace["customdata"]) == snapshot.reached_node_count
	assert sum(bool(label) for label in node_trace["text"]) == 1
	assert all(item.node_token for item in progress)
	assert all(item.display_label for item in progress)
	assert all("deterministic" not in item.status_label.lower() for item in progress)


def test_decisions_use_plain_analyst_language() -> None:
	assert decision_label(ReviewDecision.SUSPICIOUS_KEEP) == "Needs further investigation"
	assert (
		decision_label(ReviewDecision.LEGITIMATE_PRUNE)
		== "No further investigation needed"
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
