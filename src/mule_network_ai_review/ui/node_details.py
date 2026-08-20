from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from math import isfinite
from typing import Any

import pandas as pd

from mule_network_ai_review.ai import (
	CounterpartyRail,
	CustomerMetricComparison,
	build_customer_seed_comparison,
)
from mule_network_ai_review.ai.domain_policy import (
	CounterpartyDomainError,
	resolve_counterparty_domain,
)
from mule_network_ai_review.ingestion import WorkbookPackage
from mule_network_ai_review.review import (
	GraphNodeType,
	ReviewNodeState,
	ReviewNodeStatus,
)


class NodeDetailsError(ValueError):
	pass


@dataclass(frozen=True)
class NodeDetailItem:
	label: str
	value: str


@dataclass(frozen=True)
class NodeMetricComparison:
	label: str
	customer_value: str
	seed_value: str
	difference: str


@dataclass(frozen=True)
class NodeDetails:
	record_type: str
	description: str
	facts: tuple[NodeDetailItem, ...]
	indicators: tuple[NodeDetailItem, ...]
	comparisons: tuple[NodeMetricComparison, ...] = ()
	metric_family: str | None = None


CUSTOMER_COMPARISON_PRESENTATION = {
	"transaction_count_30d": ("Transactions · 30 days", "COUNT"),
	"total_inflow_30d_aed": ("Money in · 30 days", "CURRENCY"),
	"total_outflow_30d_aed": ("Money out · 30 days", "CURRENCY"),
	"new_counterparty_count_30d": ("New counterparties · 30 days", "COUNT"),
	"overall_peer_outlier_count": ("Peer outlier indicators", "COUNT"),
	"flow_through_ratio_24h": ("24-hour flow-through", "PERCENTAGE"),
}


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


def _value(value: Any) -> Any:
	if _is_missing(value):
		return None
	if hasattr(value, "item"):
		value = value.item()
	if isinstance(value, float) and not isfinite(value):
		return None
	if isinstance(value, (datetime, date)):
		return value.isoformat()
	return value


def _text(value: Any) -> str | None:
	resolved = _value(value)
	if resolved is None:
		return None
	text = str(resolved).strip()
	return text or None


def _title_text(value: Any) -> str | None:
	text = _text(value)
	if text is None:
		return None
	return text.replace("_", " ").strip().title()


def _customer_type(value: Any) -> str:
	text = _text(value)
	if text is None:
		return "Not available"
	return {
		"SME": "SME",
		"RETAIL": "Retail",
	}.get(text.upper(), _title_text(text) or text)


def _account_reference_type(value: Any) -> str:
	text = _text(value)
	if text is None:
		return "Not available"
	return {
		"IBAN": "IBAN",
		"SWIFT_ACCOUNT": "SWIFT account",
	}.get(text.upper(), _title_text(text) or text)


def _number(value: Any) -> float | None:
	resolved = _value(value)
	if resolved is None:
		return None
	try:
		return float(resolved)
	except (TypeError, ValueError):
		return None


def _count(value: Any) -> str:
	number = _number(value)
	return "Not available" if number is None else f"{int(round(number)):,}"


def _currency(value: Any) -> str:
	number = _number(value)
	return "Not available" if number is None else f"AED {number:,.0f}"


def _percentage(value: Any) -> str:
	number = _number(value)
	return "Not available" if number is None else f"{number:.0%}"


def _comparison_value(value: float, display_type: str) -> str:
	if display_type == "COUNT":
		return f"{int(round(value)):,}"
	if display_type == "CURRENCY":
		return f"AED {value:,.0f}"
	return f"{value:.0%}"


def _comparison_difference(
	comparison: CustomerMetricComparison,
	display_type: str,
) -> str:
	if display_type == "PERCENTAGE":
		percentage_points = comparison.percentage_point_difference
		if percentage_points is None:
			return "Percentage-point difference unavailable"
		return f"{percentage_points:+.1f} percentage points vs seed"
	absolute = comparison.absolute_difference
	formatted_absolute = _comparison_value(abs(absolute), display_type)
	direction = "higher" if absolute > 0 else "lower" if absolute < 0 else "the same"
	if direction == "the same":
		absolute_text = "Same as seed"
	else:
		absolute_text = f"{formatted_absolute} {direction} than seed"
	if comparison.ratio_to_seed is None:
		return f"{absolute_text} · ratio unavailable because seed is zero"
	return f"{absolute_text} · {comparison.ratio_to_seed:.2f}× seed"


