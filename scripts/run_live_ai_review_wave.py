import argparse
import json
from pathlib import Path

from mule_network_ai_review.ai import AIClientSettings, OpenAIReviewClient
from mule_network_ai_review.ingestion import load_workbook_package
from mule_network_ai_review.review import (
	MAX_AI_CALLS_PER_WAVE,
	BreadthFirstReviewEngine,
	CanonicalDecisionLedger,
	select_default_review_network,
)


def _argument_parser() -> argparse.ArgumentParser:
	parser = argparse.ArgumentParser()
	parser.add_argument("workbook_path")
	parser.add_argument("--network-id")
	parser.add_argument("--ledger-path")
	parser.add_argument("--max-ai-calls", type=int, default=1)
	parser.add_argument("--confirm-live-calls", action="store_true")
	return parser


def main() -> int:
	parser = _argument_parser()
	arguments = parser.parse_args()
	if not arguments.confirm_live_calls:
		parser.error(
			"--confirm-live-calls is required because this command makes real API calls."
		)
	if arguments.max_ai_calls < 1 or arguments.max_ai_calls > MAX_AI_CALLS_PER_WAVE:
		parser.error(f"--max-ai-calls must be between 1 and {MAX_AI_CALLS_PER_WAVE}.")

	package = load_workbook_package(arguments.workbook_path)
	network_id = arguments.network_id or select_default_review_network(package)
	ledger_path = Path(arguments.ledger_path) if arguments.ledger_path else None
	if ledger_path is not None and ledger_path.exists():
		ledger = CanonicalDecisionLedger.load(
			ledger_path,
			expected_data_snapshot_id=package.validation_summary.export_run_id,
		)
	else:
		ledger = CanonicalDecisionLedger(package.validation_summary.export_run_id)
	engine = BreadthFirstReviewEngine(package, network_id, ledger)
	requests = engine.next_ai_requests(max_calls=arguments.max_ai_calls)
	settings = AIClientSettings.from_environment()

	print(
		json.dumps(
			{
				"live_api_call_limit": arguments.max_ai_calls,
				"planned_live_api_calls": len(requests),
				"model": settings.model,
				"max_output_tokens_per_call": settings.max_output_tokens,
				"data_snapshot_id": package.validation_summary.export_run_id,
				"network_id": network_id,
				"subjects": [
					{
						"subject_token": request.subject.subject_token,
						"subject_type": request.subject.subject_type.value,
						"node_layer": request.subject.node_layer,
					}
					for request in requests
				],
			},
			indent=2,
		)
	)

	client = OpenAIReviewClient(settings)
	records = []
	for request in requests:
		record = client.review_node(request)
		ledger.record_ai_review(record)
		if ledger_path is not None:
			ledger.save(ledger_path)
		records.append(record)
		print(record.model_dump_json(indent=2))

	snapshot = engine.snapshot()
	print(
		json.dumps(
			{
				"completed_live_api_calls": len(records),
				"awaiting_ai_count": snapshot.awaiting_ai_count,
				"awaiting_analyst_count": snapshot.awaiting_analyst_count,
				"confirmed_keep_count": snapshot.confirmed_keep_count,
				"confirmed_prune_count": snapshot.confirmed_prune_count,
				"pending_upstream_node_count": snapshot.pending_upstream_node_count,
				"blocked_node_count": snapshot.blocked_node_count,
				"traversal_complete": snapshot.traversal_complete,
				"ledger_path": str(ledger_path) if ledger_path is not None else None,
			},
			indent=2,
		)
	)
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
