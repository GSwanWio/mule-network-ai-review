import pytest

from mule_network_ai_review.ai import (
	CounterpartyBranchContext,
	CounterpartyDomainError,
	CounterpartyRail,
	LinkedCustomerAssessment,
	NodeReviewDecision,
	ReviewConfidence,
	ReviewDecision,
	SubjectType,
	resolve_counterparty_domain,
	validate_counterparty_branch_decision,
	validate_counterparty_decision_language,
)


def _decision(reason: str) -> NodeReviewDecision:
	return NodeReviewDecision(
		contract_version="1.0.0",
		subject_token="CP_test",
		subject_type=SubjectType.COUNTERPARTY,
		decision=ReviewDecision.SUSPICIOUS_KEEP,
		confidence=ReviewConfidence.MEDIUM,
		decision_reason=reason,
		strongest_evidence=["Recent payment activity supports further investigation."],
		counter_evidence=[],
		data_quality_limitations=[],
	)


def test_local_relationship_selects_only_local_metrics() -> None:
	domain = resolve_counterparty_domain(["Local outward payment"], "IBAN")

	assert domain.rail == CounterpartyRail.LOCAL
	assert domain.supplied_metric_family == "LOCAL"
	assert domain.rail_basis == "PAYMENT_RELATIONSHIP"


def test_international_relationship_selects_only_international_metrics() -> None:
	domain = resolve_counterparty_domain(
		["International inward payment"],
		"IBAN",
	)

	assert domain.rail == CounterpartyRail.INTERNATIONAL
	assert domain.supplied_metric_family == "INTERNATIONAL"
	assert domain.rail_basis == "PAYMENT_RELATIONSHIP"


def test_swift_account_resolves_beneficiary_only_counterparty() -> None:
	domain = resolve_counterparty_domain(
		["Beneficiary added without an outward payment"],
		"SWIFT_ACCOUNT",
	)

	assert domain.rail == CounterpartyRail.INTERNATIONAL
	assert domain.rail_basis == "SWIFT_ACCOUNT_KEY"


def test_ambiguous_beneficiary_does_not_guess_a_payment_rail() -> None:
	domain = resolve_counterparty_domain(
		["Beneficiary added without an outward payment"],
		"IBAN",
	)

	assert domain.rail == CounterpartyRail.UNRESOLVED
	assert domain.supplied_metric_family == "NONE"


def test_conflicting_payment_rails_fail_closed() -> None:
	with pytest.raises(CounterpartyDomainError, match="conflicting"):
		resolve_counterparty_domain(
			["Local inward payment", "International outward payment"],
			"IBAN",
		)


def test_decision_cannot_introduce_a_different_payment_rail() -> None:
	domain = resolve_counterparty_domain(["Local outward payment"], "IBAN")

	with pytest.raises(CounterpartyDomainError, match="authoritative"):
		validate_counterparty_decision_language(
			domain,
			_decision("International activity supports further investigation."),
		)


def test_counterparty_decision_cannot_use_customer_only_behaviour() -> None:
	domain = resolve_counterparty_domain(["Local outward payment"], "IBAN")

	with pytest.raises(CounterpartyDomainError, match="Wio customer"):
		validate_counterparty_decision_language(
			domain,
			_decision("Pass-through behaviour supports further investigation."),
		)


def test_suspicious_linked_customer_keeps_counterparty_connection_open() -> None:
	context = CounterpartyBranchContext(
		direct_linked_customer_count=1,
		assessed_linked_customer_count=1,
		confirmed_mule_customer_count=0,
		needs_investigation_customer_count=1,
		no_further_investigation_customer_count=0,
		assessment_complete=True,
		linked_customer_assessments=[
			LinkedCustomerAssessment(
				customer_token="CUS_test",
				confirmed_mule=False,
				ai_decision=ReviewDecision.SUSPICIOUS_KEEP,
				ai_confidence=ReviewConfidence.HIGH,
			)
		],
		guidance=["Customer outcomes are branch evidence."],
	)
	decision = _decision("No further investigation is supported.").model_copy(
		update={"decision": ReviewDecision.LEGITIMATE_PRUNE}
	)

	with pytest.raises(CounterpartyDomainError, match="confirmed mule or a customer"):
		validate_counterparty_branch_decision(
			context,
			NodeReviewDecision.model_validate(decision.model_dump()),
		)
