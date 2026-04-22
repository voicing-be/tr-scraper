"""
03_tier.py — Score and tier orgs from TR profile data.

Input:  output/tr_profiles.json (from 02_scrape_profiles.py)
        optionally also the original contacts CSV to join contacts back
Output: output/contacts_tiered.csv — contacts enriched with TR data + tier

Usage:
    python scripts/03_tier.py
    python scripts/03_tier.py --profiles output/tr_profiles.json \
                               --contacts ../outreach-strategy/outreach_contacts_v2.csv \
                               --output output/contacts_tiered.csv
"""

import json
import re
from pathlib import Path

import pandas as pd

# ---------------------------------------------------------------------------
# Scoring constants
# ---------------------------------------------------------------------------

KEYWORDS_TIER1 = [
    # Core EU Grids Package terms
    "grid", "cbca", "article 17", "cross-border cost", "cross-border cost allocation",
    "transmission network", "electricity network", "interconnector", "entsoe", "entso-e",
    "power grid", "grid infrastructure", "offshore grid", "offshore network",
    # Broader grid/network operator terms
    "transmission system", "distribution system", "system operator", "network operator",
    "grid operator", "transmission operator", "distribution operator",
    "smart grid", "ten-e", "projects of common interest", "offshore wind integration",
    "network tariff", "grid access", "grid investment", "network investment",
    # German (many TSOs/DSOs register goals in German)
    "übertragungsnetz", "verteilernetz", "netzbetreiber", "netzentgelt",
    "übertragungsnetzbetreiber", "leitungsnetzbetreiber", "stromnetz",
    # French
    "réseau électrique", "gestionnaire de réseau", "transport d'électricité",
    "réseau de transport", "opérateur de réseau",
    # Dutch
    "elektriciteitsnet", "netbeheerder", "transmissienet",
]
KEYWORDS_TIER2 = [
    "energy", "electricity", "power", "renewable", "hydrogen", "gas",
    "storage", "flexibility", "demand response", "electrification",
    "eu taxonomy", "decarbonisation", "decarbonization", "clean energy",
    "energy market", "electricity market", "energy transition", "net zero",
    "offshore wind", "wind energy", "solar", "energy infrastructure",
    # German
    "energie", "strom", "erneuerbare", "energiewende",
    # French
    "énergie", "électricité", "transition énergétique",
]
KEYWORDS_TIER3 = [
    "industry", "climate", "environment", "sustainability", "digital",
    "transport", "infrastructure", "carbon", "emissions",
]

ORG_TYPE_SCORES = {
    # Trade / industry associations — exist specifically to track and influence policy
    "trade": 20, "association": 20, "federation": 20, "confederation": 20,
    "union": 15, "alliance": 15, "council": 15, "chamber": 15,
    # International non-profit / Belgian legal forms used by most EU trade associations
    "aisbl": 20, "asbl": 20, "ivzw": 20, "vzw": 20,
    # German/Austrian associations
    "verein": 18, "verband": 20, "interessenverband": 20,
    # Companies
    "corporation": 12, "company": 12, "limited": 12, "gmbh": 12,
    "ag": 10, "sa": 10, "nv": 10, "bv": 10, "spa": 10,
    "plc": 12, "se": 10, "inc": 10,
    # Other
    "ngo": 5, "foundation": 5, "institute": 5,
}


# ---------------------------------------------------------------------------
# Pure scoring logic (unit-testable — no I/O)
# ---------------------------------------------------------------------------

def parse_budget(budget_str: str) -> float | None:
    """
    Parse budget strings like '200,000 - 299,999 €' or '1,500,000'.
    Returns midpoint float or None if unparseable.
    """
    if not budget_str:
        return None
    cleaned = re.sub(r"[€$\s]", "", str(budget_str)).replace(",", "")
    range_m = re.match(r"([\d.]+)-([\d.]+)", cleaned)
    if range_m:
        lo, hi = float(range_m.group(1)), float(range_m.group(2))
        return (lo + hi) / 2
    single_m = re.match(r"([\d.]+)", cleaned)
    if single_m:
        return float(single_m.group(1))
    return None


def score_keywords(text: str) -> int:
    """Score based on keyword matches in goals + fields_of_interest."""
    t = text.lower()
    if any(kw in t for kw in KEYWORDS_TIER1):
        return 40
    if any(kw in t for kw in KEYWORDS_TIER2):
        return 20
    if any(kw in t for kw in KEYWORDS_TIER3):
        return 5
    return 0


def score_org_type(entity_form: str) -> int:
    """Score based on entity form string."""
    if not entity_form:
        return 0
    ef = entity_form.lower()
    for token, pts in ORG_TYPE_SCORES.items():
        if token in ef:
            return pts
    return 0


