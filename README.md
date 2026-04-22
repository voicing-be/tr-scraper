# TR Scraper

Scrapes the [EU Transparency Register](https://transparency-register.europa.eu/) to enrich a list of organisations with lobbying contacts and auto-assigns outreach tiers.

Give it a CSV of org names. Get back named EU affairs contacts (Director of European Affairs, accredited EP lobbyists) plus a Tier 1/2/3 score based on how relevant each org is to a given policy file.

Built for the EU Grids Package outreach campaign; reusable for any EU policy file.

---

## What it scrapes

For each organisation:

| Field | Source |
|-------|--------|
| Full registered name, entity type | TR profile |
| Lobbying budget, goals, fields of interest | TR profile |
| **Person with legal responsibility** (CEO / Secretary-General) | TR profile |
| **Person in charge of EU relations** (policy director — the person to reach) | TR profile |
| **All accredited EP lobbyists** — name + badge validity dates | TR profile |

---

## Requirements

- Python 3.11+
- Chromium (via Playwright)

```bash
pip install -r requirements.txt
playwright install chromium
```

No API key. No login. The TR is fully public.

---

## Usage

### Step 1 — Prepare your input

Create a CSV with an `Organization` column:

```csv
Organization
Eurelectric
TenneT
Hydrogen Europe
Austrian Power Grid AG
```

Your column header must be exactly `Organization`. One row per org. Multiple contacts per org in the same CSV is fine — script 01 deduplicates automatically.

### Step 2 — Search: org names → TR IDs

```bash
python scripts/01_search_tr.py --input input/your_list.csv --output output/org_tr_ids.csv
```

Searches the TR for each org name using keyword search + multi-strategy fuzzy matching. Outputs a CSV with `tr_id`, `match_score`, and matched name.

**Review the output before step 3.** Low `match_score` (< 0.5) rows may be false matches. You can manually correct or add TR IDs directly.

**Known limitation: pure acronyms** (e.g. `ENTSO-E`, `GIE`) often fail because the TR keyword search returns orgs that *mention* the acronym in their profiles, not the org itself. Fix: look up the TR ID manually at [transparency-register.europa.eu](https://transparency-register.europa.eu) and paste it into `org_tr_ids.csv`.

### Step 3 — Scrape profiles + contacts

```bash
python scripts/02_scrape_profiles.py --input output/org_tr_ids.csv --output output/tr_profiles.json
```

Visits each org's profile page and extracts all fields. Saves incrementally — safe to interrupt and resume. Skips orgs already in the output file.

### Step 4 — Score and tier

```bash
python scripts/03_tier.py \
  --profiles output/tr_profiles.json \
  --contacts input/your_list.csv \
  --ids output/org_tr_ids.csv \
  --output output/contacts_tiered.csv
```

Joins TR data back onto your original contacts CSV and adds `tr_score` and `tr_tier`.

---

## Output files

| File | Contents |
|------|----------|
| `output/org_tr_ids.csv` | TR ID + match score per org. Review this before step 3. |
| `output/tr_profiles.json` | Full raw TR data per org including all contacts. |
| `output/contacts_tiered.csv` | Your original contacts enriched with TR fields + tier. |

---

## Tiering logic

Each org is scored on three signals:

| Signal | Points |
|--------|--------|
| Keywords in goals/activities: "grid", "CBCA", "Article 17", "cross-border cost", "transmission network" | +40 |
| Keywords: "energy", "electricity", "hydrogen", "renewable" | +20 |
| Keywords: "industry", "climate", "environment" | +5 |
| Org type: trade association / federation / alliance | +20 |
| Org type: company / corporation | +12 |
| Org type: NGO / foundation | +5 |
| Lobbying budget > €200k | +20 |
| Lobbying budget €50k–200k | +10 |
| Budget unknown | +0 (neutral — don't penalise) |

**Tier 1** ≥ 60 pts — grid-focused, well-resourced, contact immediately  
**Tier 2** 30–59 pts — energy sector, strong fit but broader scope  
**Tier 3** 10–29 pts — adjacent topic or low budget  
**Untiered** < 10 pts — off-topic, skip  

### Adapting the tiers for a different policy file

Edit the keyword lists at the top of `scripts/03_tier.py`:

```python
KEYWORDS_TIER1 = ["grid", "cbca", "article 17", ...]  # your file's key terms
KEYWORDS_TIER2 = ["energy", "electricity", ...]        # broader sector terms
KEYWORDS_TIER3 = ["industry", "climate", ...]          # peripheral terms
```

The scoring weights and tier thresholds are also constants — change them without touching the logic.

---

## Running tests

Unit tests (no network):

```bash
pytest tests/test_tier.py tests/test_parse.py -v
```

Integration tests (hit live TR — slow, ~2 min):

```bash
pytest -m integration -v
```

---

## Adapting for a new campaign

1. Replace `input/companies_sample.csv` with your actor list
2. Update `KEYWORDS_TIER1/2/3` in `03_tier.py` for your policy file
3. Run scripts 01 → 02 → 03
4. Review `org_tr_ids.csv` for low-confidence matches before scraping
