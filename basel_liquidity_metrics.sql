-- Run with: snow sql -f basel_liquidity_metrics.sql
-- Assumes database/schema default to FSI_COPILOT.RAW.
--
-- IMPORTANT CAVEAT: This is an ILLUSTRATIVE PROXY, not a compliance-grade
-- Basel calculation. A real NSFR/LCR requires full balance-sheet detail
-- (asset-liability maturity ladders, HQLA asset classification, secured
-- vs unsecured funding splits, off-balance-sheet commitments) that this
-- synthetic dataset does not contain. This view maps the ACCOUNTS table's
-- `account_type` field to the closest standard Basel III category as a
-- reasonable stand-in for demo purposes, using real Basel III weighting
-- factors so the calculation methodology is sound even though the input
-- granularity is simplified.
--
-- Methodology (Net Stable Funding Ratio, per BCBS 295):
--   NSFR = Available Stable Funding (ASF) / Required Stable Funding (RSF)
--   Basel's minimum requirement is 100%.
--
-- Mapping used here:
--   SAVINGS      -> stable retail deposit      -> ASF factor 95%
--   CURRENT      -> less-stable retail deposit -> ASF factor 90%
--   NBFC_LOAN    -> retail/SME loan (an ASSET the bank must fund)
--                   -> RSF factor 85% (Basel's factor for retail/SME loans
--                      with residual maturity >= 1 year; a simplification
--                      since we don't have loan maturity data)
--   CREDIT_CARD  -> revolving unsecured credit exposure (an ASSET)
--                   -> RSF factor 100% ("other assets" bucket, Basel's
--                      conservative default for exposures not otherwise
--                      classified)
--
-- LCR (Liquidity Coverage Ratio) is deliberately NOT computed here: it
-- requires a High-Quality Liquid Assets (HQLA) inventory and a 30-day
-- stressed cash-outflow schedule, neither of which exist in this dataset.
-- Fabricating one would produce a number that looks precise but isn't
-- grounded in anything real.

CREATE OR REPLACE VIEW BASEL_NSFR_PROXY AS
WITH funding_by_type AS (
    SELECT
        account_type,
        SUM(balance) AS total_balance
    FROM ACCOUNTS
    GROUP BY account_type
),
weighted AS (
    SELECT
        account_type,
        total_balance,
        CASE account_type
            WHEN 'SAVINGS' THEN 0.95
            WHEN 'CURRENT' THEN 0.90
            ELSE NULL
        END AS asf_factor,
        CASE account_type
            WHEN 'NBFC_LOAN' THEN 0.85
            WHEN 'CREDIT_CARD' THEN 1.00
            ELSE NULL
        END AS rsf_factor
    FROM funding_by_type
)
SELECT
    SUM(CASE WHEN asf_factor IS NOT NULL THEN total_balance * asf_factor ELSE 0 END) AS available_stable_funding,
    SUM(CASE WHEN rsf_factor IS NOT NULL THEN total_balance * rsf_factor ELSE 0 END) AS required_stable_funding,
    ROUND(
        SUM(CASE WHEN asf_factor IS NOT NULL THEN total_balance * asf_factor ELSE 0 END)
        / NULLIF(SUM(CASE WHEN rsf_factor IS NOT NULL THEN total_balance * rsf_factor ELSE 0 END), 0)
        * 100, 2
    ) AS nsfr_percent,
    100.00 AS basel_minimum_requirement_percent
FROM weighted;

-- Verification
SELECT * FROM BASEL_NSFR_PROXY;

-- Supporting detail, useful for explaining the number to a compliance reviewer
SELECT
    account_type,
    COUNT(*) AS account_count,
    SUM(balance) AS total_balance,
    CASE account_type
        WHEN 'SAVINGS' THEN 'Stable retail deposit (ASF 95%)'
        WHEN 'CURRENT' THEN 'Less-stable retail deposit (ASF 90%)'
        WHEN 'NBFC_LOAN' THEN 'Retail/SME loan asset (RSF 85%)'
        WHEN 'CREDIT_CARD' THEN 'Unsecured revolving credit asset (RSF 100%)'
    END AS basel_classification
FROM ACCOUNTS
GROUP BY account_type
ORDER BY total_balance DESC;
