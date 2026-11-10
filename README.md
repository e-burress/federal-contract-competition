# Federal Contract Competition: Findings & Forecasts

[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/e-burress/federal-contract-competition) · [Dashboard](https://e-burress.github.io/federal-contract-competition/) · [Run it yourself](docs/run-it-yourself.md)

An open, reproducible look at how competitive US federal contracting is. It tests whether buying behavior bunches around legal dollar thresholds, and publishes **pre-registered forecasts** that are scored in public when the answers come in.

Everything here is built from free public data (USAspending.gov and SAM.gov), runs on a laptop, and can be rerun from the scripts in this repository. Every number in this README is written by `scripts/build_report.py` from the result files.

<!-- auto:status -->
> **Status (November 9, 2026):** every result was rebuilt from one public data snapshot, [`data-2026-11-09`](https://github.com/e-burress/federal-contract-competition/releases/tag/data-2026-11-09), downloaded from USAspending on November 9, 2026. `python run.py quick` downloads it and rebuilds everything. The first release (October 24, 2026) used an earlier download that was not kept. Rebuilt from this snapshot, single-offer shares moved by at most 0.05 percentage points (five departments: 97,643 → 97,672 awards, still 22.8%) and bunching ratios by at most 0.02 ($350K after the change: 1.95 → 1.96). The forecast model and the sealed forecasts are from October 24, 2026 and are unchanged.
>
> The in-depth analysis and the forecasts cover five civilian departments (VA, HHS, DHS, Interior and USDA) for FY2024–FY2026. Single-offer shares cover every civilian agency, and the threshold test is civilian-wide. Defense data is excluded because of DoD's 90-day reporting delay.
<!-- /auto:status -->

---

## Key findings

### 1. When the simplified-acquisition threshold moved, the pile-up of contracts moved with it

On October 1, 2025, the simplified-acquisition threshold (the dollar line below which agencies can use faster, simpler buying procedures) rose from **$250,000 to $350,000**. Before the change, civilian awards piled up just under $250K. In the year after, the pile-up under $250K shrank and a new one formed just under $350K.

![Award counts near the old and new thresholds](figures/threshold_shift.png)

<!-- auto:threshold-table -->
| Line | Bunching ratio, FY2025 (before) | FY2026 (after) | Change (95% interval) |
|---|---|---|---|
| Just under **$250K** (old line) | 1.79 | 1.24 | **−0.55** (−0.74 to −0.37) |
| Just under **$350K** (new line) | 0.97 | 1.96 | **+0.99** (+0.65 to +1.36) |
| Placebo line $200K | 1.46 | 1.31 | −0.15 (−0.38 to +0.10) |
| Placebo line $450K | 0.86 | 1.02 | +0.16 (−0.13 to +0.44) |
<!-- /auto:threshold-table -->

The bunching ratio compares the awards observed just under a line with the number a smooth curve fitted outside that window predicts. A value of 1.0 means no pile-up. The placebo lines, where nothing changed, did not move.

<!-- auto:threshold-quarters -->
The new pile-up grew every quarter after the change: 1.51 → 1.65 → 1.90 → 2.18.
<!-- /auto:threshold-quarters -->

![Bunching ratio by fiscal quarter](figures/bunching_by_quarter.png)

Pile-ups under a threshold are a well-known risk indicator. They can mean contract values are being set to fit simpler procedures, or contracts are being split. They can also have ordinary explanations, such as budgets and requirements written to fit the rules. **This analysis measures the pattern. It does not establish why it happens.**

### 2. About one in four competed awards received a single offer

<!-- auto:single-offer-five -->
Among 97,672 competed awards with a usable offer count (purchase orders, definitive contracts and contract vehicles, FY2024–FY2026):

- **22.8%** received exactly one offer (95% interval 22.5–23.0%).
- By department: HHS **40.3%**, VA **35.3%**, DHS **26.6%**, Interior **17.9%**, USDA **6.6%**.
- Weighted by dollars (purchase orders and definitive contracts), the share is **15.7%**, close to GAO's figure of about 13% for FY2005–2009.
- The share fell in every department from FY2024 to FY2026.
<!-- /auto:single-offer-five -->

![Single-offer share by department and fiscal year](figures/single_offer_by_agency.png)

**Every civilian agency** (same definitions, same data snapshot):

<!-- auto:civilian -->
- Across all 64 civilian agencies with competed awards, **34.0%** of 466,009 competed awards got a single offer.
- **GSA** accounts for 63% of those awards. Almost all are very small purchase orders from GSA's Federal Acquisition Service, mostly supply orders (median award $143), and their offer coding changed sharply: one offer on 56% of them in FY2024, 17% in FY2026. So the civilian-wide figure is also given **without GSA: 25.5%** of 170,687 awards (FY2024 27.9% → FY2025 26.3% → FY2026 21.5%).
- Outside GSA and the five departments above, the share is **29.1%** (73,015 awards).
- Measured from the civilian-wide table, the five departments match the five-department table exactly.
<!-- /auto:civilian -->

![Single-offer share, civilian agencies with at least 200 competed awards](figures/single_offer_civilian.png)

<!-- auto:civilian-table -->
| Agency (at least 200 competed awards) | Single-offer share, FY2024–26 | Competed awards |
|---|---|---|
| U.S. Agency for Global Media (USAGM) | 74.4% | 281 |
| District of Columbia Courts (DC COURTS) | 42.1% | 342 |
| Health and Human Services (HHS) | 40.3% | 9,590 |
| Justice | 39.1% | 29,088 |
| General Services Administration (GSA) | 39.0% | 295,322 |
| Education | 38.0% | 250 |
| Veterans Affairs (VA) | 35.3% | 27,017 |
| Environmental Protection Agency (EPA) | 30.7% | 941 |
| Treasury | 28.5% | 2,606 |
| Social Security Administration (SSA) | 28.4% | 218 |
| Homeland Security (DHS) | 26.6% | 15,159 |
| Transportation (DOT) | 24.1% | 5,264 |
| Labor | 24.0% | 221 |
| Agency for International Development (USAID) | 21.9% | 1,500 |
| State | 21.1% | 23,270 |
| Commerce | 19.8% | 3,396 |
| Interior (DOI) | 17.9% | 15,880 |
| Energy | 16.0% | 920 |
| National Aeronautics and Space Administration (NASA) | 13.5% | 3,130 |
| Agriculture (USDA) | 6.6% | 30,026 |

The 44 smaller agencies together: 31.8% of 1,588 awards. Full tables: [`results/atlas_civilian_by_agency.csv`](results/atlas_civilian_by_agency.csv) and the other `atlas_civilian_*` files.
<!-- /auto:civilian-table -->

### 3. A forecast of which open solicitations will get only one offer

A transparent logistic-regression model uses only information known when a solicitation is posted: department, product and service codes, industry, set-aside type, notice type, response window, and the buying office's past single-offer rate. It was trained on awards through September 2025 and tested on FY2026 awards it had never seen. These model results are from the October 24, 2026 build and are frozen until the model is rebuilt on SAM.gov's official archive files.

<!-- auto:model-table -->
| Model | AUC | Brier score |
|---|---|---|
| Department-wide rate (baseline) | 0.62 | 0.120 |
| Office history only | 0.65 | 0.122 |
| **This model** | **0.75** | **0.112** |
<!-- /auto:model-table -->

<!-- auto:model-applied -->
The model was then applied to **150 currently open solicitations** with response deadlines between Nov 1 and Dec 15, 2026. Those forecasts are sealed below.
<!-- /auto:model-applied -->

---

## Pre-registered forecasts

<!-- auto:forecasts -->
Forecasts were made on **2026-10-24** and fingerprinted with SHA-256 before any outcome was known. See [`forecasts/`](forecasts/).

| ID | What it forecasts | Resolves | SHA-256 |
|---|---|---|---|
| PIO-F1 | Probability of a single offer for each of 150 open civilian solicitations | 2027-03-31 (checks Jan 31 and Feb 28) | `a8d1bb3319bf2fc1439550ee99c971742408a99a4772db426b26dec15f0657d2` |
| PIO-F2 | FY2027 Q1 bunching ratios under $350K (2.03; 80% interval 1.40–2.66) and $250K (1.23; 0.88–1.58), plus the micro-purchase reporting share | 2027-02-28 | `5a7009ba0f17e953bd1fea32d2b7fe9241a849b85222266506c5312e4790c43b` |
<!-- /auto:forecasts -->

- The IDs start with "PIO" from the project's earlier working name (Procurement Integrity Observatory). The sealed files keep that name unchanged so their fingerprints still match.
- **PIO-F2** is published in full.
- **PIO-F1** includes office-level details, so only its fingerprint is published for now. The full file will be posted with the scoring. Anyone can then check it against the fingerprint above.

To verify a published forecast file:

```bash
sha256sum forecasts/PIO-F2-threshold-fy2027q1-20261024.json
```

---

## How it works

One download serves every analysis. All scripts are in `scripts/`; `run.py` runs them in the right order.

| Step | Script | What it does |
|---|---|---|
| 1 | `01c_download_civilian.py` | Contract transactions for every civilian agency, FY2024–FY2026, one request per quarter (USAspending download API, selected columns only) |
| older | `01_download_usaspending.py`, `01b_download_threshold_universe.py` | Older alternatives to step 1: the five departments one agency at a time, and purchase orders and definitive contracts for the threshold test |
| 2 | `02_build_awards.py` | One row per award, using the base action (modification 0): the five departments by default, every civilian agency with `--civilian` |
| 2b | `bunching_lib.py` | Builds the threshold test's base actions (`--rebuild`) and holds the bunching estimator |
| 3 | `03_atlas.py` | Single-offer shares for the five departments by department, year, award type, set-aside and size |
| 3b | `03b_atlas_civilian.py` | Single-offer shares for every civilian agency, with and without GSA |
| 4 | `04_threshold.py`, `04b_threshold_quarterly.py` | Bunching estimates with bootstrap intervals and placebo lines, for each period and each quarter |
| tool | `check_counts_sql.py` | Independent recount of the headline counts with SQLite and Python's csv module, no pandas |
| tool | `make_figures.py` | The charts in this README |
| tool | `build_report.py` | Writes every number in this README and in `docs/methodology.md` from the result files |
| 5–8, 11 | `05_sam_open_pull.py`, `06_sam_open_details.py`, `07_sam_train_lookup.py`, `08_model_forecast.py`, `11_resolution_backtest.py` | The forecast model, frozen from the October 24, 2026 build: open solicitations and response windows from SAM.gov, training, out-of-time test, live probabilities, resolution backtest. Its SAM.gov inputs were not kept, so these are not rerun |
| 10, 10b | `10_preregister.py`, `10b_preregister_threshold.py` | Created the sealed forecasts. Never rerun; they refuse to overwrite the sealed files |
| 9, 12, 13 | `09_resolve_forecast.py`, `12_resolve_threshold.py`, `13_track_threshold.py` | Scoring of PIO-F1 and PIO-F2 after they resolve, and a daily provisional check of PIO-F2 against the FY2027 Q1 data so far |
| 14 | `14_sam_daily_snapshot.py` | Saves SAM.gov's nightly list of active notices (contact details removed) and updates daily counts of open solicitations |
| 15 | `15_civilian_fytd.py` | Live single-offer table for every civilian agency, this fiscal year so far |
| tool | `build_dashboard.py` | The [public dashboard](https://e-burress.github.io/federal-contract-competition/) (`docs/index.html`) |
| tool | `run.py` (in the repository root) | Runs the steps above in order. See below |

A GitHub Actions job (`.github/workflows/daily-refresh.yml`) runs steps 14, 15 and 13 and the dashboard build every morning and publishes the result. It checks the sealed PIO-F2 fingerprint on every run and never changes the forecast files. The full daily SAM.gov snapshots are kept in draft (non-public) releases of this repository; the daily counts in `results/sam_daily_counts.csv` are public.

Details, definitions and data-quality notes are in [`docs/methodology.md`](docs/methodology.md).

### Run it yourself

You need Python 3.11 or newer. One command rebuilds everything:

```bash
pip install -r requirements.txt
python run.py quick
```

With [uv](https://docs.astral.sh/uv/), one line installs the right Python and packages and runs it: `uv run run.py quick`.

| Command | What it does |
|---|---|
| `python run.py check` | Checks your Python, packages and folders |
| `python run.py quick` | Downloads the published data snapshot and rebuilds every result, chart, the numbers in this README and the dashboard. Takes minutes |
| `python run.py all` | Rebuilds everything from the government sources. Slow, mostly waiting on downloads |
| `python run.py verify` | Compares your results with the published ones |
| `python run.py test` | Runs the offline tests |
| `python run.py daily` | The morning refresh: SAM.gov snapshot, this fiscal year so far, forecast tracking, dashboard |

No coding needed: open the project in GitHub Codespaces with the button at the top and type `python run.py quick`. Step-by-step instructions for every route (Codespaces, your own self-refreshing copy, double-click launchers) are in [`docs/run-it-yourself.md`](docs/run-it-yourself.md).

The scripts that created the sealed forecasts (10 and 10b) and the frozen forecast model (05–08 and 11) are never part of any command.

---

## Limits

- **No unit prices.** Federal award data records dollar values, not quantities or unit prices, so the price premium of single-offer awards can't be measured directly. <!-- auto:limit-premium -->A preliminary check found single-offer awards are 27% *smaller* within comparable categories, and found only small differences, in both directions, in dollars obligated after award relative to the initial value.<!-- /auto:limit-premium -->
- **Offer counts are entered by staff.** In the five departments, <!-- auto:limit-999 -->760 competed awards<!-- /auto:limit-999 --> carry a "999" placeholder and were excluded.
- **FY2026 is unusual.** It saw a government shutdown, a reporting-system move and staffing changes. <!-- auto:limit-fy2026 -->HHS and VA competed-award counts fell 34–50% from FY2024 to FY2026.<!-- /auto:limit-fy2026 -->
- **SAM.gov's public search endpoint is undocumented.** This project uses it politely, at about one request per second. The documented alternatives are SAM's API (key required) and its daily CSV extract.
- **Coverage:** every civilian agency for single-offer shares; five departments for the deeper analysis and the forecasts; DoD is excluded.
- **GSA's supply orders** dominate civilian award counts and changed how they record offers, so civilian-wide figures are also given without GSA.

## Responsible use

These numbers describe **markets and procurement patterns, not people**. A high single-offer rate or a pile-up under a threshold is a signal worth a closer look. It is not evidence of wrongdoing, and many have ordinary explanations. No individuals are named or stored. Contact details in SAM.gov records are never saved.

Office-level tables are left out of this first release until the method has been reviewed by a procurement practitioner.

Found an error? Please [open an issue](../../issues).

## Data sources

- [USAspending.gov](https://www.usaspending.gov) API and download service (US Treasury; public domain)
- [SAM.gov](https://sam.gov) contract opportunities (GSA; public)
- [Federal Acquisition Regulation](https://www.acquisition.gov/far) threshold history

## About

Built by **Ellie Burress** with AI assistance: Claude Code (an AI coding assistant) wrote most of the code and analysis for this first version. Questions, corrections and suggestions are welcome.

**License:** code under the [MIT License](LICENSE). Text, figures and result tables under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). The underlying government data is in the public domain.
