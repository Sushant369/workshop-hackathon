"""Generates synthetic unstructured claim documents (adjuster narratives).

Reads claims from data/claims.db (built by synthetic_sql_data.py) and writes
one free-text narrative per claim to data/documents/<claim_id>.txt. This is
the unstructured half of the knowledge graph.
"""

import random
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "claims.db"
DOCS_DIR = Path(__file__).resolve().parent.parent / "data" / "documents"

random.seed(7)

THIRD_PARTIES = [
    "Alvarez Plumbing & Repair", "Metro Fire Restoration", "Jordan Ellis (contractor)",
    "Priya Nair (witness)", "Dwayne Hutchins (third-party driver)", "Coastal Roofing Services",
    "Tessa Whitfield (tenant)", "GreenLeaf Property Management",
]

NARRATIVE_TEMPLATES = {
    "Fire": (
        "Adjuster on-site {days} days after loss. Fire originated near the electrical panel "
        "in the {area}. {third_party} was contacted to assess restoration scope. Smoke damage "
        "extended to adjoining storage area. No injuries reported. Preliminary cause: electrical "
        "fault, pending fire marshal report."
    ),
    "Water Damage": (
        "Insured reported water intrusion from a failed supply line in the {area}. "
        "{third_party} dispatched for emergency mitigation. Moisture readings elevated in "
        "drywall and subfloor. Similar loss location noted from prior claim history at this "
        "site; recommend reviewing recurring maintenance issue."
    ),
    "Wind/Hail": (
        "Storm event caused roof and exterior siding damage. {third_party} provided repair "
        "estimate. Hail impact marks consistent across {area}. No structural compromise "
        "identified during initial inspection."
    ),
    "Theft": (
        "Forced entry reported at {area}. Missing inventory logged by insured; police report "
        "filed. {third_party} interviewed as witness, confirmed timeline consistent with "
        "insured's statement. Security footage requested from property management."
    ),
    "Vandalism": (
        "Graffiti and broken windows discovered at {area} during morning inspection. "
        "{third_party} contacted for board-up and glass replacement. No suspects identified; "
        "local police report on file."
    ),
    "Slip and Fall": (
        "Claimant slipped on wet flooring near {area}. {third_party} present at time of "
        "incident and provided a statement corroborating insured's account. Claimant received "
        "outpatient treatment for minor soft-tissue injury. Incident report filed same day."
    ),
    "Product Liability": (
        "Third party alleges defective product caused property damage. {third_party} retained "
        "for product testing. Insured's QA records requested for the affected batch. "
        "Liability determination pending lab results."
    ),
    "Property Damage to Third Party": (
        "Insured's operations caused incidental damage to adjacent property near {area}. "
        "{third_party} representing the affected party submitted a damage estimate. "
        "Liability under review; no dispute on cause of loss."
    ),
    "Collision": (
        "Two-vehicle collision at {area}. {third_party} identified as the other driver "
        "involved. Police report filed on scene, no citations issued to insured driver. "
        "Vehicle towed for damage assessment."
    ),
    "Rear-End Collision": (
        "Insured vehicle struck from behind at {area}. {third_party} identified as the "
        "striking driver and accepted fault verbally on scene. Minor whiplash reported by "
        "insured's driver, no hospitalization."
    ),
    "Equipment Injury": (
        "Employee injured operating equipment at {area}. {third_party} witnessed the incident "
        "and assisted with first aid. OSHA-reportable status under review. Equipment "
        "maintenance logs requested."
    ),
    "Repetitive Strain": (
        "Employee reported cumulative strain injury associated with repetitive tasks at "
        "{area}. {third_party}, the treating physician, recommends modified duty. Claim under "
        "review for compensability."
    ),
    "Ransomware": (
        "Ransomware encrypted core systems affecting {area} operations. {third_party} engaged "
        "for incident response and forensics. Business interruption period estimated at 5-7 "
        "days. No confirmed data exfiltration at this stage."
    ),
    "Data Breach": (
        "Unauthorized access detected affecting customer records tied to {area} systems. "
        "{third_party} engaged for breach forensics and notification support. Scope of "
        "affected records still being determined."
    ),
    "Business Email Compromise": (
        "Fraudulent wire transfer initiated via compromised email account in {area} finance "
        "team. {third_party} engaged to trace fund flow. Insured's bank notified; recovery "
        "efforts in progress."
    ),
}

AREAS = [
    "main warehouse", "north wing office", "loading dock", "retail floor",
    "third-floor storage room", "parking structure", "distribution facility",
    "corporate headquarters",
]


def generate_documents() -> None:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    claims = cur.execute(
        """
        SELECT c.claim_id, c.cause_of_loss, c.date_of_loss, c.location, c.incurred_amount,
               ph.name AS policyholder_name
        FROM claims c
        JOIN policies p ON c.policy_number = p.policy_number
        JOIN policyholders ph ON p.policyholder_id = ph.id
        """
    ).fetchall()
    conn.close()

    count = 0
    for claim in claims:
        template = NARRATIVE_TEMPLATES.get(
            claim["cause_of_loss"],
            "Loss reported at {area}. {third_party} engaged to assist with claim handling.",
        )
        narrative = template.format(
            area=random.choice(AREAS),
            third_party=random.choice(THIRD_PARTIES),
            days=random.randint(1, 5),
        )

        header = (
            f"Claim: {claim['claim_id']}\n"
            f"Policyholder: {claim['policyholder_name']}\n"
            f"Date of Loss: {claim['date_of_loss']}\n"
            f"Location: {claim['location']}\n"
            f"Cause of Loss: {claim['cause_of_loss']}\n"
            f"Incurred: ${claim['incurred_amount']:,}\n\n"
        )

        doc_path = DOCS_DIR / f"{claim['claim_id']}.txt"
        doc_path.write_text(header + narrative, encoding="utf-8")
        count += 1

    print(f"Wrote {count} claim narrative documents to {DOCS_DIR}")


if __name__ == "__main__":
    generate_documents()
