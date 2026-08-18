import os
from pathlib import Path

import streamlit as st

from mule_network_ai_review.ai import (
	AIClientSettings,
	AIConfigurationError,
	AIReviewError,
	OpenAIReviewClient,
	ReviewDecision,
)
from mule_network_ai_review.ingestion import WorkbookValidationError, load_workbook_package
from mule_network_ai_review.review import (
	CanonicalReviewState,
	DecisionLedgerError,
	GraphNodeType,
	ReviewEngineError,
	ReviewNodeStatus,
)
from mule_network_ai_review.ui import (
	MAX_AI_CALLS_PER_DISCOVERY_RUN,
	AIDiscoveryStopReason,
	AnalystReviewWorkspace,
	ReviewWorkspaceError,
	build_interactive_review_graph,
	build_review_progress,
	default_selected_node_id,
	next_pending_node_id,
	selected_node_id_from_event,
)

DEFAULT_LEDGER_PATH = "data/review_state/canonical_ledger.json"
DEFAULT_AI_CALL_LIMIT = 25


@st.cache_data(show_spinner=False)
def _load_package(workbook_bytes: bytes):
	return load_workbook_package(workbook_bytes)


def _decision_label(decision: ReviewDecision | str) -> str:
	resolved = ReviewDecision(decision)
	return {
		ReviewDecision.SUSPICIOUS_KEEP: "Suspicious — keep and continue",
		ReviewDecision.LEGITIMATE_PRUNE: "Legitimate — prune this branch",
	}[resolved]


def _node_type_label(node_type: GraphNodeType) -> str:
	return {
		GraphNodeType.CUSTOMER: "Customer",
		GraphNodeType.EID: "Emirates ID",
		GraphNodeType.COUNTERPARTY: "Counterparty",
	}[node_type]


def _render_evidence(title: str, evidence: list[str], empty_message: str) -> None:
	st.markdown(f"**{title}**")
	if evidence:
		for item in evidence:
			st.markdown(f"- {item}")
	else:
		st.caption(empty_message)


def _render_ai_decision(entry) -> None:
	ai_decision = entry.ai_review.decision
	proposal_columns = st.columns(3)
	with proposal_columns[0], st.container(border=True):
		st.caption("Protected subject")
		st.code(entry.subject_token, language=None)
	with proposal_columns[1], st.container(border=True):
		st.caption("AI decision")
		st.write(_decision_label(ai_decision.decision))
	with proposal_columns[2], st.container(border=True):
		st.caption("Confidence")
		st.write(ai_decision.confidence.value.title())

	st.markdown("**AI decision reason**")
	st.write(ai_decision.decision_reason)
	evidence_columns = st.columns(3)
	with evidence_columns[0]:
		_render_evidence(
			"Strongest evidence",
			ai_decision.strongest_evidence,
			"None supplied.",
		)
	with evidence_columns[1]:
		_render_evidence(
			"Counter-evidence",
			ai_decision.counter_evidence,
			"None supplied.",
		)
	with evidence_columns[2]:
		_render_evidence(
			"Data-quality limitations",
			ai_decision.data_quality_limitations,
			"None supplied.",
		)


def _network_label(row) -> str:
	return (
		f"{row['network_id']} · {int(row['discovered_nodes']):,} nodes · "
		f"{int(row['discovered_relationships']):,} relationships"
	)


def _review_narrative(snapshot, awaiting_analyst_count: int) -> str:
	if not snapshot.traversal_complete:
		return (
			f"AI traversal has reached **{snapshot.reached_node_count:,}** of "
			f"**{len(snapshot.nodes):,}** deterministic network nodes. "
			f"**{snapshot.awaiting_ai_count:,}** reached nodes still require AI decisions."
		)
	parts = [
		f"AI traversal stopped with **{snapshot.reached_node_count:,}** of "
		f"**{len(snapshot.nodes):,}** deterministic network nodes reached."
	]
	if awaiting_analyst_count:
		parts.append(
			f"**{awaiting_analyst_count:,}** AI decisions remain for analyst review."
		)
	else:
		parts.append("Every reached AI decision has an analyst outcome.")
	if snapshot.blocked_node_count:
		parts.append(
			f"**{snapshot.blocked_node_count:,}** downstream nodes are stopped by "
			"legitimate branch decisions."
		)
	return " ".join(parts)


