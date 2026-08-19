from __future__ import annotations

from collections import Counter
from datetime import date, datetime
from math import isfinite
from typing import Any

import pandas as pd

from mule_network_ai_review.ai.domain_policy import (
	CounterpartyDomainError,
	resolve_counterparty_domain,
)
from mule_network_ai_review.ai.models import (
	CounterpartyRail,
	NodeReviewRequest,
	ReviewSubject,
	SubjectType,
)
from mule_network_ai_review.ingestion import WorkbookPackage


class ReviewPayloadError(ValueError):
	pass


METRIC_IDENTIFIER_COLUMNS = {
	"metric_version",
	"counterparty_metric_id",
	"discovery_version",
	"discovery_run_id",
	"discovery_timestamp_utc",
	"metric_generated_timestamp_utc",
	"counterparty_node_id",
	"network_id",
	"seed_customer_token",
	"customer_token",
	"counterparty_token",
	"counterparty_key_token",
	"account_token",
	"bank_token",
}

NETWORK_CONTEXT_COLUMNS = (
	"linked_customer_limit",
	"linked_counterparty_limit",
	"discovered_customers",
	"discovered_eids",
	"discovered_counterparties",
	"discovered_relationships",
	"expanded_eids",
	"expanded_counterparties",
	"terminal_counterparties",
	"customer_guardrail_stops",
	"counterparty_guardrail_stops",
	"maximum_customer_layer",
	"maximum_eid_layer",
	"maximum_counterparty_layer",
	"network_complete",
	"traversal_converged",
	"identity_exhaustive",
	"network_exhaustive",
	"pruned_branch_count",
	"termination_reason",
)


def _is_missing(value: Any) -> bool:
	if value is None:
		return True
	missing = pd.isna(value)
	if isinstance(missing, bool):
		return missing
	try:
		return bool(missing)
	except (TypeError, ValueError):
		return False


def _normalize_value(value: Any) -> Any:
	if _is_missing(value):
		return None
	if hasattr(value, "item"):
		value = value.item()
	if isinstance(value, (datetime, date)):
		return value.isoformat()
	if isinstance(value, float) and not isfinite(value):
		return None
	if isinstance(value, (str, int, float, bool)):
		return value
	if isinstance(value, (list, tuple, set)):
		return [normalized for item in value if (normalized := _normalize_value(item)) is not None]
	return str(value)


def _required_text(value: Any, field_name: str) -> str:
	normalized = _normalize_value(value)
	if normalized is None or not str(normalized).strip():
		raise ReviewPayloadError(f"{field_name} is missing from the selected workbook row.")
	return str(normalized).strip()


def _optional_text(value: Any) -> str | None:
	normalized = _normalize_value(value)
	if normalized is None:
		return None
	text = str(normalized).strip()
	return text or None


def _optional_bool(value: Any) -> bool | None:
	normalized = _normalize_value(value)
	if normalized is None:
		return None
	return bool(normalized)


def _single_row(frame: pd.DataFrame, description: str) -> pd.Series:
	if len(frame.index) != 1:
		raise ReviewPayloadError(
			f"Expected one {description} row but found {len(frame.index)}."
		)
	return frame.iloc[0]


def _metric_map(row: pd.Series) -> dict[str, Any]:
	metrics: dict[str, Any] = {}
	for column, value in row.items():
		if column in METRIC_IDENTIFIER_COLUMNS:
			continue
		normalized = _normalize_value(value)
		if normalized is not None:
			metrics[str(column)] = normalized
	return metrics


def _subject_token_column(subject_type: SubjectType) -> str:
	if subject_type == SubjectType.CUSTOMER:
		return "customer_token"
	return "counterparty_token"


def _reviewable_nodes(package: WorkbookPackage, subject_type: SubjectType) -> pd.DataFrame:
	nodes = package.sheet("nodes")
	token_column = _subject_token_column(subject_type)
	return nodes.loc[
		(nodes["node_type"].astype(str) == subject_type.value)
		& nodes[token_column].notna()
	].copy()


def select_review_candidate(
	package: WorkbookPackage,
	network_id: str | None = None,
	subject_type: SubjectType = SubjectType.COUNTERPARTY,
) -> tuple[str, str]:
	candidates = _reviewable_nodes(package, subject_type)
	if network_id is not None:
		candidates = candidates.loc[candidates["network_id"].astype(str) == network_id]
	if candidates.empty:
		raise ReviewPayloadError("No reviewable subjects match the requested scope.")

	summary = package.sheet("network_summary")[["network_id", "discovered_nodes"]]
	candidates = candidates.merge(summary, on="network_id", how="left", validate="many_to_one")
	candidates["has_payment_activity"] = 0
	candidates["is_direct_seed_relationship"] = 0

	if subject_type == SubjectType.COUNTERPARTY:
		activity_frames = []
		for sheet_name, activity_column in (
			("counterparty_local", "has_local_payment_activity"),
			("counterparty_intl", "has_international_payment_activity"),
		):
			metrics = package.sheet(sheet_name)[
				[
					"network_id",
					"counterparty_token",
					"is_direct_seed_relationship",
					activity_column,
				]
			].copy()
			metrics = metrics.rename(columns={activity_column: "has_payment_activity"})
			activity_frames.append(metrics)
		activity = pd.concat(activity_frames, ignore_index=True)
		activity = (
			activity.groupby(["network_id", "counterparty_token"], as_index=False)
			.agg(
				has_payment_activity=("has_payment_activity", "max"),
				is_direct_seed_relationship=("is_direct_seed_relationship", "max"),
			)
		)
		candidates = candidates.drop(
			columns=["has_payment_activity", "is_direct_seed_relationship"]
		).merge(
			activity,
			on=["network_id", "counterparty_token"],
			how="left",
			validate="one_to_one",
		)

	candidates["has_payment_activity"] = (
		pd.to_numeric(candidates["has_payment_activity"], errors="coerce").fillna(0).astype(int)
	)
	candidates["is_direct_seed_relationship"] = (
		pd.to_numeric(candidates["is_direct_seed_relationship"], errors="coerce")
		.fillna(0)
		.astype(int)
	)
	candidates["node_layer"] = pd.to_numeric(
		candidates["node_layer"], errors="coerce"
	).fillna(0)
	candidates["discovered_nodes"] = pd.to_numeric(
		candidates["discovered_nodes"], errors="coerce"
	).fillna(0)
	token_column = _subject_token_column(subject_type)
	candidates = candidates.sort_values(
		by=[
			"is_direct_seed_relationship",
			"has_payment_activity",
			"discovered_nodes",
			"node_layer",
			token_column,
		],
		ascending=[False, False, True, True, True],
		kind="stable",
	)
	selected = candidates.iloc[0]
	return (
		_required_text(selected["network_id"], "network_id"),
		_required_text(selected[token_column], token_column),
	)


