"""
01_search_tr.py — Search the EU Transparency Register by org name.

Input:  CSV with an 'Organization' column (e.g. outreach_contacts_v2.csv)
Output: output/org_tr_ids.csv — one row per unique org, with tr_id + match metadata

Usage:
    python scripts/01_search_tr.py --input input/companies.csv
    python scripts/01_search_tr.py --input ../outreach-strategy/outreach_contacts_v2.csv --test
"""

import asyncio
import csv
import json
import logging
import random
import re
import sys
from pathlib import Path
from urllib.parse import quote_plus

import pandas as pd
from playwright.async_api import async_playwright

BASE_URL = "https://transparency-register.europa.eu/search-register-or-update"
SEARCH_URL = f"{BASE_URL}/search-register_en"
RESULT_ARTICLE_SEL = "article.ecl-content-item"
RESULT_LINK_SEL = "h1.ecl-content-block__title a"
TR_ID_RE = re.compile(r"id=([\d]+-\d+)")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pure parsing helpers (no Playwright — fully unit-testable)
# ---------------------------------------------------------------------------

def extract_tr_id_from_href(href: str) -> str | None:
    """Extract TR ID from a search-details or organisation-detail href."""
    if not href:
        return None
    m = TR_ID_RE.search(href)
    return m.group(1) if m else None


def normalize_org_name(name: str) -> str:
    """Strip, collapse whitespace, lower for comparison."""
    return re.sub(r"\s+", " ", name.strip()).lower()


def _slug(name: str) -> str:
    """Remove all non-alphanumeric chars for acronym-safe comparison."""
    return re.sub(r"[^a-z0-9]", "", normalize_org_name(name))


def fuzzy_score(query: str, candidate: str) -> float:
    """
    Multi-strategy scorer.  Returns 0.0–1.0.

    Strategy order (first hit wins):
    1. Normalised substring: query ⊆ candidate or vice-versa  → 0.85
    2. Slug substring (no spaces/punct): handles concat artefacts like
       "HydrogenEurope" matching "Hydrogen Europe"           → 0.80
    3. Token overlap ratio                                    → variable
    4. Partial token overlap (token ⊆ other token)           → variable
    """
    q_norm = normalize_org_name(query)
    c_norm = normalize_org_name(candidate)

    # Strategy 1: substring after normalisation
    if q_norm and (q_norm in c_norm or c_norm in q_norm):
        shorter = min(len(q_norm), len(c_norm))
        longer = max(len(q_norm), len(c_norm))
        return 0.85 * (shorter / longer) + 0.15

    # Strategy 2: slug comparison handles space-stripped TR names
    q_slug = _slug(query)
    c_slug = _slug(candidate)
    if q_slug and len(q_slug) > 2 and (q_slug in c_slug or c_slug in q_slug):
        shorter = min(len(q_slug), len(c_slug))
        longer = max(len(q_slug), len(c_slug))
        return 0.80 * (shorter / longer) + 0.10

    # Strategy 3: token overlap
    q_tokens = set(q_norm.split())
    c_tokens = set(c_norm.split())
    if not q_tokens:
        return 0.0
    exact_overlap = q_tokens & c_tokens
    if exact_overlap:
        return len(exact_overlap) / max(len(q_tokens), len(c_tokens))

    # Strategy 4: partial token membership (e.g. "tennet" inside "tennetholding")
    partial = sum(
        1 for qt in q_tokens
        if len(qt) > 2 and any(qt in ct or ct in qt for ct in c_tokens if len(ct) > 2)
    )
    if partial:
        return 0.4 * partial / len(q_tokens)

    return 0.0


def best_match(query: str, candidates: list[dict]) -> dict | None:
    """
    Pick the candidate with the highest fuzzy score.
    Returns None if no candidate scores above MIN_SCORE.
    candidates: list of {name, tr_id, href}
    """
    MIN_SCORE = 0.3
    if not candidates:
        return None
    scored = [(fuzzy_score(query, c["name"]), c) for c in candidates]
    scored.sort(key=lambda x: x[0], reverse=True)
    top_score, top_candidate = scored[0]
    if top_score < MIN_SCORE:
        return None
    return {**top_candidate, "match_score": round(top_score, 3)}


