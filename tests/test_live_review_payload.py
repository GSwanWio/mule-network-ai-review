import os

import pytest

from mule_network_ai_review.ai import (
	CounterpartyRail,
	SubjectType,
	build_node_review_request,
	select_review_candidate,
)
from mule_network_ai_review.ingestion import load_workbook_package


@pytest.mark.live_data
def test_live_workbook_builds_protected_counterparty_review_payload() -> None:
	workbook_path = os.getenv("MULE_NETWORK_WORKBOOK_PATH")
	if not workbook_path:
		pytest.skip("MULE_NETWORK_WORKBOOK_PATH is not configured.")

	package = load_workbook_package(workbook_path)
	network_id, subject_token = select_review_candidate(package)
	request = build_node_review_request(package, network_id, subject_token)

	assert request.subject.subject_type == SubjectType.COUNTERPARTY
	assert request.subject.subject_token == subject_token
	assert request.customer_metrics is None
	assert request.customer_seed_comparison is None
	assert request.counterparty_domain is not None
	assert request.counterparty_branch_context is not None
	assert request.counterparty_branch_context.assessed_linked_customer_count == 0
	if request.counterparty_domain.rail == CounterpartyRail.LOCAL:
		assert request.counterparty_local_metrics
		assert request.counterparty_international_metrics is None
		assert "counterparty_token" not in request.counterparty_local_metrics
	elif request.counterparty_domain.rail == CounterpartyRail.INTERNATIONAL:
		assert request.counterparty_local_metrics is None
		assert request.counterparty_international_metrics
		assert "account_token" not in request.counterparty_international_metrics
	else:
		assert request.counterparty_local_metrics is None
		assert request.counterparty_international_metrics is None