def _graph_selection_callback(graph_key: str, selection_key: str) -> None:
	selected_node_id = selected_node_id_from_event(st.session_state.get(graph_key))
	if selected_node_id:
		st.session_state[selection_key] = selected_node_id


st.set_page_config(
	page_title="Mule Network AI Review",
	page_icon="🔎",
	layout="wide",
)

st.markdown(
	"""
	<style>
		.block-container {padding-top: 1.5rem; padding-bottom: 3rem; max-width: 1480px;}
		[data-testid="stSidebar"] {background: #f8fafc;}
		[data-testid="stMetric"] {
			background: #ffffff;
			border: 1px solid #e4e7ec;
			border-radius: 14px;
			padding: 12px 15px;
			box-shadow: 0 1px 2px rgba(16, 24, 40, 0.04);
		}
		[data-testid="stMetricValue"] {font-size: 1.65rem;}
		.st-key-review_progress_panel [data-testid="stButton"] button {
			justify-content: flex-start;
			text-align: left;
			border-radius: 10px;
			min-height: 2.7rem;
		}
		.mule-legend {display:flex; flex-wrap:wrap; gap:12px; margin:2px 0 10px;}
		.mule-legend span {
			font-size:12px;
			color:#475467;
			display:inline-flex;
			gap:6px;
			align-items:center;
		}
		.mule-legend i {width:9px; height:9px; border-radius:50%; display:inline-block;}
		.mule-node-heading {color:#667085; font-size:0.85rem; margin-bottom:0.2rem;}
	</style>
	""",
	unsafe_allow_html=True,
)

with st.sidebar:
	st.header("Review setup")
	uploaded_workbook = st.file_uploader(
		"Protected Databricks workbook",
		type=["xlsx"],
		accept_multiple_files=False,
	)
	analyst_reference = st.text_input(
		"Analyst reference",
		help="Use an approved opaque analyst reference. Do not enter personal information.",
	)
	ai_call_limit = st.number_input(
		"Maximum AI assessments per run",
		min_value=1,
		max_value=MAX_AI_CALLS_PER_DISCOVERY_RUN,
		value=DEFAULT_AI_CALL_LIMIT,
		step=1,
		help="The run stops safely at this limit and can continue without repeating decisions.",
	)
	if os.getenv("OPENAI_API_KEY", "").strip():
		st.success("Real AI is configured.")
	else:
		st.warning("OPENAI_API_KEY is not configured.")

st.title("Mule Network AI Review")
st.caption("Protected branch assessment with focused, auditable analyst validation")

if uploaded_workbook is None:
	st.info("Upload the protected workbook in Review setup to begin.")
	st.stop()

try:
	package = _load_package(uploaded_workbook.getvalue())
except WorkbookValidationError as error:
	st.error("The workbook failed validation and was not loaded.")
	for issue in error.issues:
		st.write(f"- {issue}")
	st.stop()

ledger_path = Path(os.getenv("MULE_NETWORK_REVIEW_LEDGER_PATH", DEFAULT_LEDGER_PATH))
try:
	workspace = AnalystReviewWorkspace(package=package, ledger_path=ledger_path)
except ReviewWorkspaceError as error:
	st.error(str(error))
	st.stop()

network_summary = package.sheet("network_summary").copy()
network_summary["discovered_nodes"] = network_summary["discovered_nodes"].astype(int)
network_summary["discovered_relationships"] = network_summary[
	"discovered_relationships"
].astype(int)
network_summary = network_summary.sort_values(
	by=["discovered_nodes", "network_id"],
	ascending=[True, True],
	kind="stable",
)
network_rows = {
	str(row["network_id"]): row
	for _, row in network_summary.iterrows()
}
network_options = list(network_rows)
if st.session_state.get("selected_network_id") not in network_rows:
	st.session_state["selected_network_id"] = network_options[0]

status_column, selector_column = st.columns([1, 3])
with status_column:
	st.success("Workbook validated")
with selector_column:
	selected_network_id = st.selectbox(
		"Network",
		options=network_options,
		format_func=lambda network_id: _network_label(network_rows[network_id]),
		key="selected_network_id",
		label_visibility="collapsed",
	)
selected_summary = network_rows[selected_network_id]

try:
	engine = workspace.engine(selected_network_id)
	snapshot = engine.snapshot()
	awaiting_entries = workspace.awaiting_analyst_entries(selected_network_id)
	confirmed_entries = workspace.confirmed_entries(selected_network_id)
