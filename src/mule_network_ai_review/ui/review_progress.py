from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass

from mule_network_ai_review.ai import ReviewDecision
from mule_network_ai_review.review import (
	GraphNodeType,
	NetworkReviewSnapshot,
	ReviewNodeState,
	ReviewNodeStatus,
)
from mule_network_ai_review.ui.language import build_node_display_labels


@dataclass(frozen=True)
class ReviewProgressItem:
	node_id: str
	node_token: str
	display_label: str
	node_type: GraphNodeType
	graph_depth: int
	status_label: str
	icon: str
	requires_analyst_review: bool
	analyst_review_complete: bool
	ai_decision: ReviewDecision | None

	@property
	def short_token(self) -> str:
		if len(self.node_token) <= 24:
			return self.node_token
		return f"{self.node_token[:21]}…"

	@property
	def button_label(self) -> str:
		return f"{self.icon} {self.display_label}"


def _progress_status(node: ReviewNodeState) -> tuple[str, str]:
	if node.status in {
		ReviewNodeStatus.CONFIRMED_KEEP,
		ReviewNodeStatus.CONFIRMED_PRUNE,
	}:
		return "Reviewed", "✅"
	if node.status == ReviewNodeStatus.AWAITING_ANALYST:
		return "Waiting for your review", "○"
	if node.status == ReviewNodeStatus.AWAITING_AI:
		return "Assessment in progress", "⋯"
	if node.status == ReviewNodeStatus.SEED_KEEP:
		return "Confirmed mule — starting point", "◆"
	if node.status == ReviewNodeStatus.IDENTITY_KEEP:
		return (
			("Confirmed mule — shared Emirates ID", "◆")
			if node.node_type == GraphNodeType.CUSTOMER
			else ("Emirates ID — connected customers are confirmed mules", "◆")
		)
	return "Not yet reached", "–"


def build_review_progress(
	snapshot: NetworkReviewSnapshot,
	visible_node_ids: Collection[str] | None = None,
) -> tuple[ReviewProgressItem, ...]:
	items = []
	visible_ids = set(visible_node_ids) if visible_node_ids is not None else None
	visible_nodes = [
		node
		for node in snapshot.nodes
		if node.reached and (visible_ids is None or node.node_id in visible_ids)
	]
	display_labels = build_node_display_labels(visible_nodes)
	for node in visible_nodes:
		status_label, icon = _progress_status(node)
		items.append(
			ReviewProgressItem(
				node_id=node.node_id,
				node_token=node.node_token,
				display_label=display_labels[node.node_id],
				node_type=node.node_type,
				graph_depth=node.graph_depth,
				status_label=status_label,
				icon=icon,
				requires_analyst_review=node.requires_analyst_review,
				analyst_review_complete=node.analyst_review_complete,
				ai_decision=node.ai_decision,
			)
		)
	return tuple(
		sorted(
			items,
			key=lambda item: (
				0 if item.status_label == "Waiting for your review" else 1,
				item.graph_depth,
				item.node_type.value,
				item.node_token,
			),
		)
	)


def default_selected_node_id(snapshot: NetworkReviewSnapshot) -> str:
	pending = [
		node
		for node in snapshot.nodes
		if node.reached and node.status == ReviewNodeStatus.AWAITING_ANALYST
	]
	if pending:
		return min(
			pending,
			key=lambda node: (node.graph_depth, node.node_type.value, node.node_token),
		).node_id
	return snapshot.seed_node_id


def next_pending_node_id(
	snapshot: NetworkReviewSnapshot,
	current_node_id: str,
) -> str | None:
	pending_ids = [
		item.node_id
		for item in build_review_progress(snapshot)
		if item.status_label == "Waiting for your review"
		and item.node_id != current_node_id
	]
	return pending_ids[0] if pending_ids else None
