"""Generates a synthetic SQLite claims/policy system of record.

Represents the structured half of the knowledge graph: a core policy admin
+ claims system an insurer would already have. Run standalone to (re)build
data/claims.db.
"""

import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "claims.db"

random.seed(42)

INDUSTRIES = [
    "Manufacturing", "Retail", "Construction", "Logistics",
    "Hospitality", "Healthcare", "Technology", "Real Estate",
]

LINES_OF_BUSINESS = ["Property", "General Liability", "Auto", "Workers Comp", "Cyber"]

CAUSES_BY_LOB = {
    "Property": ["Fire", "Water Damage", "Wind/Hail", "Theft", "Vandalism"],
    "General Liability": ["Slip and Fall", "Product Liability", "Property Damage to Third Party"],
    "Auto": ["Collision", "Rear-End Collision", "Theft", "Vandalism"],
    "Workers Comp": ["Slip and Fall", "Equipment Injury", "Repetitive Strain"],
    "Cyber": ["Ransomware", "Data Breach", "Business Email Compromise"],
}

STATUSES = ["Open", "Closed", "Closed", "Closed", "Reopened"]

CITIES = [
    "Chicago, IL", "Austin, TX", "Columbus, OH", "Phoenix, AZ",
    "Charlotte, NC", "Denver, CO", "Atlanta, GA", "Portland, OR",
]

POLICYHOLDER_NAMES = [
    "Meridian Manufacturing Co.", "Brightline Retail Group", "Cascade Construction LLC",
    "Vantage Logistics Inc.", "Harborview Hospitality Group", "Northgate Healthcare Partners",
    "Fieldstone Technology Corp.", "Summit Real Estate Holdings",
]


def build_database() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.executescript(
        """
        CREATE TABLE policyholders (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            industry TEXT NOT NULL,
            since TEXT NOT NULL
        );

        CREATE TABLE policies (
            policy_number TEXT PRIMARY KEY,
            policyholder_id TEXT NOT NULL,
            line_of_business TEXT NOT NULL,
            effective_date TEXT NOT NULL,
            expiry_date TEXT NOT NULL,
            limit_amount INTEGER NOT NULL,
            deductible INTEGER NOT NULL,
            FOREIGN KEY (policyholder_id) REFERENCES policyholders (id)
        );

        CREATE TABLE claims (
            claim_id TEXT PRIMARY KEY,
            policy_number TEXT NOT NULL,
            date_of_loss TEXT NOT NULL,
            status TEXT NOT NULL,
            cause_of_loss TEXT NOT NULL,
            location TEXT NOT NULL,
            incurred_amount INTEGER NOT NULL,
            paid_amount INTEGER NOT NULL,
            reserve_amount INTEGER NOT NULL,
            FOREIGN KEY (policy_number) REFERENCES policies (policy_number)
        );
        """
    )

    policyholders = []
    for i, name in enumerate(POLICYHOLDER_NAMES, start=1):
        ph_id = f"PH-{i:03d}"
        industry = INDUSTRIES[i - 1]
        since = date(2015 + (i % 6), 1 + (i % 12), 1).isoformat()
        policyholders.append((ph_id, name, industry, since))
    cur.executemany("INSERT INTO policyholders VALUES (?, ?, ?, ?)", policyholders)

    policies = []
    policy_lob = {}
    policy_counter = 1
    for ph_id, name, industry, since in policyholders:
        num_policies = random.choice([1, 2, 2, 3])
        lobs = random.sample(LINES_OF_BUSINESS, num_policies)
        for lob in lobs:
            policy_number = f"POL-{policy_counter:04d}"
            policy_counter += 1
            eff = date(2023, random.randint(1, 6), random.randint(1, 28))
            exp = eff.replace(year=eff.year + 1)
            limit_amount = random.choice([500_000, 1_000_000, 2_000_000, 5_000_000])
            deductible = random.choice([5_000, 10_000, 25_000, 50_000])
            policies.append(
                (policy_number, ph_id, lob, eff.isoformat(), exp.isoformat(), limit_amount, deductible)
            )
            policy_lob[policy_number] = (lob, ph_id, name)
    cur.executemany(
        "INSERT INTO policies VALUES (?, ?, ?, ?, ?, ?, ?)", policies
    )

    claims = []
    claim_counter = 1
    policy_numbers = list(policy_lob.keys())

    def add_claim(policy_number, days_ago, cause=None, incurred=None, location=None):
        nonlocal claim_counter
        lob, ph_id, ph_name = policy_lob[policy_number]
        claim_id = f"CLM-{claim_counter:05d}"
        claim_counter += 1
        loss_date = date.today() - timedelta(days=days_ago)
        cause = cause or random.choice(CAUSES_BY_LOB[lob])
        location = location or random.choice(CITIES)
        incurred = incurred if incurred is not None else random.choice(
            [8_000, 15_000, 32_000, 47_500, 61_000, 125_000, 210_000]
        )
        paid_ratio = random.choice([1.0, 1.0, 0.8, 0.6, 0.0])
        paid = int(incurred * paid_ratio)
        reserve = incurred - paid
        status = "Closed" if paid_ratio == 1.0 else random.choice(STATUSES)
        claims.append(
            (claim_id, policy_number, loss_date.isoformat(), status, cause, location, incurred, paid, reserve)
        )
        return claim_id, cause, location

    # Baseline random claims across all policies.
    for policy_number in policy_numbers:
        for _ in range(random.randint(2, 4)):
            add_claim(policy_number, days_ago=random.randint(30, 900))

    # Deliberate signal: a policyholder with a recurring cause (risk trend).
    recurring_policy = policy_numbers[0]
    recurring_cause = CAUSES_BY_LOB[policy_lob[recurring_policy][0]][0]
    for offset in (400, 260, 120, 45):
        add_claim(recurring_policy, days_ago=offset, cause=recurring_cause, location=CITIES[0])

    # Deliberate signal: two near-duplicate claims (same policy, cause, location,
    # close dates, similar amount) for find_similar_claims / fraud-flavor detection.
    dup_policy = policy_numbers[3]
    dup_lob = policy_lob[dup_policy][0]
    dup_cause = CAUSES_BY_LOB[dup_lob][0]
    add_claim(dup_policy, days_ago=20, cause=dup_cause, incurred=54_000, location=CITIES[2])
    add_claim(dup_policy, days_ago=17, cause=dup_cause, incurred=52_500, location=CITIES[2])

    cur.executemany(
        "INSERT INTO claims VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", claims
    )

    conn.commit()
    conn.close()

    print(f"Built {DB_PATH} with {len(policyholders)} policyholders, "
          f"{len(policies)} policies, {len(claims)} claims.")


if __name__ == "__main__":
    build_database()
