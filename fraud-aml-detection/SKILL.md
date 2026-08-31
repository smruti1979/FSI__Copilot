---
name: fraud-aml-detection
description: >
  Use this skill whenever the user asks about fraud, suspicious activity,
  AML (anti-money-laundering), structuring, layering, smurfing, velocity
  spikes, unusual transaction patterns, or Basel liquidity metrics (NSFR)
  in the FSI Risk & Fraud Copilot project -- including vague questions like
  "which accounts/customers look suspicious" or "is anything unusual going
  on" or "what's our NSFR." This skill provides real, validated detection
  logic and an evidence-backed answer format instead of guessing from raw
  transaction data.
---

# Fraud & AML Detection Skill

## Why this skill exists
Cortex Analyst's semantic model is good at answering questions with a clear
SQL mapping ("show me transactions over $9,000"), but it cannot judge
whether something is *suspicious* on its own -- that requires actual
detection logic. This skill wires that logic in, so vague or judgment-based
questions get real, reproducible answers instead of a semantic-model guess.

## Available detection views
All in `FSI_COPILOT.RAW`, validated against ground-truth labels with 100%
precision and recall (structuring, velocity) and 100% recall / high
precision (layering):

| View                  | Detects                                                                 |
|------------------------|--------------------------------------------------------------------------|
| `STRUCTURING_SIGNALS`  | 3+ cash deposits of $9,000-$9,999.99 by one account within any 24h window |
| `VELOCITY_SIGNALS`     | More than 10 transactions by one account within any 1h window            |
| `LAYERING_SIGNALS`     | Chains of 3+ wire transfers hopping between accounts, <60 min per hop     |
| `FRAUD_SIGNALS`        | All of the above, unified, with an `evidence` column explaining the flag  |
| `BASEL_NSFR_PROXY`     | Illustrative Net Stable Funding Ratio using real Basel III (BCBS 295) weighting factors applied to account_type. **Not compliance-grade** -- always disclose this is a proxy when reporting the number (see basel_liquidity_metrics.sql for full methodology). |

**Always query these views rather than inferring suspicious activity from
your own judgment.** That's what makes results audit-ready and reproducible
across runs -- the same input data always produces the same flagged
transactions.

## Workflow: signal -> evidence -> finding
Follow this order for every fraud/AML question:

1. **Signal** -- Query `FRAUD_SIGNALS` (optionally filtered by `rule_name`,
   `account_id`, or a time range) to find flagged transactions.
2. **Evidence** -- Pull the specific flagged transaction rows and their
   `evidence` text. Join to `ACCOUNTS` for customer context (name,
   `risk_rating`).
3. **Policy basis** -- Two sources are available, use both appropriately:
   - **Static citations** (fast, always available): the general regulatory
     basis for each rule is documented in `generate_report.py`'s
     `REGULATORY_BASIS` dict and in this skill's reference table below.
     Use these by default for routine findings.
   - **Live lookup** (`regulatory-fetch` MCP tool): when a finding needs
     the *current* wording of a regulation, a specific advisory, or a
     citation the user asks to verify/update, use the `regulatory-fetch`
     tool to pull the live page (e.g. a FinCEN advisory, a FATF
     Recommendation, an RBI Master Direction) rather than relying on
     memorized text. Always prefer this when the user explicitly asks to
     "look up," "check the latest," or "verify" a regulatory reference.
4. **Finding** -- Produce a structured answer, in this order: which
   account(s) -> which rule -> supporting transaction IDs/amounts/timestamps
   -> the policy basis (static citation, or live lookup result with the
   source URL).

## Handling vague questions
If asked something ambiguous like "which customers look suspicious" or "is
anything weird happening," do NOT speculate. Run:

```sql
SELECT a.account_id, a.customer_name, a.risk_rating, fs.rule_name,
       COUNT(*) AS signal_count, SUM(fs.amount) AS total_flagged_amount
FROM FRAUD_SIGNALS fs
JOIN ACCOUNTS a ON fs.account_id = a.account_id
GROUP BY a.account_id, a.customer_name, a.risk_rating, fs.rule_name
ORDER BY total_flagged_amount DESC;
```

Present the results as a ranked list grounded in the underlying rule
output -- never as a subjective judgment call.

## Response format (always required)
Every finding must cite:
- `account_id` / `customer_name`
- `rule_name` (which pattern: STRUCTURING, VELOCITY_SPIKE, or LAYERING)
- At least one supporting `transaction_id`
- The transaction `amount`(s) and `timestamp`(s)
- The `evidence` text from `FRAUD_SIGNALS`

Never present a finding without at least one supporting `transaction_id` --
an answer with no evidence is not audit-ready and should not be given.

## Example
**Q:** "Which customers look suspicious?"

**A:** Run the query above, then summarize the top few rows conversationally,
e.g.: "Account ACC-... (customer: ...) shows the highest flagged activity:
3 STRUCTURING signals totaling $28,400, including transaction TXN-... on
[timestamp] for $9,850 -- just under the $10,000 reporting threshold."

## Extending this skill
When adding a new detection rule (e.g., a Basel-metric-based signal), add it
as its own `<RULE>_SIGNALS` view following the same pattern, then union it
into `FRAUD_SIGNALS` with a clear `evidence` string, and add its `rule_name`
to this skill's table above so future questions route through it correctly.

## Live regulatory lookups
The `regulatory-fetch` MCP tool (registered via `cortex mcp add`) can fetch
live regulatory pages -- FinCEN advisories, FATF Recommendations, RBI Master
Directions, etc. Use it when a finding needs current/verifiable wording
rather than the static citations above, and always include the source URL
in the finding when you do.