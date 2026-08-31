-- Run with: snow sql -f load_data.sql
-- Assumes database/schema default to FSI_COPILOT.RAW (set in connections.toml)
-- and that both CSVs are already uploaded to @csv_stage via PUT.

CREATE OR REPLACE TABLE ACCOUNTS (
    account_id      STRING,
    customer_name   STRING,
    age             INT,
    occupation      STRING,
    country         STRING,
    risk_rating     STRING,
    account_type    STRING,
    open_date       DATE,
    balance         FLOAT,
    customer_id     STRING
);

COPY INTO ACCOUNTS
FROM @csv_stage/accounts_synthetic.csv.gz
FILE_FORMAT = (TYPE = CSV SKIP_HEADER = 1 FIELD_OPTIONALLY_ENCLOSED_BY = '"');

CREATE OR REPLACE TABLE TRANSACTIONS (
    transaction_id           STRING,
    account_id               STRING,
    amount                   FLOAT,
    currency                 STRING,
    timestamp                TIMESTAMP_NTZ,
    transaction_type         STRING,
    channel                  STRING,
    country                  STRING,
    fraud_pattern             STRING,
    counterparty_account_id  STRING
);

COPY INTO TRANSACTIONS
FROM @csv_stage/transactions_synthetic.csv.gz
FILE_FORMAT = (TYPE = CSV SKIP_HEADER = 1 FIELD_OPTIONALLY_ENCLOSED_BY = '"');

-- Verification
SELECT COUNT(*) AS account_count FROM ACCOUNTS;
SELECT COUNT(*) AS transaction_count FROM TRANSACTIONS;
SELECT fraud_pattern, COUNT(*) AS cnt FROM TRANSACTIONS GROUP BY fraud_pattern ORDER BY cnt DESC;