def build_node_review_request(
	package: WorkbookPackage,
	network_id: str,
	subject_token: str,
) -> NodeReviewRequest:
	nodes = package.sheet("nodes")
	token_match = (
		(nodes["customer_token"].astype(str) == subject_token)
		| (nodes["counterparty_token"].astype(str) == subject_token)
	)
	node = _single_row(
		nodes.loc[(nodes["network_id"].astype(str) == network_id) & token_match],
		"review subject",
	)
	subject_type = SubjectType(_required_text(node["node_type"], "node_type"))
	token_column = _subject_token_column(subject_type)
	resolved_subject_token = _required_text(node[token_column], token_column)

	summary = _single_row(
		package.sheet("network_summary").loc[
			lambda frame: frame["network_id"].astype(str) == network_id
		],
		"network summary",
	)
	network_context = {
		column: normalized
		for column in NETWORK_CONTEXT_COLUMNS
		if (normalized := _normalize_value(summary[column])) is not None
	}

	relationships = package.sheet("relationships")
	incident = relationships.loc[
		(relationships["network_id"].astype(str) == network_id)
		& (
			(relationships["source_node_id"].astype(str) == str(node["node_id"]))
			| (relationships["target_node_id"].astype(str) == str(node["node_id"]))
		)
	]
	relationship_type_counts = Counter(incident["relationship_type"].dropna().astype(str))
	relationship_description_counts = Counter(
		incident["relationship_description"].dropna().astype(str)
	)
	relationship_context = {
		"incident_relationship_count": int(len(incident.index)),
		"relationship_type_counts": dict(sorted(relationship_type_counts.items())),
		"relationship_description_counts": dict(
			sorted(relationship_description_counts.items())
		),
	}

	customer_metrics = None
	counterparty_domain = None
	counterparty_local_metrics = None
	counterparty_international_metrics = None
	if subject_type == SubjectType.CUSTOMER:
		customer_row = _single_row(
			package.sheet("customer_metrics").loc[
				lambda frame: (frame["network_id"].astype(str) == network_id)
				& (frame["customer_token"].astype(str) == resolved_subject_token)
			],
			"customer metric",
		)
		customer_metrics = _metric_map(customer_row)
	else:
		try:
			counterparty_domain = resolve_counterparty_domain(
				relationship_descriptions=relationship_description_counts,
				counterparty_key_type=_optional_text(node["counterparty_key_type"]),
			)
		except CounterpartyDomainError as error:
			raise ReviewPayloadError(str(error)) from error
		if counterparty_domain.rail == CounterpartyRail.LOCAL:
			local_row = _single_row(
				package.sheet("counterparty_local").loc[
					lambda frame: (frame["network_id"].astype(str) == network_id)
					& (
						frame["counterparty_token"].astype(str)
						== resolved_subject_token
					)
				],
				"local counterparty metric",
			)
			counterparty_local_metrics = _metric_map(local_row)
		elif counterparty_domain.rail == CounterpartyRail.INTERNATIONAL:
			international_row = _single_row(
				package.sheet("counterparty_intl").loc[
					lambda frame: (frame["network_id"].astype(str) == network_id)
					& (
						frame["counterparty_token"].astype(str)
						== resolved_subject_token
					)
				],
				"international counterparty metric",
			)
			counterparty_international_metrics = _metric_map(international_row)

	return NodeReviewRequest(
		network_id=network_id,
		seed_customer_token=_required_text(summary["seed_customer_token"], "seed_customer_token"),
		subject=ReviewSubject(
			subject_token=resolved_subject_token,
			subject_type=subject_type,
			node_layer=int(_normalize_value(node["node_layer"]) or 0),
			is_seed_customer=bool(_normalize_value(node["is_seed_customer"]) or False),
			counterparty_key_type=_optional_text(node["counterparty_key_type"]),
			deterministic_expansion_decision=_optional_text(
				node["deterministic_expansion_decision"]
			),
			was_expanded=_optional_bool(node["was_expanded"]),
		),
		network_context=network_context,
		relationship_context=relationship_context,
		counterparty_domain=counterparty_domain,
		customer_metrics=customer_metrics,
		counterparty_local_metrics=counterparty_local_metrics,
		counterparty_international_metrics=counterparty_international_metrics,
	)