except (ReviewEngineError, DecisionLedgerError, ReviewWorkspaceError, ValueError) as error:
	st.error(f"The selected network could not be prepared for review: {error}")
	st.stop()

flash_message = st.session_state.pop("review_flash", None)
if flash_message:
	st.success(flash_message)

st.subheader("Network review")
st.markdown(_review_narrative(snapshot, len(awaiting_entries)))

confirmed_suspicious_count = sum(
	entry.effective_decision == ReviewDecision.SUSPICIOUS_KEEP
	for entry in confirmed_entries
)
confirmed_legitimate_count = sum(
	entry.effective_decision == ReviewDecision.LEGITIMATE_PRUNE
	for entry in confirmed_entries
)
review_columns = st.columns(5)
review_columns[0].metric("Reached nodes", f"{snapshot.reached_node_count:,}")
review_columns[1].metric("Awaiting AI", f"{snapshot.awaiting_ai_count:,}")
review_columns[2].metric("Analyst review", f"{len(awaiting_entries):,}")
review_columns[3].metric(
	"Analyst outcomes",
	f"{confirmed_suspicious_count + confirmed_legitimate_count:,}",
)
review_columns[4].metric("Blocked downstream", f"{snapshot.blocked_node_count:,}")

st.markdown("### AI network review")
if snapshot.traversal_complete:
	st.success("AI traversal has stopped. The resulting graph is ready for analyst review.")
else:
	st.caption(
		"AI reviews reached nodes breadth-first. Suspicious decisions open the next branch; "
		"legitimate decisions stop it. The graph appears when this run stops."
	)
	if awaiting_entries:
		st.warning(
			f"{len(awaiting_entries):,} valid AI decisions are saved and "
			f"{snapshot.awaiting_ai_count:,} reached nodes still require AI."
		)

run_disabled = (
	not os.getenv("OPENAI_API_KEY", "").strip()
	or snapshot.traversal_complete
	or snapshot.awaiting_ai_count == 0
)
run_label = (
	"Continue AI network review"
	if awaiting_entries or confirmed_entries
	else "Run AI network review"
)
if not snapshot.traversal_complete and st.button(
	run_label,
	type="primary",
	disabled=run_disabled,
	use_container_width=True,
):
	try:
		settings = AIClientSettings.from_environment()
		progress = st.progress(0.0, text="Starting breadth-first AI review...")
		status_message = st.empty()

		def _record_progress(record, call_number: int) -> None:
			progress.progress(
				min(call_number / int(ai_call_limit), 1.0),
				text=f"Completed {call_number} AI node assessment(s).",
			)
			status_message.caption(
				f"Latest protected subject: {record.subject_token} · "
				f"{_decision_label(record.decision.decision)}"
			)

		result = workspace.run_ai_discovery(
			network_id=selected_network_id,
			client=OpenAIReviewClient(settings),
			max_calls=int(ai_call_limit),
			on_record=_record_progress,
		)
		if result.stop_reason == AIDiscoveryStopReason.CONVERGED:
			message = (
				f"AI traversal stopped after {len(result.records)} new assessment(s). "
				"The resulting graph is ready for analyst review."
			)
		else:
			message = (
				f"AI traversal paused safely after {len(result.records)} new assessment(s). "
				"Continue the run to reach convergence."
			)
		st.session_state["review_flash"] = message
		st.rerun()
	except (AIConfigurationError, AIReviewError, ReviewWorkspaceError, ValueError) as error:
		st.error(f"AI traversal stopped with an error: {error}")
		st.info(
			"Every valid decision completed before the error was saved. Continue the run to "
			"retry only unresolved subjects."
		)

