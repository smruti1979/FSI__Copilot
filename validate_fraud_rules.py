"""
Validates fraud detection rules against the ground-truth fraud_pattern labels
in the synthetic dataset. Run this BEFORE porting the logic to Snowflake SQL,
so you know the rules actually catch what they're supposed to catch.
"""
import pandas as pd

txn = pd.read_csv("output/transactions_synthetic.csv", parse_dates=["timestamp"])
txn = txn.sort_values("timestamp").reset_index(drop=True)


def evaluate(name, detected_ids, truth_label):
    truth_ids = set(txn.loc[txn["fraud_pattern"] == truth_label, "transaction_id"])
    detected_ids = set(detected_ids)
    tp = len(detected_ids & truth_ids)
    fp = len(detected_ids - truth_ids)
    fn = len(truth_ids - detected_ids)
    precision = tp / len(detected_ids) if detected_ids else 0
    recall = tp / len(truth_ids) if truth_ids else 0
    print(f"\n--- {name} ---")
    print(f"Ground truth {truth_label} transactions: {len(truth_ids)}")
    print(f"Rule flagged: {len(detected_ids)} | True positives: {tp} | "
          f"False positives: {fp} | Missed: {fn}")
    print(f"Precision: {precision:.2f}  Recall: {recall:.2f}")


# --------------------------------------------------------------------------
# RULE 1: STRUCTURING
# 3+ cash deposits between $9,000-$9,999.99 by the same account within any
# ROLLING 24-hour window (not a calendar-day bucket, which incorrectly
# splits bursts that cross midnight).
# --------------------------------------------------------------------------
deposits = txn[
    (txn["transaction_type"] == "CASH_DEPOSIT")
    & (txn["amount"] >= 9000)
    & (txn["amount"] < 10000)
].sort_values("timestamp")

structuring_flagged = []
for account_id, group in deposits.groupby("account_id"):
    group = group.sort_values("timestamp")
    times = group["timestamp"].values
    ids = group["transaction_id"].values
    left = 0
    for right in range(len(times)):
        while times[right] - times[left] > pd.Timedelta(hours=24):
            left += 1
        window_size = right - left + 1
        if window_size >= 3:
            structuring_flagged.extend(ids[left:right + 1])

structuring_flagged = list(set(structuring_flagged))
evaluate("STRUCTURING rule", structuring_flagged, "STRUCTURING")


# --------------------------------------------------------------------------
# RULE 2: VELOCITY SPIKE
# More than 10 transactions by the same account within any rolling 1-hour
# window.
# --------------------------------------------------------------------------
velocity_flagged = []
for account_id, group in txn.groupby("account_id"):
    group = group.sort_values("timestamp")
    times = group["timestamp"].values
    ids = group["transaction_id"].values
    # rolling count using a sliding window pointer
    left = 0
    for right in range(len(times)):
        while times[right] - times[left] > pd.Timedelta(hours=1):
            left += 1
        window_size = right - left + 1
        if window_size > 10:
            velocity_flagged.extend(ids[left:right + 1])

velocity_flagged = list(set(velocity_flagged))
evaluate("VELOCITY_SPIKE rule", velocity_flagged, "VELOCITY_SPIKE")


# --------------------------------------------------------------------------
# RULE 3: LAYERING
# Chains of 3+ WIRE transfers where money hops account -> counterparty ->
# counterparty's counterparty, etc., each hop within 60 minutes of the last.
# --------------------------------------------------------------------------
wires = txn[txn["transaction_type"] == "WIRE"].sort_values("timestamp")
# build an index: for a given account_id, the wires it SENT, sorted by time
sent_by_account = {}
for _, row in wires.iterrows():
    sent_by_account.setdefault(row["account_id"], []).append(row)

layering_flagged = set()


def follow_chain(row, depth, chain_txn_ids, visited):
    chain_txn_ids = chain_txn_ids + [row["transaction_id"]]
    if depth >= 3:
        layering_flagged.update(chain_txn_ids)
    next_account = row["counterparty_account_id"]
    if next_account in visited:
        return
    for next_row in sent_by_account.get(next_account, []):
        gap = next_row["timestamp"] - row["timestamp"]
        if pd.Timedelta(0) <= gap <= pd.Timedelta(minutes=60):
            follow_chain(next_row, depth + 1, chain_txn_ids, visited | {next_account})


for _, row in wires.iterrows():
    follow_chain(row, 1, [], {row["account_id"]})

evaluate("LAYERING rule", layering_flagged, "LAYERING")
