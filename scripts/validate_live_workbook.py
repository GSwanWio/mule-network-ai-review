import argparse
import json

from mule_network_ai_review.ingestion import WorkbookValidationError, load_workbook_package


def main() -> int:
	parser = argparse.ArgumentParser()
	parser.add_argument("workbook_path")
	arguments = parser.parse_args()

	try:
		package = load_workbook_package(arguments.workbook_path)
	except WorkbookValidationError as error:
		print(json.dumps({"valid": False, "issues": list(error.issues)}, indent=2))
		return 1

	summary = package.validation_summary
	print(
		json.dumps(
			{
				"valid": True,
				"schema_version": summary.schema_version,
				"network_count": summary.network_count,
				"customer_node_count": summary.customer_node_count,
				"eid_node_count": summary.eid_node_count,
				"counterparty_node_count": summary.counterparty_node_count,
				"relationship_count": summary.relationship_count,
				"shared_customer_count": summary.shared_customer_count,
				"shared_counterparty_count": summary.shared_counterparty_count,
				"sheet_row_counts": summary.sheet_row_counts,
			},
			indent=2,
		)
	)
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
