"""
02_scrape_profiles.py — Scrape full TR profile + contacts for each matched org.

Input:  output/org_tr_ids.csv (from 01_search_tr.py) — must have 'tr_id' column
Output: output/tr_profiles.json — one entry per org with all scraped fields

Usage:
    python scripts/02_scrape_profiles.py
    python scripts/02_scrape_profiles.py --input output/org_tr_ids.csv --test
"""

import asyncio
import json
import logging
import random
import re
import sys
from pathlib import Path

import pandas as pd
from playwright.async_api import async_playwright

BASE_URL = "https://transparency-register.europa.eu/search-register-or-update"
PROFILE_URL = f"{BASE_URL}/organisation-detail_en?id={{tr_id}}"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger(__name__)

# Fields extracted from the main label→value table
FIELD_MAP = {
    "Organisation name": "org_name",
    "Status": "status",
    "Registration date": "registration_date",
    "Form of entity": "entity_form",
    "Next annual update due": "n_update_due",
    "Closed financial year": "financial_year",
    "Total budget": "total_budget",
    "EU grants": "eu_grants",
    "Goals/remits of your organisation": "goals",
    "Estimated costs related to lobbying activities": "lobbying_cost",
    "Number of persons involved in EU lobbying activities": "lobbying_persons",
    "Level of interest represented": "level_of_interest",
    "Fields of interest": "fields_of_interest",
    "Activities": "activities",
}


# ---------------------------------------------------------------------------
# Pure parsing helpers (unit-testable)
# ---------------------------------------------------------------------------

def parse_metadata_table(html: str) -> dict:
    """
    Parse the main label→value table on a TR profile page.
    Returns dict of field_name → value.
    """
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    data = {}

    for row in soup.select("tr.ecl-table__row"):
        cells = row.select("td.ecl-table__cell")
        if len(cells) < 2:
            continue
        raw_label = cells[0].get_text(strip=True).replace(":", "")
        label = re.sub(r"\s+", " ", raw_label).strip()
        value = " ".join(cells[1].get_text().split())

        for k, v in FIELD_MAP.items():
            if k in label:
                data[v] = value
                break

    return data


def parse_contacts(html: str) -> dict:
    """
    Extract contact persons from a TR profile page HTML.
    Returns:
        legal_responsible: {name, position}
        eu_relations: {name, position}
        accredited_ep: [{surname, first_name, start_date, end_date}, ...]
    """
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    contacts = {
        "legal_responsible": None,
        "eu_relations": None,
        "accredited_ep": [],
    }

    # --- Person with legal responsibility ---
    legal_section = soup.find("h2", id="person-with-legal-responsibility")
    if legal_section:
        table = legal_section.find_next("table")
        if table:
            rows = table.select("tr.ecl-table__row")
            name_parts, position = [], None
            for row in rows:
                cells = row.select("td.ecl-table__cell")
                if len(cells) < 2:
                    continue
                label = cells[0].get_text(strip=True)
                value_spans = cells[1].find_all("span")
                if "legal responsibility" in label.lower() or "name" in label.lower():
                    name_parts = [s.get_text(strip=True) for s in value_spans if s.get_text(strip=True)]
                elif "position" in label.lower():
                    position = cells[1].get_text(strip=True)
            if name_parts:
                contacts["legal_responsible"] = {
                    "name": " ".join(name_parts),
                    "position": position,
                }

    # --- Person in charge of EU relations ---
    eu_section = soup.find("h2", id="person-in-charge-of-eu-relations")
    if eu_section:
        table = eu_section.find_next("table")
        if table:
            rows = table.select("tr.ecl-table__row")
            name_parts, position = [], None
            for row in rows:
                cells = row.select("td.ecl-table__cell")
                if len(cells) < 2:
                    continue
                label = cells[0].get_text(strip=True)
                value_spans = cells[1].find_all("span")
                if "eu relations" in label.lower() or "name" in label.lower():
                    name_parts = [s.get_text(strip=True) for s in value_spans if s.get_text(strip=True)]
                elif "position" in label.lower():
                    position = cells[1].get_text(strip=True)
            if name_parts:
                contacts["eu_relations"] = {
                    "name": " ".join(name_parts),
                    "position": position,
                }

    # --- Accredited EP persons (nested table with Surname/First name cols) ---
    ep_section = soup.find("h2", id="persons-accredited-for-access-to-european-parliament-premises")
    if ep_section:
        outer_table = ep_section.find_next("table")
        if outer_table:
            # The accredited persons are in a nested inner table
            inner_table = outer_table.find("table")
            target_table = inner_table if inner_table else outer_table
            rows = target_table.select("tbody tr.ecl-table__row")
            for row in rows:
                cells = row.select("td.ecl-table__cell")
                if len(cells) >= 2:
                    contacts["accredited_ep"].append({
                        "surname": cells[0].get_text(strip=True),
                        "first_name": cells[1].get_text(strip=True),
                        "start_date": cells[2].get_text(strip=True) if len(cells) > 2 else None,
                        "end_date": cells[3].get_text(strip=True) if len(cells) > 3 else None,
                    })

    return contacts


