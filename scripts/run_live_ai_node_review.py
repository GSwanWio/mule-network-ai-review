import argparse
import json

from mule_network_ai_review.ai import (
	AIClientSettings,
	OpenAIReviewClient,
	build_node_review_request,
)
from mule_network_ai_review.ingestion import load_workbook_package
from mule_network_ai_review.review import (
	BreadthFirstReviewEngine,
	select_default_review_network,
)


def _argument_parser() -> argparse.ArgumentParser:
	parser = argparse.ArgumentParser()
	parser.add_argument("workbook_path")
	parser.add_argument("--network-id")
	parser.add_argument("--subject-token")
	parser.add_argument("--confirm-live-call", action="store_true")
	return parser


def main() -> int:
	parser = _argument_parser()
	arguments = parser.parse_args()
	if not arguments.confirm_live_call:
		parser.error(
			"--confirm-live-call is required because this command makes one real API call."
		)
	if arguments.subject_token and not arguments.network_id:
		parser.error("--network-id is required when --subject-token is supplied.")

	package = load_workbook_package(arguments.workbook_path)
	if arguments.subject_token:
		network_id = arguments.network_id
		request = build_node_review_request(
			package,
			network_id,
			arguments.subject_token,
		)
	else:
		network_id = arguments.network_id or select_default_review_network(package)
		requests = BreadthFirstReviewEngine(package, network_id).next_ai_requests(
			max_calls=1
		)
		if not requests:
			raise RuntimeError("The selected network has no unresolved AI subject.")
		request = requests[0]
	settings = AIClientSettings.from_environment()

	print(
		json.dumps(
			{
				"live_api_call_count": 1,
				"model": settings.model,
				"max_output_tokens": settings.max_output_tokens,
				"network_id": request.network_id,
				"subject_token": request.subject.subject_token,
				"subject_type": request.subject.subject_type.value,
				"counterparty_rail": (
					request.counterparty_domain.rail.value
					if request.counterparty_domain
					else None
				),
				"counterparty_rail_basis": (
					request.counterparty_domain.rail_basis
					if request.counterparty_domain
					else None
				),
				"customer_metric_count": len(request.customer_metrics or {}),
				"seed_comparison_metric_count": len(
					request.customer_seed_comparison.comparisons
					if request.customer_seed_comparison
					else {}
				),
				"linked_customer_assessment_count": (
					request.counterparty_branch_context.assessed_linked_customer_count
					if request.counterparty_branch_context
					else 0
				),
				"local_counterparty_metric_count": len(
					request.counterparty_local_metrics or {}
				),
				"international_counterparty_metric_count": len(
					request.counterparty_international_metrics or {}
				),
			},
			indent=2,
		)
	)
	record = OpenAIReviewClient(settings).review_node(request)
	print(record.model_dump_json(indent=2))
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
