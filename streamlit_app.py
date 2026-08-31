"""
FSI Risk, Fraud & Regulatory Intelligence Copilot -- Compliance Dashboard
Phase 5: Streamlit in Snowflake app.

Deploy this via Snowsight (Streamlit apps) or `snow streamlit deploy`.
Runs INSIDE Snowflake, so it uses the active Snowpark session directly --
no separate credentials needed.

Tabs:
  1. Overview   -- headline metrics + breakdown by rule
  2. Findings   -- filterable, evidence-backed signal table
  3. Ask a Question -- natural language via Cortex Analyst
  4. Generate Report -- produces the same audit-ready Markdown report
                        as generate_report.py, downloadable from the browser
"""

import json
import uuid
from datetime import datetime, timezone

import pandas as pd
import streamlit as st
from snowflake.snowpark.context import get_active_session

st.set_page_config(page_title="FSI Risk & Fraud Copilot", layout="wide")

session = get_active_session()

DATABASE = "FSI_COPILOT"
SCHEMA = "RAW"
# Note: no USE DATABASE/SCHEMA here -- Warehouse Runtime doesn't permit
# session-context-switching statements via session.sql(). This app's
# default context is already FSI_COPILOT.RAW since that's where the
# STREAMLIT object itself was created, so all unqualified table/view names
# below (ACCOUNTS, FRAUD_SIGNALS, etc.) resolve correctly without it.

SEMANTIC_MODEL_STAGE_PATH = f"@{DATABASE}.{SCHEMA}.SEMANTIC_MODELS/fsi_semantic_model.yaml"

REGULATORY_BASIS = {
    "STRUCTURING": (
        "Deposits kept just under the USD 10,000 CTR reporting threshold. "
        "Addressed under the Bank Secrecy Act anti-structuring provisions "
        "(31 U.S.C. Section 5324) in the US, with equivalent threshold-based "
        "reporting rules in most jurisdictions (e.g. India's PMLA cash "
        "transaction reporting requirements)."
    ),
    "VELOCITY_SPIKE": (
        "Abnormally high transaction frequency in a short window -- a "
        "standard red flag under FATF Recommendation 10 (Customer Due "
        "Diligence) and local transaction-monitoring frameworks (e.g. RBI's "
        "Master Direction on KYC)."
    ),
    "LAYERING": (
        "Rapid multi-hop fund transfers characteristic of the 'layering' "
        "stage of money laundering, addressed under FATF Recommendation 20 "
        "and local Suspicious Activity/Transaction Report (SAR/STR) "
        "obligations."
    ),
}

st.title("Risk, Fraud & Regulatory Intelligence Copilot")
st.caption("Governed, evidence-backed answers over synthetic banking data")

tab_overview, tab_findings, tab_ask, tab_report = st.tabs(
    ["Overview", "Findings", "Ask a Question", "Generate Report"]
)

