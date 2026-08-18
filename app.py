import streamlit as st

from mule_network_ai_review.ingestion import WorkbookValidationError, load_workbook_package

st.set_page_config(
	page_title="Mule Network AI Review",
	page_icon="🔎",
	layout="wide",
)

st.title("Mule Network AI Review")
st.caption("Protected live-data package validation")

uploaded_workbook = st.file_uploader(
	"Select the protected Databricks workbook",
	type=["xlsx"],
	accept_multiple_files=False,
)

if uploaded_workbook is None:
	st.info("Select a protected workbook to validate its network and metric contract.")
	st.stop()

try:
	package = load_workbook_package(uploaded_workbook.getvalue())
except WorkbookValidationError as error:
	st.error("The workbook failed validation and was not loaded.")
	for issue in error.issues:
		st.write(f"- {issue}")
	st.stop()

summary = package.validation_summary

st.success("The protected workbook passed validation.")

summary_columns = st.columns(5)
summary_columns[0].metric("Networks", summary.network_count)
summary_columns[1].metric("Customers", summary.customer_node_count)
summary_columns[2].metric("Emirates IDs", summary.eid_node_count)
summary_columns[3].metric("Counterparties", summary.counterparty_node_count)
summary_columns[4].metric("Relationships", summary.relationship_count)

network_summary = package.sheet("network_summary")
network_options = network_summary["network_id"].astype(str).tolist()
selected_network_id = st.selectbox("Network", network_options)
selected_summary = network_summary.loc[
	network_summary["network_id"].astype(str) == selected_network_id
].iloc[0]

st.subheader("Selected network")
network_columns = st.columns(4)
network_columns[0].metric("Discovered customers", int(selected_summary["discovered_customers"]))
network_columns[1].metric("Discovered EIDs", int(selected_summary["discovered_eids"]))
network_columns[2].metric(
	"Discovered counterparties",
	int(selected_summary["discovered_counterparties"]),
)
network_columns[3].metric(
	"Pruned deterministic branches",
	int(selected_summary["pruned_branch_count"]),
)

st.info(
	"The package is ready for the real AI review layer. No simulated AI decision path is enabled."
)
