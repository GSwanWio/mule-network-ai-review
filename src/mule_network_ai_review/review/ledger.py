from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from os import fsync
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

from mule_network_ai_review.ai.models import AIReviewRecord, ReviewDecision, SubjectType
from mule_network_ai_review.review.models import (
	AnalystAction,
	AnalystDecisionEvent,
	CanonicalDecisionEntry,
	CanonicalLedgerSnapshot,
	CanonicalReviewState,
)


class DecisionLedgerError(ValueError):
	pass


class DecisionLedgerConflictError(DecisionLedgerError):
	pass


def canonical_decision_key(
	data_snapshot_id: str,
	subject_type: SubjectType,
	subject_token: str,
) -> str:
	key_material = "|".join(
		(
			data_snapshot_id.strip(),
			subject_type.value,
			subject_token.strip(),
		)
	)
	return sha256(key_material.encode("utf-8")).hexdigest()


class CanonicalDecisionLedger:
	def __init__(
		self,
		data_snapshot_id: str,
		entries: list[CanonicalDecisionEntry] | None = None,
	):
		self.data_snapshot_id = data_snapshot_id.strip()
		if not self.data_snapshot_id:
			raise DecisionLedgerError("data_snapshot_id cannot be blank.")
		self._entries: dict[tuple[SubjectType, str], CanonicalDecisionEntry] = {}
		for entry in entries or []:
			self._load_entry(entry)

	def _entry_key(
		self,
		subject_type: SubjectType,
		subject_token: str,
	) -> tuple[SubjectType, str]:
		token = subject_token.strip()
		if not token:
			raise DecisionLedgerError("subject_token cannot be blank.")
		return subject_type, token

	def _load_entry(self, entry: CanonicalDecisionEntry) -> None:
		if entry.data_snapshot_id != self.data_snapshot_id:
			raise DecisionLedgerError("Ledger entry belongs to a different data snapshot.")
		expected_key = canonical_decision_key(
			entry.data_snapshot_id,
			entry.subject_type,
			entry.subject_token,
		)
		if entry.canonical_key != expected_key:
			raise DecisionLedgerError("Ledger entry has an invalid canonical key.")
		key = self._entry_key(entry.subject_type, entry.subject_token)
		if key in self._entries:
			raise DecisionLedgerConflictError("Ledger contains a duplicate canonical subject.")
		self._entries[key] = entry

	def get(
		self,
		subject_type: SubjectType,
		subject_token: str,
	) -> CanonicalDecisionEntry | None:
		return self._entries.get(self._entry_key(subject_type, subject_token))

	def record_ai_review(self, review: AIReviewRecord) -> CanonicalDecisionEntry:
		key = self._entry_key(review.subject_type, review.subject_token)
		existing = self._entries.get(key)
		if existing is not None:
			if (
				existing.ai_review.openai_response_id == review.openai_response_id
				and existing.ai_review.request_fingerprint == review.request_fingerprint
			):
				return existing
			raise DecisionLedgerConflictError(
				"A canonical AI proposal already exists for this subject and data snapshot."
			)
		entry = CanonicalDecisionEntry(
			canonical_key=canonical_decision_key(
				self.data_snapshot_id,
				review.subject_type,
				review.subject_token,
			),
			data_snapshot_id=self.data_snapshot_id,
			subject_token=review.subject_token,
			subject_type=review.subject_type,
			source_network_id=review.network_id,
			ai_review=review,
		)
		self._entries[key] = entry
		return entry

	def confirm(
		self,
		subject_type: SubjectType | str,
		subject_token: str,
		decision: ReviewDecision | str,
		analyst_reference: str,
		rationale: str,
		request_fingerprint: str,
	) -> CanonicalDecisionEntry:
		resolved_subject_type = SubjectType(subject_type)
		resolved_decision = ReviewDecision(decision)
		key = self._entry_key(resolved_subject_type, subject_token)
		entry = self._entries.get(key)
		if entry is None:
			raise DecisionLedgerError("An AI proposal is required before analyst confirmation.")
		if entry.review_state == CanonicalReviewState.ANALYST_CONFIRMED:
			raise DecisionLedgerConflictError(
				"This subject is already confirmed; use revise for an explicit change."
			)
		if request_fingerprint != entry.ai_review.request_fingerprint:
			raise DecisionLedgerConflictError("Analyst confirmation refers to stale AI evidence.")
		action = (
			AnalystAction.CONFIRM_AI
			if resolved_decision == entry.ai_review.decision.decision
			else AnalystAction.OVERRIDE_AI
		)
		event = self._analyst_event(
			entry=entry,
			action=action,
			decision=resolved_decision,
			analyst_reference=analyst_reference,
			rationale=rationale,
			previous_decision=None,
		)
		confirmed = entry.model_copy(
			update={
				"review_state": CanonicalReviewState.ANALYST_CONFIRMED,
				"effective_decision": resolved_decision,
				"analyst_events": [event],
			}
		)
		confirmed = CanonicalDecisionEntry.model_validate(confirmed.model_dump())
		self._entries[key] = confirmed
		return confirmed

	def revise(
		self,
		subject_type: SubjectType | str,
		subject_token: str,
		decision: ReviewDecision | str,
		analyst_reference: str,
		rationale: str,
		request_fingerprint: str,
	) -> CanonicalDecisionEntry:
		resolved_subject_type = SubjectType(subject_type)
		resolved_decision = ReviewDecision(decision)
		key = self._entry_key(resolved_subject_type, subject_token)
		entry = self._entries.get(key)
		if entry is None or entry.review_state != CanonicalReviewState.ANALYST_CONFIRMED:
			raise DecisionLedgerError("A confirmed canonical decision is required before revision.")
		if request_fingerprint != entry.ai_review.request_fingerprint:
			raise DecisionLedgerConflictError("Analyst revision refers to stale AI evidence.")
		if resolved_decision == entry.effective_decision:
			raise DecisionLedgerError("A revision must change the effective decision.")
		event = self._analyst_event(
			entry=entry,
			action=AnalystAction.REVISE_CONFIRMED,
			decision=resolved_decision,
			analyst_reference=analyst_reference,
			rationale=rationale,
			previous_decision=entry.effective_decision,
		)
		revised = entry.model_copy(
			update={
				"effective_decision": resolved_decision,
				"analyst_events": [*entry.analyst_events, event],
			}
		)
		revised = CanonicalDecisionEntry.model_validate(revised.model_dump())
		self._entries[key] = revised
		return revised

	def _analyst_event(
		self,
		entry: CanonicalDecisionEntry,
		action: AnalystAction,
		decision: ReviewDecision,
		analyst_reference: str,
		rationale: str,
		previous_decision: ReviewDecision | None,
	) -> AnalystDecisionEvent:
		return AnalystDecisionEvent(
			event_id=f"ANL_{uuid4().hex}",
			recorded_at_utc=datetime.now(UTC),
			analyst_reference=analyst_reference.strip(),
			action=action,
			decision=decision,
			rationale=rationale.strip(),
			request_fingerprint=entry.ai_review.request_fingerprint,
			previous_decision=previous_decision,
		)

	def snapshot(self) -> CanonicalLedgerSnapshot:
		entries = sorted(
			self._entries.values(),
			key=lambda entry: (entry.subject_type.value, entry.subject_token),
		)
		return CanonicalLedgerSnapshot(
			data_snapshot_id=self.data_snapshot_id,
			entries=entries,
		)

	def to_json(self, indent: int = 2) -> str:
		return self.snapshot().model_dump_json(indent=indent)

	def save(self, path: str | Path) -> None:
		target_path = Path(path)
		if not target_path.parent.exists():
			raise DecisionLedgerError("The ledger output directory does not exist.")
		temporary_path: Path | None = None
		try:
			with NamedTemporaryFile(
				mode="w",
				encoding="utf-8",
				dir=target_path.parent,
				prefix=f".{target_path.name}.",
				suffix=".tmp",
				delete=False,
			) as temporary_file:
				temporary_path = Path(temporary_file.name)
				temporary_file.write(self.to_json() + "\n")
				temporary_file.flush()
				fsync(temporary_file.fileno())
			temporary_path.replace(target_path)
		except Exception:
			if temporary_path is not None:
				temporary_path.unlink(missing_ok=True)
			raise

	@classmethod
	def load(
		cls,
		path: str | Path,
		expected_data_snapshot_id: str | None = None,
	) -> CanonicalDecisionLedger:
		snapshot = CanonicalLedgerSnapshot.model_validate_json(
			Path(path).read_text(encoding="utf-8")
		)
		if (
			expected_data_snapshot_id is not None
			and snapshot.data_snapshot_id != expected_data_snapshot_id
		):
			raise DecisionLedgerError("Ledger file belongs to a different data snapshot.")
		return cls(snapshot.data_snapshot_id, snapshot.entries)