def parse_search_results(html: str) -> list[dict]:
    """
    Parse raw HTML of a TR search results page.
    Returns list of {name, tr_id, href}.
    Kept pure so it can be tested with fixture HTML.
    """
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    results = []
    for article in soup.select("article.ecl-content-item"):
        link = article.select_one("h1.ecl-content-block__title a")
        if not link:
            continue
        # separator=" " prevents spans from fusing: "HydrogenEurope" → "Hydrogen Europe"
        name = re.sub(r"\s+", " ", link.get_text(separator=" ", strip=True))
        href = link.get("href", "")
        tr_id = extract_tr_id_from_href(href)
        if tr_id:
            results.append({"name": name, "tr_id": tr_id, "href": href})
    return results


# ---------------------------------------------------------------------------
# Playwright scraping
# ---------------------------------------------------------------------------

async def _cookies_once(page) -> None:
    try:
        await page.click("text=Accept only essential cookies", timeout=3000)
        await page.wait_for_load_state("networkidle", timeout=5000)
    except Exception:
        pass


_cookies_accepted = False


async def search_org(page, org_name: str) -> dict | None:
    """
    Search TR for org_name, return best match or None.
    Returned dict: {name, tr_id, href, match_score, query}
    """
    global _cookies_accepted
    encoded = quote_plus(org_name)
    url = f"{SEARCH_URL}?queryText={encoded}"
    log.info(f"Searching: {org_name!r}")

    try:
        await page.goto(url, timeout=60000)
        await page.wait_for_load_state("networkidle", timeout=15000)

        if not _cookies_accepted:
            await _cookies_once(page)
            _cookies_accepted = True

        html = await page.content()
        candidates = parse_search_results(html)
        log.info(f"  → {len(candidates)} results for {org_name!r}")

        match = best_match(org_name, candidates)
        if match:
            log.info(f"  ✓ matched {match['name']!r} (score={match['match_score']})")
            low_conf = match["match_score"] < 0.5
            return {**match, "query": org_name, "low_confidence": low_conf}
        else:
            log.warning(f"  ✗ no match for {org_name!r}")
        return {"query": org_name, "tr_id": None, "name": None, "match_score": 0, "low_confidence": True}

    except Exception as e:
        log.error(f"Error searching {org_name!r}: {e}")
        return {"query": org_name, "tr_id": None, "name": None, "match_score": 0, "error": str(e)}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main(input_path: str, output_path: str, test_mode: bool = False) -> None:
    df = pd.read_csv(input_path)
    if "Organization" not in df.columns:
        raise ValueError(f"CSV must have an 'Organization' column. Found: {list(df.columns)}")

    org_names = df["Organization"].dropna().unique().tolist()
    if test_mode:
        org_names = org_names[:5]
        log.info(f"TEST MODE: processing {len(org_names)} orgs")
    else:
        log.info(f"Processing {len(org_names)} unique orgs")

    results = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        )
        page = await context.new_page()

        for i, name in enumerate(org_names):
            result = await search_org(page, name)
            results.append(result)

            if (i + 1) % 10 == 0:
                _save(results, output_path)
                log.info(f"Progress: {i+1}/{len(org_names)}")

            await asyncio.sleep(random.uniform(0.8, 1.8))

        await browser.close()

    _save(results, output_path)
    found = sum(1 for r in results if r.get("tr_id"))
    log.info(f"Done. {found}/{len(results)} orgs matched. Saved to {output_path}")


def _save(results: list[dict], path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(results).to_csv(path, index=False)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="CSV with Organization column")
    parser.add_argument("--output", default="output/org_tr_ids.csv")
    parser.add_argument("--test", action="store_true", help="Process only first 5 orgs")
    args = parser.parse_args()
    asyncio.run(main(args.input, args.output, test_mode=args.test))
