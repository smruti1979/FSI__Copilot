"""
FSI Risk, Fraud & Regulatory Intelligence Copilot
Phase 1: Synthetic Data Generation

Approach:
  1. Generate a small "seed" dataset of NORMAL banking behavior using Faker.
     Customer + account attributes are combined into one "accounts" table
     (see note in synthesize() for why -- SDV's free HMASynthesizer only
     supports 2-table relationship chains).
  2. Train an SDV multi-table synthesizer on it to learn realistic distributions
     and referential structure (account -> transaction).
  3. Sample a larger synthetic "normal" dataset from the trained model.
  4. Deliberately inject labeled fraud/AML patterns into the synthetic output
     (structuring, layering, velocity spikes) -- GANs smooth out rare patterns,
     so we add them back in a controlled, labeled way.

Output: two CSVs in ./output/
  - accounts_synthetic.csv      (customer + account attributes)
  - transactions_synthetic.csv  (includes a `fraud_pattern` label column)
"""

import os
import random
import uuid
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from faker import Faker

RNG_SEED = 42
random.seed(RNG_SEED)
np.random.seed(RNG_SEED)
fake = Faker()
Faker.seed(RNG_SEED)

OUTPUT_DIR = "output"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# --------------------------------------------------------------------------
# CONFIG - tweak these to control dataset size
# --------------------------------------------------------------------------
N_SEED_CUSTOMERS = 150
N_SEED_TRANSACTIONS = 3000
SYNTH_SCALE = 5          # multiplier for how much bigger the final synthetic set is
N_FRAUD_RINGS = 8        # number of injected structuring/layering scenarios
N_VELOCITY_SPIKES = 6    # number of injected velocity-spike accounts

TRANSACTION_TYPES = ["ACH_TRANSFER", "WIRE", "CASH_DEPOSIT", "CASH_WITHDRAWAL", "CARD_PAYMENT", "UPI"]
CHANNELS = ["MOBILE", "BRANCH", "ATM", "ONLINE", "AGENT"]
COUNTRIES = ["IN", "US", "AE", "SG", "GB"]
ACCOUNT_TYPES = ["SAVINGS", "CURRENT", "NBFC_LOAN", "CREDIT_CARD"]


# --------------------------------------------------------------------------
# STEP A: Build a small seed dataset of NORMAL behavior
#
# Note: customer and account info are combined into ONE "accounts" table
# (rather than separate customers + accounts tables). SDV's free
# HMASynthesizer only supports 2-table relationship chains, and
# customers -> accounts -> transactions would be a 3-table chain. Merging
# customer attributes directly onto the accounts table (a very normal way
# to model this in a real warehouse) keeps us at accounts -> transactions,
# a supported 2-table chain.
# --------------------------------------------------------------------------
def make_seed_accounts(n_customers):
    rows = []
    for _ in range(n_customers):
        customer_id = str(uuid.uuid4())
        name = fake.name()
        age = random.randint(18, 75)
        occupation = fake.job()
        country = random.choice(COUNTRIES)
        risk_rating = random.choices(["LOW", "MEDIUM", "HIGH"], weights=[0.75, 0.20, 0.05])[0]
        # each customer has 1-2 accounts
        for _ in range(random.randint(1, 2)):
            rows.append({
                "account_id": str(uuid.uuid4()),
                "customer_id": customer_id,
                "customer_name": name,
                "age": age,
                "occupation": occupation,
                "country": country,
                "risk_rating": risk_rating,
                "account_type": random.choice(ACCOUNT_TYPES),
                "open_date": fake.date_between(start_date="-5y", end_date="-30d"),
                "balance": round(random.uniform(500, 50000), 2),
            })
    return pd.DataFrame(rows)


