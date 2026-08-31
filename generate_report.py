"""
Phase 4: Orchestrates the full flow -- signal -> evidence -> audit-ready
regulatory finding report.

Pulls detected fraud/AML signals from Snowflake (via the `snow` CLI, reusing
your existing connection -- no separate credentials needed), groups them by
account, attaches a plain-language regulatory citation for each rule type,
and writes a structured Markdown report you can hand to a compliance
officer or attach to a case file.

Usage:
    python generate_report.py
    python generate_report.py --rule STRUCTURING
    python generate_report.py --output reports/my_report.md
"""

import argparse
import json
import subprocess
import sys
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# --------------------------------------------------------------------------
# Regulatory basis for each detection rule. These are general, well-known
# citations (not exact statutory text) meant to point a reviewer to the
# right regulation -- always have compliance/legal confirm exact applicable
# text for your jurisdiction before using this in a real filing.
# --------------------------------------------------------------------------
REGULATORY_BASIS = {
    "STRUCTURING": (
        "Potential structuring activity: deposits kept just under the "
        "USD 10,000 Currency Transaction Report (CTR) threshold. In the "
        "US, this pattern is addressed under the Bank Secrecy Act "
        "anti-structuring provisions (31 U.S.C. Section 5324). Many "
        "jurisdictions have an equivalent threshold-based reporting rule "
        "(e.g. India's Prevention of Money Laundering Act (PMLA) cash "
        "transaction reporting requirements) -- confirm the applicable "
        "local threshold and citation before filing."
    ),
    "VELOCITY_SPIKE": (
        "Abnormally high transaction frequency in a short window is a "
        "standard red flag under FATF Recommendation 10 (Customer Due "
        "Diligence) and most local AML transaction-monitoring frameworks "
        "(e.g. RBI's Master Direction on KYC for Indian banks/NBFCs), "
        "which call for enhanced monitoring of accounts with unusual "
        "activity relative to the customer's normal profile."
    ),
    "LAYERING": (
        "Rapid multi-hop fund transfers across accounts are characteristic "
        "of the 'layering' stage of money laundering, addressed under FATF "
        "Recommendation 20 (reporting of suspicious transactions) and "
        "corresponding local Suspicious Activity/Transaction Report (SAR/STR) "
        "obligations."
    ),
}


def run_snow_query_as_json(query: str):
    """Runs a query via the `snow` CLI and returns parsed JSON rows.
    Reuses whatever connection/database/schema is already configured as
    default, so no separate credentials are needed here."""
    result = subprocess.run(
        ["snow", "sql", "-q", query, "--format", "json"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print("ERROR running query via snow CLI:", file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        sys.exit(1)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        print("Could not parse snow CLI output as JSON. Raw output:", file=sys.stderr)
        print(result.stdout, file=sys.stderr)
        print(
            "\nIf your snow CLI version doesn't support --format json, "
            "check `snow sql --help` for the correct flag name and update "
            "run_snow_query_as_json() accordingly.",
            file=sys.stderr,
        )
        sys.exit(1)


def fetch_signals(rule_filter=None):
    where_clause = f"WHERE fs.rule_name = '{rule_filter}'" if rule_filter else ""
    query = f"""
        SELECT
            a.account_id, a.customer_name, a.risk_rating,
            fs.rule_name, fs.transaction_id, fs.amount,
            fs.transaction_timestamp, fs.evidence
        FROM FRAUD_SIGNALS fs
        JOIN ACCOUNTS a ON fs.account_id = a.account_id
        {where_clause}
        ORDER BY a.account_id, fs.rule_name, fs.transaction_timestamp;
    """
    return run_snow_query_as_json(query)


def build_report(rows):
    report_id = str(uuid.uuid4())
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Group by account, then by rule
    by_account = defaultdict(lambda: defaultdict(list))
    for row in rows:
        by_account[row["ACCOUNT_ID"]][row["RULE_NAME"]].append(row)

    lines = []
    lines.append(f"# Fraud & AML Finding Report")
    lines.append(f"")
    lines.append(f"**Report ID:** {report_id}")
    lines.append(f"**Generated:** {generated_at}")
    lines.append(f"**Accounts flagged:** {len(by_account)}")
    lines.append(f"**Total signals:** {len(rows)}")
    lines.append(f"")
    lines.append("---")
    lines.append("")

    for account_id, rules in by_account.items():
        sample_row = next(iter(rules.values()))[0]
        lines.append(f"## Account {account_id}")
        lines.append(f"- **Customer:** {sample_row['CUSTOMER_NAME']}")
        lines.append(f"- **Risk rating:** {sample_row['RISK_RATING']}")
        lines.append("")

        for rule_name, txns in rules.items():
            total_amount = sum(float(t["AMOUNT"]) for t in txns)
            lines.append(f"### Signal: {rule_name}")
            lines.append(f"- **Transactions flagged:** {len(txns)}")
            lines.append(f"- **Total amount involved:** ${total_amount:,.2f}")
            lines.append(f"- **Evidence:**")
            for t in txns:
                lines.append(
                    f"  - Transaction `{t['TRANSACTION_ID']}` — "
                    f"${float(t['AMOUNT']):,.2f} at {t['TRANSACTION_TIMESTAMP']}"
                )
            lines.append(f"- **Regulatory basis:** {REGULATORY_BASIS.get(rule_name, 'N/A')}")
            lines.append("")

        lines.append("---")
        lines.append("")

    return "\n".join(lines), report_id


def main():
    parser = argparse.ArgumentParser(description="Generate an audit-ready fraud/AML finding report.")
    parser.add_argument("--rule", choices=list(REGULATORY_BASIS.keys()), default=None,
                         help="Filter to a single rule type (default: all)")
    parser.add_argument("--output", default=None, help="Output file path (default: reports/<timestamp>.md)")
    args = parser.parse_args()

    print("Fetching signals and evidence from Snowflake...")
    rows = fetch_signals(rule_filter=args.rule)

    if not rows:
        print("No signals found for the given filter. Nothing to report.")
        return

    report_text, report_id = build_report(rows)

    output_path = args.output or f"reports/finding_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(report_text, encoding="utf-8")

    print(f"\nReport generated: {output_path}")
    print(f"Report ID: {report_id}")
    print(f"Accounts flagged: {len(set(r['ACCOUNT_ID'] for r in rows))}")
    print(f"Total signals: {len(rows)}")


if __name__ == "__main__":
    main()
