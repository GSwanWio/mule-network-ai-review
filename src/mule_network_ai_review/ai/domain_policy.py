from __future__ import annotations

from collections.abc import Iterable

from mule_network_ai_review.ai.models import (
	CounterpartyDomainContext,
	CounterpartyRail,
	NodeReviewDecision,
)


class CounterpartyDomainError(ValueError):
	pass


LOCAL_RELATIONSHIP_MARKERS = (
	"LOCAL INWARD",
	"LOCAL OUTWARD",
)
INTERNATIONAL_RELATIONSHIP_MARKERS = (
	"INTERNATIONAL INWARD",
	"INTERNATIONAL OUTWARD",
)


def _contains_marker(description: str, markers: tuple[str, ...]) -> bool:
	normalized = description.upper().strip()
	return any(marker in normalized for marker in markers)


def resolve_counterparty_domain(
	relationship_descriptions: Iterable[str],
	counterparty_key_type: str | None,
) -> CounterpartyDomainContext:
	descriptions = [
		str(description).strip()
		for description in relationship_descriptions
		if str(description).strip()
	]
	has_local_relationship = any(
		_contains_marker(description, LOCAL_RELATIONSHIP_MARKERS)
		for description in descriptions
	)
	has_international_relationship = any(
		_contains_marker(description, INTERNATIONAL_RELATIONSHIP_MARKERS)
		for description in descriptions
	)
	if has_local_relationship and has_international_relationship:
		raise CounterpartyDomainError(
			"The counterparty has conflicting local and international relationship data."
		)

	common_guidance = [
		"A counterparty belongs to one payment rail for this review.",
		"A metric row with no activity does not prove that the counterparty belongs to that rail.",
		"Network expansion limits describe discovery only and are not risk evidence.",
	]
	if has_local_relationship:
		return CounterpartyDomainContext(
			rail=CounterpartyRail.LOCAL,
			rail_basis="PAYMENT_RELATIONSHIP",
			supplied_metric_family="LOCAL",
			guidance=[
				*common_guidance,
				"Use only the supplied local counterparty metrics for this subject.",
				"Do not infer or discuss international activity for this subject.",
			],
		)
	if has_international_relationship:
		return CounterpartyDomainContext(
			rail=CounterpartyRail.INTERNATIONAL,
			rail_basis="PAYMENT_RELATIONSHIP",
			supplied_metric_family="INTERNATIONAL",
			guidance=[
				*common_guidance,
				"Use only the supplied international counterparty metrics for this subject.",
				"Do not infer or discuss local activity for this subject.",
			],
		)
	if (counterparty_key_type or "").upper().strip() == "SWIFT_ACCOUNT":
		return CounterpartyDomainContext(
			rail=CounterpartyRail.INTERNATIONAL,
			rail_basis="SWIFT_ACCOUNT_KEY",
			supplied_metric_family="INTERNATIONAL",
			guidance=[
				*common_guidance,
				"The SWIFT-account key classifies this subject as international.",
				"Use only the supplied international counterparty metrics for this subject.",
				"Do not infer or discuss local activity for this subject.",
			],
		)
	return CounterpartyDomainContext(
		rail=CounterpartyRail.UNRESOLVED,
		rail_basis="INSUFFICIENT_SOURCE_DATA",
		supplied_metric_family="NONE",
		guidance=[
			*common_guidance,
			"The source data does not resolve this subject's payment rail.",
			"No rail-specific transaction metrics are supplied for this subject.",
			"Do not infer or describe this subject as local or international.",
		],
	)


def validate_counterparty_decision_language(
	domain: CounterpartyDomainContext,
	decision: NodeReviewDecision,
) -> None:
	statements = [
		decision.decision_reason,
		*decision.strongest_evidence,
		*decision.counter_evidence,
		*decision.data_quality_limitations,
	]
	decision_text = " ".join(statements).lower()
	forbidden_terms = {
		CounterpartyRail.LOCAL: ("international",),
		CounterpartyRail.INTERNATIONAL: ("local",),
		CounterpartyRail.UNRESOLVED: ("local", "international"),
	}[domain.rail]
	used_terms = sorted(term for term in forbidden_terms if term in decision_text)
	if used_terms:
		raise CounterpartyDomainError(
			"The AI decision conflicts with the authoritative counterparty rail: "
			+ ", ".join(used_terms)
		)
