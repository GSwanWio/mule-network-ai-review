from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from mule_network_ai_review.ai import ReviewDecision
from mule_network_ai_review.review import (
	GraphNodeType,
	ReviewNodeState,
	ReviewNodeStatus,
)


def decision_label(decision: ReviewDecision | str) -> str:
	resolved = ReviewDecision(decision)
	return {
		ReviewDecision.SUSPICIOUS_KEEP: "Needs further investigation",
		ReviewDecision.LEGITIMATE_PRUNE: "No further investigation needed",
	}[resolved]


def decision_explanation(decision: ReviewDecision | str) -> str:
	resolved = ReviewDecision(decision)
	return {
		ReviewDecision.SUSPICIOUS_KEEP: (
			"Keep this connection in the network and continue checking what it links to."
		),
		ReviewDecision.LEGITIMATE_PRUNE: (
			"Stop following this connection unless the available evidence changes."
		),
	}[resolved]


def node_type_label(node_type: GraphNodeType) -> str:
	return {
		GraphNodeType.CUSTOMER: "Customer",
		GraphNodeType.EID: "Emirates ID connection",
		GraphNodeType.COUNTERPARTY: "Counterparty",
	}[node_type]


def build_node_display_labels(
	nodes: Iterable[ReviewNodeState],
) -> dict[str, str]:
	ordered_nodes = sorted(
		(node for node in nodes if node.reached),
		key=lambda node: (
			node.graph_depth,
			node.node_type.value,
			node.node_token,
		),
	)
	counters: defaultdict[GraphNodeType, int] = defaultdict(int)
	labels: dict[str, str] = {}
	for node in ordered_nodes:
		if node.is_seed_customer:
			labels[node.node_id] = "Confirmed mule"
			continue
		counters[node.node_type] += 1
		if node.status == ReviewNodeStatus.IDENTITY_KEEP:
			identity_label = (
				"Confirmed mule customer"
				if node.node_type == GraphNodeType.CUSTOMER
				else "Emirates ID connection"
			)
			labels[node.node_id] = (
				f"{identity_label} {counters[node.node_type]}"
			)
		else:
			labels[node.node_id] = (
				f"{node_type_label(node.node_type)} {counters[node.node_type]}"
			)
	return labels
