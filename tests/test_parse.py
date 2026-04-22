"""Unit tests for parsing helpers in 01 and 02 — no network required."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import importlib
import pytest

_s01 = importlib.import_module("01_search_tr")
extract_tr_id_from_href = _s01.extract_tr_id_from_href
normalize_org_name = _s01.normalize_org_name
fuzzy_score = _s01.fuzzy_score
best_match = _s01.best_match
parse_search_results = _s01.parse_search_results

_s02 = importlib.import_module("02_scrape_profiles")
parse_metadata_table = _s02.parse_metadata_table
parse_contacts = _s02.parse_contacts


# ---------------------------------------------------------------------------
# 01_search_tr helpers
# ---------------------------------------------------------------------------

class TestExtractTrId:
    def test_relative_href(self):
        href = "search-details_en?id=25805148045-87"
        assert extract_tr_id_from_href(href) == "25805148045-87"

    def test_absolute_href(self):
        href = "/search-details_en?id=969504320672-09"
        assert extract_tr_id_from_href(href) == "969504320672-09"

    def test_organisation_detail_url(self):
        href = "organisation-detail_en?id=123456789012-34"
        assert extract_tr_id_from_href(href) == "123456789012-34"

    def test_none_on_missing(self):
        assert extract_tr_id_from_href("") is None
        assert extract_tr_id_from_href(None) is None
        assert extract_tr_id_from_href("https://example.com/no-id-here") is None


class TestNormalizeOrgName:
    def test_strips_whitespace(self):
        assert normalize_org_name("  ENTSO-E  ") == "entso-e"

    def test_collapses_internal_spaces(self):
        assert normalize_org_name("European  Network  of  TSOs") == "european network of tsos"


class TestFuzzyScore:
    def test_exact_match(self):
        assert fuzzy_score("ENTSO-E", "ENTSO-E") == 1.0

    def test_substring_match(self):
        # "Eurelectric" is substring of "Eurelectric aisbl"
        score = fuzzy_score("Eurelectric", "Eurelectric aisbl")
        assert score >= 0.3

    def test_slug_match_handles_fused_names(self):
        # TR sometimes returns names without spaces: "HydrogenEurope"
        score = fuzzy_score("Hydrogen Europe", "HydrogenEurope")
        assert score >= 0.3

    def test_no_overlap(self):
        assert fuzzy_score("ENTSO-E", "Pharmaceutical Federation") == 0.0

    def test_empty_query(self):
        assert fuzzy_score("", "ENTSO-E") == 0.0


class TestBestMatch:
    def test_picks_best_candidate(self):
        candidates = [
            {"name": "ENTSO-E", "tr_id": "111-11", "href": "?id=111-11"},
            {"name": "ENTSOG", "tr_id": "222-22", "href": "?id=222-22"},
            {"name": "European Network of TSOs for Electricity", "tr_id": "333-33", "href": "?id=333-33"},
        ]
        match = best_match("ENTSO-E", candidates)
        assert match is not None
        assert match["tr_id"] == "111-11"

    def test_returns_none_below_threshold(self):
        candidates = [
            {"name": "Pharmaceutical Federation", "tr_id": "999-99", "href": "?id=999-99"},
        ]
        assert best_match("ENTSO-E", candidates) is None

    def test_empty_candidates(self):
        assert best_match("ENTSO-E", []) is None


class TestParseSearchResults:
    SAMPLE_HTML = """
    <html><body>
    <article class="ecl-content-item">
        <div class="ecl-content-block ecl-content-item__content-block">
            <h1 class="ecl-content-block__title">
                <a href="search-details_en?id=25805148045-87" class="ecl-link">
                    <span class="ecl-link__label">European Network of Transmission System Operators for Electricity</span>
                </a>
            </h1>
        </div>
    </article>
    <article class="ecl-content-item">
        <div class="ecl-content-block ecl-content-item__content-block">
            <h1 class="ecl-content-block__title">
                <a href="search-details_en?id=12345678901-23" class="ecl-link">
                    <span class="ecl-link__label">Energinet</span>
                </a>
            </h1>
        </div>
    </article>
    </body></html>
    """

    def test_returns_all_results(self):
        results = parse_search_results(self.SAMPLE_HTML)
        assert len(results) == 2

    def test_extracts_name_and_id(self):
        results = parse_search_results(self.SAMPLE_HTML)
        names = [r["name"] for r in results]
        ids = [r["tr_id"] for r in results]
        assert "Energinet" in names
        assert "25805148045-87" in ids

    def test_empty_html(self):
        assert parse_search_results("<html><body></body></html>") == []

    def test_skips_article_without_id(self):
        html = """
        <article class="ecl-content-item">
            <h1 class="ecl-content-block__title">
                <a href="some-other-link">No ID here</a>
            </h1>
        </article>
        """
        assert parse_search_results(html) == []


# ---------------------------------------------------------------------------
# 02_scrape_profiles helpers
# ---------------------------------------------------------------------------

class TestParseMetadataTable:
    SAMPLE_HTML = """
    <html><body>
    <table>
      <tbody>
        <tr class="ecl-table__row">
          <td class="ecl-table__cell">Organisation name:</td>
          <td class="ecl-table__cell">ENTSO-E</td>
        </tr>
        <tr class="ecl-table__row">
          <td class="ecl-table__cell">Form of entity:</td>
          <td class="ecl-table__cell">European association</td>
        </tr>
        <tr class="ecl-table__row">
          <td class="ecl-table__cell">Total budget:</td>
          <td class="ecl-table__cell">7,000,000 - 7,999,999 €</td>
        </tr>
        <tr class="ecl-table__row">
          <td class="ecl-table__cell">Goals/remits of your organisation:</td>
          <td class="ecl-table__cell">ENTSO-E promotes cooperation between TSOs for electricity.</td>
        </tr>
      </tbody>
    </table>
    </body></html>
    """

    def test_extracts_org_name(self):
        data = parse_metadata_table(self.SAMPLE_HTML)
        assert data["org_name"] == "ENTSO-E"

    def test_extracts_entity_form(self):
        data = parse_metadata_table(self.SAMPLE_HTML)
        assert data["entity_form"] == "European association"

    def test_extracts_budget(self):
        data = parse_metadata_table(self.SAMPLE_HTML)
        assert "7,000,000" in data["total_budget"]

    def test_extracts_goals(self):
        data = parse_metadata_table(self.SAMPLE_HTML)
        assert "TSOs" in data["goals"]

    def test_empty_html(self):
        assert parse_metadata_table("<html></html>") == {}


class TestParseContacts:
    SAMPLE_HTML = """
    <html><body>
    <h2 id="person-with-legal-responsibility">Person with legal responsibility</h2>
    <table class="ecl-table ecl-table--zebra">
      <tbody class="ecl-table__body">
        <tr class="ecl-table__row">
          <td class="ecl-table__cell"><strong>Person with legal responsibility for the organisation</strong>:</td>
          <td class="ecl-table__cell"><span>Ms</span><span>Sonya</span><span>Twohig</span></td>
        </tr>
        <tr class="ecl-table__row">
          <td class="ecl-table__cell"><strong>Position</strong>:</td>
          <td class="ecl-table__cell"><span>Secretary-General</span></td>
        </tr>
      </tbody>
    </table>

    <h2 id="persons-accredited-for-access-to-european-parliament-premises">Persons accredited for access to European Parliament premises</h2>
    <table class="ecl-table ecl-table--zebra">
      <tbody class="ecl-table__body">
        <tr class="ecl-table__row">
          <td class="ecl-table__cell">
            <table class="ecl-table">
              <thead class="ecl-table__head">
                <tr class="ecl-table__row">
                  <th>Surname</th><th>First name</th><th>Start date</th><th>End date</th>
                </tr>
              </thead>
              <tbody class="ecl-table__body">
                <tr class="ecl-table__row">
                  <td class="ecl-table__cell">Verstraeten</td>
                  <td class="ecl-table__cell">Christelle</td>
                  <td class="ecl-table__cell">29/04/2024</td>
                  <td class="ecl-table__cell">28/04/2026</td>
                </tr>
                <tr class="ecl-table__row">
                  <td class="ecl-table__cell">Mailleux</td>
                  <td class="ecl-table__cell">Felix</td>
                  <td class="ecl-table__cell">29/04/2024</td>
                  <td class="ecl-table__cell">28/04/2026</td>
                </tr>
              </tbody>
            </table>
          </td>
        </tr>
      </tbody>
    </table>
    </body></html>
    """

    def test_extracts_legal_responsible_name(self):
        contacts = parse_contacts(self.SAMPLE_HTML)
        assert contacts["legal_responsible"] is not None
        assert "Twohig" in contacts["legal_responsible"]["name"]

    def test_extracts_legal_responsible_position(self):
        contacts = parse_contacts(self.SAMPLE_HTML)
        assert contacts["legal_responsible"]["position"] == "Secretary-General"

    def test_extracts_accredited_persons(self):
        contacts = parse_contacts(self.SAMPLE_HTML)
        assert len(contacts["accredited_ep"]) == 2

    def test_accredited_person_fields(self):
        contacts = parse_contacts(self.SAMPLE_HTML)
        first = contacts["accredited_ep"][0]
        assert first["surname"] == "Verstraeten"
        assert first["first_name"] == "Christelle"
        assert first["start_date"] == "29/04/2024"

    def test_no_contacts_section(self):
        contacts = parse_contacts("<html><body><p>No contacts here</p></body></html>")
        assert contacts["legal_responsible"] is None
        assert contacts["eu_relations"] is None
        assert contacts["accredited_ep"] == []
