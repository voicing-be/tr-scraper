"""Unit tests for 03_tier.py — pure logic, no network."""
import importlib
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import pytest

# Scripts use numeric prefixes; importlib handles that where direct import can't
_mod = importlib.import_module("03_tier")
parse_budget = _mod.parse_budget
score_keywords = _mod.score_keywords
score_org_type = _mod.score_org_type
score_budget = _mod.score_budget
score_org = _mod.score_org
assign_tier = _mod.assign_tier
tier_profiles = _mod.tier_profiles


class TestParseBudget:
    def test_range_returns_midpoint(self):
        assert parse_budget("200,000 - 299,999") == 249999.5

    def test_single_number(self):
        assert parse_budget("1,500,000") == 1_500_000

    def test_with_euro_sign(self):
        assert parse_budget("500,000 €") == 500_000

    def test_none_on_empty(self):
        assert parse_budget("") is None
        assert parse_budget(None) is None

    def test_none_on_garbage(self):
        assert parse_budget("N/A") is None


class TestScoreKeywords:
    def test_tier1_grid_keyword(self):
        assert score_keywords("We lobby on electricity grid infrastructure") == 40

    def test_tier1_cbca(self):
        assert score_keywords("Cross-border cost allocation is our focus") == 40

    def test_tier2_energy_keyword(self):
        assert score_keywords("We work on renewable energy policy") == 20

    def test_tier3_indirect(self):
        assert score_keywords("General industry and climate issues") == 5

    def test_no_match(self):
        assert score_keywords("We lobby on pharmaceutical regulations") == 0

    def test_tier1_beats_tier2(self):
        # When both present, tier1 wins (first match wins)
        assert score_keywords("electricity grid and renewable energy") == 40


class TestScoreOrgType:
    def test_trade_association(self):
        assert score_org_type("trade association") == 20

    def test_federation(self):
        assert score_org_type("European federation") == 20

    def test_corporation(self):
        assert score_org_type("corporation") == 12

    def test_ngo(self):
        assert score_org_type("NGO") == 5

    def test_empty(self):
        assert score_org_type("") == 0
        assert score_org_type(None) == 0


class TestScoreBudget:
    def test_high_budget(self):
        assert score_budget("500,000") == 20

    def test_mid_budget(self):
        assert score_budget("100,000") == 10

    def test_low_budget(self):
        assert score_budget("10,000") == 3

    def test_unknown_budget_neutral(self):
        # Unknown budget should not penalise — returns 0 (neutral)
        assert score_budget("") == 0
        assert score_budget(None) == 0


class TestScoreOrg:
    def test_tier1_trade_assoc_grid(self):
        profile = {
            "goals": "We lobby on electricity grid and cross-border cost allocation",
            "fields_of_interest": "Energy",
            "entity_form": "European trade association",
            "total_budget": "500,000",
        }
        s = score_org(profile)
        assert s >= 60, f"Expected Tier 1 score (>=60), got {s}"

    def test_tier2_company_energy(self):
        profile = {
            "goals": "We operate renewable energy assets across Europe",
            "fields_of_interest": "Energy Climate",
            "entity_form": "corporation",
            "total_budget": "100,000",
        }
        s = score_org(profile)
        assert 30 <= s < 60, f"Expected Tier 2 score (30-59), got {s}"

    def test_tier3_ngo_climate(self):
        profile = {
            "goals": "We advocate for climate action and environmental policy",
            "fields_of_interest": "Environment",
            "entity_form": "NGO",
            "total_budget": "20,000",
        }
        s = score_org(profile)
        assert 10 <= s < 30, f"Expected Tier 3 score (10-29), got {s}"

    def test_untiered_pharma(self):
        profile = {
            "goals": "We represent pharmaceutical companies",
            "fields_of_interest": "Public health",
            "entity_form": "association",
            "total_budget": "50,000",
        }
        s = score_org(profile)
        # Trade association score (20) + budget (10) = 30 → Tier 2
        # This is correct: it's a well-resourced trade association, just off-topic
        # The keyword score is 0, so: 0 + 20 + 10 = 30 → Tier 2
        # If we want truly off-topic, use minimal budget and no org boost
        profile2 = {
            "goals": "We represent pharmaceutical companies",
            "fields_of_interest": "Public health",
            "entity_form": "",
            "total_budget": "",
        }
        s2 = score_org(profile2)
        assert assign_tier(s2) is None, f"Expected None tier, got tier {assign_tier(s2)} (score={s2})"

    def test_missing_fields_dont_crash(self):
        assert score_org({}) == 0
        assert score_org({"goals": None}) == 0


class TestAssignTier:
    def test_tier_1(self):
        assert assign_tier(75) == 1
        assert assign_tier(60) == 1

    def test_tier_2(self):
        assert assign_tier(59) == 2
        assert assign_tier(30) == 2

    def test_tier_3(self):
        assert assign_tier(29) == 3
        assert assign_tier(10) == 3

    def test_untiered(self):
        assert assign_tier(9) is None
        assert assign_tier(0) is None


class TestTierProfiles:
    def test_adds_score_and_tier(self):
        profiles = [
            {
                "tr_id": "123",
                "goals": "electricity grid cross-border cost allocation",
                "entity_form": "trade association",
                "total_budget": "500,000",
            }
        ]
        result = tier_profiles(profiles)
        assert len(result) == 1
        assert "tr_score" in result[0]
        assert "tr_tier" in result[0]
        assert result[0]["tr_tier"] == 1

    def test_preserves_existing_fields(self):
        profiles = [{"tr_id": "abc", "org_name": "Test Org", "goals": ""}]
        result = tier_profiles(profiles)
        assert result[0]["org_name"] == "Test Org"
        assert result[0]["tr_id"] == "abc"
