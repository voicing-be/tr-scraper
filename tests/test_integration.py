"""
Integration tests — hit the live TR website.
Skipped by default; run with: pytest -m integration
"""
import asyncio
import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

_s01 = importlib.import_module("01_search_tr")
_s02 = importlib.import_module("02_scrape_profiles")

pytestmark = pytest.mark.integration

ENTSO_E_TR_ID = "25805148045-87"
ENTSO_E_NAME = "European Network of Transmission System Operators for Electricity"


@pytest.fixture(scope="module")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="module")
async def browser_page():
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        )
        page = await context.new_page()
        yield page
        await browser.close()


@pytest.mark.asyncio
async def test_search_entso_e(browser_page):
    """Searching for 'ENTSO-E' should return the right TR ID."""
    result = await _s01.search_org(browser_page, "ENTSO-E")
    assert result is not None
    assert result.get("tr_id") == ENTSO_E_TR_ID, (
        f"Expected {ENTSO_E_TR_ID}, got {result.get('tr_id')}"
    )


@pytest.mark.asyncio
async def test_search_eurelectric(browser_page):
    """Eurelectric is a major energy trade association — must be in TR."""
    result = await _s01.search_org(browser_page, "Eurelectric")
    assert result is not None
    assert result.get("tr_id") is not None
    assert result.get("match_score", 0) > 0.3


@pytest.mark.asyncio
async def test_scrape_entso_e_profile(browser_page):
    """Scraping ENTSO-E profile should return org name, goals, and contacts."""
    data = await _s02.scrape_profile(browser_page, ENTSO_E_TR_ID, "ENTSO-E")
    assert data.get("org_name"), "Expected org_name"
    assert "ENTSO" in data.get("org_name", "")
    assert data.get("goals"), "Expected goals field"
    assert data.get("entity_form"), "Expected entity_form field"


@pytest.mark.asyncio
async def test_scrape_entso_e_contacts(browser_page):
    """ENTSO-E should have accredited EP persons."""
    data = await _s02.scrape_profile(browser_page, ENTSO_E_TR_ID, "ENTSO-E")
    ep = data.get("accredited_ep", [])
    assert len(ep) > 0, "Expected at least one accredited EP person"
    first = ep[0]
    assert "surname" in first and first["surname"]
    assert "first_name" in first and first["first_name"]


@pytest.mark.asyncio
async def test_search_unknown_org_returns_no_match(browser_page):
    """A nonsense org name should return no tr_id."""
    result = await _s01.search_org(browser_page, "XYZZY_NONEXISTENT_ORG_12345")
    assert result.get("tr_id") is None
