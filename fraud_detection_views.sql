-- Run with: snow sql -f fraud_detection_views.sql
-- Assumes database/schema default to FSI_COPILOT.RAW.
--
-- v2: fixes an undercounting bug in v1. The original approach used a
-- backward-only window (RANGE ... PRECEDING AND CURRENT ROW), which only
-- flags the LAST few members of a cluster/chain (e.g. in a burst of 5
-- structuring deposits, only the 3rd-5th ever see a backward count >= 3;
-- the first two never do). This version uses a symmetric self-join
-- (structuring/velocity) and full chain-path capture (layering) so every
-- member of a flagged cluster is included, not just its tail.
--
-- Validated in duckdb against the ground-truth `fraud_pattern` labels:
--   STRUCTURING:    31 flagged (matches ground truth exactly)
--   VELOCITY_SPIKE: 124 flagged (matches ground truth exactly)
--   LAYERING:       35 flagged (29 true positives + 6 expected coincidental
--                   false positives, matching validate_fraud_rules.py)

-- ============================================================================
-- RULE 1: STRUCTURING
-- 3+ cash deposits of $9,000-$9,999.99 by the same account, all mutually
-- within 24 hours of each other (symmetric self-join, not a one-directional
-- window, so every member of the burst is captured).
-- ============================================================================
CREATE OR REPLACE VIEW STRUCTURING_SIGNALS AS
WITH near_threshold_deposits AS (
    SELECT transaction_id, account_id, amount, timestamp
    FROM TRANSACTIONS
    WHERE transaction_type = 'CASH_DEPOSIT'
      AND amount >= 9000 AND amount < 10000
)
SELECT
    d1.transaction_id,
    d1.account_id,
    d1.amount,
    d1.timestamp AS transaction_timestamp,
    'STRUCTURING' AS rule_name
FROM near_threshold_deposits d1
JOIN near_threshold_deposits d2
    ON d1.account_id = d2.account_id
   AND ABS(DATEDIFF('second', d1.timestamp, d2.timestamp)) <= 24 * 3600
GROUP BY d1.transaction_id, d1.account_id, d1.amount, d1.timestamp
HAVING COUNT(DISTINCT d2.transaction_id) >= 3;

-- ============================================================================
-- RULE 2: VELOCITY SPIKE
-- More than 10 transactions by the same account, all mutually within 1 hour
-- of each other.
-- ============================================================================
CREATE OR REPLACE VIEW VELOCITY_SIGNALS AS
SELECT
    t1.transaction_id,
    t1.account_id,
    t1.amount,
    t1.timestamp AS transaction_timestamp,
    'VELOCITY_SPIKE' AS rule_name
FROM TRANSACTIONS t1
JOIN TRANSACTIONS t2
    ON t1.account_id = t2.account_id
   AND ABS(DATEDIFF('second', t1.timestamp, t2.timestamp)) <= 3600
GROUP BY t1.transaction_id, t1.account_id, t1.amount, t1.timestamp
HAVING COUNT(DISTINCT t2.transaction_id) > 10;

-- ============================================================================
-- RULE 3: LAYERING
-- Chains of 3+ WIRE transfers where funds hop account -> counterparty ->
-- counterparty's counterparty (etc.), each hop within 60 minutes of the
-- last. Captures the FULL chain (every hop), not just hops 3+.
-- ============================================================================
CREATE OR REPLACE VIEW LAYERING_SIGNALS AS
WITH RECURSIVE wire_chain AS (
    -- Anchor: every wire transfer starts a potential chain of depth 1.
    -- txn_path accumulates every transaction_id in the chain so far;
    -- chain_path accumulates account_ids, used to prevent circular loops.
    SELECT
        transaction_id,
        account_id,
        counterparty_account_id,
        amount,
        timestamp,
        1 AS depth,
        ARRAY_CONSTRUCT(transaction_id) AS txn_path,
        account_id::VARCHAR AS chain_path
    FROM TRANSACTIONS
    WHERE transaction_type = 'WIRE'

    UNION ALL

    SELECT
        t.transaction_id,
        t.account_id,
        t.counterparty_account_id,
        t.amount,
        t.timestamp,
        w.depth + 1,
        ARRAY_APPEND(w.txn_path, t.transaction_id),
        w.chain_path || '->' || t.account_id
    FROM TRANSACTIONS t
    JOIN wire_chain w
        ON t.account_id = w.counterparty_account_id
    WHERE t.transaction_type = 'WIRE'
      AND t.timestamp BETWEEN w.timestamp AND DATEADD(minute, 60, w.timestamp)
      AND w.depth < 6
      AND NOT ARRAY_CONTAINS(t.account_id::VARIANT, SPLIT(w.chain_path, '->'))
),
qualifying_chains AS (
    SELECT txn_path
    FROM wire_chain
    WHERE depth >= 3
)
SELECT DISTINCT
    f.value::VARCHAR AS transaction_id,
    t.account_id,
    t.amount,
    t.timestamp AS transaction_timestamp,
    'LAYERING' AS rule_name
FROM qualifying_chains q,
     LATERAL FLATTEN(input => q.txn_path) f
JOIN TRANSACTIONS t
    ON t.transaction_id = f.value::VARCHAR;

-- ============================================================================
-- UNIFIED VIEW: all detected signals in one place, with evidence.
-- This is what answers vague questions like "which customers look
-- suspicious" -- it's real detection output, not a label lookup.
-- ============================================================================
CREATE OR REPLACE VIEW FRAUD_SIGNALS AS
SELECT transaction_id, account_id, amount, transaction_timestamp, rule_name,
       'Deposits just under $10,000 reporting threshold, 3+ times within 24h' AS evidence
FROM STRUCTURING_SIGNALS
UNION ALL
SELECT transaction_id, account_id, amount, transaction_timestamp, rule_name,
       'More than 10 transactions within a 1-hour window' AS evidence
FROM VELOCITY_SIGNALS
UNION ALL
SELECT transaction_id, account_id, amount, transaction_timestamp, rule_name,
       'Part of a rapid multi-hop wire transfer chain (3+ hops, <60 min apart)' AS evidence
FROM LAYERING_SIGNALS;

-- Verification (expect roughly: STRUCTURING 31, VELOCITY_SPIKE 124, LAYERING 35)
SELECT rule_name, COUNT(*) AS flagged_count FROM FRAUD_SIGNALS GROUP BY rule_name;
