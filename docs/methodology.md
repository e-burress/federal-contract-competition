# Methodology

<!-- auto:m-header -->
First version built October 23–24, 2026. Every result was rebuilt on November 9, 2026 from one public data snapshot, [`data-2026-11-09`](https://github.com/e-burress/federal-contract-competition/releases/tag/data-2026-11-09) (USAspending, downloaded November 9, 2026). USAspending data runs through 2026-09-30. The forecast model (section 5) and the sealed forecasts (section 6) are from the October 24, 2026 build and are frozen.
<!-- /auto:m-header -->

Every number in this document is written by `scripts/build_report.py` from the result files in `results/` and `forecasts/`.

## 1. Data

| Source | What was used | Access |
|---|---|---|
| USAspending.gov transaction download (`POST /api/v2/download/transactions/`) | Contract transactions for every civilian top-tier agency (all except DoD), FY2024–FY2026: purchase orders (B), definitive contracts (D) and contract vehicles (IDV types). One download serves every analysis. | Free, no key |
| SAM.gov public search (the back end of sam.gov's search page) | Open solicitations (notice types *Solicitation* and *Combined Synopsis/Solicitation*) and historical notices for response windows | Free, no key, undocumented |
| FAR history | Simplified-acquisition threshold $250K → $350K and micro-purchase threshold $10K → $15K, both effective 2025-10-01 | Public |

<!-- auto:m-download -->
**One download, one snapshot.** `01c_download_civilian.py` downloads the contract transactions of every civilian agency (1,880,096 transactions, FY2024–FY2026), one request per quarter. From them, `02_build_awards.py` builds the five-department table (default) and the civilian table (`--civilian`), and `bunching_lib.py --rebuild` builds the threshold test's base actions. These three tables are published as the snapshot [`data-2026-11-09`](https://github.com/e-burress/federal-contract-competition/releases/tag/data-2026-11-09), downloaded November 9, 2026; `python run.py quick` downloads it and rebuilds every result. `01_download_usaspending.py` (the five departments, one request per agency) and `01b_download_threshold_universe.py` (purchase orders and definitive contracts only) are older alternatives.

The first release (October 24, 2026) used an earlier download that was not kept. Rebuilt from this snapshot, single-offer shares moved by at most 0.05 percentage points (five departments: 97,643 → 97,672 awards, still 22.8%) and bunching ratios by at most 0.02 ($350K after the change: 1.95 → 1.96).
<!-- /auto:m-download -->

**Useful discovery.** The USAspending download service accepts a `columns` list. Requesting 36 columns instead of the 297-column bulk format cuts file size about sixfold (about 47 bytes per row instead of about 300). That makes the full FY2008–FY2026 history practical on a laptop. The service caps each file at 500,000 rows, so the download goes one quarter at a time and splits a quarter into months if it would pass the cap.

**Privacy.** SAM.gov notice details include contact names and email addresses. The scripts copy only whitelisted fields (product/service code, industry code, set-aside, dates) in memory and never write raw notice records to disk. No individuals are named or stored anywhere in this project.

## 2. Definitions

- **Award:** one contract award, represented by its **base action** (modification number 0) dated FY2024–FY2026.
- **Competed:** extent-competed codes A, D, E or F, or CDO. Awards competed under simplified procedures count as competed.
- **Single offer:** `number_of_offers_received` equals 1. Awards with blank values or the `999` placeholder <!-- auto:m-999 -->(760 competed awards in the five departments)<!-- /auto:m-999 --> are excluded.
- **Coverage (five departments):** <!-- auto:m-coverage -->261,240 awards → 182,905 with a base action in the window → 98,432 competed → **97,672 with a usable offer count** (99.2%). Every civilian agency: section 3b.<!-- /auto:m-coverage -->

## 3. Single-offer analysis

Single-offer shares are computed by department, fiscal year, award type, set-aside and size, with Wilson 95% intervals.

<!-- auto:m-recount -->
An independent recount, `scripts/check_counts_sql.py`, reads the raw download with Python's csv module and counts in SQLite (SQL), with no pandas and none of the pipeline's code. It reproduces every count, for the five departments and for every civilian agency (in the five departments: 182,905 base awards, 97,672 competed with a usable offer count, 22,246 single-offer awards). It needs the raw download, so it runs in `python run.py all`.
<!-- /auto:m-recount -->

<!-- auto:m-five-table -->
| Department | Single-offer share, FY2024–26 | FY2024 | FY2025 | FY2026 |
|---|---|---|---|---|
| HHS | 40.3% (n = 9,590) | 41.8% | 39.6% | 38.1% |
| VA | 35.3% (n = 27,017) | 37.4% | 36.8% | 30.1% |
| DHS | 26.6% (n = 15,159) | 31.0% | 27.8% | 20.3% |
| Interior | 17.9% (n = 15,880) | 20.6% | 17.5% | 14.9% |
| USDA | 6.6% (n = 30,026) | 9.8% | 6.7% | 4.1% |
| **All five** | **22.8%** (n = 97,672) | | | |
<!-- /auto:m-five-table -->

<!-- auto:m-other-cuts -->
Other cuts:
- **Full-and-open competition only:** VA 32.2%, HHS 19.9%, DHS 19.2%, Interior 10.2%, USDA 3.0%.
- **By solicitation identifier:** awards carrying one had a 14.5% single-offer rate; awards without one, 45.1%.
- **Dollar-weighted** (purchase orders and definitive contracts): 15.7% of competed obligations.
- **Awards of $20M or more:** 18.0% (n = 339).
<!-- /auto:m-other-cuts -->

<!-- auto:m-premium -->
**Price premium (preliminary).** Federal award data has no unit prices, so a true price premium can't be measured. Within agency × year × award type × 4-character product code cells (58,284 awards, 2,157 cells), single-offer awards are 27.2% smaller in initial value (95% interval −33.6% to −20.1%). That is a size difference, not a price. Growth in potential value of 25% or more after award shows no detectable difference: −0.27 percentage points for purchase orders (−0.55 to +0.01).
<!-- /auto:m-premium -->

<!-- auto:m-offices -->
Office-level shares were computed (370 offices, 180 with at least 100 awards). They are persistent over time: the correlation between FY2024–25 and FY2026 office shares is 0.75. They are not published in this release.
<!-- /auto:m-offices -->

## 3b. Every civilian agency (added November 9, 2026)

<!-- auto:m-civ-data -->
**Data.** The same download, all civilian agencies, FY2024–FY2026: 1,880,096 transactions. `02_build_awards.py --civilian` applies the same rules as above: 969,601 awards → 812,446 with a base action in the window → 475,212 competed (58.5%) → **466,009 with a usable offer count** (98.1% of competed; 9,201 carry the 999 placeholder). 64 agencies have competed awards with a usable offer count.
<!-- /auto:m-civ-data -->

<!-- auto:m-civ-check -->
**Check against the five-department table.** The five departments, measured from the civilian table, match it exactly, award for award. See `results/atlas_civilian_five_check.csv`.
<!-- /auto:m-civ-check -->

<!-- auto:m-gsa -->
**GSA.** 295,322 of the 466,009 awards (63%) are GSA's, almost all very small purchase orders from GSA's Federal Acquisition Service (median award $143). In a check of FY2026 Q1, most came from its supply-chain contracting division, and the most common products were office supplies, hand tools and measuring tools. Their recorded offers changed sharply: one offer on 55.6% in FY2024, 46.6% in FY2025 and 16.8% in FY2026, as most began recording more than one offer. That may be a change in how these automated orders are recorded rather than in competition. Every civilian-wide total is therefore also given without GSA.
<!-- /auto:m-gsa -->

<!-- auto:m-civ-table -->
| Scope | Single-offer share, FY2024–26 | Competed awards |
|---|---|---|
| All civilian agencies | 34.0% (95% interval 33.9–34.2%) | 466,009 |
| All civilian agencies except GSA | 25.5% (25.3–25.7%) | 170,687 |
| Other civilian agencies, not GSA and not the original five | 29.1% (28.7–29.4%) | 73,015 |
| GSA only | 39.0% | 295,322 |
<!-- /auto:m-civ-table -->

<!-- auto:m-civ-trend -->
Without GSA, the share fell from 27.9% (FY2024) to 26.3% (FY2025) and 21.5% (FY2026).
<!-- /auto:m-civ-trend -->

Shares by agency, agency and year, award type and set-aside (each with and without GSA) are in `results/atlas_civilian_*.csv`; `03b_atlas_civilian.py` writes them.

## 4. Threshold bunching test

<!-- auto:m-threshold-data -->
**Data:** civilian purchase orders and definitive contracts (66 agencies), base actions only, from the same download. FY2024: 258,718. FY2025: 236,470. FY2026: 263,196.
<!-- /auto:m-threshold-data -->

**Method,** fixed before looking at the post-change period:
- Award values are base-and-all-options value at the base action.
- Values are grouped into $5,000 right-closed bins.
- A degree-5 Poisson polynomial is fitted to the counts outside the window (T − $20K, T + $10K].
- **Bunching ratio** = observed awards in the four bins just under the line ÷ the counterfactual from the fitted curve.
- Intervals come from 300 bootstrap resamples.
- Placebo lines at $200K and $450K.

**Results:**

<!-- auto:m-threshold-table -->
| Line | FY2024 | FY2025 | FY2026 | Change, FY2025 → FY2026 |
|---|---|---|---|---|
| $250K | 1.80 (1.64–1.94) | 1.79 (1.64–1.97) | 1.24 (1.13–1.35) | −0.55 (−0.74 to −0.37) |
| $350K | 1.16 (0.95–1.38) | 0.97 (0.81–1.19) | 1.96 (1.69–2.35) | +0.99 (+0.65 to +1.36) |
| $200K (placebo) | 1.31 | 1.46 | 1.31 | −0.15 (−0.38 to +0.10) |
| $450K (placebo) | 1.17 | 0.86 | 1.02 | +0.16 (−0.13 to +0.44) |
<!-- /auto:m-threshold-table -->

**Robustness:** the same pattern holds when using obligations instead of value, for purchase orders only, and in each half of FY2026 (`results/threshold_sat_robustness.csv`). <!-- auto:m-threshold-quarters -->By fiscal quarter after the change, the $350K ratio rose 1.51 → 1.65 → 1.90 → 2.18.<!-- /auto:m-threshold-quarters -->

**Micro-purchase line ($10K → $15K).** <!-- auto:m-micro -->Reported purchase orders between $10K and $15K fell 67% (17,293 → 5,675), against −18% between $15K and $20K.<!-- /auto:m-micro --> Actions below the micro-purchase threshold generally aren't reported individually, so the reporting floor itself moved. Counts near $10K–$15K therefore aren't comparable across the change without adjusting for that.

**Exploratory competition test.** The single-offer share in the band that became "simplified" ($250K–$350K) was compared with a control band ($350K–$450K) using a difference-in-differences. <!-- auto:m-did -->The result was −0.86 percentage points (−5.9 to +4.6): no detectable effect so far.<!-- /auto:m-did -->

## 5. Single-offer forecasting model

<!-- auto:m-model-frozen -->
These results are from the October 24, 2026 build and are frozen. Its SAM.gov training inputs were not kept, so `run.py` does not rebuild them (scripts 05–08 and 11); the model will be rebuilt on SAM.gov's official archive files. The files are `results/model_*` and `results/resolution_backtest_summary.csv`.
<!-- /auto:m-model-frozen -->

<!-- auto:m-model-pop -->
- **Population:** 49,556 competed solicitations (one per solicitation number, FY2024–FY2026) with a single-offer share of 19.6%.
- **Response windows:** looked up in SAM.gov for a random sample. 2,398 solicitation numbers were looked up and 1,826 found (76.1%). After dropping missing, negative or extreme windows, 1,802 were usable.
- **Model:** L2 logistic regression (C = 0.03, chosen by 5-fold cross-validation inside the training period).
<!-- /auto:m-model-pop -->
- **Features, all known when a notice is posted:**
  - department; product/service code (2 characters); industry code (3 digits); set-aside group
  - solicitation-number letter; notice type
  - the buying office's past single-offer rate (shrunk toward the department rate, computed out-of-fold) and its volume
  - the original response window and a ≤14-day flag
  - presolicitation and sources-sought flags; two keyword flags (brand name; sole/single/limited source)
- **Excluded because they leak future information:** the number of amendments and extended deadlines.

<!-- auto:m-oot -->
**Out-of-time test** (train on awards through 2025-09-30, n = 1,251; test on FY2026 awards, n = 551, 14.2% single-offer):
- AUC 0.754 (95% interval 0.695–0.811), against 0.622 for the department-wide baseline.
- Brier score 0.1116 against 0.1196 (6.7% skill).
- Calibration is good below 0.2 (0.142 forecast vs 0.140 observed in the 0.1–0.2 bin). The 0.2–0.3 bin was under-forecast (0.239 vs 0.394, n = 71).
<!-- /auto:m-oot -->

<!-- auto:m-effects -->
**Largest effects** (odds ratios):
- no set-aside 1.57
- buying-office history 1.33 per standard deviation
- medical-supply product codes 1.19
- small-business set-aside 0.79
- service-disabled-veteran set-aside 0.83
- longer response window 0.89 per standard deviation
<!-- /auto:m-effects -->

## 6. Live forecasts and how they will be scored

<!-- auto:m-open -->
**Open solicitations** (pulled 2026-10-24): 240 candidate notices in the five modeled departments, 239 of them open, not canceled, of an eligible type and with a deadline between 2026-11-01 and 2026-12-15.
<!-- /auto:m-open -->

**Eligibility rule,** fixed in advance: the notice must be open, not canceled, of type Solicitation or Combined Synopsis/Solicitation, with a deadline in the window, first posted on or after 2026-06-01, and an original response window of 0–120 days.

<!-- auto:m-registered -->
- 224 notices met the rule. 15 were excluded by the date-plausibility check, mostly for typo'd deadlines.
- **150 were registered**, by stratified random sample (seed 20261023): VA 69, USDA 33, Interior 21, DHS 14, HHS 13.
<!-- /auto:m-registered -->

**Resolution:**
- Each registered solicitation number is matched (normalized) to USAspending base actions dated after the forecast. The earliest action wins.
- A forecast is scored only if the award was competed with 1–998 offers.
- The baseline is the department-wide rate.

<!-- auto:m-backtest -->
**Expected resolution rate.** A backtest on 2,251 solicitations with March–April 2026 deadlines found that 70.8% matched an award and 66.3% were scoreable. Expect about 100 of the 150 forecasts to resolve by 2027-03-31. Single-offer events among them will be few, so the first batch will measure calibration roughly, not skill.
<!-- /auto:m-backtest -->

<!-- auto:m-f2 -->
**Threshold forecast (PIO-F2):** FY2027 Q1 bunching ratios, scored once that quarter's data is in USAspending (from 2027-02-15).
<!-- /auto:m-f2 -->

## 7. Known limitations

- Offer counts are entered by contracting staff. GAO found miscoding in 2010.
- FY2026 is a break year: the Oct 1 – Nov 12, 2025 shutdown, the move of reporting into SAM.gov, and staffing cuts. <!-- auto:m-limit-fy2026 -->HHS and VA competed-award counts fell 34–50% from FY2024 to FY2026.<!-- /auto:m-limit-fy2026 -->
- DoD is excluded because of its 90-day public reporting delay.
- The SAM.gov search endpoint is undocumented. Its `publishDate` field means the date of the *last amendment*, so original posting dates need a lookup on the parent notice. Some deadlines are typos.
- This is a first slice: five departments for the deeper analysis and the forecasts, every civilian agency for single-offer shares, and three fiscal years.
