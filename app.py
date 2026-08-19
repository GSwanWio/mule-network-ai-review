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
	ReviewEngineError,
	ReviewNodeStatus,
)
from mule_network_ai_review.ui import (
	MAX_AI_CALLS_PER_DISCOVERY_RUN,
	AIDiscoveryStopReason,
	AnalystReviewWorkspace,
	ReviewWorkspaceError,
	build_interactive_review_graph,
	build_node_display_labels,
	build_review_progress,
	decision_explanation,
	decision_label,
	default_selected_node_id,
	next_pending_node_id,
	node_type_label,
	selected_node_id_from_event,
)

DEFAULT_LEDGER_PATH = "data/review_state/canonical_ledger.json"
DEFAULT_AI_CALL_LIMIT = 25


@st.cache_data(show_spinner=False)
def _load_package(workbook_bytes: bytes):
	return load_workbook_package(workbook_bytes)


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
		st.caption("Reference")
		st.code(entry.subject_token, language=None)
	with proposal_columns[1], st.container(border=True):
		st.caption("System recommendation")
		st.write(decision_label(ai_decision.decision))
	with proposal_columns[2], st.container(border=True):
		st.caption("Confidence")
		st.write(ai_decision.confidence.value.title())

	st.markdown("**Why this was recommended**")
	st.write(ai_decision.decision_reason)
	st.caption(decision_explanation(ai_decision.decision))
	evidence_columns = st.columns(3)
	with evidence_columns[0]:
		_render_evidence(
			"What supports this",
			ai_decision.strongest_evidence,
			"No supporting evidence was supplied.",
		)
	with evidence_columns[1]:
		_render_evidence(
			"What lowers concern",
			ai_decision.counter_evidence,
			"No evidence lowering concern was supplied.",
		)
	with evidence_columns[2]:
		_render_evidence(
			"Information to keep in mind",
			ai_decision.data_quality_limitations,
			"No information limitations were supplied.",
		)


def _network_label(row, index: int, total: int) -> str:
	return (
		f"Network {index} of {total} · "
		f"{int(row['discovered_nodes']):,} linked records · "
		f"{int(row['discovered_relationships']):,} connections"
	)


def _graph_selection_callback(graph_key: str, selection_key: str) -> None:
	selected_node_id = selected_node_id_from_event(st.session_state.get(graph_key))
	if selected_node_id:
		st.session_state[selection_key] = selected_node_id


