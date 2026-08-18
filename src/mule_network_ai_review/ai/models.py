from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

AI_CONTRACT_VERSION = "1.0.0"


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
	customer_metrics: dict[str, Any] | None
	counterparty_local_metrics: dict[str, Any] | None
	counterparty_international_metrics: dict[str, Any] | None

	@model_validator(mode="after")
	def validate_metric_scope(self) -> NodeReviewRequest:
		if self.subject.subject_type == SubjectType.CUSTOMER:
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
			if self.customer_metrics is not None:
				raise ValueError("Counterparty review requests cannot contain customer metrics.")
			if (
				self.counterparty_local_metrics is None
				and self.counterparty_international_metrics is None
			):
				raise ValueError("Counterparty review requests require counterparty metrics.")
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

	@model_validator(mode="after")
	def validate_pruning_confidence(self) -> NodeReviewDecision:
		if (
			self.decision == ReviewDecision.LEGITIMATE_PRUNE
			and self.confidence == ReviewConfidence.LOW
		):
			raise ValueError("Low-confidence decisions cannot prune a branch.")
		return self


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
