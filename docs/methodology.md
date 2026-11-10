# Methodology

<!-- auto:m-header -->First version built October 23–24, 2026. Every result was rebuilt on November 9, 2026 from one public data snapshot, [`data-2026-11-09`](https://github.com/e-burress/federal-contract-competition/releases/tag/data-2026-11-09) (USAspending, downloaded November 9, 2026). USAspending data runs through 2026-09-30. The forecast model (section 5) and the sealed forecasts (section 6) are from the October 24, 2026 build and are frozen.<!-- /auto:m-header -->

Every number in this document is written by `scripts/build_report.py` from the result files in `results/` and `forecasts/`.

Sections 1 to 6 use the FY2024–FY2026 snapshot. Sections 8 to 15 use the long history (FY2008 onward, civilian agencies and Defense), which is still being filled in. Each of those sections names the fiscal years its results use, read from its own result file, and says "not yet available" when the file is missing.

## 1. Data

| Source | What was used | Access |
|---|---|---|
| USAspending.gov transaction download (`POST /api/v2/download/transactions/`) | Contract transactions for every civilian top-tier agency (all except DoD), FY2024–FY2026: purchase orders (B), definitive contracts (D) and contract vehicles (IDV types). One download serves every analysis. | Free, no key |
| SAM.gov public search (the back end of sam.gov's search page) | Open solicitations (notice types *Solicitation* and *Combined Synopsis/Solicitation*) and historical notices for response windows | Free, no key, undocumented |
| FAR history | Simplified-acquisition threshold $250K → $350K and micro-purchase threshold $10K → $15K, both effective 2025-10-01. Every FAR dollar threshold since 2006 is in `reference/thresholds.csv` (section 16) | Public |
| USAspending.gov transaction download, one fiscal year at a time | The long history: the same 36 columns for FY2008 onward, civilian agencies (one request per quarter) and the Department of Defense (one request per month). Sections 8 to 15 | Free, no key |
| SAM.gov contract-opportunity archive files | One file per fiscal year with every past notice. Training data of forecast model version 2 (section 17) | Free, no key |
| SAM.gov entity registration extract (Public V2, monthly) | Counts of registered companies per industry code (section 16). Only counts are kept | Free, no key |

<!-- auto:m-download -->**One download, one snapshot.** `01c_download_civilian.py` downloads the contract transactions of every civilian agency (1,880,096 transactions, FY2024–FY2026), one request per quarter. From them, `02_build_awards.py` builds the five-department table (default) and the civilian table (`--civilian`), and `bunching_lib.py --rebuild` builds the threshold test's base actions. These three tables are published as the snapshot [`data-2026-11-09`](https://github.com/e-burress/federal-contract-competition/releases/tag/data-2026-11-09), downloaded November 9, 2026; `python run.py quick` downloads it and rebuilds every result. `01_download_usaspending.py` (the five departments, one request per agency) and `01b_download_threshold_universe.py` (purchase orders and definitive contracts only) are older alternatives.

The first release (October 24, 2026) used an earlier download that was not kept. Rebuilt from this snapshot, single-offer shares moved by at most 0.05 percentage points (five departments: 97,643 → 97,672 awards, still 22.8%) and bunching ratios by at most 0.02 ($350K after the change: 1.95 → 1.96).<!-- /auto:m-download -->

**Useful discovery.** The USAspending download service accepts a `columns` list. Requesting 36 columns instead of the 297-column bulk format cuts file size about sixfold (about 47 bytes per row instead of about 300). That makes the full FY2008–FY2026 history practical on a laptop. The service caps each file at 500,000 rows, so the download goes one quarter at a time and splits a quarter into months if it would pass the cap.

**Privacy.** SAM.gov notice details include contact names and email addresses. The scripts copy only whitelisted fields (product/service code, industry code, set-aside, dates) in memory and never write raw notice records to disk. No individuals are named or stored anywhere in this project.

## 2. Definitions

- **Award:** one contract award, represented by its **base action** (modification number 0) dated FY2024–FY2026.
- **Competed:** extent-competed codes A, D, E or F, or CDO. Awards competed under simplified procedures count as competed.
- **Single offer:** `number_of_offers_received` equals 1. Awards with blank values or the `999` placeholder <!-- auto:m-999 -->(760 competed awards in the five departments)<!-- /auto:m-999 --> are excluded.
- **Sectors (long history):** civilian agencies except GSA; GSA (shown on its own, because its very small supply orders outnumber everything else and their offer coding changed); the Department of Defense.
- **Coverage (five departments):** <!-- auto:m-coverage -->261,240 awards → 182,905 with a base action in the window → 98,432 competed → **97,672 with a usable offer count** (99.2%). Every civilian agency: section 3b.<!-- /auto:m-coverage -->

## 3. Single-offer analysis

Single-offer shares are computed by department, fiscal year, award type, set-aside and size, with Wilson 95% intervals.

<!-- auto:m-recount -->An independent recount, `scripts/check_counts_sql.py`, reads the raw download with Python's csv module and counts in SQLite (SQL), with no pandas and none of the pipeline's code. It reproduces every count, for the five departments and for every civilian agency (in the five departments: 182,905 base awards, 97,672 competed with a usable offer count, 22,246 single-offer awards). It needs the raw download, so it runs in `python run.py all`.<!-- /auto:m-recount -->

<!-- auto:m-five-table -->| Department | Single-offer share, FY2024–26 | FY2024 | FY2025 | FY2026 |
|---|---|---|---|---|
| HHS | 40.3% (n = 9,590) | 41.8% | 39.6% | 38.1% |
| VA | 35.3% (n = 27,017) | 37.4% | 36.8% | 30.1% |
| DHS | 26.6% (n = 15,159) | 31.0% | 27.8% | 20.3% |
| Interior | 17.9% (n = 15,880) | 20.6% | 17.5% | 14.9% |
| USDA | 6.6% (n = 30,026) | 9.8% | 6.7% | 4.1% |
| **All five** | **22.8%** (n = 97,672) | | | |<!-- /auto:m-five-table -->

<!-- auto:m-other-cuts -->Other cuts:
- **Full-and-open competition only:** VA 32.2%, HHS 19.9%, DHS 19.2%, Interior 10.2%, USDA 3.0%.
- **By solicitation identifier:** awards carrying one had a 14.5% single-offer rate; awards without one, 45.1%.
- **Dollar-weighted** (purchase orders and definitive contracts): 15.7% of competed obligations.
- **Awards of $20M or more:** 18.0% (n = 339).<!-- /auto:m-other-cuts -->

<!-- auto:m-premium -->**Price premium (preliminary).** Federal award data has no unit prices, so a true price premium can't be measured. Within agency × year × award type × 4-character product code cells (58,284 awards, 2,157 cells), single-offer awards are 27.2% smaller in initial value (95% interval −33.6% to −20.1%). That is a size difference, not a price. After award, we compare the dollars obligated by every action in the window with the award's initial potential value (base and all options), within agency × year × 2-character product code × value quintile cells, with standard errors clustered by buying office. Among multi-offer purchase orders in those cells, 4.4% were obligated at 125% or more of their initial value; single-offer ones differed by −0.61 percentage points (−1.15 to −0.08). For definitive contracts the base rate was 2.8% and the difference +1.30 percentage points (−0.17 to +2.77). Dollars obligated as a percentage of initial value (limited to 0% to 300%) averaged 90.9% for multi-offer purchase orders, and single-offer ones differed by +1.01 percentage points (+0.04 to +1.98); for definitive contracts, 97.0% and +1.53 percentage points (−0.96 to +4.02). The differences are small and do not all point the same way, so they show no clear pattern. An earlier version reported growth in potential value of 25% or more, measured with the largest value on any single action. In this data each action records a change, not a running total, so that measure missed most growth; it was replaced by the obligation measures above. Growth of the potential value itself needs the sum of every action's change, which the public data snapshot does not hold.<!-- /auto:m-premium -->

<!-- auto:m-offices -->Office-level shares were computed (370 offices, 180 with at least 100 awards). They are persistent over time: the correlation between FY2024–25 and FY2026 office shares is 0.75. They are not published in this release.<!-- /auto:m-offices -->

## 3b. Every civilian agency (added November 9, 2026)

<!-- auto:m-civ-data -->**Data.** The same download, all civilian agencies, FY2024–FY2026: 1,880,096 transactions. `02_build_awards.py --civilian` applies the same rules as above: 969,601 awards → 812,446 with a base action in the window → 475,212 competed (58.5%) → **466,009 with a usable offer count** (98.1% of competed; 9,201 carry the 999 placeholder). 64 agencies have competed awards with a usable offer count.<!-- /auto:m-civ-data -->

<!-- auto:m-civ-check -->**Check against the five-department table.** The five departments, measured from the civilian table, match it exactly, award for award. See `results/atlas_civilian_five_check.csv`.<!-- /auto:m-civ-check -->

<!-- auto:m-gsa -->**GSA.** 295,322 of the 466,009 awards (63%) are GSA's, almost all very small purchase orders (median award $143). In a check of FY2026 Q1, the most common products were office supplies, hand tools and measuring tools. Their recorded offers changed sharply: one offer on 55.6% in FY2024, 46.6% in FY2025 and 16.8% in FY2026, as most began recording more than one offer. That may be a change in how these automated orders are recorded rather than in competition. Every civilian-wide total is therefore also given without GSA.<!-- /auto:m-gsa -->

<!-- auto:m-civ-table -->| Scope | Single-offer share, FY2024–26 | Competed awards |
|---|---|---|
| All civilian agencies | 34.0% (95% interval 33.9–34.2%) | 466,009 |
| All civilian agencies except GSA | 25.5% (25.3–25.7%) | 170,687 |
| Other civilian agencies, not GSA and not the original five | 29.1% (28.7–29.4%) | 73,015 |
| GSA only | 39.0% | 295,322 |<!-- /auto:m-civ-table -->

<!-- auto:m-civ-trend -->Without GSA, the share fell from 27.9% (FY2024) to 26.3% (FY2025) and 21.5% (FY2026).<!-- /auto:m-civ-trend -->

Shares by agency, agency and year, award type and set-aside (each with and without GSA) are in `results/atlas_civilian_*.csv`; `03b_atlas_civilian.py` writes them.

## 4. Threshold bunching test

<!-- auto:m-threshold-data -->**Data:** civilian purchase orders and definitive contracts (66 agencies), base actions only, from the same download. FY2024: 258,718. FY2025: 236,470. FY2026: 263,196.<!-- /auto:m-threshold-data -->

**Method,** fixed before looking at the post-change period:
- Award values are base-and-all-options value at the base action.
- Values are grouped into $5,000 right-closed bins.
- A degree-5 Poisson polynomial is fitted to the counts outside the window (T − $20K, T + $10K].
- **Bunching ratio** = observed awards in the four bins just under the line ÷ the counterfactual from the fitted curve.
- Intervals come from 300 bootstrap resamples.
- Placebo lines at $200K and $450K.

**Results:**

<!-- auto:m-threshold-table -->| Line | FY2024 | FY2025 | FY2026 | Change, FY2025 → FY2026 |
|---|---|---|---|---|
| $250K | 1.80 (1.64–1.94) | 1.79 (1.64–1.97) | 1.24 (1.13–1.35) | −0.55 (−0.74 to −0.37) |
| $350K | 1.16 (0.95–1.38) | 0.97 (0.81–1.19) | 1.96 (1.69–2.35) | +0.99 (+0.65 to +1.36) |
| $200K (placebo) | 1.31 | 1.46 | 1.31 | −0.15 (−0.38 to +0.10) |
| $450K (placebo) | 1.17 | 0.86 | 1.02 | +0.16 (−0.13 to +0.44) |<!-- /auto:m-threshold-table -->

**Robustness:** the same pattern holds when using obligations instead of value, for purchase orders only, and in each half of FY2026 (`results/threshold_sat_robustness.csv`). <!-- auto:m-threshold-quarters -->By fiscal quarter after the change, the $350K ratio rose 1.51 → 1.65 → 1.90 → 2.18.<!-- /auto:m-threshold-quarters -->

**Micro-purchase line ($10K → $15K).** <!-- auto:m-micro -->Reported purchase orders between $10K and $15K fell 67% (17,293 → 5,675), against −18% between $15K and $20K.<!-- /auto:m-micro --> Actions below the micro-purchase threshold generally aren't reported individually, so the reporting floor itself moved. Counts near $10K–$15K therefore aren't comparable across the change without adjusting for that.

**Exploratory competition test.** The single-offer share in the band that became "simplified" ($250K–$350K) was compared with a control band ($350K–$450K) using a difference-in-differences. <!-- auto:m-did -->The result was −0.86 percentage points (−5.9 to +4.6): no detectable effect so far.<!-- /auto:m-did -->

## 5. Single-offer forecasting model

<!-- auto:m-model-frozen -->These results are from the October 24, 2026 build and are frozen. Its SAM.gov training inputs were not kept, so `run.py` does not rebuild them (scripts 05–08 and 11); the model will be rebuilt on SAM.gov's official archive files. The files are `results/model_*` and `results/resolution_backtest_summary.csv`.<!-- /auto:m-model-frozen -->

<!-- auto:m-model-pop -->- **Population:** 49,556 competed solicitations (one per solicitation number, FY2024–FY2026) with a single-offer share of 19.6%.
- **Response windows:** looked up in SAM.gov for a random sample. 2,398 solicitation numbers were looked up and 1,826 found (76.1%). After dropping missing, negative or extreme windows, 1,802 were usable.
- **Model:** L2 logistic regression (C = 0.03, chosen by 5-fold cross-validation inside the training period).<!-- /auto:m-model-pop -->
- **Features, all known when a notice is posted:**
  - department; product/service code (2 characters); industry code (3 digits); set-aside group
  - solicitation-number letter; notice type
  - the buying office's past single-offer rate (shrunk toward the department rate, computed out-of-fold) and its volume
  - the original response window and a ≤14-day flag
  - presolicitation and sources-sought flags; two keyword flags (brand name; sole/single/limited source)
- **Excluded because they leak future information:** the number of amendments and extended deadlines.

<!-- auto:m-oot -->**Out-of-time test** (train on awards through 2025-09-30, n = 1,251; test on FY2026 awards, n = 551, 14.2% single-offer):
- AUC 0.754 (95% interval 0.695–0.811), against 0.622 for the department-wide baseline.
- Brier score 0.1116 against 0.1196 (6.7% skill).
- Calibration is good below 0.2 (0.142 forecast vs 0.140 observed in the 0.1–0.2 bin). The 0.2–0.3 bin was under-forecast (0.239 vs 0.394, n = 71).<!-- /auto:m-oot -->

<!-- auto:m-effects -->**Largest effects** (odds ratios):
- no set-aside 1.57
- buying-office history 1.33 per standard deviation
- medical-supply product codes 1.19
- small-business set-aside 0.79
- service-disabled-veteran set-aside 0.83
- longer response window 0.89 per standard deviation<!-- /auto:m-effects -->

## 6. Live forecasts and how they will be scored

<!-- auto:m-open -->**Open solicitations** (pulled 2026-10-24): 240 candidate notices in the five modeled departments, 239 of them open, not canceled, of an eligible type and with a deadline between 2026-11-01 and 2026-12-15.<!-- /auto:m-open -->

**Eligibility rule,** fixed in advance: the notice must be open, not canceled, of type Solicitation or Combined Synopsis/Solicitation, with a deadline in the window, first posted on or after 2026-06-01, and an original response window of 0–120 days.

<!-- auto:m-registered -->- 224 notices met the rule. 15 were excluded by the date-plausibility check, mostly for typo'd deadlines.
- **150 were registered**, by stratified random sample (seed 20261023): VA 69, USDA 33, Interior 21, DHS 14, HHS 13.<!-- /auto:m-registered -->

**Resolution:**
- Each registered solicitation number is matched (normalized) to USAspending base actions dated after the forecast. The earliest action wins.
- A forecast is scored only if the award was competed with 1–998 offers.
- The baseline is the department-wide rate.

<!-- auto:m-backtest -->**Expected resolution rate.** A backtest on 2,251 solicitations with March–April 2026 deadlines found that 70.8% matched an award and 66.3% were scoreable. Expect about 100 of the 150 forecasts to resolve by 2027-03-31. Single-offer events among them will be few, so the first batch will measure calibration roughly, not skill.<!-- /auto:m-backtest -->

<!-- auto:m-f2 -->**Threshold forecast (PIO-F2):** FY2027 Q1 bunching ratios, scored once that quarter's data is in USAspending (from 2027-02-15).<!-- /auto:m-f2 -->

**How the scoring works.** [`scoring-forecasts.md`](scoring-forecasts.md) is a step-by-step guide for a person, with no AI involved. PIO-F1 is scored with `python run.py score-f1` (`scripts/09_resolve_forecast.py`) on each check date. The script first fingerprints the private forecast file and refuses to score it if the SHA-256 does not match the published one. It then applies the resolution rule written into the file: normalised solicitation number, base actions dated on or after the forecast date, the earliest one wins, scored only if competed with 1 to 998 offers. Unresolved forecasts are counted by reason and not scored. Scores are the Brier score, log loss, ROC-AUC and a calibration table, with the same numbers for the stored baseline. Only aggregate numbers are made public, and only once at least 20 forecasts have resolved, with small cells hidden; the row-level file stays private because it names buying offices. PIO-F2 is scored by `12_resolve_threshold.py` with exactly the estimator of section 4; the daily job tracks it with `13_track_threshold.py` and checks its fingerprint on every run.

## 6b. Daily sealed forecasts

Every morning `31_daily_forecast.py` forecasts each new open solicitation in SAM.gov's daily list of active notices with model version 2 (section 17), writes the day's forecasts to one private batch file and publishes its SHA-256 fingerprint, a row of counts and short hashes of the solicitation numbers in [`forecasts/daily/`](../forecasts/daily/). `32_score_daily.py` scores the sealed batches once the awards appear, with the rule written in every batch: an award counts only if it is a base action with the same normalised solicitation number dated on or after the day offers were due, competed with 1 to 998 offers. Only totals are published, with small cells hidden. The step-by-step guide is [`daily-forecasts.md`](daily-forecasts.md).

<!-- auto:m-daily-forecasts -->**Daily forecasts.** The daily series is built and tested; the first batch has not been sealed yet.<!-- /auto:m-daily-forecasts -->

## 7. Data-quality monitor

**Script:** `16_quality_monitor.py`, run by `python run.py quick` on the FY2024–FY2026 civilian table.

**Why.** When an agency starts coding "2 or 3 offers" where it used to code "1 offer", its single-offer share falls although real competition did not change. A sudden drop in award counts can mean late or missing reporting. The monitor looks for such breaks automatically.

**What is checked,** for every agency × award kind (purchase order, definitive contract, contract vehicle) × fiscal quarter (from the action date): the shares of the evaluation set with exactly 1, 2 and 3 offers; the share of competed awards whose offer count is blank or 999; the share of all awards marked competed; and the number of awards.

**Method.** A share in one quarter is compared with the previous four quarters pooled (at least two of them must exist) using a two-proportion z-test with a pooled standard error. Both conditions must hold: a high |z| bar, because there are thousands of comparisons, and a minimum move in percentage points. Award counts are compared with the same quarter one year earlier, to avoid seasonality, with no z-test. The newest quarter is skipped for counts if the data stop before its last day.

<!-- auto:m-quality -->**Rules** (from `results/quality_summary.json`): both sides need at least 200 awards; a share is flagged only if |z| ≥ 6 and it moved by at least 10 percentage points; award counts are flagged when they move by 40% or more against the same quarter one year earlier. Share baseline: previous four quarters pooled (at least 2 present).

**Results** (FY2024–FY2026 civilian table): 85 flags, in quarters from FY2024 Q3 to FY2026 Q4. The largest of each kind:

| What changed | Flags | Largest flag |
|---|---|---|
| the share of awards marked competed | 28 | General Services Administration, definitive contracts, FY2025 Q4: 78.5% → 15.6% |
| award counts | 27 | General Services Administration, contract vehicles, FY2025 Q1: 401 → 2,805 awards |
| the share with one offer | 14 | Department of Justice, purchase orders, FY2024 Q4: 28.6% → 57.4% |
| the share with three offers | 6 | USDA, definitive contracts, FY2026 Q1: 35.8% → 7.9% |
| the share with no usable offer count | 5 | General Services Administration, contract vehicles, FY2024 Q4: 80.2% → 34.7% |
| the share with two offers | 5 | General Services Administration, purchase orders, FY2026 Q2: 39.9% → 56.6% |<!-- /auto:m-quality -->

**Limits.** A flag does not say which is true: a coding change, late reporting or a real change. It says "check before you trust numbers from this agency and quarter". The monitor reads the FY2024–FY2026 table only; the long history is not monitored yet.

## 8. The long history: yearly tables

**Scripts:** `17_history_download.py` downloads one fiscal year of contract transactions (the same 36 columns and award types as section 1; civilian agencies one request per quarter, Defense one request per month; a window too big for the download service is split into smaller windows until it fits; resumable). Every file is checked: the rows in it must match USAspending's own count for the same filters and dates, or the window is asked again in smaller pieces. Each year's `history_FY<year>.json` says whether the year passed (`verified`). See [`download-integrity.md`](download-integrity.md). It runs on GitHub's computers (`.github/workflows/history.yml`, one job per year). `18_history_build.py` turns each year into two compact tables, published as a public release of this repository; `python run.py history` downloads them.

**What the tables hold.**
- `base_actions_FY<year>`: one row per award whose base action (modification 0) is dated in the year: what was bought, how it was competed, offers received, set-aside, values at award, agency, buying-office code, a company code, and whether it is a Defense award. Same rules as section 2 (competed = A, D, E, F or CDO; usable offers = 1 to 998; single = exactly one).
- `award_year_FY<year>`: one row per award with any action in the year: number of actions, dollars obligated, the largest and the latest single change in potential value and in exercised value, the last action date, and how many actions were option exercises, scope changes (supplemental agreements, change orders) or terminations. Joining the years gives each award's history after it was made. On each transaction the base-and-all-options value is the change made by that action, not a running total. A later build of the tables adds the sum of each year's changes (`value_sum`, `exercised_sum`); the first build (`history-v1`) does not have it, so total growth of potential value cannot be built from it.

**Company codes, and why they are not anonymous.** `vendor_id` is the first 16 hexadecimal digits of SHA-256("fcc-v1:" + UEI), or of SHA-256("fcc-v1:duns:" + DUNS) when there is no UEI. It lets the same company be followed across awards (incumbency in section 13, clusters in section 15). It is **not anonymous**: anyone with the identifier can compute the same code, contract numbers are kept, and the underlying records are public on USAspending.gov. A company whose records carry a UEI in some years and only a DUNS in others (the switch came in April 2022) gets two codes; separate UEI and DUNS codes on each base action can link them. Buying-office names, company names and raw company identifiers are left out to keep the tables small and focused, not to hide anything. Every published result is at agency level or coarser; office codes and company codes are used only inside the computations.

**Completeness check** (`28_reconcile.py`). For every fiscal year with a yearly table, our number of transactions and our obligated dollars are compared with USAspending's own totals (`spending_by_transaction_count` and `spending_over_time`), once with the same filters as our downloads (award types, action dates in the fiscal year, the same list of top-tier awarding agencies) and once with no agency filter. The first comparison checks that nothing was lost; the second measures what the agency list leaves out. <!-- auto:m-reconcile -->`28_reconcile.py` compares our yearly transaction counts and dollars with USAspending's own totals. With the same filters as our downloads, FY2008–FY2026 (19 years checked) differ by at most 0.00 percent. Awarding agencies missing from USAspending's top-tier agency list are not downloaded; they hold at most 0.29 percent of a year's transactions.<!-- /auto:m-reconcile -->

## 9. Single-offer shares since FY2008

**Script:** `19_history_atlas.py`. Same definitions as sections 3 and 3b: among base awards that were competed with a usable offer count, the share with exactly one offer, with 95% Wilson intervals, by sector, fiscal year and award kind (`results/history_by_fy.csv`) and by agency and fiscal year (`results/history_by_agency_fy.csv`).

<!-- auto:m-history -->**Results** (FY2008–FY2026). Competed awards with a usable offer count: 2,137,895 (civilian agencies except GSA), 920,560 (GSA) and 6,395,387 (Defense). Single-offer share (95% interval):

| Fiscal year | Civilian agencies except GSA | GSA | Defense |
|---|---|---|---|
| FY2008 | 47.9% (47.6–48.1%), n = 150,395 | 21.3% (21.0–21.7%), n = 50,671 | 34.5% (34.3–34.7%), n = 277,736 |
| FY2009 | 34.4% (34.1–34.6%), n = 132,506 | 33.5% (33.0–33.9%), n = 43,641 | 31.1% (30.9–31.3%), n = 272,739 |
| FY2010 | 34.8% (34.6–35.1%), n = 176,043 | 37.2% (36.8–37.7%), n = 40,233 | 31.2% (31.0–31.3%), n = 274,009 |
| FY2011 | 36.9% (36.7–37.2%), n = 181,159 | 35.9% (35.3–36.4%), n = 31,140 | 26.6% (26.4–26.7%), n = 246,865 |
| FY2012 | 31.8% (31.6–32.1%), n = 163,046 | 35.8% (35.2–36.4%), n = 24,913 | 22.5% (22.4–22.7%), n = 219,426 |
| FY2013 | 30.4% (30.1–30.6%), n = 148,629 | 32.5% (31.0–34.1%), n = 3,374 | 22.9% (22.7–23.1%), n = 222,579 |
| FY2014 | 29.0% (28.8–29.2%), n = 146,399 | 24.1% (22.7–25.4%), n = 3,825 | 23.7% (23.5–23.9%), n = 231,121 |
| FY2015 | 29.4% (29.1–29.6%), n = 139,916 | 24.8% (23.4–26.3%), n = 3,450 | 22.4% (22.3–22.5%), n = 428,450 |
| FY2016 | 29.6% (29.3–29.8%), n = 133,228 | 27.9% (26.3–29.6%), n = 2,935 | 20.8% (20.7–20.9%), n = 486,383 |
| FY2017 | 29.3% (29.1–29.6%), n = 132,429 | 33.8% (32.8–34.8%), n = 8,464 | 20.3% (20.1–20.4%), n = 478,867 |
| FY2018 | 28.4% (28.1–28.6%), n = 109,368 | 30.0% (29.0–31.1%), n = 7,160 | 21.7% (21.5–21.8%), n = 510,256 |
| FY2019 | 30.2% (29.8–30.5%), n = 78,078 | 33.4% (33.1–33.7%), n = 73,859 | 25.4% (25.3–25.6%), n = 451,059 |
| FY2020 | 31.2% (30.8–31.5%), n = 71,212 | 40.5% (40.1–40.8%), n = 73,338 | 24.6% (24.4–24.7%), n = 379,697 |
| FY2021 | 30.8% (30.4–31.1%), n = 68,141 | 39.5% (39.1–39.8%), n = 77,127 | 23.9% (23.8–24.0%), n = 352,917 |
| FY2022 | 30.5% (30.2–30.9%), n = 66,530 | 42.8% (42.5–43.2%), n = 79,084 | 25.8% (25.6–25.9%), n = 341,560 |
| FY2023 | 29.6% (29.2–29.9%), n = 70,129 | 51.7% (51.4–52.0%), n = 102,024 | 26.1% (26.0–26.3%), n = 320,003 |
| FY2024 | 27.9% (27.6–28.2%), n = 65,609 | 55.6% (55.3–55.9%), n = 96,643 | 26.8% (26.7–27.0%), n = 323,223 |
| FY2025 | 26.3% (25.9–26.6%), n = 54,314 | 46.6% (46.3–46.9%), n = 94,271 | 27.1% (26.9–27.2%), n = 340,308 |
| FY2026 | 21.5% (21.1–21.8%), n = 50,764 | 16.8% (16.6–17.0%), n = 104,408 | 26.9% (26.7–27.0%), n = 238,189 |<!-- /auto:m-history -->

**Limits.** Defense data are published 90 days late, so the latest Defense year is incomplete until its last quarter is out. A change in a share can be a change in how offers are recorded (section 7). GSA's yearly counts swing widely, so its share is hard to read as competition.

## 10. Pile-ups under every simplified-acquisition limit since FY2008

**Script:** `20_threshold_history.py`. **Data:** base actions of purchase orders and definitive contracts, civilian agencies and Defense separately, every fiscal year present.

**Lines.** The limit was $100,000 until FY2010, $150,000 from 2010-10-01 (FY2011), $250,000 from 2018 (agency by agency through class deviations, so FY2018 and FY2019 are transition years) and $350,000 from 2025-10-01 (FY2026).

**The multi-threshold estimator.** For every fiscal year and each of the four lines, the bunching ratio is the number of awards in the four $5,000 bins just under the line divided by the number a smooth curve predicts there. The curve is the one of section 4 (a degree-5 Poisson polynomial over the line's window), but it is fitted outside the bins near **any** of the four lines, so one line's pile-up cannot distort another line's baseline. The windows for $250K and $350K are the ones fixed in `bunching_lib.SPEC`. Intervals come from 200 bootstrap resamples of the awards. If the rule shapes behavior, each line's ratio should rise when it becomes the limit and fade after it stops being one.

<!-- auto:m-threshold-history -->**Results** (FY2008–FY2026; bold = the line was the limit that year):

| Fiscal year | Civilian agencies, $100K | Civilian agencies, $150K | Civilian agencies, $250K | Civilian agencies, $350K | Defense, $100K | Defense, $150K | Defense, $250K | Defense, $350K |
|---|---|---|---|---|---|---|---|---|
| FY2008 | **1.40** | 1.13 | 1.32 | 1.41 | **1.71** | 1.18 | 1.35 | 1.26 |
| FY2009 | **1.55** | 1.09 | 1.25 | 1.27 | **1.79** | 1.15 | 1.30 | 1.15 |
| FY2010 | **1.53** | 1.16 | 1.24 | 1.09 | **1.75** | 1.31 | 1.26 | 1.04 |
| FY2011 | 1.34 | **1.39** | 1.36 | 1.14 | 1.25 | **2.09** | 1.23 | 1.04 |
| FY2012 | 1.09 | **1.42** | 1.21 | 1.26 | 1.05 | **2.83** | 1.22 | 1.07 |
| FY2013 | 1.16 | **1.59** | 1.12 | 1.21 | 1.05 | **2.87** | 1.10 | 1.33 |
| FY2014 | 1.17 | **1.26** | 1.09 | 1.23 | 1.01 | **2.86** | 1.10 | 1.08 |
| FY2015 | 1.14 | **1.40** | 1.18 | 1.15 | 1.02 | **2.89** | 1.18 | 0.98 |
| FY2016 | 1.03 | **1.45** | 1.26 | 1.25 | 0.97 | **2.66** | 1.19 | 1.14 |
| FY2017 | 1.05 | **1.36** | 1.18 | 1.36 | 1.03 | **2.47** | 0.78 | 1.27 |
| FY2018 | 1.04 | **1.33** | **1.33** | 1.25 | 1.00 | **1.92** | **1.18** | 1.16 |
| FY2019 | 1.06 | 1.09 | **1.46** | 1.22 | 1.07 | 1.19 | **2.00** | 1.02 |
| FY2020 | 1.08 | 1.08 | **1.80** | 1.22 | 1.14 | 0.97 | **2.65** | 1.06 |
| FY2021 | 1.04 | 1.08 | **1.83** | 0.93 | 1.13 | 1.03 | **2.61** | 1.06 |
| FY2022 | 0.91 | 1.30 | **1.80** | 1.19 | 1.10 | 1.06 | **2.67** | 1.20 |
| FY2023 | 1.13 | 1.25 | **2.02** | 1.15 | 0.87 | 1.22 | **2.50** | 1.05 |
| FY2024 | 1.15 | 1.34 | **1.81** | 1.16 | 0.93 | 1.01 | **2.72** | 1.13 |
| FY2025 | 1.26 | 1.31 | **1.80** | 0.97 | 0.99 | 1.08 | **2.53** | 0.99 |
| FY2026 | 1.17 | 1.08 | 1.24 | **1.96** | 0.99 | 1.09 | 1.43 | **1.64** |

In FY2008–FY2026, the pile-up under a simplified-acquisition limit was larger in the years it was the limit than in the years it was not, for all 8 line-and-sector pairs that had both kinds of year. Bunching ratio on average: 1.57 against 1.17 for civilian agencies and 2.32 against 1.11 for Defense. At each of the 6 changes of the limit in these years (civilian and Defense counted separately), the pile-up under the new line grew and the one under the old line shrank, from the last year before the change to the first full year after it. The largest move: Defense under $250K, from 0.78 (0.70–0.88) in FY2017 to 2.00 (1.85–2.15) in FY2019 (95% intervals).

Each change, from the last year before it to the first full year after it (95% intervals):

- Civilian agencies, $100K → $150K: under $150K, 1.16 (1.05–1.29) in FY2010 → 1.39 (1.27–1.55) in FY2011; under $100K, 1.53 (1.45–1.60) → 1.34 (1.27–1.42).
- Civilian agencies, $150K → $250K: under $250K, 1.18 (1.06–1.33) in FY2017 → 1.46 (1.32–1.61) in FY2019; under $150K, 1.36 (1.22–1.49) → 1.09 (0.98–1.17).
- Civilian agencies, $250K → $350K: under $350K, 0.97 (0.81–1.20) in FY2025 → 1.96 (1.63–2.30) in FY2026; under $250K, 1.80 (1.62–1.97) → 1.24 (1.13–1.37).
- Defense, $100K → $150K: under $150K, 1.31 (1.20–1.43) in FY2010 → 2.09 (1.90–2.27) in FY2011; under $100K, 1.75 (1.69–1.81) → 1.25 (1.20–1.29).
- Defense, $150K → $250K: under $250K, 0.78 (0.70–0.88) in FY2017 → 2.00 (1.85–2.15) in FY2019; under $150K, 2.47 (2.29–2.69) → 1.19 (1.11–1.27).
- Defense, $250K → $350K: under $350K, 0.99 (0.83–1.14) in FY2025 → 1.64 (1.39–1.89) in FY2026; under $250K, 2.53 (2.36–2.72) → 1.43 (1.31–1.54).<!-- /auto:m-threshold-history -->

**Limits.** This is a pattern, not proof of why it happens: budgets written to fit the rules, small-business rules tied to the line and contract splitting all produce it. Ratios above 1 in years a line was not the limit show that round values attract awards anyway.

## 11. The 2018 change, agency by agency

**Script:** `21_threshold_staggered.py`. **Data:** base actions of civilian purchase orders and definitive contracts (and Defense separately), FY2016–FY2021, of agencies with a documented adoption date of high or medium confidence in `reference/threshold_adoption_2018.csv`. Agencies without a date are left out, never guessed.

**Staggered design.** The FY2018 National Defense Authorization Act raised the limit from $150,000 to $250,000, but each agency could use it only after its own class deviation, issued on dates from February 2018 to 2020. If the rule itself drives the pile-up, each agency's pile-up should move from $150K to $250K when **that** agency switched, not when others did. Event time is counted in quarters (91.31 days) from the agency's own date; quarter 0 starts on adoption day. For each event quarter from −8 to +8, all agencies' awards in that quarter are pooled and the bunching ratios under $150K and $250K are computed with the estimator of section 10, with 200 bootstrap resamples.

**Placebo.** The same calculation with every adoption date moved one year **earlier**. A real effect shows a break at quarter 0 with the true dates and at quarter +4 (not 0) with the placebo dates.

<!-- auto:m-staggered -->**Agencies:** 22 civilian agencies with a documented date of high or medium confidence (7 medium).

**Civilian agencies.** Pooling the awards of civilian agencies (FY2016–FY2021) by quarter around each agency's own date, the ratio under $250K averaged 1.10 in the 8 quarters before the switch and 1.51 in the 9 quarters from it on. Its largest one-quarter rise came at quarter 0 (1.12 to 1.64). The 95% intervals of those two quarters (0.83–1.57 and 1.32–2.14) overlap, so the timing rests on the whole pattern, not on one quarter. Placebo: with every date moved one year earlier, the largest rise came at quarter +4, four quarters later, as a real effect predicts.

**Defense.** Pooling the awards of Defense (FY2016–FY2021) by quarter around its adoption date, the ratio under $250K averaged 0.99 in the 8 quarters before the switch and 2.09 in the 9 quarters from it on. Its largest one-quarter rise came at quarter +8 (2.07 to 3.16). The 95% intervals of those two quarters (1.75–2.47 and 2.80–3.66) do not overlap. Placebo: with every date moved one year earlier, the largest rise came at quarter +8; a real effect would put it at quarter +12, outside the window.

Defense has one adoption date (April 13, 2018), so its test is a before-and-after comparison around that date, not a staggered one.

Bunching ratio (95% interval) by quarter around the adoption date (quarter 0 starts on adoption day):

| Quarter | Civilian agencies, $150K | Civilian agencies, $250K | Civilian agencies, $250K, placebo dates | Defense, $150K | Defense, $250K | Defense, $250K, placebo dates |
|---|---|---|---|---|---|---|
| −8 | 2.07 (1.65–2.62) | 1.05 (0.83–1.38) | 0.94 (0.15–3.21) | 3.13 (2.62–3.85) | 1.32 (1.08–1.68) |  |
| −7 | 1.92 (1.55–2.30) | 1.23 (1.00–1.52) | 0.92 (0.50–1.70) | 2.56 (2.28–2.92) | 1.19 (1.02–1.40) | 1.46 (0.70–2.60) |
| −6 | 1.10 (0.84–1.43) | 0.99 (0.78–1.30) | 1.16 (0.88–1.59) | 2.81 (2.27–3.64) | 1.04 (0.79–1.43) | 1.02 (0.74–1.32) |
| −5 | 1.56 (1.17–2.04) | 1.01 (0.73–1.41) | 1.31 (1.02–1.77) | 2.47 (2.08–2.95) | 0.98 (0.77–1.27) | 1.07 (0.80–1.41) |
| −4 | 1.76 (1.35–2.20) | 1.28 (0.99–1.63) | 1.05 (0.81–1.31) | 2.24 (1.91–2.65) | 0.70 (0.56–0.88) | 1.32 (1.07–1.68) |
| −3 | 1.56 (1.30–1.85) | 1.05 (0.85–1.30) | 1.24 (0.97–1.54) | 2.36 (2.11–2.67) | 0.71 (0.61–0.84) | 1.19 (1.02–1.40) |
| −2 | 1.42 (1.14–1.79) | 1.07 (0.82–1.34) | 0.99 (0.75–1.33) | 2.92 (2.46–3.64) | 0.98 (0.76–1.32) | 1.04 (0.74–1.44) |
| −1 | 1.69 (1.28–2.22) | 1.12 (0.83–1.57) | 1.01 (0.69–1.36) | 3.05 (2.54–3.63) | 1.02 (0.82–1.30) | 0.98 (0.76–1.21) |
| 0 | 1.95 (1.59–2.39) | 1.64 (1.32–2.14) | 1.28 (1.03–1.61) | 1.42 (1.26–1.62) | 0.99 (0.85–1.15) | 0.70 (0.58–0.83) |
| +1 | 1.46 (1.25–1.73) | 1.52 (1.26–1.77) | 1.05 (0.86–1.31) | 1.45 (1.30–1.66) | 1.45 (1.31–1.63) | 0.71 (0.61–0.82) |
| +2 | 1.17 (0.91–1.47) | 1.29 (1.07–1.63) | 1.07 (0.82–1.41) | 1.47 (1.23–1.74) | 1.49 (1.25–1.78) | 0.98 (0.71–1.18) |
| +3 | 1.37 (1.09–1.71) | 1.47 (1.17–1.87) | 1.15 (0.86–1.52) | 1.40 (1.18–1.65) | 1.74 (1.48–2.05) | 1.02 (0.81–1.29) |
| +4 | 1.32 (1.10–1.60) | 1.51 (1.27–1.88) | 1.61 (1.33–2.09) | 1.36 (1.20–1.62) | 2.49 (2.17–2.91) | 0.99 (0.85–1.12) |
| +5 | 1.19 (1.00–1.40) | 1.30 (1.07–1.54) | 1.52 (1.28–1.78) | 0.89 (0.79–1.01) | 2.54 (2.30–2.84) | 1.45 (1.30–1.63) |
| +6 | 1.13 (0.85–1.42) | 1.52 (1.27–1.90) | 1.28 (0.96–1.62) | 1.14 (0.94–1.36) | 2.90 (2.37–3.37) | 1.50 (1.28–1.81) |
| +7 | 1.24 (0.97–1.54) | 1.52 (1.20–1.91) | 1.49 (1.16–2.01) | 0.85 (0.72–0.97) | 2.07 (1.75–2.47) | 1.74 (1.48–2.09) |
| +8 | 1.28 (1.07–1.50) | 1.81 (1.53–2.14) | 1.51 (1.25–1.82) | 1.12 (0.93–1.31) | 3.16 (2.80–3.66) | 2.49 (2.15–2.91) |<!-- /auto:m-staggered -->

**Limits.** A pattern test: it cannot tell budgets written to fit the rules from contracts split to stay under them. Some dates are of medium confidence, and contracting offices may have started earlier or later than their agency's memo.

## 12. A scan of every FAR dollar threshold

**Script:** `27_threshold_scan.py`. **Data:** base actions of purchase orders and definitive contracts, with one universe per threshold family (for example, certified cost or pricing data: awards that were not competed and not commercial; subcontracting plans: awards not set aside for small business; the 8(a) limits: 8(a) sole-source awards). Value = base and all options at award. Sectors: civilian, Defense, and both together. Thresholds and their dates come from `reference/thresholds.csv`; the 2018 civilian change uses each agency's own date, and agencies without a documented date are left out of FY2018–FY2020.

**Method.**
- Bins scale with the line: 40 bins of 2% of the line over a window from 40% below to 40% above it. The zone is the four bins just under the line. The two bins just above it are left out of the fit, because awards that moved down leave a gap there.
- The smooth curve is a cubic Poisson polynomial fitted to the other bins. Also left out of the fit: bins near any value that was a threshold of any family in that year or the year before, and bins holding a multiple of the line's leading power of ten, where awards heap on round values. Lines with too few awards or fitted bins get no ratio.
- Intervals: 200 Poisson-bootstrap replicates of a fine histogram; all lines of one family use the same replicates, so their differences have correct intervals.
- Round numbers: each line is compared with nearby round placebo values that were never a threshold, and with the same value in years when it was not a threshold.
- Each change: ratios under the old and the new line in the two years before and the two years after. Difference-in-differences = (new line: after − before) − (old line: after − before). It is positive if the pile-up moved with the line; "moved" means its 95% interval is above zero, "moved the wrong way" below zero.

<!-- auto:m-scan -->**Results** (FY2008–FY2026; civilian agencies and Defense; the combined rows are in the file). 56 of 104 changes can be tested:

| Limit | Change | Date | Sector | Difference-in-differences (95% interval) | Reading |
|---|---|---|---|---|---|
| micro-purchase | $3,000 → $3,500 | 2015-10-01 | civilian | −1.11 (−1.20 to −1.01) | moved the wrong way; neither side alone is clear |
| micro-purchase | $3,500 → $10K | by agency, 2018-2020 | civilian | −0.66 (−0.73 to −0.59) | moved the wrong way; neither side alone is clear |
| micro-purchase | $10K → $15K | 2025-10-01 | civilian | −0.42 (−0.54 to −0.28) | moved the wrong way; neither side alone is clear |
| micro-purchase | $3,000 → $3,500 | 2015-10-01 | Defense | −0.20 (−0.25 to −0.16) | moved the wrong way; neither side alone is clear |
| micro-purchase | $3,500 → $5,000 | 2017-07-13 | Defense | +0.10 (+0.04 to +0.16) | moved; the old line's pile-up shrank |
| micro-purchase | $5,000 → $10K | 2018-08-31 | Defense | +0.04 (0.00 to +0.09) | moved; the new line's pile-up grew |
| micro-purchase | $10K → $15K | 2025-10-01 | Defense | +0.26 (+0.18 to +0.33) | moved; the new line's pile-up grew and the old one's shrank |
| simplified acquisition | $100K → $150K | 2010-10-01 | civilian | +1.04 (+0.90 to +1.20) | moved; the new line's pile-up grew and the old one's shrank |
| simplified acquisition | $150K → $250K | by agency, 2018-2020 | civilian | +0.57 (+0.41 to +0.69) | moved; the new line's pile-up grew and the old one's shrank |
| simplified acquisition | $250K → $350K | 2025-10-01 | civilian | +1.16 (+0.93 to +1.39) | moved; the new line's pile-up grew and the old one's shrank |
| simplified acquisition | $100K → $150K | 2010-10-01 | Defense | +2.71 (+2.56 to +2.84) | moved; the new line's pile-up grew and the old one's shrank |
| simplified acquisition | $150K → $250K | 2018-04-13 | Defense | +2.88 (+2.71 to +3.06) | moved; the new line's pile-up grew and the old one's shrank |
| simplified acquisition | $250K → $350K | 2025-10-01 | Defense | +1.77 (+1.53 to +2.01) | moved; the new line's pile-up grew and the old one's shrank |
| certified cost or pricing data | $650K → $700K | 2010-10-01 | civilian | −0.13 (−0.42 to +0.27) | no clear move; neither side alone is clear |
| certified cost or pricing data | $700K → $750K | 2015-10-01 | civilian | +0.38 (−0.12 to +0.82) | no clear move; neither side alone is clear |
| certified cost or pricing data | $750K → $2M | 2018-07-01 | civilian | −0.60 (−1.34 to +0.01) | no clear move; neither side alone is clear |
| certified cost or pricing data | $2M → $2.5M | 2025-10-01 | civilian | −0.24 (−1.90 to +1.58) | no clear move; neither side alone is clear |
| certified cost or pricing data | $650K → $700K | 2010-10-01 | Defense | −0.00 (−0.29 to +0.36) | no clear move; the new line's pile-up grew |
| certified cost or pricing data | $700K → $750K | 2015-10-01 | Defense | +0.82 (+0.16 to +1.50) | moved; the old line's pile-up shrank |
| certified cost or pricing data | $750K → $2M | 2018-07-01 | Defense | +0.97 (+0.36 to +1.71) | moved; the old line's pile-up shrank |
| certified cost or pricing data | $2M → $2.5M | 2025-10-01 | Defense | +0.74 (−0.27 to +1.81) | no clear move; neither side alone is clear |
| simplified procedures for commercial items | $5.5M → $6.5M | 2010-10-01 | civilian | +0.39 (−0.20 to +0.96) | no clear move; neither side alone is clear |
| simplified procedures for commercial items | $6.5M → $7M | 2015-10-01 | civilian | 0.00 (−0.61 to +0.85) | no clear move; neither side alone is clear |
| simplified procedures for commercial items | $7M → $7.5M | 2020-10-01 | civilian | +0.04 (−0.48 to +0.61) | no clear move; neither side alone is clear |
| simplified procedures for commercial items | $7.5M → $9M | 2025-10-01 | civilian | −0.10 (−0.92 to +0.45) | no clear move; neither side alone is clear |
| simplified procedures for commercial items | $5.5M → $6.5M | 2010-10-01 | Defense | +0.11 (−0.44 to +0.69) | no clear move; neither side alone is clear |
| simplified procedures for commercial items | $6.5M → $7M | 2015-10-01 | Defense | +0.36 (−0.50 to +1.03) | no clear move; the old line's pile-up shrank |
| simplified procedures for commercial items | $7M → $7.5M | 2020-10-01 | Defense | −0.33 (−0.89 to +0.20) | no clear move; neither side alone is clear |
| subcontracting plan | $550K → $650K | 2010-10-01 | civilian | +0.28 (+0.04 to +0.50) | moved; the new line's pile-up grew |
| subcontracting plan | $650K → $700K | 2015-10-01 | civilian | +0.17 (−0.14 to +0.43) | no clear move; neither side alone is clear |
| subcontracting plan | $700K → $750K | 2020-10-01 | civilian | +0.35 (+0.13 to +0.54) | moved; the old line's pile-up shrank |
| subcontracting plan | $750K → $900K | 2025-10-01 | civilian | +0.71 (+0.32 to +1.13) | moved; the new line's pile-up grew |
| subcontracting plan | $550K → $650K | 2010-10-01 | Defense | +0.36 (+0.20 to +0.55) | moved; the new line's pile-up grew |
| subcontracting plan | $650K → $700K | 2015-10-01 | Defense | −0.05 (−0.34 to +0.17) | no clear move; neither side alone is clear |
| subcontracting plan | $700K → $750K | 2020-10-01 | Defense | −0.27 (−0.59 to −0.03) | moved the wrong way; neither side alone is clear |
| subcontracting plan | $750K → $900K | 2025-10-01 | Defense | +0.91 (+0.59 to +1.39) | moved; the new line's pile-up grew and the old one's shrank |
| subcontracting plan (construction) | $1M → $1.5M | 2010-10-01 | civilian | −0.32 (−1.77 to +1.26) | no clear move; neither side alone is clear |
| subcontracting plan (construction) | $1M → $1.5M | 2010-10-01 | Defense | −0.38 (−1.54 to +0.59) | no clear move; neither side alone is clear |
| justification approval, level 1 | $550K → $650K | 2010-10-01 | civilian | +0.27 (−0.09 to +0.65) | no clear move; neither side alone is clear |
| justification approval, level 1 | $650K → $700K | 2015-10-01 | civilian | +0.51 (+0.03 to +0.99) | moved; neither side alone is clear |
| justification approval, level 1 | $700K → $750K | 2020-10-01 | civilian | +0.30 (−0.07 to +0.65) | no clear move; neither side alone is clear |
| justification approval, level 1 | $750K → $900K | 2025-10-01 | civilian | +1.22 (+0.54 to +1.89) | moved; the new line's pile-up grew |
| justification approval, level 1 | $550K → $650K | 2010-10-01 | Defense | +0.59 (+0.27 to +0.90) | moved; the new line's pile-up grew |
| justification approval, level 1 | $650K → $700K | 2015-10-01 | Defense | −0.15 (−0.61 to +0.36) | no clear move; neither side alone is clear |
| justification approval, level 1 | $700K → $750K | 2020-10-01 | Defense | +0.57 (+0.12 to +0.95) | moved; neither side alone is clear |
| justification approval, level 1 | $750K → $900K | 2025-10-01 | Defense | +1.45 (+0.65 to +2.28) | moved; the new line's pile-up grew and the old one's shrank |
| justification approval, level 2 | $11.5M → $12.5M | 2010-10-01 | civilian | −0.19 (−1.41 to +1.15) | no clear move; neither side alone is clear |
| justification approval, level 2 | $12.5M → $13.5M | 2015-10-01 | civilian | +0.31 (−1.39 to +1.78) | no clear move; the old line's pile-up shrank |
| justification approval, level 2 | $13.5M → $15M | 2020-10-01 | civilian | −0.64 (−2.36 to +0.57) | no clear move; neither side alone is clear |
| justification approval, level 2 | $11.5M → $12.5M | 2010-10-01 | Defense | +0.45 (−0.19 to +1.28) | no clear move; neither side alone is clear |
| justification approval, level 2 | $12.5M → $13.5M | 2015-10-01 | Defense | +0.84 (−0.20 to +1.90) | no clear move; neither side alone is clear |
| justification approval, level 2 | $13.5M → $15M | 2020-10-01 | Defense | +0.96 (−0.36 to +2.55) | no clear move; neither side alone is clear |
| 8(a) competition limit (not manufacturing) | $3.5M → $4M | 2010-10-01 | civilian | +7.21 (+4.30 to +11.50) | moved; the new line's pile-up grew and the old one's shrank |
| 8(a) competition limit (not manufacturing) | $4M → $4.5M | 2020-10-01 | civilian | +4.69 (+2.93 to +6.29) | moved; the new line's pile-up grew |
| 8(a) competition limit (not manufacturing) | $3.5M → $4M | 2010-10-01 | Defense | +5.35 (+3.89 to +7.32) | moved; the new line's pile-up grew and the old one's shrank |
| 8(a) competition limit (not manufacturing) | $4M → $4.5M | 2020-10-01 | Defense | +3.95 (+2.78 to +4.98) | moved; the new line's pile-up grew and the old one's shrank |

Not testable (48): 18 need years before or after the data's range and 30 have too few awards near the line.

**Level check.** In 9 of 17 limit-and-sector pairs with both kinds of year, the pile-up was larger in the years the value was the limit than in years it was not (95% interval of the difference above zero).<!-- /auto:m-scan -->

**Limits.** Many lines move on the same day (1 October of 2010, 2015, 2020 and 2025), so a test of one change is confounded by the others, and the first day of a fiscal year has its own pattern. Justification level 1 and the subcontracting plan have had the same value since 2010 and overlapping universes, so their results are not independent. The reporting floor moves with the micro-purchase line, so counts just under it change for reporting reasons too. Samples near the largest lines are small. Delivery orders and BPA calls are not in the data.

## 13. What goes with single offers

**Script:** `23_drivers.py`. **Data:** the competed awards with a usable offer count in the yearly tables.

**Shares.** For every factor, the single-offer share with a 95% Wilson interval, pooled and by sector: award kind; size band; set-aside group; product or service and its category; industry sector (2-digit NAICS); extent competed; solicitation procedure; pricing type; commercial-item procedures; whether the award carries a solicitation number; fiscal quarter, September and its last 7 days; buying-office workload (base awards per office per year, in bands); incumbency (the winning company won an award from the same office for the same 4-character product code in one of the three previous fiscal years, so it is measured only for years whose three previous years are present); market depth (registered companies in the award's 6-digit NAICS, section 16).

**Model.** A logistic regression on a stratified random sample of competed purchase orders and definitive contracts: an equal quota per fiscal year × sector, with a fixed seed, at most 400,000 awards, so that each sector-year counts equally. Controls: agency (for Defense, its component) and sector × fiscal year; their effects are not reported. Extent competed and solicitation procedure enter as one combined "competition route" factor, because the two codes almost always move together. Results are average marginal effects in percentage points, each level against the most common level of its factor, with 95% intervals from standard errors clustered by buying office. The model is fitted by Newton-Raphson on a sparse design with a tiny ridge (1.0) that keeps rare levels finite; levels with fewer than 300 sampled awards are pooled into "other"; tests check the fit against statsmodels.

**Blocks.** The model is fitted on earlier years and scored on the latest complete year held out. The change in log loss and AUC when each block of factors is dropped, and when it is used alone, shows how much each block explains.

<!-- auto:m-drivers -->**Results** (model years FY2011–FY2026; shares use FY2008–FY2026). Quota per fiscal year × sector: 8,333 awards (seed 20261109). Incumbency is measured for FY2011–FY2026. Defense years left out as incomplete: FY2026.

| Scope | Sampled awards | Single-offer share | Buying offices (clusters) | McFadden R² | AUC (in sample) |
|---|---|---|---|---|---|
| all sectors | 364,914 | 31.1% | 4,791 | 0.142 | 0.745 |
| civilian agencies except GSA | 133,328 | 30.7% | 3,442 | 0.161 | 0.765 |
| GSA | 106,591 | 39.5% | 338 | 0.248 | 0.816 |
| Defense | 124,995 | 24.3% | 1,015 | 0.048 | 0.643 |

**Strongest associations** (one level per factor, ranked by the end of the 95% interval nearest zero; average marginal effects in percentage points against the most common level):

- product/service category: product group 37: agricultural machinery rather than product group 53: hardware and abrasives, −29.9 percentage points (−36.9 to −23.0)
- set-aside: service-disabled veteran-owned small business rather than no set-aside, −24.9 percentage points (−27.8 to −22.1)
- industry (NAICS sector): 51: information rather than 31-33: manufacturing, +13.2 percentage points (+8.8 to +17.6)
- competition route: full and open after exclusion of sources; sealed bid rather than competed under simplified acquisition; simplified acquisition, −12.6 percentage points (−17.0 to −8.3)
- initial value: >10M rather than <25K, −12.2 percentage points (−16.4 to −8.0)

**Blocks** (train FY2011-FY2024, test FY2025; 323,249 training and 24,999 test awards; full model AUC 0.810):

| Block | AUC drop without it (95% interval) | Log-loss increase without it (95% interval) | AUC with this block alone |
|---|---|---|---|
| set-aside | 0.0203 (0.0173 to 0.0224) | 0.0237 (0.0216 to 0.0251) | 0.660 |
| agency or Defense component | 0.0194 (0.0167 to 0.0216) | 0.0216 (0.0199 to 0.0231) | 0.610 |
| what is bought | 0.0192 (0.0160 to 0.0225) | 0.0416 (0.0399 to 0.0442) | 0.718 |
| competition route and solicitation number | 0.0074 (0.0062 to 0.0084) | 0.0048 (0.0041 to 0.0055) | 0.626 |
| contract terms | 0.0013 (0.0008 to 0.0017) | 0.0009 (0.0006 to 0.0012) | 0.587 |
| incumbency | 0.0012 (0.0008 to 0.0016) | 0.0013 (0.0011 to 0.0015) | 0.559 |
| award type and size | 0.0004 (−0.0002 to 0.0009) | 0.0010 (0.0005 to 0.0013) | 0.560 |
| buying-office workload | −0.0002 (−0.0005 to 0.0001) | 0.0000 (−0.0001 to 0.0002) | 0.561 |
| timing (September rush) | −0.0004 (−0.0006 to −0.0001) | −0.0001 (−0.0002 to 0.0000) | 0.508 |
| market depth | −0.0007 (−0.0012 to −0.0001) | 0.0000 (−0.0002 to 0.0004) | 0.539 |<!-- /auto:m-drivers -->

**Limits.** These are associations, not causes: the factors travel together and with things the data do not record. Coding differences between agencies can look like drivers; the agency and sector × year controls absorb level differences but not coding that differs within an agency. Company codes changed with the switch from DUNS to UEI in 2022, so incumbency is undercounted around it. Market depth is one 2026 count applied to every year. Out-of-time log loss also moves with the overall single-offer rate; AUC is the steadier measure. Defense years whose September is missing or thin are left out.

## 14. After award

**Script:** `24_outcomes.py`. **Question:** do competed awards that drew one offer turn out differently after award than awards with two or more?

**Cohorts.** Purchase orders and definitive contracts (not contract vehicles, whose values are ceilings) that were competed, have a usable offer count, and were made in fiscal year t with an initial potential value of $25,000 or more. That is the line above which a solicitation must be posted publicly, and it did not move in the period. Follow-up runs from t to t+3, and from t to t+1 so that recent cohorts can be shown; a cohort is used only when every follow-up year is present.

**Outcomes.** Dollars obligated over the follow-up divided by the initial potential value (limited to 0 to 3); obligated 125% or more; the largest single rise in potential value divided by the initial value, and whether one rise reached 25%; any scope change and their number (limited to 10); any termination; an option exercised, among awards that had options. Outliers are handled with fixed limits, not percentiles.

**Design.** Single-offer and multi-offer awards are compared **within cells** of buying office × fiscal year and quarter × award kind × 4-character product code × value bin (fixed bins, each about a doubling), by subtracting cell means. Cells in which every award, or no award, had one offer drop out. Standard errors are clustered **two ways**, by buying office and by cell, and are never smaller than by office alone; intervals use Student's t with (offices − 1) degrees of freedom. The office is in the cell because, with department or bureau cells, the comparison failed its own checks on FY2008 and FY2009; the quarter is in the cell because single-offer awards are made later in the fiscal year.

**Checks.** Two placebos that competition cannot affect: whether the award was signed on an even day of the month, and a coin flip tied to the award's ID number. Balance tests on things fixed at award (value, planned length, options, month). Other size floors; bureau and department cells; cells without the product code; value deciles; cells without the quarter; cells of five or more awards only.

<!-- auto:m-outcomes -->**Results** (award-year tables present: FY2008–FY2026). Follow-up t to t+3 (through the third fiscal year after award): cohorts FY2008–FY2023, 1,503,545 awards at or above the size floor (28.6% single-offer), of which 491,223 in 64,805 cells with both kinds of award, from 2,360 buying offices. Follow-up t to t+1 (through the next fiscal year): cohorts FY2008–FY2025, 1,681,226 awards at or above the size floor (28.6% single-offer), of which 562,391 in 73,448 cells with both kinds of award, from 2,422 buying offices.

| Outcome | Multi-offer mean, t to t+3 | Single minus multi, t to t+3 (95% interval) | Multi-offer mean, t to t+1 | Single minus multi, t to t+1 (95% interval) |
|---|---|---|---|---|
| dollars obligated, as a share of the initial value | 94.6% | +2.25 (+1.43 to +3.08) | 94.4% | +2.26 (+1.60 to +2.92) |
| obligated 125% or more of the initial value | 2.2% | −0.08 (−0.25 to +0.09) | 1.6% | −0.06 (−0.20 to +0.08) |
| largest single rise in potential value, as a share of the initial value | 2.5% | −0.07 (−0.34 to +0.19) | 1.8% | −0.06 (−0.30 to +0.17) |
| a single rise in potential value of 25% or more | 2.3% | −0.11 (−0.26 to +0.05) | 1.6% | −0.05 (−0.17 to +0.07) |
| growth of potential value | −3.6% | +2.34 (+1.55 to +3.14) | −3.4% | +2.30 (+1.66 to +2.94) |
| potential value grew 25% or more | 2.5% | −0.06 (−0.21 to +0.09) | 1.9% | −0.03 (−0.16 to +0.09) |
| any scope change | 5.2% | −0.19 (−0.35 to −0.04) | 4.0% | −0.14 (−0.28 to −0.01) |
| number of scope changes | 0.091 | −0.003 (−0.007 to 0.000) | 0.063 | −0.003 (−0.006 to −0.000) |
| any termination | 1.2% | −0.42 (−0.56 to −0.29) | 0.8% | −0.34 (−0.42 to −0.26) |
| an option exercised (awards with options) | 61.5% | +1.85 (+0.30 to +3.40) | 52.2% | +2.23 (+0.76 to +3.69) |

Shares and ratios are in percent and percentage points; the number of scope changes is a count.

**Placebo checks** (t to t+3): signed on an even day (placebo) −0.052 (−0.332 to +0.229); coin flip tied to the award number (placebo) +0.168 (−0.139 to +0.474).

**Balance on things fixed at award** (t to t+3): log of the initial value +0.006 (+0.004 to +0.009) (interval excludes zero); planned length in years +0.016 (−0.000 to +0.031); has options −0.021 (−0.149 to +0.107); month of award +0.003 (−0.004 to +0.010).

**Checks** (t to t+3; other size floors and other cells): 30 alternative estimates. In 7 of them the interval changes side of zero compared with the main design: dollars obligated, as a share of the initial value, size floor $250,000; dollars obligated, as a share of the initial value, size floor $1,000,000; obligated 125% or more of the initial value, cells: without the product code; any scope change, size floor $250,000; any scope change, size floor $1,000,000; any scope change, cells: deciles of initial value in place of fixed value bins (first design); any scope change, cells: only cells with five or more awards (the rule in 03_atlas.py).<!-- /auto:m-outcomes -->

**What is not measured.** Prices (the data hold none). Total growth of potential value: the yearly tables of the first build keep each year's largest and last change, not their sum, so the value-jump outcomes are a lower bound. Whether extra obligations, scope changes or terminations were justified; they are often legitimate. **Limits.** Only awards in cells with both kinds of award carry the estimate, a minority, and offices that never or always get one offer are not in it. Single-offer awards can differ in ways the cells do not capture (urgency, specialised requirements, incumbents). Values are typed in by staff. A follow-up that ends in the newest year is short for Defense.

## 15. Split-like purchase clusters

**Script:** `26_split_screen.py`. **Data:** base actions of purchase orders and definitive contracts with a company code, a buying office and a positive value; sectors civilian except GSA, GSA and Defense.

**Clusters.** Same company + same office + same 4-character product code, linked when consecutive awards are at most 30 days apart. Clusters are followed across fiscal-year boundaries when the next year is present; a cluster belongs to the fiscal year of its last award. A cluster is **split-like** at line L when it has two or more awards, every award is at or under L, and the total is above L. L is the threshold in force for the agency on the date of the cluster's first award (the 2018 change by agency).

**Placebo lines.** Repeat buying makes split-like clusters at any line. The shares at fake lines (multiples 0.5 to 2.0 of the real line) trace the smooth pattern; a straight line through log(share) against log(multiple) gives the expected share at the real line. Excess ratio = actual share / expected share (1.0 = no excess). Fake lines within 8% of another real dollar line are not used. 95% intervals: 200 bootstrap resamples of companies within each sector and year.

**Donut variant.** Every award at least 10% under the line. Single awards bunched just under the line make split-like clusters more common at the real line even without splitting; the donut removes that mechanical channel.

**When a limit rose.** For each increase (old line X0 to new line X1), the band measure counts clusters with every award at or under X0 and a total above X0 but at or under X1. Once the line rises, such a requirement fits in one simplified purchase, so its excess should fall.

<!-- auto:m-split -->**Results** (FY2008–FY2026; links at most 30 days apart; placebo multiples 0.50, 0.60, 0.70, 0.80, 1.25, 1.40, 1.60, 2.00; 200 bootstrap resamples of company within sector and fiscal year). Excess ratio, pooled over the years (1.00 = no excess; 95% interval):

| Sector | Simplified acquisition | Same, donut | Micro-purchase | Same, donut |
|---|---|---|---|---|
| civilian agencies except GSA | 1.06 (1.05–1.07) | 1.03 (1.02–1.05) | 1.24 (1.22–1.26) | 1.08 (1.06–1.10) |
| GSA | 1.06 (1.01–1.17) | 1.07 (1.02–1.18) | 1.39 (1.24–1.58) | 1.05 (0.93–1.18) |
| Defense | 1.11 (1.09–1.13) | 1.03 (1.01–1.05) | 1.06 (1.04–1.07) | 1.03 (1.01–1.04) |

**When a limit rose** (band between the old and the new limit, all awards; change in the excess ratio, after minus before):

| Limit | Change | Sector | Before | After | Change (95% interval) |
|---|---|---|---|---|---|
| simplified acquisition | $100K → $150K | civilian agencies except GSA | 1.08 (FY2008–FY2010) | 0.97 (FY2011–FY2013) | −0.11 (−0.27 to +0.03) |
| simplified acquisition | $100K → $150K | GSA | 1.44 (FY2008–FY2010) | 1.17 (FY2011–FY2013) | −0.27 (−1.48 to +0.56) |
| simplified acquisition | $100K → $150K | Defense | 1.09 (FY2008–FY2010) | 1.02 (FY2011–FY2013) | −0.07 (−0.12 to −0.02) |
| simplified acquisition | $150K → $250K | civilian agencies except GSA | 1.05 (FY2015–FY2017) | 0.93 (FY2021–FY2023) | −0.12 (−0.19 to −0.06) |
| simplified acquisition | $150K → $250K | GSA | 1.20 (FY2015–FY2017) | 1.02 (FY2021–FY2023) | −0.18 (−0.71 to +0.67) |
| simplified acquisition | $150K → $250K | Defense | 1.05 (FY2015–FY2017) | 0.99 (FY2021–FY2023) | −0.07 (−0.19 to +0.05) |
| simplified acquisition | $250K → $350K | civilian agencies except GSA | 1.03 (FY2023–FY2025) | 1.05 (FY2026) | +0.02 (−0.19 to +0.27) |
| simplified acquisition | $250K → $350K | GSA | 0.39 (FY2023–FY2025) | 0.60 (FY2026) | +0.22 (−0.26 to +0.86) |
| simplified acquisition | $250K → $350K | Defense | 1.04 (FY2023–FY2025) | 0.98 (FY2026) | −0.06 (−0.35 to +0.24) |
| micro-purchase | $3,000 → $3,500 | civilian agencies except GSA | 1.10 (FY2013–FY2015) | 1.25 (FY2016–FY2017) | +0.14 (−0.10 to +0.46) |
| micro-purchase | $3,000 → $3,500 | Defense | 1.16 (FY2013–FY2015) | 1.04 (FY2016–FY2017) | −0.12 (−0.23 to −0.02) |
| micro-purchase | $3,500 → $10K | civilian agencies except GSA | 1.06 (FY2016–FY2017) | 0.93 (FY2021–FY2023) | −0.13 (−0.25 to +0.03) |
| micro-purchase | $3,500 → $10K | GSA | 1.03 (FY2016–FY2017) | 0.98 (FY2021–FY2023) | −0.06 (−0.55 to +0.45) |
| micro-purchase | $3,500 → $10K | Defense | 1.05 (FY2016–FY2017) | 0.99 (FY2021–FY2023) | −0.05 (−0.09 to −0.02) |
| micro-purchase | $10K → $15K | civilian agencies except GSA | 0.92 (FY2023–FY2025) | 0.86 (FY2026) | −0.06 (−0.25 to +0.20) |
| micro-purchase | $10K → $15K | GSA | 1.01 (FY2023–FY2025) | 1.68 (FY2026) | +0.66 (−0.37 to +2.29) |
| micro-purchase | $10K → $15K | Defense | 1.06 (FY2023–FY2025) | 0.95 (FY2026) | −0.10 (−0.19 to −0.03) |

It fell clearly in 5, rose clearly in 0 and was unclear in 12 of 17 tests.<!-- /auto:m-split -->

**Limits.** It cannot tell a prohibited split from legitimate repeat buying (monthly supplies, task-sized orders). It is a pattern in public data, not evidence that any purchase was split. Dollar totals of split-like clusters are left blank when fewer than 3 clusters stand behind them; company and office codes never leave the computation.

## 15b. Who wins the awards: the supplier base

**Script:** `33_supplier_base.py`. **Data:** every base action (new award) in the yearly tables, one fiscal year read at a time; sectors civilian except GSA, GSA and Defense, and all sectors together.

**Company.** One company id per award: `vendor_id`, with DUNS-based and UEI-based codes linked into one id wherever a record carries both (see *Identifier switch*). Counts are of registered identifiers, so a company with several registrations counts more than once, and parents and subsidiaries count separately.

**Two series.** All awards, and awards above $25,000 of initial potential value (base and all options). Reporting of very small purchases changed over time (for example the FY2008 Veterans Affairs micro-purchases, see the `note` column of `results/history_by_fy.csv`, and the higher micro-purchase limits from FY2016 and FY2018), so the above-$25,000 series is the headline. Inside a series every rule uses that series' awards: in the above-$25,000 series, a company "won" in a year if it won an award above $25,000. Dollars are not adjusted for inflation.

**New companies.** A company is new in year t if it won no award in t−1, t−2 and t−3. The look-back is fixed, so every year is measured the same way; the first measurable year is the fourth year present, and a year whose three previous years are not all present gets no value. As a sensitivity, "never seen" means no award in any earlier year present (since FY2008); it falls over time by construction, because the look-back grows. New and staying are judged across all sectors and award kinds: a company that won from any agency in the look-back years is not new. Reported: the number of new companies and their share of companies, of awards and of initial dollars.

**Staying.** Of the companies that won in year t, the share that won again in t+1, and within t+1 to t+3. Only when those years are present and complete (a Defense year whose September is missing or thin is incomplete).

**Concentration.** Dollar measures use purchase orders and definitive contracts: a contract vehicle's initial value is a ceiling for future orders, often billions, not money to be spent. Initial values above $10 billion are left out as likely recording errors (a few a year, counted in `results/supplier_base_concentration.csv`). Top shares: the share of initial dollars won by the top 1% and top 10% of companies, with the dollars obligated at award as a check. Market concentration: for each 4-digit NAICS industry with at least 20 awards in the year, the Herfindahl-Hirschman index (HHI, the sum of the squared percentage shares of initial dollars, from near 0 to 10,000), summarized as the award-weighted median and the share of awards in industries above 2,500. A 4-digit NAICS industry is much broader than a real market, and agencies do not buy everything in one market, so these HHIs are rough.

**Link to competition.** Among competed awards with a usable offer count (as in section 9): the single-offer share of awards won by new companies and by established ones, with 95% Wilson intervals; new companies' share of awards by set-aside group (the groups of section 13); and new companies' share of awards with one offer and with two or more. Pooled over the years whose new-company measures are comparable.

**Identifier switch.** Until April 2022 companies were identified by DUNS numbers, after that by UEI. `vendor_id` is made from the UEI when a record has one, otherwise from the DUNS, so one company could carry two ids and look new around 2022. `vendor_uei_code` and `vendor_duns_code` use the same hash, so every `vendor_id` equals one of them. Wherever a table has both columns, each record carrying both links the two codes (union-find over all such records; one id per linked group), and every `vendor_id` of every year is mapped to its group. A measure whose window of years spans FY2022 is marked "not comparable" (column `note`, flags `*_comparable`, hollow markers on the chart) unless every year of the window up to FY2022 has the two columns and under 1% of its awards carry only a DUNS that no record links to a UEI. The unlinked series (raw `vendor_id`) is kept beside the linked one (`*_unlinked_ids`). No rows are dropped.

**Privacy.** Company and buying-office codes are used inside the computation only. No output names or codes a company or an office, no statistic describes one company, and every cell resting on fewer than 10 companies is left blank. No single industry's HHI is published.

<!-- auto:m-supplier-base -->**Results** (fiscal years present: FY2008–FY2026; incomplete: FY2026). All sectors, awards above $25,000:

| Fiscal year | Awards | Companies | New companies (3-year look-back) | Never seen since the first year | Won again next year | Won again within 3 years | Top 1% share of dollars | Top 10% share | Median HHI (award-weighted) |
|---|---|---|---|---|---|---|---|---|---|
| FY2008 | 183,648 | 57,373 |  |  | 46.2% | 61.7% | 78.8% | 94.5% | 652 |
| FY2009 | 180,208 | 57,825 |  | 54.2% | 46.9% | 60.7% | 71.2% | 92.1% | 446 |
| FY2010 | 184,495 | 58,414 |  | 43.4% | 45.2% | 59.2% | 68.1% | 91.5% | 437 |
| FY2011 | 176,644 | 54,117 | 35.8% | 35.8% | 46.0% | 60.5% | 74.1% | 93.2% | 546 |
| FY2012 | 164,388 | 50,460 | 34.7% | 31.5% | 46.0% | 62.1% | 73.4% | 93.1% | 568 |
| FY2013 | 155,041 | 45,957 | 32.7% | 26.6% | 49.6% | 65.2% | 78.6% | 94.3% | 442 |
| FY2014 | 160,703 | 48,684 | 36.3% | 27.7% | 47.8% | 64.5% | 64.8% | 90.5% | 482 |
| FY2015 | 157,291 | 47,646 | 35.4% | 25.9% | 48.5% | 65.0% | 66.5% | 91.2% | 322 |
| FY2016 | 158,315 | 47,855 | 36.0% | 25.7% | 49.1% | 65.2% | 66.0% | 91.2% | 444 |
| FY2017 | 161,185 | 47,991 | 34.2% | 24.1% | 49.0% | 65.5% | 71.1% | 92.6% | 351 |
| FY2018 | 172,881 | 47,588 | 34.0% | 23.7% | 49.4% | 65.3% | 68.5% | 91.6% | 529 |
| FY2019 | 164,772 | 46,623 | 33.0% | 21.8% | 50.0% | 66.4% | 65.4% | 90.8% | 331 |
| FY2020 | 162,182 | 47,338 | 34.3% | 22.8% | 48.1% | 64.9% | 68.0% | 91.7% | 557 |
| FY2021 | 154,502 | 45,969 | 34.1% | 22.2% | 48.4% | 64.7% | 69.0% | 92.1% | 515 |
| FY2022 | 154,704 | 45,135 | 33.2% | 21.9% | 50.8% | 66.6% | 71.3% | 92.7% | 411 |
| FY2023 | 160,178 | 46,590 | 34.0% | 22.3% | 49.9% |  | 67.3% | 91.6% | 497 |
| FY2024 | 158,158 | 45,757 | 33.9% | 22.4% | 49.2% |  | 67.0% | 91.3% | 409 |
| FY2025 | 162,999 | 44,466 | 33.0% | 22.0% |  |  | 65.1% | 90.7% | 410 |
| FY2026 | 139,045 | 39,759 | 30.0% | 19.3% |  |  | 68.5% | 92.0% | 577 |

**Identifier switch.** Tables with both identifier codes: FY2008–FY2026. 434,425 DUNS codes are linked to a UEI code; 0 linked groups hold more than two codes. In those tables every award carries a UEI, and none carries only a DUNS that no record links to a UEI. Linking changes the new-company share by at most 0.00 percentage points in any year. A one-time sample from the USAspending download service (1,452 transactions of Department of the Interior, 2010-03-01 to 2010-03-05 (FY2010), checked November 10, 2026) found 100.0% with a UEI ([reference/uei_backfill_check.json](../reference/uei_backfill_check.json)).

**Link to competition** (FY2011–FY2025 pooled, all sectors, awards above $25,000): single-offer share of competed awards won by new companies 21.0% (20.8–21.1%), by established companies 26.9% (26.8–27.0%); new companies won 9.5% (9.4–9.6%) of competed awards with one offer and 12.7% (12.7–12.8%) of those with two or more; new companies' share of awards by set-aside: service-disabled veteran-owned small business 8.6% (8.4–8.8%); 8(a) 8.6% (8.5–8.8%); no set-aside 11.1% (11.0–11.1%); HUBZone small business 11.2% (10.5–11.8%); veteran-owned small business 15.0% (13.4–16.9%); small business 15.5% (15.4–15.6%); women-owned small business 21.0% (20.2–21.9%).<!-- /auto:m-supplier-base -->

**Limits.** Only prime awards: subcontractors are not counted. Only new awards: orders under existing contract vehicles (delivery and task orders) are not in these tables, so a company that wins only such orders is missing, and a falling count can partly reflect a shift of buying toward orders under vehicles. These are patterns, not accusations and not proof of causes: fewer companies is not by itself less competition. GAO also reports fewer businesses, small and larger, receiving Defense contract obligations over FY2011 to FY2020 ([GAO-22-104621](https://www.gao.gov/products/gao-22-104621)); its counts use a different base (all obligations, Defense only) and are not compared number for number here.

## 15c. Market lookup for buyers

**Script:** `34_market_lookup.py`. **Data:** the yearly tables, one fiscal year read at a time with explicit columns (no office, company or solicitation columns are read). **Population:** competed awards with a usable offer count (the eval set of section 9).

**Groups.** Industry group: the first 4 digits of the NAICS code, with the official 2022 titles from the Census Bureau's code file (`reference/naics4_titles.csv`); a code that the awards still carry from an earlier list gets its official title from the 2017 list, labeled for example "Software Publishers (2017 code)", and `older_code` is true; a code found in none of the 2022, 2017, 2012 or 2007 lists is labeled "industry <code> (older code)". Codes are not merged, so when the list changed the same industry can appear under two codes, each holding only the awards recorded under it; groups with an older code are left out of the lists of thinnest and deepest markets. Product or service group: the first 2 characters of the product or service code, as in section 3 (for services, the title is that of the first letter). Scopes: all sectors, civilian except GSA, GSA and Defense.

**Window.** The last five complete fiscal years, the same years for every scope. A year is complete when it has ended and its Defense data are not cut off by the 90-day publication delay (`defense_incomplete` in `19_history_atlas.py`); when the newest year is incomplete for Defense it is left out for every scope. The earlier window is the five years before. A change between the windows is given only when every year of both is present.

**Measures.** Single-offer share with a 95% Wilson interval; the 25th, 50th and 75th percentiles of the number of offers (linear interpolation); the share with one or two offers; the single-offer share under a small-business set-aside (every small-business type together: small business, 8(a), HUBZone, SDVOSB, WOSB/EDWOSB, VOSB) and with no set-aside; the share in each size band of section 13; the change from the earlier window in percentage points, with a 95% interval (normal approximation).

**Cells.** A share, percentile or change is published only when it rests on at least 100 competed awards; fewer means blank, not zero. An "all sectors" row where GSA holds more than half of the awards says so in its `note`, because GSA's very small supply orders and their changing offer coding can set the whole group's share; such groups are left out of the lists of thinnest and deepest markets. A change is marked "compare with care" (`change_note`) when the group's awards grew or shrank more than three times between the windows, for product groups whose IT codes were redefined in FY2021, and for GSA. The earlier window holds the 2018 rise of the micro-purchase and simplified-acquisition limits, so the mix of small purchases differs between the windows.

**Registered companies** (industry groups only), from `reference/market_depth_naics.csv`. `registered_primary` counts companies whose main NAICS code is in the group; each company has one main code, so this is a count of distinct companies. A company that lists several codes of the group would be counted once per code if the 6-digit `n_any` counts were added up, so the file gives a range instead: at least `registered_any_largest` distinct companies (the largest single code) and at most `registered_any_listings` (the sum) list a code of the group. The SAM.gov extract is not kept on disk, so distinct companies were not recounted.

<!-- auto:m-market-lookup -->**Results.** Recent window FY2021–FY2025, earlier window FY2016–FY2020; left out as incomplete: FY2026. Cell minimum: 100 competed awards.

| Scope | Competed awards | Single-offer share (95% interval) | Offers: 25th, 50th, 75th percentile | One or two offers |
|---|---|---|---|---|
| All sectors | 2,451,883 | 30.4% (30.3–30.4%) | 1, 2, 4 | 50.3% |
| Civilian except GSA | 324,723 | 29.1% (29.0–29.3%) | 1, 3, 5 | 44.0% |
| GSA | 449,149 | 47.8% (47.7–47.9%) | 1, 2, 3 | 72.9% |
| Department of Defense | 1,678,011 | 25.9% (25.8–26.0%) | 1, 3, 5 | 45.5% |

Groups with at least 100 competed awards (all sectors): 247 of 330 industry groups and 149 of 164 product or service groups. Check: the yearly totals of competed awards with a usable offer count equal those of `results/history_by_fy.csv` for every sector in FY2016–FY2025. Registered companies: SAM.gov extract of November 1, 2026.<!-- /auto:m-market-lookup -->

## 16. Reference data

<!-- auto:m-reference -->**Reference data.** [`reference/thresholds.csv`](../reference/thresholds.csv) lists 108 value periods of 28 FAR dollar thresholds, each with its source and the words that show the value ([notes](../reference/thresholds.md)). [`reference/threshold_adoption_2018.csv`](../reference/threshold_adoption_2018.csv) gives the date each agency adopted the 2018 limits: 24 of its 33 rows (agencies and the FAR itself) have a date, from February 27, 2018 to August 31, 2020, 7 of them of medium confidence ([notes](../reference/README.md)). [`reference/market_depth_naics.csv`](../reference/market_depth_naics.csv) counts the companies registered in SAM.gov to compete for federal contracts in each industry code: 1,085 codes, a median of 842 companies per code, on November 1, 2026. A registration is not a capable bidder ([notes](../reference/market_depth.md)).<!-- /auto:m-reference -->

- **Thresholds** (`reference/thresholds.csv`, compiled by hand): values read in the Federal Register inflation rules, the statutes and DoD class deviations, checked against the eCFR. Several thresholds move on the same day.
- **2018 adoption dates** (`reference/threshold_adoption_2018.csv`, compiled by hand): the date each agency's class deviation took effect, with the evidence quoted and a confidence level. Dates of low confidence are left blank and never guessed.
- **Market depth** (`25_market_depth.py`): from SAM.gov's monthly public entity extract, active registrations for all awards, counted per 6-digit NAICS code and by state. Only counts are written; names, addresses and identifiers never leave the line parser. A registration is not a capable bidder, firms list many codes, and the count is one recent snapshot.

## 17. SAM.gov archive and forecast model version 2

**Archive tables** (`22_sam_archive.py`). SAM.gov publishes one archive file per fiscal year with every past notice (about 0.5 to 1.3 GB each). A notice sits in the file of the fiscal year of its posting date, and each row is one version of a notice, so a solicitation's versions can sit in two files. The script streams a file to a temporary folder outside the repository, reads only whitelisted columns, and writes one table of notice versions and one of solicitations (first posting, first and final deadline, earlier presolicitation or sources-sought notice, award notice, response windows). Never written: contacts, awardee, addresses, descriptions, links, title text and office names; e-mail addresses and phone numbers inside numbers are masked. The description is used in memory only, for two keyword flags.

**Model version 2** (`30_model_v2.py`). Trained on every archived solicitation that can be matched to a competed award in the history tables, for every agency, with the outcome rule of `09_resolve_forecast.py`. Evaluation is rolling-origin: for each test year, train on solicitations posted and awarded before it and test on the solicitations posted in it, against simple baselines and version 1's feature set refitted. Which model is shipped follows rules fixed in the script. Public files are at agency level or coarser.

<!-- auto:m-model-v2 -->- **Forecast model, version 2 (built November 10, 2026).** It is the model of the daily forecasts (see *Pre-registered forecasts*). Trained on 930,423 solicitations from SAM.gov's archive that were matched to a competed award (award years FY2008–FY2026; its award history ends September 30, 2026). Tested out of time on FY2009–FY2026, the shipped L2 logistic regression ranks solicitations with an AUC of 0.714 (0.590 for the best simple baseline, the buying office's past rate, and 0.701 for version 1's features refitted), with a Brier score of 0.159 against 0.181 for that baseline and a calibration slope of 0.98 (1.00 is ideal). How it was chosen: by a rule written down before fitting (lowest out-of-time log loss over the last five test years; among candidates within 0.5% of the best, the one closest to the earlier model). The rule picked "logistic v2, half-life 3 years"; "logistic v2, half-life 3 years, correction by sector" is shipped instead, because the simpler pick left forecasts miscalibrated by sector. That choice was made after the results were known; the card records it with the numbers (`selection.amendment`). Version 1 (section 5) is the earlier model, frozen since the October 24, 2026 build. Both are risk indicators about markets, not statements about anyone's conduct. Details: [`results/model_v2_card.json`](../results/model_v2_card.json).<!-- /auto:m-model-v2 -->

## 18. Known limitations

- Offer counts are entered by contracting staff. GAO found miscoding in 2010.
- FY2026 is a break year: the Oct 1 – Nov 12, 2025 shutdown, the move of reporting into SAM.gov, and staffing cuts. <!-- auto:m-limit-fy2026 -->HHS and VA competed-award counts fell 34–50% from FY2024 to FY2026.<!-- /auto:m-limit-fy2026 -->
- DoD is excluded from the FY2024–FY2026 tables because of its 90-day public reporting delay. It is in the long history (sections 8 to 15), where the newest Defense year is incomplete until its last quarter is published.
- The SAM.gov search endpoint is undocumented. Its `publishDate` field means the date of the *last amendment*, so original posting dates need a lookup on the parent notice. Some deadlines are typos.
- The FY2024–FY2026 analysis covers five departments for the deeper analysis and the forecasts and every civilian agency for single-offer shares. The long history adds civilian agencies and Defense from FY2008 on, and is still being filled in: <!-- auto:limit-history -->The single-offer history uses FY2008–FY2026 so far.<!-- /auto:limit-history -->
- The history tables carry company codes derived from public identifiers. They are not anonymous (section 8).
- Agencies change how they record offers and competition (section 7). A change over time can be a change in recording.
- None of these analyses measures prices or shows why a pattern exists. They are signals about markets, not accusations.
