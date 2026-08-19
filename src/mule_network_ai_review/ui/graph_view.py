from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from mule_network_ai_review.ai import ReviewDecision
from mule_network_ai_review.review import (
	BreadthFirstReviewEngine,
	GraphNodeType,
	NetworkReviewSnapshot,
	ReviewNodeState,
	ReviewNodeStatus,
)
from mule_network_ai_review.ui.language import (
	build_node_display_labels,
	decision_label,
)

MAX_RENDERED_NODES = 160


@dataclass(frozen=True)
class InteractiveReviewGraph:
	figure: dict[str, Any]
	node_ids: tuple[str, ...]
	shown_node_count: int
	total_node_count: int
	truncated: bool


def _node_priority(node: ReviewNodeState) -> tuple[int, int, str]:
	status_order = {
		ReviewNodeStatus.SEED_KEEP: 0,
		ReviewNodeStatus.AWAITING_ANALYST: 1,
		ReviewNodeStatus.CONFIRMED_KEEP: 2,
		ReviewNodeStatus.CONFIRMED_PRUNE: 3,
		ReviewNodeStatus.AWAITING_AI: 4,
		ReviewNodeStatus.IDENTITY_KEEP: 5,
		ReviewNodeStatus.PENDING_UPSTREAM: 6,
		ReviewNodeStatus.BLOCKED_BY_PRUNE: 7,
	}
	return status_order[node.status], node.graph_depth, node.node_token


def _selected_node_ids(
	snapshot: NetworkReviewSnapshot,
	max_nodes: int,
) -> set[str]:
	ordered = sorted(
		(node for node in snapshot.nodes if node.reached),
		key=_node_priority,
	)
	selected = {snapshot.seed_node_id}
	for node in ordered:
		if len(selected) >= max_nodes:
			break
		selected.add(node.node_id)
	return selected


def _node_colour(node: ReviewNodeState) -> str:
	if node.status == ReviewNodeStatus.AWAITING_ANALYST:
		return (
			"#d92d20"
			if node.ai_decision == ReviewDecision.SUSPICIOUS_KEEP
			else "#12b76a"
		)
	return {
		ReviewNodeStatus.SEED_KEEP: "#d92d20",
		ReviewNodeStatus.IDENTITY_KEEP: "#0891b2",
		ReviewNodeStatus.AWAITING_AI: "#6941c6",
		ReviewNodeStatus.CONFIRMED_KEEP: "#d92d20",
		ReviewNodeStatus.CONFIRMED_PRUNE: "#12b76a",
		ReviewNodeStatus.PENDING_UPSTREAM: "#98a2b3",
		ReviewNodeStatus.BLOCKED_BY_PRUNE: "#98a2b3",
	}[node.status]


def _outcome_label(node: ReviewNodeState) -> str:
	if node.status == ReviewNodeStatus.AWAITING_ANALYST:
		return f"{decision_label(node.ai_decision)} — waiting for your review"
	return {
		ReviewNodeStatus.SEED_KEEP: "Confirmed mule — starting point",
		ReviewNodeStatus.IDENTITY_KEEP: "Identity connection — no action needed",
		ReviewNodeStatus.AWAITING_AI: "Assessment in progress",
		ReviewNodeStatus.CONFIRMED_KEEP: "Reviewed — needs further investigation",
		ReviewNodeStatus.CONFIRMED_PRUNE: "Reviewed — no further investigation",
		ReviewNodeStatus.PENDING_UPSTREAM: "Not yet reached",
		ReviewNodeStatus.BLOCKED_BY_PRUNE: "Not followed after an earlier decision",
	}[node.status]


def _node_size(node: ReviewNodeState) -> int:
	if node.is_seed_customer:
		return 34
	if node.node_type == GraphNodeType.CUSTOMER:
		return 25
	if node.node_type == GraphNodeType.EID:
		return 21
	return 23


def _edge_style(
	relationship_id: str,
	snapshot: NetworkReviewSnapshot,
	source: ReviewNodeState,
	target: ReviewNodeState,
) -> tuple[str, str, float]:
	if source.node_type == GraphNodeType.EID or target.node_type == GraphNodeType.EID:
		return "#0891b2", "dot", 0.55
	if relationship_id in snapshot.pruned_relationship_ids:
		return "#12b76a", "dash", 0.55
	if relationship_id in snapshot.pending_relationship_ids:
		return "#98a2b3", "dot", 0.42
	if ReviewNodeStatus.AWAITING_AI in {source.status, target.status}:
		return "#98a2b3", "dot", 0.45
	for node in (source, target):
		decision = node.effective_decision or node.ai_decision
		if decision == ReviewDecision.LEGITIMATE_PRUNE:
			return "#12b76a", "solid", 0.58
	return "#d92d20", "solid", 0.5


def _node_positions(states: list[ReviewNodeState]) -> dict[str, tuple[float, float]]:
	layers: dict[int, list[ReviewNodeState]] = defaultdict(list)
	for node in states:
		layers[node.graph_depth].append(node)
	positions: dict[str, tuple[float, float]] = {}
	for depth, nodes in sorted(layers.items()):
		nodes.sort(key=lambda node: (node.node_type.value, node.node_token))
		count = len(nodes)
		for index, node in enumerate(nodes):
			x = 0.5 if count == 1 else (index + 0.5) / count
			positions[node.node_id] = x, -float(depth)
	return positions


