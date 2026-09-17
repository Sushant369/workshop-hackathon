"""Generates synthetic loss run reports: one per policyholder, summarizing
their full claim history across all policies. Written as prose text to
data/loss_run_reports/<report_id>.txt.

A loss run report is a distinct document genre from an adjuster narrative:
it's the administrative/financial summary an underwriter requests at
renewal, not a per-incident field note. It legitimately restates claim data
in a different form, which is exactly the kind of second source a real
underwriting knowledge graph needs to reconcile.
"""

import sqlite3
from datetime import date
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "claims.db"
REPORTS_DIR = Path(__file__).resolve().parent.parent / "data" / "loss_run_reports"


def generate_reports() -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    policyholders = [dict(r) for r in cur.execute("SELECT * FROM policyholders")]

    count = 0
    for ph in policyholders:
        policies = [
            dict(r) for r in cur.execute(
                "SELECT * FROM policies WHERE policyholder_id = ?", (ph["id"],)
            )
        ]
        policy_numbers = [p["policy_number"] for p in policies]
        if not policy_numbers:
            continue

        placeholders = ",".join("?" * len(policy_numbers))
        claims = [
            dict(r) for r in cur.execute(
                f"SELECT * FROM claims WHERE policy_number IN ({placeholders}) "
                "ORDER BY date_of_loss DESC",
                policy_numbers,
            )
        ]
        if not claims:
            continue

        report_id = f"LRR-{ph['id']}-{date.today().year}"
        total_incurred = sum(c["incurred_amount"] for c in claims)
        total_paid = sum(c["paid_amount"] for c in claims)
        total_reserve = sum(c["reserve_amount"] for c in claims)
        cause_counts: dict = {}
        for c in claims:
            cause_counts[c["cause_of_loss"]] = cause_counts.get(c["cause_of_loss"], 0) + 1
        top_cause, top_cause_count = max(cause_counts.items(), key=lambda x: x[1])

        lines = [
            "LOSS RUN REPORT",
            f"Report ID: {report_id}",
            f"Policyholder: {ph['name']} ({ph['id']})",
            f"Industry: {ph['industry']}",
            f"Policies: {', '.join(f'{p['policy_number']} ({p['line_of_business']})' for p in policies)}",
            f"Generated: {date.today().isoformat()}",
            "",
            "SUMMARY",
            (
                f"This account has {len(claims)} claims on record across "
                f"{len(policies)} polic{'y' if len(policies) == 1 else 'ies'}, "
                f"totaling ${total_incurred:,} incurred (${total_paid:,} paid, "
                f"${total_reserve:,} reserved). The most frequent cause of loss "
                f"is {top_cause}, accounting for {top_cause_count} of {len(claims)} claims."
            ),
            "",
            "CLAIM DETAIL",
        ]
        for c in claims:
            lines.append(
                f"Claim {c['claim_id']} on policy {c['policy_number']}: a {c['cause_of_loss']} "
                f"loss on {c['date_of_loss']} at {c['location']}, status {c['status']}, "
                f"incurred ${c['incurred_amount']:,} (${c['paid_amount']:,} paid, "
                f"${c['reserve_amount']:,} reserved)."
            )

        report_path = REPORTS_DIR / f"{report_id}.txt"
        report_path.write_text("\n".join(lines), encoding="utf-8")
        count += 1

    conn.close()
    print(f"Wrote {count} loss run reports to {REPORTS_DIR}")


if __name__ == "__main__":
    generate_reports()