def score_budget(profile: dict) -> int:
    """
    Score based on lobbying cost. Uses lobbying_cost if available (scraped from
    'Estimated costs related to lobbying activities'), falls back to total_budget.
    total_budget for public utilities reflects org size, not lobbying effort — treat
    it as a weak signal by capping the contribution.
    """
    lobbying = parse_budget(profile.get("lobbying_cost", ""))
    if lobbying is not None:
        if lobbying >= 200_000:
            return 20
        if lobbying >= 50_000:
            return 10
        return 3

    total = parse_budget(profile.get("total_budget", ""))
    if total is None:
        return 0
    # total_budget for large public companies is in the billions — cap at 10pts
    # to avoid inflating scores for utilities that aren't lobbying heavily
    if total >= 500_000:
        return 10
    if total >= 50_000:
        return 5
    return 0


def score_org(profile: dict) -> int:
    """Combine all signals into a single score."""
    text = " ".join(filter(None, [
        profile.get("goals", ""),
        profile.get("fields_of_interest", ""),
        profile.get("activities", ""),
    ]))
    return (
        score_keywords(text)
        + score_org_type(profile.get("entity_form", ""))
        + score_budget(profile)
    )


def assign_tier(score: int) -> int | None:
    """
    Map score to Tier 1/2/3 or None.
    Thresholds calibrated so that energy trade associations (aisbl + electricity keywords)
    land in Tier 1, general energy companies in Tier 2, adjacent orgs in Tier 3.
    """
    if score >= 40:
        return 1
    if score >= 22:
        return 2
    if score >= 10:
        return 3
    return None


def tier_profiles(profiles: list[dict]) -> list[dict]:
    """Add tr_score and tr_tier fields to each profile dict."""
    tiered = []
    for p in profiles:
        s = score_org(p)
        tiered.append({**p, "tr_score": s, "tr_tier": assign_tier(s)})
    return tiered


# ---------------------------------------------------------------------------
# Join back to contacts CSV and emit final output
# ---------------------------------------------------------------------------

def build_output(
    profiles: list[dict],
    contacts_path: str | None,
    org_tr_ids_path: str | None,
) -> pd.DataFrame:
    """
    If a contacts CSV is provided, join TR data onto each contact row.
    Otherwise return a flat org-level DataFrame.
    """
    tiered = tier_profiles(profiles)
    profiles_df = pd.DataFrame(tiered)

    if contacts_path and Path(contacts_path).exists():
        contacts_df = pd.read_csv(contacts_path)

        # Build a mapping: query_name (from 01) → tr fields
        # We need to join via the org_tr_ids CSV if available
        if org_tr_ids_path and Path(org_tr_ids_path).exists():
            ids_df = pd.read_csv(org_tr_ids_path)[["query", "tr_id"]].rename(
                columns={"query": "Organization"}
            )
            contacts_df = contacts_df.merge(ids_df, on="Organization", how="left")

        tr_cols = ["tr_id", "tr_score", "tr_tier", "total_budget", "entity_form",
                   "goals", "fields_of_interest", "legal_responsible", "eu_relations",
                   "accredited_ep"]
        available = [c for c in tr_cols if c in profiles_df.columns]
        profiles_slim = profiles_df[["tr_id"] + [c for c in available if c != "tr_id"]]

        result = contacts_df.merge(profiles_slim, on="tr_id", how="left")
        return result

    return profiles_df


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(
    profiles_path: str = "output/tr_profiles.json",
    contacts_path: str | None = None,
    org_tr_ids_path: str = "output/org_tr_ids.csv",
    output_path: str = "output/contacts_tiered.csv",
) -> None:
    with open(profiles_path) as f:
        profiles = json.load(f)

    result = build_output(profiles, contacts_path, org_tr_ids_path)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(output_path, index=False)

    # Summary
    if "tr_tier" in result.columns:
        counts = result["tr_tier"].value_counts(dropna=False)
        print("\nTier distribution:")
        for tier, count in sorted(counts.items(), key=lambda x: (x[0] is None, x[0])):
            label = f"Tier {tier}" if tier is not None else "Untiered"
            print(f"  {label}: {count}")

    print(f"\nSaved {len(result)} rows to {output_path}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--profiles", default="output/tr_profiles.json")
    parser.add_argument("--contacts", default=None, help="Original contacts CSV to enrich")
    parser.add_argument("--ids", default="output/org_tr_ids.csv", help="org_tr_ids.csv from script 01")
    parser.add_argument("--output", default="output/contacts_tiered.csv")
    args = parser.parse_args()
    main(args.profiles, args.contacts, args.ids, args.output)
