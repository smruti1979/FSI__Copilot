SELECT
    t.fraud_pattern AS ground_truth,
    fs.rule_name AS detected_as,
    COUNT(*) AS cnt
FROM TRANSACTIONS t
LEFT JOIN FRAUD_SIGNALS fs ON t.transaction_id = fs.transaction_id
WHERE t.fraud_pattern IS NOT NULL OR fs.rule_name IS NOT NULL
GROUP BY t.fraud_pattern, fs.rule_name
ORDER BY t.fraud_pattern;