# --------------------------------------------------------------------------
# TAB 1: OVERVIEW
# --------------------------------------------------------------------------
with tab_overview:
    st.subheader("Signal summary")

    summary_df = session.sql(
        "SELECT rule_name, COUNT(*) AS signal_count, SUM(amount) AS total_amount "
        "FROM FRAUD_SIGNALS GROUP BY rule_name ORDER BY signal_count DESC"
    ).to_pandas()

    accounts_flagged = session.sql(
        "SELECT COUNT(DISTINCT account_id) AS c FROM FRAUD_SIGNALS"
    ).to_pandas()["C"][0]

    col1, col2, col3 = st.columns(3)
    col1.metric("Accounts flagged", int(accounts_flagged))
    col2.metric("Total signals", int(summary_df["SIGNAL_COUNT"].sum()))
    col3.metric("Total amount involved", f"${summary_df['TOTAL_AMOUNT'].sum():,.0f}")

    st.bar_chart(summary_df.set_index("RULE_NAME")["SIGNAL_COUNT"])
    st.dataframe(summary_df, use_container_width=True)

    st.divider()
    st.subheader("Basel liquidity metric (illustrative proxy)")
    st.caption(
        "Uses real Basel III (BCBS 295) NSFR weighting factors applied to "
        "account_type as a stand-in for full balance-sheet classification. "
        "Not a compliance-grade calculation -- see basel_liquidity_metrics.sql "
        "for full methodology notes."
    )
    nsfr_df = session.sql("SELECT * FROM BASEL_NSFR_PROXY").to_pandas()
    if not nsfr_df.empty:
        nsfr_val = nsfr_df["NSFR_PERCENT"][0]
        min_val = nsfr_df["BASEL_MINIMUM_REQUIREMENT_PERCENT"][0]
        delta = nsfr_val - min_val
        st.metric(
            "NSFR (proxy)",
            f"{nsfr_val:.2f}%",
            delta=f"{delta:+.2f} pts vs. {min_val:.0f}% Basel minimum",
        )
        detail_df = session.sql("""
            SELECT account_type, COUNT(*) AS account_count, SUM(balance) AS total_balance,
                   CASE account_type
                       WHEN 'SAVINGS' THEN 'Stable retail deposit (ASF 95%)'
                       WHEN 'CURRENT' THEN 'Less-stable retail deposit (ASF 90%)'
                       WHEN 'NBFC_LOAN' THEN 'Retail/SME loan asset (RSF 85%)'
                       WHEN 'CREDIT_CARD' THEN 'Unsecured revolving credit asset (RSF 100%)'
                   END AS basel_classification
            FROM ACCOUNTS GROUP BY account_type ORDER BY total_balance DESC
        """).to_pandas()
        st.dataframe(detail_df, use_container_width=True)

# --------------------------------------------------------------------------
# TAB 2: FINDINGS (filterable, evidence-backed)
# --------------------------------------------------------------------------
with tab_findings:
    st.subheader("Flagged transactions")

    rule_filter = st.selectbox(
        "Filter by rule", ["All"] + list(REGULATORY_BASIS.keys())
    )
    risk_filter = st.selectbox("Filter by account risk rating", ["All", "LOW", "MEDIUM", "HIGH"])

    where_clauses = []
    if rule_filter != "All":
        where_clauses.append(f"fs.rule_name = '{rule_filter}'")
    if risk_filter != "All":
        where_clauses.append(f"a.risk_rating = '{risk_filter}'")
    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    findings_df = session.sql(f"""
        SELECT a.account_id, a.customer_name, a.risk_rating, fs.rule_name,
               fs.transaction_id, fs.amount, fs.transaction_timestamp, fs.evidence
        FROM FRAUD_SIGNALS fs
        JOIN ACCOUNTS a ON fs.account_id = a.account_id
        {where_sql}
        ORDER BY fs.transaction_timestamp DESC
    """).to_pandas()

    st.dataframe(findings_df, use_container_width=True)

    if not findings_df.empty:
        selected_rule = findings_df["RULE_NAME"].iloc[0]
        with st.expander("Why does this pattern matter? (regulatory basis)"):
            for rule in findings_df["RULE_NAME"].unique():
                st.markdown(f"**{rule}:** {REGULATORY_BASIS.get(rule, 'N/A')}")

