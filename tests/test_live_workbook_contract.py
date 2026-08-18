import os

import pytest

from mule_network_ai_review.ingestion import load_workbook_package


@pytest.mark.live_data
def test_protected_live_workbook_contract() -> None:
	workbook_path = os.getenv("MULE_NETWORK_WORKBOOK_PATH")
	if not workbook_path:
		pytest.skip("MULE_NETWORK_WORKBOOK_PATH is not configured.")

	package = load_workbook_package(workbook_path)
	summary = package.validation_summary

	assert summary.network_count > 0
	assert summary.customer_node_count > 0
	assert summary.relationship_count > 0