st.set_page_config(
	page_title="Mule Network Review",
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
		.mule-step {
			background:#f8fafc;
			border:1px solid #e4e7ec;
			border-radius:14px;
			padding:14px 16px;
			margin:6px 0 16px;
		}
	</style>
	""",
	unsafe_allow_html=True,
)

with st.sidebar:
	st.header("Start your review")
	uploaded_workbook = st.file_uploader(
		"Choose the network review file",
		type=["xlsx"],
		accept_multiple_files=False,
		help="Use the approved file prepared for this review.",
	)
	analyst_reference = st.text_input(
		"Your analyst reference",
		help="Enter your approved analyst reference. Do not enter personal information.",
	)
	with st.expander("Assessment settings"):
		ai_call_limit = st.number_input(
			"Maximum connections assessed at once",
			min_value=1,
			max_value=MAX_AI_CALLS_PER_DISCOVERY_RUN,
			value=DEFAULT_AI_CALL_LIMIT,
			step=1,
			help=(
				"A larger number may take longer. Completed recommendations are saved."
			),
		)
	if not os.getenv("OPENAI_API_KEY", "").strip():
		st.error("The assessment service is unavailable. Contact support.")

st.title("Mule Network Review")
st.caption(
	"Review the connections found around a confirmed mule account and confirm what needs "
	"further investigation."
)

if uploaded_workbook is None:
	st.info("Choose the network review file from the left to begin.")
	st.stop()

try:
	package = _load_package(uploaded_workbook.getvalue())
except WorkbookValidationError as error:
	st.error("This review file could not be opened. Ask support for a new file.")
	with st.expander("Support details"):
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
network_labels = {
	network_id: _network_label(
		network_rows[network_id],
		index,
		len(network_options),
	)
	for index, network_id in enumerate(network_options, start=1)
}

st.markdown("### 1. Choose a network")
st.caption("Each option is one mule-linked network. Select the network you want to review.")
selected_network_id = st.selectbox(
	"Network to review",
	options=network_options,
	format_func=lambda network_id: network_labels[network_id],
	key="selected_network_id",
)

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

recommendation_count = len(awaiting_entries) + len(confirmed_entries)
reviewed_count = len(confirmed_entries)
review_progress_ratio = (
	reviewed_count / recommendation_count if recommendation_count else 1.0
)

if snapshot.traversal_complete:
	st.markdown("### 2. Check the recommendations")
	if awaiting_entries:
		st.write(
			f"The system prepared **{recommendation_count:,}** recommendations. "
			"Select each highlighted customer or counterparty, review the evidence, then "
			"save your decision."
		)
		st.progress(
			review_progress_ratio,
			text=f"{reviewed_count:,} of {recommendation_count:,} decisions checked",
		)
	else:
		st.success(
			f"Review complete. You checked all {recommendation_count:,} recommendations."
		)
	if snapshot.blocked_node_count:
		st.caption(
			f"{snapshot.blocked_node_count:,} linked records were not followed because an "
			"earlier connection did not need further investigation."
		)
else:
	st.markdown("### 2. Prepare the recommendations")
	st.write(
		"Start the assessment to check the network connections. It will stop following a "
		"connection when the evidence does not support further investigation. Completed "
		"recommendations are saved automatically."
	)
	if awaiting_entries:
		st.info(
			f"{len(awaiting_entries):,} recommendations are already saved. Continue to "
			"finish this network."
		)

run_disabled = (
	not os.getenv("OPENAI_API_KEY", "").strip()
	or snapshot.traversal_complete
	or snapshot.awaiting_ai_count == 0
)
run_label = "Continue assessment" if awaiting_entries else "Start assessment"
if not snapshot.traversal_complete and st.button(
	run_label,
	type="primary",
	disabled=run_disabled,
	width="stretch",
):
	try:
		settings = AIClientSettings.from_environment()
		progress = st.progress(0.0, text="Starting the network assessment...")
		status_message = st.empty()

		def _record_progress(record, call_number: int) -> None:
			progress.progress(
				min(call_number / int(ai_call_limit), 1.0),
				text=f"Prepared {call_number} recommendation(s).",
			)
			status_message.caption(
				f"Latest recommendation: {decision_label(record.decision.decision)}"
			)

		result = workspace.run_ai_discovery(
			network_id=selected_network_id,
			client=OpenAIReviewClient(settings),
			max_calls=int(ai_call_limit),
			on_record=_record_progress,
		)
		if result.stop_reason == AIDiscoveryStopReason.CONVERGED:
			message = (
				f"The assessment prepared {len(result.records)} new recommendation(s). "
				"You can now review the network."
			)
		else:
			message = (
				f"The assessment prepared {len(result.records)} new recommendation(s). "
				"Select Continue assessment to finish the network."
			)
		st.session_state["review_flash"] = message
		st.rerun()
	except (AIConfigurationError, AIReviewError, ReviewWorkspaceError, ValueError) as error:
		st.error(
			"The assessment paused because one recommendation could not be completed. "
			"Your completed work is safe. Select Continue assessment to try again."
		)
		st.info(
			"Only the unfinished connection will be retried. Earlier recommendations will "
			"not run again."
		)
		with st.expander("Support details"):
			st.code(str(error), language=None)

if snapshot.traversal_complete:
	progress_items = build_review_progress(snapshot)
	display_labels = build_node_display_labels(snapshot.nodes)
	reached_node_ids = {item.node_id for item in progress_items}
	selection_key = f"selected_review_node_id_{selected_network_id}"
	if st.session_state.get(selection_key) not in reached_node_ids:
		st.session_state[selection_key] = default_selected_node_id(snapshot)

	graph_column, progress_column = st.columns([2.25, 1])
	with progress_column:
		st.markdown("### Your review checklist")
		reviewable_items = [item for item in progress_items if item.requires_analyst_review]
		reviewed_count = sum(item.analyst_review_complete for item in reviewable_items)
		review_total = len(reviewable_items)
		progress_ratio = reviewed_count / review_total if review_total else 1.0
		st.progress(progress_ratio)
		st.caption(f"{reviewed_count:,} of {review_total:,} decisions checked")
		with st.container(height=470, border=True, key="review_progress_panel"):
			for item in progress_items:
				selected = item.node_id == st.session_state[selection_key]
				if st.button(
					item.button_label,
					key=f"node_list_{selected_network_id}_{item.node_id}",
					type="primary" if selected else "secondary",
					width="stretch",
					help=item.node_token,
				):
					st.session_state[selection_key] = item.node_id
				st.caption(item.status_label)

	with graph_column:
		st.markdown("### Network map")
		st.caption(
			"Select a highlighted customer or counterparty to open its recommendation."
		)
		st.markdown(
			"""
			<div class="mule-legend">
				<span><i style="background:#d92d20"></i>Needs investigation</span>
				<span><i style="background:#12b76a"></i>No further action</span>
				<span><i style="background:#f79009"></i>Waiting for your review</span>
				<span><i style="background:#0891b2"></i>Identity connection</span>
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
			width="stretch",
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
				f"Showing {graph.shown_node_count:,} of "
				f"{graph.total_node_count:,} reviewed network records."
			)
		elif snapshot.blocked_node_count:
			st.caption(
				f"{snapshot.blocked_node_count:,} linked records are hidden because an earlier "
				"connection did not need further investigation."
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
		f"<div class='mule-node-heading'>Selected "
		f"{node_type_label(selected_node.node_type)}</div>",
		unsafe_allow_html=True,
	)
	st.markdown(f"### {display_labels[selected_node.node_id]}")
	st.caption(f"Reference: {selected_node.node_token}")

	if selected_entry is None:
		if selected_node.status in {
			ReviewNodeStatus.SEED_KEEP,
			ReviewNodeStatus.IDENTITY_KEEP,
		}:
			st.info(
				"This record helps explain how the network is connected. You do not need to "
				"make a decision here. Choose an item marked ‘Waiting for your review’."
			)
		else:
			st.info("There is no decision to make for this record right now.")
	else:
		_render_ai_decision(selected_entry)
		if selected_entry.review_state == CanonicalReviewState.AI_PROPOSED:
			st.markdown("#### What is your decision?")
			with st.form(f"analyst_review_{selected_entry.canonical_key}"):
				decision_options = [
					ReviewDecision.SUSPICIOUS_KEEP,
					ReviewDecision.LEGITIMATE_PRUNE,
				]
				ai_decision = selected_entry.ai_review.decision
				analyst_decision = st.radio(
					"Choose one",
					options=decision_options,
					index=decision_options.index(ai_decision.decision),
					format_func=decision_label,
					horizontal=True,
				)
				st.caption(decision_explanation(analyst_decision))
				analyst_rationale = st.text_area(
					"Why did you choose this?",
					placeholder="Summarise the evidence that supports your decision.",
				)
				analyst_attestation = st.checkbox(
					"I have reviewed the recommendation and the supporting information."
				)
				submitted = st.form_submit_button(
					"Save decision and open next",
					type="primary",
					disabled=not analyst_reference.strip(),
					width="stretch",
				)
			if submitted:
				if not analyst_rationale.strip():
					st.error("Explain why you chose this decision before saving.")
				elif not analyst_attestation:
					st.error("Confirm that you reviewed the supporting information.")
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
							message = "Decision saved."
						else:
							message = (
								"Decision saved. This choice opened more linked records. "
								"Continue the assessment before checking the next decision."
							)
						st.session_state["review_flash"] = message
						st.rerun()
					except (ReviewWorkspaceError, DecisionLedgerError, ValueError) as error:
						st.error("Your decision could not be saved. Try again or contact support.")
						with st.expander("Support details"):
							st.code(str(error), language=None)
		else:
			latest_event = selected_entry.analyst_events[-1]
			st.success(
				f"Checked ✓ · Your decision: "
				f"{decision_label(selected_entry.effective_decision)}"
			)
			st.markdown("**Why you chose this**")
			st.write(latest_event.rationale)
			with st.expander("Change this decision"):
				revised_decision = (
					ReviewDecision.LEGITIMATE_PRUNE
					if selected_entry.effective_decision == ReviewDecision.SUSPICIOUS_KEEP
					else ReviewDecision.SUSPICIOUS_KEEP
				)
				st.write(f"New decision: **{decision_label(revised_decision)}**")
				st.caption(decision_explanation(revised_decision))
				with st.form(f"revision_{selected_entry.canonical_key}"):
					revision_rationale = st.text_area(
						"Why are you changing this decision?",
						placeholder="Summarise the evidence that supports the change.",
					)
					revision_attestation = st.checkbox(
						"I understand that this may reveal or hide linked records."
					)
					revision_submitted = st.form_submit_button(
						"Save changed decision",
						disabled=not analyst_reference.strip(),
						width="stretch",
					)
				if revision_submitted:
					if not revision_rationale.strip():
						st.error("Explain why you are changing the decision.")
					elif not revision_attestation:
						st.error("Confirm that you understand the effect of this change.")
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
								message = "Changed decision saved."
							else:
								message = (
									"Changed decision saved. This choice opened more linked "
									"records. Continue the assessment before reviewing them."
								)
							st.session_state["review_flash"] = message
							st.rerun()
						except (
							ReviewWorkspaceError,
							DecisionLedgerError,
							ValueError,
						) as error:
							st.error(
								"The changed decision could not be saved. Try again or "
								"contact support."
							)
							with st.expander("Support details"):
								st.code(str(error), language=None)

	if not awaiting_entries and confirmed_entries:
		st.success("You have checked every recommendation in this network.")