def make_seed_transactions(accounts_df, n):
    account_ids = accounts_df["account_id"].tolist()
    rows = []
    start = datetime.now() - timedelta(days=180)
    for _ in range(n):
        rows.append({
            "transaction_id": str(uuid.uuid4()),
            "account_id": random.choice(account_ids),
            "counterparty_account_id": random.choice(account_ids),
            "amount": round(np.random.lognormal(mean=5.5, sigma=1.0), 2),  # realistic skewed amounts
            "currency": "USD",
            "timestamp": start + timedelta(
                days=random.randint(0, 180), hours=random.randint(0, 23), minutes=random.randint(0, 59)
            ),
            "transaction_type": random.choice(TRANSACTION_TYPES),
            "channel": random.choice(CHANNELS),
            "country": random.choice(COUNTRIES),
            "fraud_pattern": None,  # normal transaction
        })
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# STEP B: Train SDV on the seed data and sample a larger synthetic set
# --------------------------------------------------------------------------
def synthesize(accounts_df, transactions_df, scale):
    from sdv.multi_table import HMASynthesizer
    from sdv.metadata import MultiTableMetadata

    # SDV doesn't need the label column while learning normal patterns.
    # We also drop counterparty_account_id before training: since it contains
    # the same values as accounts.account_id, SDV's auto-detection treats it
    # as a SECOND relationship to the accounts table, which pushes the schema
    # past HMASynthesizer's supported relationship depth. We add a random
    # valid counterparty back in after sampling instead.
    txn_for_training = transactions_df.drop(columns=["fraud_pattern", "counterparty_account_id"])
    # customer_id is just a descriptive attribute here (not a relationship key,
    # since customers are folded into the accounts table), so drop it too and
    # regenerate it post-sampling to avoid confusing SDV's key detection.
    accounts_for_training = accounts_df.drop(columns=["customer_id"])

    tables = {
        "accounts": accounts_for_training,
        "transactions": txn_for_training,
    }

    metadata = MultiTableMetadata()
    metadata.detect_from_dataframes(tables)
    metadata.update_column("accounts", "account_id", sdtype="id")
    metadata.update_column("transactions", "transaction_id", sdtype="id")
    metadata.update_column("transactions", "account_id", sdtype="id")
    metadata.set_primary_key("accounts", "account_id")
    metadata.set_primary_key("transactions", "transaction_id")
    # detect_from_dataframes() already auto-detected the account->transaction
    # relationship from the matching account_id column names.

    synthesizer = HMASynthesizer(metadata)
    print("Training synthesizer on seed data (this can take a minute)...")
    synthesizer.fit(tables)

    print(f"Sampling synthetic data at {scale}x scale...")
    synthetic = synthesizer.sample(scale=scale)

    synth_accounts = synthetic["accounts"]
    synth_transactions = synthetic["transactions"]

    # Regenerate customer_id: one per account here since customer<->account
    # is folded 1:1 post-synthesis (fine for a synthetic demo dataset).
    synth_accounts["customer_id"] = [str(uuid.uuid4()) for _ in range(len(synth_accounts))]

    synth_transactions["fraud_pattern"] = None
    # We dropped counterparty_account_id before training (see note above), so
    # add it back now by assigning a random valid account to each transaction.
    valid_accounts = synth_accounts["account_id"].tolist()
    synth_transactions["counterparty_account_id"] = [
        random.choice(valid_accounts) for _ in range(len(synth_transactions))
    ]
    return synth_accounts, synth_transactions


# --------------------------------------------------------------------------
# STEP C: Deliberately inject labeled fraud/AML patterns
# --------------------------------------------------------------------------
def inject_structuring(transactions_df, accounts_df, n_rings):
    """Multiple deposits just under the $10,000 reporting threshold within 24h."""
    account_ids = accounts_df["account_id"].tolist()
    injected = []
    for i in range(n_rings):
        acct = random.choice(account_ids)
        day = datetime.now() - timedelta(days=random.randint(1, 150))
        n_deposits = random.randint(3, 5)
        for _ in range(n_deposits):
            injected.append({
                "transaction_id": str(uuid.uuid4()),
                "account_id": acct,
                "counterparty_account_id": random.choice(account_ids),
                "amount": round(random.uniform(9000, 9900), 2),
                "currency": "USD",
                "timestamp": day + timedelta(hours=random.randint(0, 20)),
                "transaction_type": "CASH_DEPOSIT",
                "channel": "BRANCH",
                "country": "US",
                "fraud_pattern": "STRUCTURING",
            })
    return pd.DataFrame(injected)