# --------------------------------------------------------------------------
# TAB 3: ASK A QUESTION (Cortex Analyst)
# --------------------------------------------------------------------------
with tab_ask:
    st.subheader("Ask a question in plain English")
    st.caption(
        "Answered by Cortex Analyst against the governed semantic model -- "
        "not a freeform LLM guess."
    )

    question = st.text_input(
        "e.g. 'Which customers look suspicious?' or 'Show structuring transactions this month'"
    )

    if st.button("Ask") and question:
        try:
            import _snowflake
        except ImportError:
            st.error(
                "The `_snowflake` module isn't available. This app must be "
                "deployed on Snowflake's WAREHOUSE runtime for this tab to "
                "work (not Container Runtime, which requires External Access "
                "Integration -- unavailable on trial accounts -- to install "
                "extra packages)."
            )
        else:
            try:
                request_body = {
                    "messages": [{"role": "user", "content": [{"type": "text", "text": question}]}],
                    "semantic_model_file": SEMANTIC_MODEL_STAGE_PATH,
                }
                resp = _snowflake.send_snow_api_request(
                    "POST",
                    "/api/v2/cortex/analyst/message",
                    {},
                    {},
                    request_body,
                    {},
                    30000,
                )
                if resp["status"] < 400:
                    raw_content = resp["content"]
                    content = raw_content if isinstance(raw_content, dict) else json.loads(raw_content)
                    for item in content.get("message", {}).get("content", []):
                        if item.get("type") == "text":
                            st.write(item["text"])
                        elif item.get("type") == "sql":
                            st.code(item["statement"], language="sql")
                            result_df = session.sql(item["statement"]).to_pandas()
                            st.dataframe(result_df, use_container_width=True)
                else:
                    st.error(f"Cortex Analyst returned status {resp['status']}: {resp.get('content')}")
            except Exception as e:
                st.error(f"Error calling Cortex Analyst ({type(e).__name__}): {e}")
                st.exception(e)

# --------------------------------------------------------------------------
# TAB 4: GENERATE REPORT (same logic as generate_report.py, browser-downloadable)
# --------------------------------------------------------------------------
with tab_report:
    st.subheader("Generate an audit-ready finding report")

    report_rule_filter = st.selectbox(
        "Report scope", ["All rules"] + list(REGULATORY_BASIS.keys()), key="report_rule"
    )

    if st.button("Generate report"):
        where_sql = (
            f"WHERE fs.rule_name = '{report_rule_filter}'"
            if report_rule_filter != "All rules" else ""
        )
        rows_df = session.sql(f"""
            SELECT a.account_id, a.customer_name, a.risk_rating, fs.rule_name,
                   fs.transaction_id, fs.amount, fs.transaction_timestamp
            FROM FRAUD_SIGNALS fs
            JOIN ACCOUNTS a ON fs.account_id = a.account_id
            {where_sql}
            ORDER BY a.account_id, fs.rule_name, fs.transaction_timestamp
        """).to_pandas()

        if rows_df.empty:
            st.warning("No signals found for this filter.")
        else:
            report_id = str(uuid.uuid4())
            generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

            lines = [
                "# Fraud & AML Finding Report", "",
                f"**Report ID:** {report_id}",
                f"**Generated:** {generated_at}",
                f"**Accounts flagged:** {rows_df['ACCOUNT_ID'].nunique()}",
                f"**Total signals:** {len(rows_df)}", "", "---", "",
            ]
            for account_id, acct_group in rows_df.groupby("ACCOUNT_ID"):
                sample = acct_group.iloc[0]
                lines.append(f"## Account {account_id}")
                lines.append(f"- **Customer:** {sample['CUSTOMER_NAME']}")
                lines.append(f"- **Risk rating:** {sample['RISK_RATING']}")
                lines.append("")
                for rule_name, rule_group in acct_group.groupby("RULE_NAME"):
                    lines.append(f"### Signal: {rule_name}")
                    lines.append(f"- **Transactions flagged:** {len(rule_group)}")
                    lines.append(f"- **Total amount involved:** ${rule_group['AMOUNT'].sum():,.2f}")
                    lines.append("- **Evidence:**")
                    for _, t in rule_group.iterrows():
                        lines.append(
                            f"  - Transaction `{t['TRANSACTION_ID']}` — "
                            f"${t['AMOUNT']:,.2f} at {t['TRANSACTION_TIMESTAMP']}"
                        )
                    lines.append(f"- **Regulatory basis:** {REGULATORY_BASIS.get(rule_name, 'N/A')}")
                    lines.append("")
                lines.append("---")
                lines.append("")

            report_text = "\n".join(lines)
            st.success(f"Report generated (ID: {report_id})")
            st.download_button(
                "Download report (Markdown)",
                data=report_text,
                file_name=f"finding_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md",
                mime="text/markdown",
            )
            with st.expander("Preview"):
                st.markdown(report_text)