def _customer_comparisons(
	package: WorkbookPackage,
	network_id: str,
	customer_token: str,
) -> tuple[NodeMetricComparison, ...]:
	context = build_customer_seed_comparison(package, network_id, customer_token)
	if context is None:
		return ()
	items = []
	for metric_name, (label, display_type) in CUSTOMER_COMPARISON_PRESENTATION.items():
		comparison = context.comparisons.get(metric_name)
		if comparison is None:
			continue
		items.append(
			NodeMetricComparison(
				label=label,
				customer_value=_comparison_value(
					comparison.customer_value,
					display_type,
				),
				seed_value=_comparison_value(
					comparison.seed_value,
					display_type,
				),
				difference=_comparison_difference(comparison, display_type),
			)
		)
	return tuple(items)


def _days(value: Any) -> str:
	number = _number(value)
	if number is None:
		return "Not available"
	return f"{int(round(number)):,} days"


def _single_row(frame: pd.DataFrame, description: str) -> pd.Series:
	if len(frame.index) != 1:
		raise NodeDetailsError(
			f"Expected one {description} row but found {len(frame.index)}."
		)
	return frame.iloc[0]


def _node_row(
	package: WorkbookPackage,
	network_id: str,
	node_id: str,
) -> pd.Series:
	nodes = package.sheet("nodes")
	return _single_row(
		nodes.loc[
			(nodes["network_id"].astype(str) == network_id)
			& (nodes["node_id"].astype(str) == node_id)
		],
		"node",
	)


def _incident_relationships(
	package: WorkbookPackage,
	network_id: str,
	node_id: str,
) -> pd.DataFrame:
	relationships = package.sheet("relationships")
	return relationships.loc[
		(relationships["network_id"].astype(str) == network_id)
		& (
			(relationships["source_node_id"].astype(str) == node_id)
			| (relationships["target_node_id"].astype(str) == node_id)
		)
	].copy()


def _customer_details(
	package: WorkbookPackage,
	network_id: str,
	node: ReviewNodeState,
) -> NodeDetails:
	metrics = package.sheet("customer_metrics")
	row = _single_row(
		metrics.loc[
			(metrics["network_id"].astype(str) == network_id)
			& (metrics["customer_token"].astype(str) == node.node_token)
		],
		"customer metric",
	)
	confirmed_mule = node.status in {
		ReviewNodeStatus.SEED_KEEP,
		ReviewNodeStatus.IDENTITY_KEEP,
	}
	if node.status == ReviewNodeStatus.SEED_KEEP:
		description = "Confirmed mule that started this network."
	elif node.status == ReviewNodeStatus.IDENTITY_KEEP:
		description = (
			"Confirmed mule because this customer shares an Emirates ID with a "
			"confirmed mule."
		)
	else:
		description = "Customer reached through a connection in this network."

	customer_type = _customer_type(row.get("customer_type"))
	profile_detail = (
		_title_text(row.get("declared_business_activity"))
		if customer_type.upper() == "SME"
		else _title_text(row.get("salary_segment"))
	)
	profile_label = "Business activity" if customer_type.upper() == "SME" else "Segment"
	facts = [
		NodeDetailItem(
			"Classification",
			"Confirmed mule" if confirmed_mule else "Linked customer",
		),
		NodeDetailItem("Customer type", customer_type),
		NodeDetailItem(
			"Account status",
			_title_text(row.get("customer_status")) or "Not available",
		),
		NodeDetailItem(
			"KYC / KYB risk",
			_title_text(row.get("kyc_or_kyb_risk_rating")) or "Not available",
		),
	]
	if profile_detail:
		facts.append(NodeDetailItem(profile_label, profile_detail))
	elif _title_text(row.get("occupation")):
		facts.append(NodeDetailItem("Occupation", _title_text(row.get("occupation")) or ""))
	facts.append(NodeDetailItem("Customer tenure", _days(row.get("customer_tenure_days"))))

	indicators = [
		NodeDetailItem("Transactions · 30 days", _count(row.get("transaction_count_30d"))),
		NodeDetailItem("Money in · 30 days", _currency(row.get("total_inflow_30d_aed"))),
		NodeDetailItem("Money out · 30 days", _currency(row.get("total_outflow_30d_aed"))),
		NodeDetailItem(
			"New counterparties · 30 days",
			_count(row.get("new_counterparty_count_30d")),
		),
		NodeDetailItem(
			"Peer outlier indicators",
			_count(row.get("overall_peer_outlier_count")),
		),
	]
	if _number(row.get("flow_through_ratio_24h")) is not None:
		indicators.append(
			NodeDetailItem(
				"24-hour flow-through",
				_percentage(row.get("flow_through_ratio_24h")),
			)
		)
	return NodeDetails(
		record_type="Confirmed mule customer" if confirmed_mule else "Customer",
		description=description,
		facts=tuple(facts),
		indicators=tuple(indicators),
		comparisons=_customer_comparisons(
			package,
			network_id,
			node.node_token,
		),
		metric_family="CUSTOMER",
	)


