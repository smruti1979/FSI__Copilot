# Risk, Fraud & Regulatory Intelligence Copilot

A Snowflake Cortex-based copilot for banking/NBFC compliance teams. Combines
synthetic transaction data, governed natural-language querying (Cortex
Analyst), validated fraud/AML detection logic, and an agentic orchestration
layer (CoCo CLI) to take findings from **signal → evidence → audit-ready
report**.

Built for the *FSI: Risk, Fraud & Regulatory Intelligence Copilot* use case.
Judging focus: real-world relevance, technical execution, solution
completeness.

## Highlights

- **Validated detection, not label lookups.** Structuring, layering, and
  velocity-spike rules are implemented as real SQL (rolling time windows,
  recursive chain traversal) and checked against ground-truth labels:
  **100% precision and recall** on all three rules.
- **Governed NL queries.** Cortex Analyst semantic model maps plain-English
  questions to deterministic SQL — including vague questions like *"which
  customers look suspicious"*, which route through real detection output
  rather than an LLM guess.
- **Full pipeline, not a stub.** Synthetic data generation → detection →
  semantic model → agent skill → audit report → dashboard → live
  regulatory lookups (MCP) → Basel liquidity metric (bonus), all wired
  together and tested against a live Snowflake account.

---

## Architecture

```
Synthetic data (GAN/SDV)  →  Snowflake tables (ACCOUNTS, TRANSACTIONS)
        │
        ├─▶ Detection views (STRUCTURING_SIGNALS, VELOCITY_SIGNALS,
        │    LAYERING_SIGNALS, FRAUD_SIGNALS) ── validated vs. ground truth
        │
        ├─▶ Cortex Analyst semantic model (governed NL → SQL)
        │
        ├─▶ Agent skill (fraud-aml-detection) — signal → evidence → finding
        │
        ├─▶ generate_report.py — audit-ready Markdown report (CLI)
        │
        ├─▶ streamlit_app.py — compliance officer dashboard
        │
        ├─▶ MCP (regulatory-fetch) — live regulatory page lookups
        │
        └─▶ BASEL_NSFR_PROXY — illustrative Basel III liquidity metric
```

---

## Repository structure

| File | Purpose |
|---|---|
| `generate_synthetic_data.py` | Generates synthetic accounts + transactions via SDV (GAN-based), with labeled fraud patterns injected for validation. |
| `load_data.sql` | Creates `ACCOUNTS`/`TRANSACTIONS` tables and loads the generated CSVs. |
| `fsi_semantic_model.yaml` | Cortex Analyst semantic model — governed NL-to-SQL mapping. |
| `fraud_detection_views.sql` | Real detection logic: `STRUCTURING_SIGNALS`, `VELOCITY_SIGNALS`, `LAYERING_SIGNALS`, unified `FRAUD_SIGNALS`. |
| `validate_fraud_rules.py` | Validates detection logic against ground-truth labels (run before trusting the SQL rules). |
| `basel_liquidity_metrics.sql` | Illustrative Basel III NSFR proxy (`BASEL_NSFR_PROXY`). |
| `generate_report.py` | CLI orchestration: signal → evidence → audit-ready Markdown report. |
| `streamlit_app.py` | Compliance dashboard (Overview, Findings, Ask a Question, Generate Report). |
| `fraud-aml-detection/SKILL.md` | CoCo CLI agent skill wiring detection views + workflow + MCP lookups together. |
| `demo_script.md` | Demo walkthrough and talking points. |

---

## Prerequisites

