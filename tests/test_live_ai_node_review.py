import os

import pytest

from mule_network_ai_review.ai import (
	AIClientSettings,
	OpenAIReviewClient,
	ReviewDecision,
	build_node_review_request,
	select_review_candidate,
)
from mule_network_ai_review.ingestion import load_workbook_package


@pytest.mark.live_ai
def test_one_bounded_live_openai_node_decision() -> None:
	if os.getenv("RUN_LIVE_AI_ACCEPTANCE") != "1":
		pytest.skip("RUN_LIVE_AI_ACCEPTANCE=1 is required for the bounded real API call.")
	workbook_path = os.getenv("MULE_NETWORK_WORKBOOK_PATH")
	if not workbook_path:
		pytest.skip("MULE_NETWORK_WORKBOOK_PATH is not configured.")
	if not os.getenv("OPENAI_API_KEY"):
		pytest.skip("OPENAI_API_KEY is not configured.")

	package = load_workbook_package(workbook_path)
	network_id, subject_token = select_review_candidate(package)
	request = build_node_review_request(package, network_id, subject_token)
	record = OpenAIReviewClient(AIClientSettings.from_environment()).review_node(request)

	assert record.network_id == network_id
	assert record.subject_token == subject_token
	assert record.decision.subject_token == subject_token
	assert record.decision.decision in {
		ReviewDecision.SUSPICIOUS_KEEP,
		ReviewDecision.LEGITIMATE_PRUNE,
	}
	assert record.openai_response_id.startswith("resp_")
	assert record.input_tokens is not None
	assert record.output_tokens is not None
