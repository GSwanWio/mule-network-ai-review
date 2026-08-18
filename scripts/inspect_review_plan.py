import argparse
import json

from mule_network_ai_review.ingestion import load_workbook_package
from mule_network_ai_review.review import (
	BreadthFirstReviewEngine,
	select_default_review_network,
)


def _argument_parser() -> argparse.ArgumentParser:
	parser = argparse.ArgumentParser()
	parser.add_argument("workbook_path")
	parser.add_argument("--network-id")
	return parser


def main() -> int:
	arguments = _argument_parser().parse_args()
	package = load_workbook_package(arguments.workbook_path)
	network_id = arguments.network_id or select_default_review_network(package)
	engine = BreadthFirstReviewEngine(package, network_id)
	snapshot = engine.snapshot()
	candidate_tokens = set(snapshot.next_ai_subject_tokens)
	candidates = [
		{
			"subject_token": node.node_token,
			"subject_type": node.node_type.value,
			"graph_depth": node.graph_depth,
			"forward_child_count": node.forward_child_count,
			"status": node.status.value,
		}
		for node in snapshot.nodes
		if node.node_token in candidate_tokens
	]
	print(
		json.dumps(
			{
				"engine_version": snapshot.engine_version,
				"data_snapshot_id": snapshot.data_snapshot_id,
				"network_id": snapshot.network_id,
				"seed_node_id": snapshot.seed_node_id,
				"reached_node_count": snapshot.reached_node_count,
				"reviewable_node_count": snapshot.reviewable_node_count,
				"identity_keep_count": snapshot.identity_keep_count,
				"awaiting_ai_count": snapshot.awaiting_ai_count,
				"awaiting_analyst_count": snapshot.awaiting_analyst_count,
				"pending_upstream_node_count": snapshot.pending_upstream_node_count,
				"blocked_node_count": snapshot.blocked_node_count,
				"traversal_complete": snapshot.traversal_complete,
				"next_ai_candidates": candidates,
			},
			indent=2,
		)
	)
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