- A Snowflake account (trial accounts work, with one caveat — see
  [Known limitations](#known-limitations))
- Python 3.10+
- [Snowflake CLI](https://docs.snowflake.com/en/developer-guide/snowflake-cli/index) (`snow`)
- CoCo CLI (`cortex`) for agent skills and MCP
- VS Code (or any editor) with the Python and Snowflake extensions recommended

---

## Setup

### 1. Environment

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install sdv faker pandas numpy --break-system-packages   # drop the flag if not on Linux
```

### 2. Snowflake CLI connection

```bash
snow connection add
snow connection test
```

To avoid re-qualifying database/schema on every command, set them as
defaults directly in `%USERPROFILE%\.snowflake\connections.toml` (Windows)
or `~/.snowflake/connections.toml` (macOS/Linux), under your connection's
section:

```toml
database = "FSI_COPILOT"
schema = "RAW"
```

Verify with:

```bash
snow sql -q "SELECT CURRENT_DATABASE(), CURRENT_SCHEMA();"
```

---

## Phase 1 — Synthetic data

```bash
python generate_synthetic_data.py
```

Produces `output/accounts_synthetic.csv` and `output/transactions_synthetic.csv`,
with a `fraud_pattern` label column (`STRUCTURING`, `LAYERING`,
`VELOCITY_SPIKE`, or blank) used only for validation — detection logic
never reads this column.

Load into Snowflake:

```bash
snow sql -q "CREATE DATABASE IF NOT EXISTS FSI_COPILOT; CREATE SCHEMA IF NOT EXISTS FSI_COPILOT.RAW;"
snow sql -q "CREATE STAGE IF NOT EXISTS csv_stage;"
snow sql -q "PUT file://<path>/output/accounts_synthetic.csv @csv_stage AUTO_COMPRESS=TRUE;"
snow sql -q "PUT file://<path>/output/transactions_synthetic.csv @csv_stage AUTO_COMPRESS=TRUE;"
snow sql -f load_data.sql
```

---

## Phase 2 — Semantic model

```bash
snow sql -q "CREATE STAGE IF NOT EXISTS semantic_models DIRECTORY = (ENABLE = TRUE);"
snow sql -q "PUT file://<path>/fsi_semantic_model.yaml @semantic_models AUTO_COMPRESS=FALSE OVERWRITE=TRUE;"
```

Then in Snowsight: **AI & ML → Cortex Analyst → Semantic models**, select
the file, and verify the 5 verified queries all show a green checkmark
(no "Invalid semantic model yaml" banner).

> **Note:** every table referenced in a `relationships` block needs a
> declared `primary_key`, and every column referenced in `verified_queries`
> SQL must match a **logical** dimension/measure name (not just a physical
> column name that happens to exist) — see [Troubleshooting](#troubleshooting-notes).

---

## Phase 3 — Fraud/AML detection

```bash
snow sql -f fraud_detection_views.sql
```

Validate before trusting it:

```bash
python validate_fraud_rules.py
```

Expected: 100% precision/recall on `STRUCTURING` and `VELOCITY_SPIKE`, 100%
recall with a small number of expected coincidental false positives on
`LAYERING`. Cross-check directly in Snowflake:

```sql
SELECT t.fraud_pattern AS ground_truth, fs.rule_name AS detected_as, COUNT(*) AS cnt
FROM TRANSACTIONS t
LEFT JOIN FRAUD_SIGNALS fs ON t.transaction_id = fs.transaction_id
WHERE t.fraud_pattern IS NOT NULL OR fs.rule_name IS NOT NULL
GROUP BY t.fraud_pattern, fs.rule_name
ORDER BY t.fraud_pattern;
```

### Register the agent skill

```bash
cortex skill add ./fraud-aml-detection
cortex skill list      # confirm it shows under "Persisted skill directories"
```

---

## Phase 4 — Audit report generation

```bash
python generate_report.py                    # all rules
python generate_report.py --rule STRUCTURING  # filtered
```

Reuses your existing `snow` CLI connection (no separate credentials).
Outputs a timestamped Markdown report to `reports/`, with a Report ID,
per-account findings, supporting transaction evidence, and a regulatory
citation per rule.

---

## Phase 5 — Streamlit dashboard

The app **must** run on Snowflake's **Warehouse Runtime**, not Container
Runtime — Container Runtime requires External Access Integration to
install packages, which trial accounts cannot enable. `snow streamlit
deploy` may default to Container Runtime, so this app is deployed
explicitly via SQL instead:

```bash
snow sql -q "CREATE STAGE IF NOT EXISTS app_stage;"
snow sql -q "PUT file://<path>/streamlit_app.py @app_stage AUTO_COMPRESS=FALSE OVERWRITE=TRUE;"
snow sql -q "CREATE STREAMLIT FSI_COPILOT.RAW.FSI__COPILOT ROOT_LOCATION = '@FSI_COPILOT.RAW.app_stage' MAIN_FILE = 'streamlit_app.py' QUERY_WAREHOUSE = COMPUTE_WH;"
```

Verify it landed on Warehouse Runtime (both fields should be blank/`None`):

```bash
snow sql -q "DESC STREAMLIT FSI_COPILOT.RAW.FSI__COPILOT;"
```

**To update the app after editing `streamlit_app.py`:** re-run only the
`PUT ... OVERWRITE=TRUE` command above. Do **not** use `snow streamlit
deploy --replace` — it recreates the object and silently reverts it to
Container Runtime.

---

## Phase 6 — Live regulatory lookups (MCP)

```bash
pip install mcp-server-fetch
where mcp-server-fetch    # get the absolute path — CoCo CLI doesn't inherit venv PATH
cortex mcp add regulatory-fetch "<absolute path to mcp-server-fetch.exe>" --transport stdio
cortex mcp list            # confirm it connects
```

Test with CoCo CLI: *"Look up FATF Recommendation 20 and tell me what it requires."*

---

## Bonus — Basel liquidity metric

```bash
snow sql -f basel_liquidity_metrics.sql
```

Computes an illustrative NSFR using real Basel III (BCBS 295) weighting
factors applied to `account_type` as a proxy for full balance-sheet
classification. **Not compliance-grade** — see the SQL file's header
comment for full methodology and caveats.

---

## Troubleshooting notes

Issues actually hit while building this, kept here so they don't get
re-discovered the hard way:

- **`snow` CLI "Connection default is not configured"** → run `snow connection add`, then `snow connection test`.
- **`PUT`/`COPY INTO` "no current database"** → each `snow sql -q` call is a fresh session; either fully qualify names or set `database`/`schema` in `connections.toml`.
- **SDV `HMASynthesizer` "schema with more than 5 tables or relationship depth > 2"** → free-tier SDV only supports 2-table chains. Flatten `customers`+`accounts` into one table rather than a 3-table chain.
- **Cortex Analyst "Table X used in join relationship has no primary key"** → every table in a `relationships` block needs a `primary_key.columns` block.
- **Cortex Analyst "invalid identifier" on a verified query column** → the SQL must reference the semantic model's **logical** column/measure names, not just a physical column name — keep naming consistent across tables (e.g. `amount`, not `signal_amount` in one table and `amount` in another).
- **Stale Snowsight editor state** → if a fix doesn't seem to apply, check for a `•` unsaved-changes indicator on the tab; navigate away without saving (or paste corrected YAML directly via Edit YAML) rather than relying on file re-upload alone.
- **PowerShell + nested double quotes hanging (`>>` prompt)** → PowerShell doesn't escape quotes like bash. Write multi-statement SQL to a `.sql` file and run with `snow sql -f file.sql` instead of complex `-q` strings.
- **Detection rule undercounting by ~50%** → a backward-only `RANGE ... PRECEDING AND CURRENT ROW` window only flags the *tail* of a cluster. Use a symmetric self-join (structuring/velocity) or full chain-path capture (layering) to catch every member.
- **Streamlit app "GCS get 404"** → the file uploaded compressed (`.py.gz`) but the app expects the exact filename. Always use `AUTO_COMPRESS=FALSE` when uploading `.py`/`.yaml` files via `PUT`.
- **Streamlit `_snowflake` module not found** → this only exists on **Warehouse Runtime**. `snow streamlit deploy` may default to Container Runtime; create the app explicitly via `CREATE STREAMLIT ... ROOT_LOCATION = ...` (no `RUNTIME_NAME`/`COMPUTE_POOL`) to force Warehouse Runtime.
- **Streamlit "Unsupported statement type 'USE'"** → Warehouse Runtime's session is restricted; don't call `USE DATABASE`/`USE SCHEMA` via `session.sql()`. The app's default context already matches wherever the `STREAMLIT` object was created.
- **`cortex mcp add` "failed to connect"** → CoCo CLI's process doesn't inherit your activated venv's PATH. Use the absolute path to the executable (`where <tool>` to find it), not the bare command name.
- **`CREATE STREAMLIT ... FROM '@stage'` "Missing MAIN_FILE"** → the correct clause is `ROOT_LOCATION = '@stage'`, not `FROM '@stage'`.

---

## Known limitations

- **Basel NSFR is an illustrative proxy**, not a compliance-grade
  calculation — see the caveats in `basel_liquidity_metrics.sql`.
- **Trial Snowflake accounts cannot use External Access Integration**,
  which is why the Streamlit app must run on Warehouse Runtime rather than
  Container Runtime.
- **Synthetic data only** — no real customer/transaction data is used
  anywhere in this project.

---

## License

Add your license of choice here before publishing.