def inject_layering(transactions_df, accounts_df, n_chains):
    """Rapid transfer chains through multiple accounts to obscure fund origin."""
    account_ids = accounts_df["account_id"].tolist()
    injected = []
    for i in range(n_chains):
        chain_len = random.randint(4, 6)
        chain_accounts = random.sample(account_ids, chain_len)
        start_time = datetime.now() - timedelta(days=random.randint(1, 150))
        amount = round(random.uniform(20000, 80000), 2)
        for step in range(chain_len - 1):
            injected.append({
                "transaction_id": str(uuid.uuid4()),
                "account_id": chain_accounts[step],
                "counterparty_account_id": chain_accounts[step + 1],
                "amount": round(amount * random.uniform(0.9, 0.98), 2),  # skims a bit each hop
                "currency": "USD",
                "timestamp": start_time + timedelta(minutes=step * 15),
                "transaction_type": "WIRE",
                "channel": "ONLINE",
                "country": random.choice(COUNTRIES),
                "fraud_pattern": "LAYERING",
            })
    return pd.DataFrame(injected)


def inject_velocity_spikes(transactions_df, accounts_df, n_accounts):
    """Abnormally high number of transactions in a very short window."""
    account_ids = accounts_df["account_id"].tolist()
    injected = []
    for i in range(n_accounts):
        acct = random.choice(account_ids)
        spike_time = datetime.now() - timedelta(days=random.randint(1, 150))
        for _ in range(random.randint(15, 25)):
            injected.append({
                "transaction_id": str(uuid.uuid4()),
                "account_id": acct,
                "counterparty_account_id": random.choice(account_ids),
                "amount": round(random.uniform(50, 2000), 2),
                "currency": "USD",
                "timestamp": spike_time + timedelta(minutes=random.randint(0, 60)),
                "transaction_type": random.choice(TRANSACTION_TYPES),
                "channel": "MOBILE",
                "country": random.choice(COUNTRIES),
                "fraud_pattern": "VELOCITY_SPIKE",
            })
    return pd.DataFrame(injected)


# --------------------------------------------------------------------------
# MAIN
# --------------------------------------------------------------------------
def main():
    print("Step A: generating seed data (normal behavior)...")
    accounts = make_seed_accounts(N_SEED_CUSTOMERS)
    transactions = make_seed_transactions(accounts, N_SEED_TRANSACTIONS)

    print("Step B: training GAN-based synthesizer (SDV) and sampling synthetic data...")
    synth_accounts, synth_transactions = synthesize(accounts, transactions, SYNTH_SCALE)

    print("Step C: injecting labeled fraud/AML patterns...")
    structuring = inject_structuring(synth_transactions, synth_accounts, N_FRAUD_RINGS)
    layering = inject_layering(synth_transactions, synth_accounts, N_FRAUD_RINGS)
    velocity = inject_velocity_spikes(synth_transactions, synth_accounts, N_VELOCITY_SPIKES)

    final_transactions = pd.concat(
        [synth_transactions, structuring, layering, velocity], ignore_index=True
    )
    final_transactions = final_transactions.sort_values("timestamp").reset_index(drop=True)

    # Save outputs
    synth_accounts.to_csv(f"{OUTPUT_DIR}/accounts_synthetic.csv", index=False)
    final_transactions.to_csv(f"{OUTPUT_DIR}/transactions_synthetic.csv", index=False)

    print("\n--- DONE ---")
    print(f"Accounts (incl. customer attributes): {len(synth_accounts)} rows")
    print(f"Transactions: {len(final_transactions)} rows "
          f"({len(structuring)} structuring, {len(layering)} layering, {len(velocity)} velocity rows)")
    print(f"Files written to ./{OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
