from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from mule_network_ai_review.ai import (
	AIReviewError,
	AIReviewRecord,
	OpenAIReviewClient,
	ReviewDecision,
	SubjectType,
)
from mule_network_ai_review.ingestion import WorkbookPackage
from mule_network_ai_review.review import (
	BreadthFirstReviewEngine,
	CanonicalDecisionEntry,
	CanonicalDecisionLedger,
	CanonicalReviewState,
	DecisionLedgerError,
	NetworkReviewSnapshot,
)


class ReviewWorkspaceError(RuntimeError):
	pass


MAX_AI_CALLS_PER_DISCOVERY_RUN = 50
MAX_CONCURRENT_AI_CALLS = 3


class AIDiscoveryStopReason(StrEnum):
	CONVERGED = "CONVERGED"
	CALL_LIMIT_REACHED = "CALL_LIMIT_REACHED"


@dataclass(frozen=True)
class AIDiscoveryRunResult:
	records: tuple[AIReviewRecord, ...]
	stop_reason: AIDiscoveryStopReason
	snapshot: NetworkReviewSnapshot


class AnalystReviewWorkspace:
	def __init__(
		self,
		package: WorkbookPackage,
		ledger_path: str | Path,
	):
		self.package = package
		self.ledger_path = Path(ledger_path)
		self.ledger = self._load_ledger()

	@property
	def data_snapshot_id(self) -> str:
		return self.package.validation_summary.export_run_id

	def engine(self, network_id: str) -> BreadthFirstReviewEngine:
		return BreadthFirstReviewEngine(
			package=self.package,
			network_id=network_id,
			ledger=self.ledger,
		)

	def snapshot(self, network_id: str) -> NetworkReviewSnapshot:
		return self.engine(network_id).snapshot()

	def run_ai_discovery(
		self,
		network_id: str,
		client: OpenAIReviewClient,
		max_calls: int,
		on_record: Callable[[AIReviewRecord, int], None] | None = None,
	) -> AIDiscoveryRunResult:
		if max_calls < 1 or max_calls > MAX_AI_CALLS_PER_DISCOVERY_RUN:
			raise ReviewWorkspaceError(
				f"max_calls must be between 1 and {MAX_AI_CALLS_PER_DISCOVERY_RUN}."
			)
		engine = self.engine(network_id)
		records: list[AIReviewRecord] = []
		while len(records) < max_calls:
			remaining_calls = max_calls - len(records)
			requests = engine.next_ai_requests(max_calls=min(remaining_calls, 10))
			if not requests:
				break
			wave_errors: list[Exception] = []
			if len(requests) == 1:
				try:
					wave_records = [client.review_node(requests[0])]
				except Exception as error:
					wave_records = []
					wave_errors.append(error)
			else:
				wave_records = []
				with ThreadPoolExecutor(
					max_workers=min(MAX_CONCURRENT_AI_CALLS, len(requests)),
					thread_name_prefix="mule-ai-review",
				) as executor:
					futures = [
						executor.submit(client.review_node, request)
						for request in requests
					]
					for future in as_completed(futures):
						try:
							wave_records.append(future.result())
						except Exception as error:
							wave_errors.append(error)
			for record in wave_records:
				self.ledger.record_ai_review(record)
				self.save()
				records.append(record)
				if on_record is not None:
					on_record(record, len(records))
			if wave_errors:
				first_error = wave_errors[0]
				if isinstance(first_error, AIReviewError):
					raise first_error
				raise ReviewWorkspaceError(
					"An AI assessment failed after completed decisions were saved."
				) from first_error
		snapshot = engine.snapshot()
		if not records and snapshot.traversal_complete:
			return AIDiscoveryRunResult(
				records=(),
				stop_reason=AIDiscoveryStopReason.CONVERGED,
				snapshot=snapshot,
			)
		if not records:
			raise ReviewWorkspaceError("No reached node currently requires an AI assessment.")
		stop_reason = (
			AIDiscoveryStopReason.CONVERGED
			if snapshot.traversal_complete
			else AIDiscoveryStopReason.CALL_LIMIT_REACHED
		)
		return AIDiscoveryRunResult(
			records=tuple(records),
			stop_reason=stop_reason,
			snapshot=snapshot,
		)

	def confirm_analyst_decision(
		self,
		network_id: str,
		subject_type: SubjectType,
		subject_token: str,
		decision: ReviewDecision,
		analyst_reference: str,
		rationale: str,
		request_fingerprint: str,
	) -> NetworkReviewSnapshot:
		analyst = analyst_reference.strip()
		reason = rationale.strip()
		if not analyst:
			raise ReviewWorkspaceError("An analyst reference is required.")
		if not reason:
			raise ReviewWorkspaceError("An analyst rationale is required.")
		snapshot = self.engine(network_id).confirm_analyst_decision(
			subject_type=subject_type,
			subject_token=subject_token,
			decision=decision,
			analyst_reference=analyst,
			rationale=reason,
			request_fingerprint=request_fingerprint,
		)
		self.save()
		return snapshot

	def revise_analyst_decision(
		self,
		network_id: str,
		subject_type: SubjectType,
		subject_token: str,
		decision: ReviewDecision,
		analyst_reference: str,
		rationale: str,
		request_fingerprint: str,
	) -> NetworkReviewSnapshot:
		analyst = analyst_reference.strip()
		reason = rationale.strip()
		if not analyst:
			raise ReviewWorkspaceError("An analyst reference is required.")
		if not reason:
			raise ReviewWorkspaceError("An analyst rationale is required.")
		snapshot = self.engine(network_id).revise_analyst_decision(
			subject_type=subject_type,
			subject_token=subject_token,
			decision=decision,
			analyst_reference=analyst,
			rationale=reason,
			request_fingerprint=request_fingerprint,
		)
		self.save()
		return snapshot

	def awaiting_analyst_entries(
		self,
		network_id: str,
	) -> list[CanonicalDecisionEntry]:
		snapshot = self.snapshot(network_id)
		entries = []
		for node in snapshot.nodes:
			if not node.requires_analyst_review:
				continue
			if node.node_type.value not in {
				SubjectType.CUSTOMER.value,
				SubjectType.COUNTERPARTY.value,
			}:
				continue
			entry = self._entry_for_node(node.node_type.value, node.node_token)
			if entry is not None and entry.review_state == CanonicalReviewState.AI_PROPOSED:
				entries.append(entry)
		return sorted(
			entries,
			key=lambda entry: (
				next(
					node.graph_depth
					for node in snapshot.nodes
					if node.node_token == entry.subject_token
				),
				entry.subject_type.value,
				entry.subject_token,
			),
		)

	def pending_analyst_entries(self) -> list[CanonicalDecisionEntry]:
		return [
			entry
			for entry in self.ledger.snapshot().entries
			if entry.review_state == CanonicalReviewState.AI_PROPOSED
		]

	def confirmed_entries(self, network_id: str) -> list[CanonicalDecisionEntry]:
		network_tokens = {
			(node.node_type.value, node.node_token)
			for node in self.snapshot(network_id).nodes
			if node.requires_analyst_review
		}
		return [
			entry
			for entry in self.ledger.snapshot().entries
			if entry.review_state == CanonicalReviewState.ANALYST_CONFIRMED
			and (entry.subject_type.value, entry.subject_token) in network_tokens
		]

	def save(self) -> None:
		self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
		self.ledger.save(self.ledger_path)

	def _load_ledger(self) -> CanonicalDecisionLedger:
		if not self.ledger_path.exists():
			return CanonicalDecisionLedger(self.data_snapshot_id)
		try:
			return CanonicalDecisionLedger.load(
				self.ledger_path,
				expected_data_snapshot_id=self.data_snapshot_id,
			)
		except (OSError, DecisionLedgerError, ValueError) as error:
			raise ReviewWorkspaceError(
				"The existing canonical review ledger is invalid or belongs to an older "
				"review policy. Archive it and start a new ledger before running this version."
			) from error

	def _entry_for_node(
		self,
		node_type: str,
		node_token: str,
	) -> CanonicalDecisionEntry | None:
		if node_type not in {SubjectType.CUSTOMER.value, SubjectType.COUNTERPARTY.value}:
			return None
		return self.ledger.get(SubjectType(node_type), node_token)
