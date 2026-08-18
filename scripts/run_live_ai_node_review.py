import argparse
import json

from mule_network_ai_review.ai import (
	AIClientSettings,
	OpenAIReviewClient,
	build_node_review_request,
	select_review_candidate,
)
from mule_network_ai_review.ingestion import load_workbook_package


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
		subject_token = arguments.subject_token
	else:
		network_id, subject_token = select_review_candidate(
			package,
			network_id=arguments.network_id,
		)
	request = build_node_review_request(package, network_id, subject_token)
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
				"customer_metric_count": len(request.customer_metrics or {}),
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
