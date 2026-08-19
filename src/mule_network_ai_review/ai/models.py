from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

AI_CONTRACT_VERSION = "1.0.0"
COUNTERPARTY_DOMAIN_POLICY_VERSION = "mule_counterparty_rail_v1.0.0"


class StrictModel(BaseModel):
	model_config = ConfigDict(extra="forbid")


class SubjectType(StrEnum):
	CUSTOMER = "CUSTOMER"
	COUNTERPARTY = "COUNTERPARTY"


class ReviewDecision(StrEnum):
	SUSPICIOUS_KEEP = "SUSPICIOUS_KEEP"
	LEGITIMATE_PRUNE = "LEGITIMATE_PRUNE"


class ReviewConfidence(StrEnum):
	HIGH = "HIGH"
	MEDIUM = "MEDIUM"
	LOW = "LOW"


class CounterpartyRail(StrEnum):
	LOCAL = "LOCAL"
	INTERNATIONAL = "INTERNATIONAL"
	UNRESOLVED = "UNRESOLVED"


class CounterpartyDomainContext(StrictModel):
	policy_version: Literal["mule_counterparty_rail_v1.0.0"] = (
		COUNTERPARTY_DOMAIN_POLICY_VERSION
	)
	rail: CounterpartyRail
	rail_basis: str
	supplied_metric_family: Literal["LOCAL", "INTERNATIONAL", "NONE"]
	guidance: list[str] = Field(min_length=1)


class ReviewSubject(StrictModel):
	subject_token: str
	subject_type: SubjectType
	node_layer: int
	is_seed_customer: bool
	counterparty_key_type: str | None
	deterministic_expansion_decision: str | None
	was_expanded: bool | None


class NodeReviewRequest(StrictModel):
	contract_version: Literal["1.0.0"] = AI_CONTRACT_VERSION
	network_id: str
	seed_customer_token: str
	subject: ReviewSubject
	network_context: dict[str, Any]
	relationship_context: dict[str, Any]
	counterparty_domain: CounterpartyDomainContext | None
	customer_metrics: dict[str, Any] | None
	counterparty_local_metrics: dict[str, Any] | None
	counterparty_international_metrics: dict[str, Any] | None

	@model_validator(mode="after")
	def validate_metric_scope(self) -> NodeReviewRequest:
		if self.subject.subject_type == SubjectType.CUSTOMER:
			if self.counterparty_domain is not None:
				raise ValueError(
					"Customer review requests cannot contain counterparty domain context."
				)
			if self.customer_metrics is None:
				raise ValueError("Customer review requests require customer metrics.")
			if self.counterparty_local_metrics is not None:
				raise ValueError(
					"Customer review requests cannot contain local counterparty metrics."
				)
			if self.counterparty_international_metrics is not None:
				raise ValueError(
					"Customer review requests cannot contain international counterparty metrics."
				)
		if self.subject.subject_type == SubjectType.COUNTERPARTY:
			if self.counterparty_domain is None:
				raise ValueError(
					"Counterparty review requests require counterparty domain context."
				)
			if self.customer_metrics is not None:
				raise ValueError("Counterparty review requests cannot contain customer metrics.")
			if self.counterparty_domain.rail == CounterpartyRail.LOCAL:
				if self.counterparty_local_metrics is None:
					raise ValueError("Local counterparties require local metrics.")
				if self.counterparty_international_metrics is not None:
					raise ValueError(
					"Local counterparties cannot contain international metrics."
				)
			if self.counterparty_domain.rail == CounterpartyRail.INTERNATIONAL:
				if self.counterparty_international_metrics is None:
					raise ValueError(
					"International counterparties require international metrics."
				)
				if self.counterparty_local_metrics is not None:
					raise ValueError(
					"International counterparties cannot contain local metrics."
				)
			if self.counterparty_domain.rail == CounterpartyRail.UNRESOLVED:
				if self.counterparty_local_metrics is not None:
					raise ValueError(
					"Unresolved counterparties cannot contain local metrics."
				)
				if self.counterparty_international_metrics is not None:
					raise ValueError(
					"Unresolved counterparties cannot contain international metrics."
				)
		return self


class NodeReviewDecision(StrictModel):
	contract_version: Literal["1.0.0"]
	subject_token: str
	subject_type: SubjectType
	decision: ReviewDecision
	confidence: ReviewConfidence
	decision_reason: str = Field(min_length=1)
	strongest_evidence: list[str] = Field(min_length=1, max_length=5)
	counter_evidence: list[str] = Field(max_length=3)
	data_quality_limitations: list[str] = Field(max_length=3)


class AIReviewRecord(StrictModel):
	record_version: Literal["1.0.0"] = AI_CONTRACT_VERSION
	reviewed_at_utc: datetime
	policy_version: str
	request_fingerprint: str
	network_id: str
	subject_token: str
	subject_type: SubjectType
	openai_response_id: str
	model: str
	input_tokens: int | None
	output_tokens: int | None
	total_tokens: int | None
	decision: NodeReviewDecision