def build_interactive_review_graph(
	engine: BreadthFirstReviewEngine,
	snapshot: NetworkReviewSnapshot | None = None,
	selected_node_id: str | None = None,
	max_nodes: int = MAX_RENDERED_NODES,
) -> InteractiveReviewGraph:
	resolved_snapshot = snapshot or engine.snapshot()
	if max_nodes < 2:
		raise ValueError("max_nodes must be at least 2.")
	states = {node.node_id: node for node in resolved_snapshot.nodes}
	selected_ids = _selected_node_ids(resolved_snapshot, max_nodes)
	selected_states = sorted(
		(states[node_id] for node_id in selected_ids),
		key=lambda node: (node.graph_depth, node.node_type.value, node.node_token),
	)
	display_labels = build_node_display_labels(resolved_snapshot.nodes)
	positions = _node_positions(selected_states)
	active_ids = set(resolved_snapshot.active_relationship_ids)
	pending_ids = set(resolved_snapshot.pending_relationship_ids)
	pruned_ids = set(resolved_snapshot.pruned_relationship_ids)
	edge_groups: dict[tuple[str, str, float], tuple[list[float | None], list[float | None]]] = {}
	for relationship in engine.graph.relationships.values():
		if (
			relationship.source_node_id not in positions
			or relationship.target_node_id not in positions
			or relationship.relationship_id not in active_ids | pending_ids | pruned_ids
		):
			continue
		style = _edge_style(
			relationship.relationship_id,
			resolved_snapshot,
			states[relationship.source_node_id],
			states[relationship.target_node_id],
		)
		x_values, y_values = edge_groups.setdefault(style, ([], []))
		x1, y1 = positions[relationship.source_node_id]
		x2, y2 = positions[relationship.target_node_id]
		x_values.extend([x1, x2, None])
		y_values.extend([y1, y2, None])

	traces: list[dict[str, Any]] = []
	for (colour, dash, opacity), (x_values, y_values) in edge_groups.items():
		traces.append(
			{
				"type": "scatter",
				"mode": "lines",
				"x": x_values,
				"y": y_values,
				"hoverinfo": "skip",
				"showlegend": False,
				"line": {"color": colour, "width": 1.8, "dash": dash},
				"opacity": opacity,
			}
		)

	node_text = []
	for node in selected_states:
		if node.node_id == selected_node_id:
			node_text.append(display_labels[node.node_id])
		elif node.is_seed_customer:
			node_text.append("Confirmed mule")
		else:
			node_text.append("")
	traces.append(
		{
			"type": "scatter",
			"mode": "markers+text",
			"x": [positions[node.node_id][0] for node in selected_states],
			"y": [positions[node.node_id][1] for node in selected_states],
			"text": node_text,
			"textposition": "bottom center",
			"textfont": {"size": 11, "color": "#344054"},
			"customdata": [
				[
					node.node_id,
					display_labels[node.node_id],
					node.node_token,
					_outcome_label(node),
					"Yes" if node.analyst_review_complete else "No",
				]
				for node in selected_states
			],
			"hovertemplate": (
				"<b>%{customdata[1]}</b><br>"
				"Status: %{customdata[3]}<br>"
				"Checked by analyst: %{customdata[4]}<br>"
				"Reference: %{customdata[2]}<extra></extra>"
			),
			"marker": {
				"color": [_node_colour(node) for node in selected_states],
				"size": [_node_size(node) for node in selected_states],
				"line": {
					"color": [
						"#1570ef"
						if node.node_id == selected_node_id
						else "#f79009"
						if node.status == ReviewNodeStatus.AWAITING_ANALYST
						else "#ffffff"
						for node in selected_states
					],
					"width": [
						5
						if node.node_id == selected_node_id
						else 3
						if node.status == ReviewNodeStatus.AWAITING_ANALYST
						else 2
						for node in selected_states
					],
				},
			},
			"showlegend": False,
		},
	)
	maximum_depth = max((node.graph_depth for node in selected_states), default=0)
	height = max(460, min(700, 280 + (maximum_depth * 105)))
	figure = {
		"data": traces,
		"layout": {
			"height": height,
			"margin": {"l": 18, "r": 18, "t": 22, "b": 28},
			"paper_bgcolor": "#ffffff",
			"plot_bgcolor": "#ffffff",
			"clickmode": "event+select",
			"dragmode": "pan",
			"hovermode": "closest",
			"showlegend": False,
			"xaxis": {
				"visible": False,
				"range": [-0.05, 1.05],
				"fixedrange": False,
			},
			"yaxis": {
				"visible": False,
				"range": [-(maximum_depth + 0.45), 0.45],
				"fixedrange": False,
			},
		},
	}
	return InteractiveReviewGraph(
		figure=figure,
		node_ids=tuple(node.node_id for node in selected_states),
		shown_node_count=len(selected_ids),
		total_node_count=resolved_snapshot.reached_node_count,
		truncated=len(selected_ids) < resolved_snapshot.reached_node_count,
	)


def selected_node_id_from_event(event: Any) -> str | None:
	if not event:
		return None
	selection = event.get("selection", {})
	points = selection.get("points", [])
	if not points:
		return None
	customdata = points[-1].get("customdata")
	if isinstance(customdata, (list, tuple)) and customdata:
		return str(customdata[0])
	return None