# ---------------------------------------------------------------------------
# Playwright scraping
# ---------------------------------------------------------------------------

_cookies_accepted = False


async def _cookies_once(page) -> None:
    global _cookies_accepted
    if _cookies_accepted:
        return
    try:
        await page.click("text=Accept only essential cookies", timeout=3000)
        await page.wait_for_load_state("networkidle", timeout=5000)
        _cookies_accepted = True
    except Exception:
        pass


async def scrape_profile(page, tr_id: str, query_name: str = "") -> dict:
    """Scrape a single TR profile page. Returns merged metadata + contacts dict."""
    url = PROFILE_URL.format(tr_id=tr_id)
    log.info(f"Scraping {tr_id} ({query_name})")

    try:
        await page.goto(url, timeout=60000)
        await page.wait_for_load_state("networkidle", timeout=15000)
        await _cookies_once(page)

        html = await page.content()

        data = {"tr_id": tr_id, "query_name": query_name, "profile_url": url}
        data.update(parse_metadata_table(html))
        data.update(parse_contacts(html))

        log.info(f"  ✓ {data.get('org_name', tr_id)}")
        return data

    except Exception as e:
        log.error(f"Error scraping {tr_id}: {e}")
        return {"tr_id": tr_id, "query_name": query_name, "error": str(e)}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main(input_path: str, output_path: str, test_mode: bool = False) -> None:
    df = pd.read_csv(input_path)
    if "tr_id" not in df.columns:
        raise ValueError(f"CSV must have a 'tr_id' column. Found: {list(df.columns)}")

    rows = df[df["tr_id"].notna()].copy()
    if test_mode:
        rows = rows.head(5)
        log.info(f"TEST MODE: processing {len(rows)} orgs")
    else:
        log.info(f"Processing {len(rows)} orgs with TR IDs")

    # Load existing progress if any
    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    results = []
    done_ids = set()
    if out_path.exists():
        with open(out_path) as f:
            results = json.load(f)
        done_ids = {r["tr_id"] for r in results}
        log.info(f"Resuming: {len(done_ids)} already done")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        )
        page = await context.new_page()

        for i, row in enumerate(rows.itertuples()):
            tr_id = str(row.tr_id).strip()
            if tr_id in done_ids:
                continue

            query_name = getattr(row, "query", getattr(row, "name", ""))
            data = await scrape_profile(page, tr_id, query_name=str(query_name))
            results.append(data)
            done_ids.add(tr_id)

            if (i + 1) % 10 == 0:
                _save(results, output_path)
                log.info(f"Progress: {i+1}/{len(rows)}")

            await asyncio.sleep(random.uniform(0.8, 1.8))

        await browser.close()

    _save(results, output_path)
    log.info(f"Done. {len(results)} profiles saved to {output_path}")


def _save(results: list[dict], path: str) -> None:
    with open(path, "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="output/org_tr_ids.csv")
    parser.add_argument("--output", default="output/tr_profiles.json")
    parser.add_argument("--test", action="store_true", help="Process only first 5 orgs")
    args = parser.parse_args()
    asyncio.run(main(args.input, args.output, test_mode=args.test))
