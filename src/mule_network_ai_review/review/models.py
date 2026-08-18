from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mule_network_ai_review.ai.models import (
	AIReviewRecord,
	ReviewConfidence,
	ReviewDecision,
	SubjectType,
)

REVIEW_ENGINE_VERSION = "2.0.0"


class ReviewStrictModel(BaseModel):
	model_config = ConfigDict(extra="forbid")


class GraphNodeType(StrEnum):
	CUSTOMER = "CUSTOMER"
	EID = "EID"
	COUNTERPARTY = "COUNTERPARTY"


class CanonicalReviewState(StrEnum):
	AI_PROPOSED = "AI_PROPOSED"
	ANALYST_CONFIRMED = "ANALYST_CONFIRMED"


class AnalystAction(StrEnum):
	CONFIRM_AI = "CONFIRM_AI"
	OVERRIDE_AI = "OVERRIDE_AI"
	REVISE_CONFIRMED = "REVISE_CONFIRMED"


class ReviewNodeStatus(StrEnum):
	SEED_KEEP = "SEED_KEEP"
	IDENTITY_KEEP = "IDENTITY_KEEP"
	AWAITING_AI = "AWAITING_AI"
	AWAITING_ANALYST = "AWAITING_ANALYST"
	CONFIRMED_KEEP = "CONFIRMED_KEEP"
	CONFIRMED_PRUNE = "CONFIRMED_PRUNE"
	PENDING_UPSTREAM = "PENDING_UPSTREAM"
	BLOCKED_BY_PRUNE = "BLOCKED_BY_PRUNE"


class AnalystDecisionEvent(ReviewStrictModel):
	event_id: str
	recorded_at_utc: datetime
	analyst_reference: str = Field(min_length=1)
	action: AnalystAction
	decision: ReviewDecision
	rationale: str = Field(min_length=1)
	request_fingerprint: str = Field(min_length=64, max_length=64)
	previous_decision: ReviewDecision | None


class CanonicalDecisionEntry(ReviewStrictModel):
	ledger_version: Literal["2.0.0"] = REVIEW_ENGINE_VERSION
	canonical_key: str = Field(min_length=64, max_length=64)
	data_snapshot_id: str = Field(min_length=1)
	subject_token: str = Field(min_length=1)
	subject_type: SubjectType
	source_network_id: str = Field(min_length=1)
	ai_review: AIReviewRecord
	review_state: CanonicalReviewState = CanonicalReviewState.AI_PROPOSED
	effective_decision: ReviewDecision | None = None
	analyst_events: list[AnalystDecisionEvent] = Field(default_factory=list)

	@model_validator(mode="after")
	def validate_entry_state(self) -> CanonicalDecisionEntry:
		if self.ai_review.subject_token != self.subject_token:
			raise ValueError("AI review subject token does not match the canonical entry.")
		if self.ai_review.subject_type != self.subject_type:
			raise ValueError("AI review subject type does not match the canonical entry.")
		if self.ai_review.network_id != self.source_network_id:
			raise ValueError("AI review network does not match the canonical source network.")
		if not self.analyst_events:
			if self.review_state != CanonicalReviewState.AI_PROPOSED:
				raise ValueError("An unconfirmed entry must remain AI_PROPOSED.")
			if self.effective_decision is not None:
				raise ValueError(
					"An AI proposal cannot be an effective decision before confirmation."
				)
			return self
		last_event = self.analyst_events[-1]
		if self.review_state != CanonicalReviewState.ANALYST_CONFIRMED:
			raise ValueError("An entry with analyst events must be ANALYST_CONFIRMED.")
		if self.effective_decision != last_event.decision:
			raise ValueError("The effective decision must match the latest analyst event.")
		if last_event.request_fingerprint != self.ai_review.request_fingerprint:
			raise ValueError("The latest analyst event refers to stale AI evidence.")
		return self


class CanonicalLedgerSnapshot(ReviewStrictModel):
	ledger_version: Literal["2.0.0"] = REVIEW_ENGINE_VERSION
	data_snapshot_id: str = Field(min_length=1)
	entries: list[CanonicalDecisionEntry]


class ReviewNodeState(ReviewStrictModel):
	node_id: str
	node_type: GraphNodeType
	node_token: str
	graph_depth: int = Field(ge=0)
	node_layer: int = Field(ge=0)
	is_seed_customer: bool
	deterministic_identity_keep: bool
	deterministic_expansion_decision: str | None
	was_expanded: bool | None
	predecessor_node_ids: list[str]
	forward_child_node_ids: list[str]
	forward_child_count: int = Field(ge=0)
	status: ReviewNodeStatus
	reached: bool
	expands: bool
	requires_ai_review: bool
	requires_analyst_review: bool
	analyst_review_complete: bool
	canonical_key: str | None
	ai_decision: ReviewDecision | None
	ai_confidence: ReviewConfidence | None
	effective_decision: ReviewDecision | None


class NetworkReviewSnapshot(ReviewStrictModel):
	engine_version: Literal["2.0.0"] = REVIEW_ENGINE_VERSION
	data_snapshot_id: str
	network_id: str
	seed_node_id: str
	nodes: list[ReviewNodeState]
	active_relationship_ids: list[str]
	pending_relationship_ids: list[str]
	pruned_relationship_ids: list[str]
	next_ai_subject_tokens: list[str]
	seed_keep_count: int = Field(ge=0)
	identity_keep_count: int = Field(ge=0)
	awaiting_ai_count: int = Field(ge=0)
	awaiting_analyst_count: int = Field(ge=0)
	confirmed_keep_count: int = Field(ge=0)
	confirmed_prune_count: int = Field(ge=0)
	pending_upstream_node_count: int = Field(ge=0)
	blocked_node_count: int = Field(ge=0)
	reached_node_count: int = Field(ge=0)
	reviewable_node_count: int = Field(ge=0)
	traversal_complete: bool
