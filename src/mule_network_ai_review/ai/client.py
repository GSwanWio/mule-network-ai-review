from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256

import openai
from openai import OpenAI
from pydantic import ValidationError

from mule_network_ai_review.ai.domain_policy import (
	CounterpartyDomainError,
	validate_counterparty_branch_decision,
	validate_counterparty_decision_language,
)
from mule_network_ai_review.ai.models import (
	AIReviewRecord,
	NodeReviewDecision,
	NodeReviewRequest,
	SubjectType,
)
from mule_network_ai_review.ai.policy import AI_POLICY_VERSION, system_instructions_for


class AIConfigurationError(ValueError):
	pass


class AIReviewError(RuntimeError):
	pass


@dataclass(frozen=True)
class AIClientSettings:
	model: str
	max_output_tokens: int
	timeout_seconds: float
	max_retries: int

	@classmethod
	def from_environment(cls) -> AIClientSettings:
		if not os.getenv("OPENAI_API_KEY", "").strip():
			raise AIConfigurationError("OPENAI_API_KEY is not configured.")
		model = os.getenv("OPENAI_MODEL", "gpt-5.6").strip()
		if not model:
			raise AIConfigurationError("OPENAI_MODEL cannot be blank.")
		max_output_tokens = _environment_int("OPENAI_MAX_OUTPUT_TOKENS", 4000, 512, 5000)
		timeout_seconds = _environment_float("OPENAI_TIMEOUT_SECONDS", 60.0, 5.0, 300.0)
		max_retries = _environment_int("OPENAI_MAX_RETRIES", 0, 0, 2)
		return cls(
			model=model,
			max_output_tokens=max_output_tokens,
			timeout_seconds=timeout_seconds,
			max_retries=max_retries,
		)


def _environment_int(name: str, default: int, minimum: int, maximum: int) -> int:
	raw_value = os.getenv(name, str(default)).strip()
	try:
		value = int(raw_value)
	except ValueError as error:
		raise AIConfigurationError(f"{name} must be an integer.") from error
	if value < minimum or value > maximum:
		raise AIConfigurationError(f"{name} must be between {minimum} and {maximum}.")
	return value


def _environment_float(name: str, default: float, minimum: float, maximum: float) -> float:
	raw_value = os.getenv(name, str(default)).strip()
	try:
		value = float(raw_value)
	except ValueError as error:
		raise AIConfigurationError(f"{name} must be numeric.") from error
	if value < minimum or value > maximum:
		raise AIConfigurationError(f"{name} must be between {minimum} and {maximum}.")
	return value


def _usage_value(usage: object | None, field_name: str) -> int | None:
	value = getattr(usage, field_name, None)
	return int(value) if value is not None else None


class OpenAIReviewClient:
	def __init__(self, settings: AIClientSettings):
		self.settings = settings
		self._client = OpenAI(
			timeout=settings.timeout_seconds,
			max_retries=settings.max_retries,
		)

	def review_node(self, request: NodeReviewRequest) -> AIReviewRecord:
		request_json = request.model_dump_json(exclude_none=True)
		try:
			response = self._client.responses.parse(
				model=self.settings.model,
				input=[
					{
						"role": "system",
						"content": system_instructions_for(
							request.subject.subject_type
						),
					},
					{"role": "user", "content": request_json},
				],
				text_format=NodeReviewDecision,
				store=False,
				max_output_tokens=self.settings.max_output_tokens,
			)
		except ValidationError as error:
			if any(issue.get("type") == "json_invalid" for issue in error.errors()):
				raise AIReviewError(
					"OpenAI returned an incomplete structured decision. "
					"Completed earlier decisions remain saved; continue the run to retry "
					"only the unresolved subject."
				) from error
			raise AIReviewError("OpenAI returned a decision that failed validation.") from error
		except openai.APIError as error:
			status_code = getattr(error, "status_code", None)
			request_id = getattr(error, "request_id", None)
			details = ["OpenAI request failed"]
			if status_code is not None:
				details.append(f"status={status_code}")
			if request_id:
				details.append(f"request_id={request_id}")
			raise AIReviewError("; ".join(details)) from error

		if response.status != "completed":
			reason = getattr(getattr(response, "incomplete_details", None), "reason", None)
			if reason == "max_output_tokens":
				raise AIReviewError(
					"OpenAI reached the structured-output token limit. "
					"Completed earlier decisions remain saved; continue the run to retry "
					"only the unresolved subject."
				)
			suffix = f" reason={reason}" if reason else ""
			raise AIReviewError(f"OpenAI response was not completed.{suffix}")
		decision = response.output_parsed
		if not isinstance(decision, NodeReviewDecision):
			raise AIReviewError("OpenAI response did not contain a parsed node decision.")
		if decision.contract_version != request.contract_version:
			raise AIReviewError("OpenAI response contract version does not match the request.")
		if decision.subject_token != request.subject.subject_token:
			raise AIReviewError("OpenAI response subject token does not match the request.")
		if decision.subject_type != request.subject.subject_type:
			raise AIReviewError("OpenAI response subject type does not match the request.")
		if request.subject.subject_type == SubjectType.COUNTERPARTY:
			if request.counterparty_domain is None:
				raise AIReviewError("Counterparty domain context is missing from the request.")
			if request.counterparty_branch_context is None:
				raise AIReviewError("Counterparty branch context is missing from the request.")
			try:
				validate_counterparty_decision_language(
					request.counterparty_domain,
					decision,
				)
				validate_counterparty_branch_decision(
					request.counterparty_branch_context,
					decision,
				)
			except CounterpartyDomainError as error:
				raise AIReviewError(
					"OpenAI returned a decision that conflicts with the counterparty rail."
				) from error

		usage = getattr(response, "usage", None)
		return AIReviewRecord(
			reviewed_at_utc=datetime.now(UTC),
			policy_version=AI_POLICY_VERSION,
			request_fingerprint=sha256(request_json.encode("utf-8")).hexdigest(),
			network_id=request.network_id,
			subject_token=request.subject.subject_token,
			subject_type=request.subject.subject_type,
			openai_response_id=response.id,
			model=response.model,
			input_tokens=_usage_value(usage, "input_tokens"),
			output_tokens=_usage_value(usage, "output_tokens"),
			total_tokens=_usage_value(usage, "total_tokens"),
			decision=decision,
		)