if snapshot.traversal_complete:
	progress_items = build_review_progress(snapshot)
	reached_node_ids = {item.node_id for item in progress_items}
	selection_key = f"selected_review_node_id_{selected_network_id}"
	if st.session_state.get(selection_key) not in reached_node_ids:
		st.session_state[selection_key] = default_selected_node_id(snapshot)

	graph_column, progress_column = st.columns([2.25, 1])
	with progress_column:
		st.markdown("### Review progress")
		reviewable_items = [item for item in progress_items if item.requires_analyst_review]
		reviewed_count = sum(item.analyst_review_complete for item in reviewable_items)
		review_total = len(reviewable_items)
		progress_ratio = reviewed_count / review_total if review_total else 1.0
		st.progress(progress_ratio)
		st.caption(f"{reviewed_count:,} of {review_total:,} AI decisions reviewed")
		with st.container(height=470, border=True, key="review_progress_panel"):
			for item in progress_items:
				selected = item.node_id == st.session_state[selection_key]
				if st.button(
					item.button_label,
					key=f"node_list_{selected_network_id}_{item.node_id}",
					type="primary" if selected else "secondary",
					use_container_width=True,
					help=item.node_token,
				):
					st.session_state[selection_key] = item.node_id
				st.caption(f"Layer {item.graph_depth} · {item.status_label}")

	with graph_column:
		st.markdown("### Decision graph")
		st.caption("Click a node to open its decision. Hover for the full protected details.")
		st.markdown(
			"""
			<div class="mule-legend">
				<span><i style="background:#d92d20"></i>Suspicious</span>
				<span><i style="background:#12b76a"></i>Legitimate</span>
				<span><i style="background:#f79009"></i>Review pending</span>
				<span><i style="background:#0891b2"></i>Identity</span>
				<span><i style="background:#1570ef"></i>Selected</span>
			</div>
			""",
			unsafe_allow_html=True,
		)
		graph = build_interactive_review_graph(
			engine=engine,
			snapshot=snapshot,
			selected_node_id=st.session_state[selection_key],
		)
		graph_key = f"decision_graph_{selected_network_id}"
		st.plotly_chart(
			graph.figure,
			use_container_width=True,
			key=graph_key,
			on_select=lambda: _graph_selection_callback(graph_key, selection_key),
			selection_mode="points",
			config={
				"displaylogo": False,
				"scrollZoom": True,
				"modeBarButtonsToRemove": ["select2d", "lasso2d"],
			},
		)
		if graph.truncated:
			st.caption(
				f"Review-focused view: {graph.shown_node_count:,} of "
				f"{graph.total_node_count:,} reached nodes shown."
			)
		elif snapshot.blocked_node_count:
			st.caption(
				f"All {graph.total_node_count:,} reached nodes are shown. "
				f"{snapshot.blocked_node_count:,} blocked descendants remain hidden."
			)

	selected_node_id = st.session_state[selection_key]
	selected_node = next(node for node in snapshot.nodes if node.node_id == selected_node_id)
	entry_by_subject = {
		(entry.subject_type.value, entry.subject_token): entry
		for entry in awaiting_entries + confirmed_entries
	}
	selected_entry = entry_by_subject.get(
		(selected_node.node_type.value, selected_node.node_token)
	)

	st.markdown("---")
	st.markdown(
		f"<div class='mule-node-heading'>Selected {_node_type_label(selected_node.node_type)} "
		f"· graph depth {selected_node.graph_depth}</div>",
		unsafe_allow_html=True,
	)
	st.markdown(f"### {selected_node.node_token}")

	if selected_entry is None:
		if selected_node.status in {
			ReviewNodeStatus.SEED_KEEP,
			ReviewNodeStatus.IDENTITY_KEEP,
		}:
			st.info(
				"This is deterministic network context and does not require an AI or analyst "
				"decision. Select an amber-outlined customer or counterparty to review its AI "
				"assessment."
			)
		else:
			st.info("This selected node has no analyst decision available in the current state.")
	else:
		_render_ai_decision(selected_entry)
		if selected_entry.review_state == CanonicalReviewState.AI_PROPOSED:
			st.markdown("#### Record analyst outcome")
			with st.form(f"analyst_review_{selected_entry.canonical_key}"):
				decision_options = [
					ReviewDecision.SUSPICIOUS_KEEP,
					ReviewDecision.LEGITIMATE_PRUNE,
				]
				ai_decision = selected_entry.ai_review.decision
				analyst_decision = st.radio(
					"Effective analyst decision",
					options=decision_options,
					index=decision_options.index(ai_decision.decision),
					format_func=_decision_label,
					horizontal=True,
				)
				analyst_rationale = st.text_area(
					"Analyst rationale",
					placeholder="Explain why the evidence supports this effective decision.",
				)
				analyst_attestation = st.checkbox(
					"I reviewed the AI reason, evidence, counter-evidence, and data limitations."
				)
				submitted = st.form_submit_button(
					"Record analyst decision",
					type="primary",
					disabled=not analyst_reference.strip(),
					use_container_width=True,
				)
			if submitted:
				if not analyst_rationale.strip():
					st.error("Enter an analyst rationale before recording the decision.")
				elif not analyst_attestation:
					st.error("Complete the evidence attestation before recording the decision.")
				else:
					try:
						updated_snapshot = workspace.confirm_analyst_decision(
							network_id=selected_network_id,
							subject_type=selected_entry.subject_type,
							subject_token=selected_entry.subject_token,
							decision=analyst_decision,
							analyst_reference=analyst_reference,
							rationale=analyst_rationale,
							request_fingerprint=(
								selected_entry.ai_review.request_fingerprint
							),
						)
						next_node_id = next_pending_node_id(
							updated_snapshot,
							selected_node.node_id,
						)
						if next_node_id:
							st.session_state[selection_key] = next_node_id
						if updated_snapshot.traversal_complete:
							message = (
								f"Analyst decision recorded for {selected_entry.subject_token}."
							)
						else:
							message = (
								f"Analyst decision recorded for {selected_entry.subject_token}. "
								"The override opened a branch; continue AI network review."
							)
						st.session_state["review_flash"] = message
						st.rerun()
					except (ReviewWorkspaceError, DecisionLedgerError, ValueError) as error:
						st.error(f"The analyst decision was not recorded: {error}")
		else:
			latest_event = selected_entry.analyst_events[-1]
			st.success(
				f"Reviewed ✓ · Effective outcome: "
				f"{_decision_label(selected_entry.effective_decision)}"
			)
			st.markdown("**Analyst rationale**")
			st.write(latest_event.rationale)
			st.caption(
				f"Recorded {latest_event.recorded_at_utc} · "
				f"Action {latest_event.action.value}"
			)
			with st.expander("Revise this analyst outcome"):
				revised_decision = (
					ReviewDecision.LEGITIMATE_PRUNE
					if selected_entry.effective_decision == ReviewDecision.SUSPICIOUS_KEEP
					else ReviewDecision.SUSPICIOUS_KEEP
				)
				st.write(f"Revised outcome: **{_decision_label(revised_decision)}**")
				with st.form(f"revision_{selected_entry.canonical_key}"):
					revision_rationale = st.text_area(
						"Revision rationale",
						placeholder="Explain why the confirmed decision must change.",
					)
					revision_attestation = st.checkbox(
						"I understand this changes the branch outcome and recalculates traversal."
					)
					revision_submitted = st.form_submit_button(
						"Record decision revision",
						disabled=not analyst_reference.strip(),
						use_container_width=True,
					)
				if revision_submitted:
					if not revision_rationale.strip():
						st.error("Enter a revision rationale before recording the change.")
					elif not revision_attestation:
						st.error("Complete the revision attestation before recording the change.")
					else:
						try:
							updated_snapshot = workspace.revise_analyst_decision(
								network_id=selected_network_id,
								subject_type=selected_entry.subject_type,
								subject_token=selected_entry.subject_token,
								decision=revised_decision,
								analyst_reference=analyst_reference,
								rationale=revision_rationale,
								request_fingerprint=(
									selected_entry.ai_review.request_fingerprint
								),
							)
							if updated_snapshot.traversal_complete:
								message = (
									f"Analyst revision recorded for "
									f"{selected_entry.subject_token}."
								)
							else:
								message = (
									f"Analyst revision recorded for "
									f"{selected_entry.subject_token}. "
									"The revision opened a branch; continue AI review."
								)
							st.session_state["review_flash"] = message
							st.rerun()
						except (
							ReviewWorkspaceError,
							DecisionLedgerError,
							ValueError,
						) as error:
							st.error(f"The analyst revision was not recorded: {error}")

	if not awaiting_entries and confirmed_entries:
		st.success("All reached AI decisions have been reviewed by an analyst.")

with st.expander("Protected package details"):
	summary = package.validation_summary
	detail_columns = st.columns(4)
	detail_columns[0].metric("Workbook networks", f"{summary.network_count:,}")
	detail_columns[1].metric(
		"Selected customers",
		f"{int(selected_summary['discovered_customers']):,}",
	)
	detail_columns[2].metric(
		"Selected counterparties",
		f"{int(selected_summary['discovered_counterparties']):,}",
	)
	detail_columns[3].metric(
		"Deterministic prunes",
		f"{int(selected_summary['pruned_branch_count']):,}",
	)
	st.caption(
		"Only protected identifiers are displayed. The workbook and canonical ledger remain "
		"sensitive and must stay in the approved environment."
	)
	st.caption(f"Snapshot: `{workspace.data_snapshot_id}`")
	st.caption(f"Ledger: `{ledger_path}`")
