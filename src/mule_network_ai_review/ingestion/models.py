from collections.abc import Mapping
from dataclasses import dataclass

import pandas as pd


class WorkbookValidationError(ValueError):
	def __init__(self, issues: list[str] | tuple[str, ...]):
		self.issues = tuple(issues)
		suffix = "issue" if len(self.issues) == 1 else "issues"
		super().__init__(f"Workbook validation failed with {len(self.issues)} {suffix}.")


@dataclass(frozen=True)
class ValidationSummary:
	schema_version: str
	export_run_id: str
	network_count: int
	customer_node_count: int
	eid_node_count: int
	counterparty_node_count: int
	relationship_count: int
	shared_customer_count: int
	shared_counterparty_count: int
	sheet_row_counts: Mapping[str, int]


@dataclass(frozen=True)
class WorkbookPackage:
	frames: Mapping[str, pd.DataFrame]
	validation_summary: ValidationSummary

	def sheet(self, sheet_name: str) -> pd.DataFrame:
		return self.frames[sheet_name].copy(deep=False)