def _counterparty_details(
	package: WorkbookPackage,
	network_id: str,
	node: ReviewNodeState,
	node_row: pd.Series,
	incident: pd.DataFrame,
) -> NodeDetails:
	descriptions = sorted(
		{
			text
			for value in incident["relationship_description"].tolist()
			if (text := _text(value))
		}
	)
	try:
		domain = resolve_counterparty_domain(
			relationship_descriptions=descriptions,
			counterparty_key_type=_text(node_row.get("counterparty_key_type")),
		)
	except CounterpartyDomainError as error:
		raise NodeDetailsError(str(error)) from error

	if domain.rail == CounterpartyRail.LOCAL:
		sheet_name = "counterparty_local"
		activity_column = "has_local_payment_activity"
		metric_family = "LOCAL"
		payment_type = "Local payments"
	elif domain.rail == CounterpartyRail.INTERNATIONAL:
		sheet_name = "counterparty_intl"
		activity_column = "has_international_payment_activity"
		metric_family = "INTERNATIONAL"
		payment_type = "International payments"
	else:
		return NodeDetails(
			record_type="Counterparty",
			description="Counterparty reached through a connection in this network.",
			facts=(
				NodeDetailItem("Payment type", "Not resolved from source data"),
				NodeDetailItem(
					"Connection type",
					_account_reference_type(node_row.get("counterparty_key_type")),
				),
			),
			indicators=(),
			metric_family=None,
		)

	metrics = package.sheet(sheet_name)
	row = _single_row(
		metrics.loc[
			(metrics["network_id"].astype(str) == network_id)
			& (metrics["counterparty_token"].astype(str) == node.node_token)
		],
		f"{metric_family.lower()} counterparty metric",
	)
	has_activity = bool(_number(row.get(activity_column)) or 0)
	relationship_summary = ", ".join(descriptions) if descriptions else "Not available"
	facts = (
		NodeDetailItem("Payment type", payment_type),
		NodeDetailItem(
			"Recent payment activity",
			"Observed" if has_activity else "Not observed",
		),
		NodeDetailItem("Connection", relationship_summary),
		NodeDetailItem(
			"Account reference type",
			_account_reference_type(node_row.get("counterparty_key_type")),
		),
	)
	indicators = (
		NodeDetailItem(
			"Transactions · 30 days",
			_count(row.get("total_transaction_count_30d")),
		),
		NodeDetailItem(
			"Payment value · 30 days",
			_currency(row.get("total_transaction_value_30d_aed")),
		),
		NodeDetailItem(
			"Known mule customers · 90 days",
			_count(row.get("known_mule_customers_interacting_90d")),
		),
		NodeDetailItem(
			"Linked customers",
			_count(row.get("total_linked_customer_count")),
		),
		NodeDetailItem(
			"New linked customers",
			_count(row.get("new_linked_customer_count")),
		),
		NodeDetailItem(
			"Last payment",
			_days(row.get("days_since_last_transaction")),
		),
	)
	return NodeDetails(
		record_type="Counterparty",
		description="Counterparty reached through a connection in this network.",
		facts=facts,
		indicators=indicators,
		metric_family=metric_family,
	)


def _identity_details(
	package: WorkbookPackage,
	network_id: str,
	node: ReviewNodeState,
	incident: pd.DataFrame,
) -> NodeDetails:
	nodes = package.sheet("nodes")
	network_nodes = nodes.loc[nodes["network_id"].astype(str) == network_id]
	connected_node_ids = set(incident["source_node_id"].astype(str)) | set(
		incident["target_node_id"].astype(str)
	)
	connected_customers = network_nodes.loc[
		(network_nodes["node_id"].astype(str).isin(connected_node_ids))
		& (network_nodes["node_type"].astype(str) == GraphNodeType.CUSTOMER.value)
	]
	return NodeDetails(
		record_type="Confirmed mule identity",
		description=(
			"Shared Emirates ID connecting confirmed mule customers in this network."
		),
		facts=(
			NodeDetailItem("Classification", "Confirmed mule identity"),
			NodeDetailItem("Connected customers", _count(len(connected_customers.index))),
			NodeDetailItem("Network layer", _count(node.node_layer)),
			NodeDetailItem(
				"Customer summaries",
				"Select either connected customer to view its profile, activity, "
				"and seed comparison.",
			),
		),
		indicators=(),
		metric_family=None,
	)


def build_node_details(
	package: WorkbookPackage,
	network_id: str,
	node: ReviewNodeState,
) -> NodeDetails:
	node_row = _node_row(package, network_id, node.node_id)
	incident = _incident_relationships(package, network_id, node.node_id)
	if node.node_type == GraphNodeType.CUSTOMER:
		return _customer_details(package, network_id, node)
	if node.node_type == GraphNodeType.COUNTERPARTY:
		return _counterparty_details(
			package,
			network_id,
			node,
			node_row,
			incident,
		)
	if node.node_type == GraphNodeType.EID:
		return _identity_details(package, network_id, node, incident)
	raise NodeDetailsError(f"Unsupported node type: {node.node_type.value}")
