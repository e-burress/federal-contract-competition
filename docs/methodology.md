# Methodology

First version, built October 23–24, 2026. All numbers are as of data pulled on those dates. USAspending data runs through 2026-09-30.

## 1. Data

| Source | What was used | Access |
|---|---|---|
| USAspending.gov transaction download (`POST /api/v2/download/transactions/`) | Contract transactions: purchase orders (B), definitive contracts (D) and contract vehicles (IDV types) for VA, HHS, DHS, Interior and USDA, FY2024–FY2026. A second pull covers purchase orders and definitive contracts for all 110 civilian top-tier agencies queried. | Free, no key |
| SAM.gov public search (the back end of sam.gov's search page) | Open solicitations (notice types *Solicitation* and *Combined Synopsis/Solicitation*) and historical notices for response windows | Free, no key, undocumented |
| FAR history | Simplified-acquisition threshold $250K → $350K and micro-purchase threshold $10K → $15K, both effective 2025-10-01 | Public |

**Useful discovery.** The USAspending download service accepts a `columns` list. Requesting 36 columns instead of the 297-column bulk format cuts file size about sixfold (about 47 bytes per row instead of about 300). That makes the full FY2008–FY2026 history practical on a laptop. The service caps each file at 500,000 rows, so the scripts download by agency and quarter.

**Privacy.** SAM.gov notice details include contact names and email addresses. The scripts copy only whitelisted fields (product/service code, industry code, set-aside, dates) in memory and never write raw notice records to disk. No individuals are named or stored anywhere in this project.

## 2. Definitions

- **Award:** one contract award, represented by its **base action** (modification number 0) dated FY2024–FY2026.
- **Competed:** extent-competed codes A, D, E or F, or CDO. Awards competed under simplified procedures count as competed.
- **Single offer:** `number_of_offers_received` equals 1. Awards with blank values or the `999` placeholder (760 competed awards) are excluded.
- **Coverage:** 261,134 awards → 182,802 with a base action in the window → 98,403 competed → **97,643 with a usable offer count** (99.2%).

## 3. Single-offer analysis

Single-offer shares are computed by department, fiscal year, award type, set-aside and size, with Wilson 95% intervals. An independent recount in DuckDB (SQL) reproduced every count (182,802 / 97,643 / 22,230 single-offer awards).

| Department | Single-offer share, FY2024–26 | FY2024 | FY2025 | FY2026 |
|---|---|---|---|---|
| HHS | 40.3% (n = 9,592) | 41.8% | 39.7% | 38.2% |
| VA | 35.3% (n = 27,017) | 37.4% | 36.8% | 30.1% |
| DHS | 26.6% (n = 15,149) | 31.0% | 27.8% | 20.2% |
| Interior | 17.9% (n = 15,879) | 20.6% | 17.5% | 14.9% |
| USDA | 6.5% (n = 30,006) | 9.8% | 6.7% | 4.0% |
| **All five** | **22.8%** (n = 97,643) | | | |

Other cuts:
- **Full-and-open competition only:** VA 32.2%, HHS 20.0%, DHS 18.9%, Interior 10.2%, USDA 3.0%.
- **By solicitation identifier:** awards carrying one had a 14.5% single-offer rate; awards without one, 45.0%.
- **Dollar-weighted** (purchase orders and definitive contracts): 15.7% of competed obligations.
- **Awards of $20M or more:** 18.0% (n = 339).

**Price premium (preliminary).** Federal award data has no unit prices, so a true price premium can't be measured. Within agency × year × award type × 4-character product code cells (58,269 awards, 2,157 cells), single-offer awards are 27.2% smaller in initial value (95% interval −33.7% to −20.2%). That is a size difference, not a price. Growth in potential value of 25% or more after award shows no detectable difference: −0.27 percentage points for purchase orders (−0.55 to +0.01).

Office-level shares were computed (370 offices, 180 with at least 100 awards). They are persistent over time: the correlation between FY2024–25 and FY2026 office shares is 0.75. They are not published in this release.

## 4. Threshold bunching test

**Data:** civilian purchase orders and definitive contracts, base actions only. FY2024: 258,717. FY2025: 236,468. FY2026: 262,978.

**Method,** fixed before looking at the post-change period:
- Award values are base-and-all-options value at the base action.
- Values are grouped into $5,000 right-closed bins.
- A degree-5 Poisson polynomial is fitted to the counts outside the window (T − $20K, T + $10K].
- **Bunching ratio** = observed awards in the four bins just under the line ÷ the counterfactual from the fitted curve.
- Intervals come from 300 bootstrap resamples.
- Placebo lines at $200K and $450K.

**Results:**

| Line | FY2024 | FY2025 | FY2026 | Change, FY2025 → FY2026 |
|---|---|---|---|---|
| $250K | 1.80 (1.66–1.97) | 1.79 (1.64–1.96) | 1.24 (1.13–1.34) | −0.55 (−0.75 to −0.35) |
| $350K | 1.16 (0.96–1.38) | 0.97 (0.81–1.18) | 1.95 (1.67–2.28) | +0.99 (+0.64 to +1.42) |
| $200K (placebo) | 1.31 | 1.46 | 1.30 | −0.15 (−0.41 to +0.11) |
| $450K (placebo) | 1.17 | 0.86 | 1.02 | +0.15 (−0.12 to +0.43) |

**Robustness:** the same pattern holds when using obligations instead of value, for purchase orders only, and in each half of FY2026. By fiscal quarter after the change, the $350K ratio rose 1.51 → 1.65 → 1.89 → 2.17.

**Micro-purchase line ($10K → $15K).** Reported purchase orders between $10K and $15K fell 67% (17,293 → 5,665), against −19% between $15K and $20K. Actions below the micro-purchase threshold generally aren't reported individually, so the reporting floor itself moved. Counts near $10K–$15K therefore aren't comparable across the change without adjusting for that.

**Exploratory competition test.** The single-offer share in the band that became "simplified" ($250K–$350K) was compared with a control band ($350K–$450K) using a difference-in-differences. The result was −0.84 percentage points (−6.1 to +4.4): no detectable effect so far.

## 5. Single-offer forecasting model

- **Population:** 49,556 competed solicitations (one per solicitation number, FY2024–FY2026) with a single-offer share of 19.6%.
- **Response windows:** looked up in SAM.gov for a random sample. 2,398 solicitation numbers were looked up and 1,826 found (76.1%). After dropping missing, negative or extreme windows, 1,802 were usable.
- **Model:** L2 logistic regression (C = 0.03, chosen by 5-fold cross-validation inside the training period).
- **Features, all known when a notice is posted:**
  - department; product/service code (2 characters); industry code (3 digits); set-aside group
  - solicitation-number letter; notice type
  - the buying office's past single-offer rate (shrunk toward the department rate, computed out-of-fold) and its volume
  - the original response window and a ≤14-day flag
  - presolicitation and sources-sought flags; two keyword flags (brand name; sole/single/limited source)
- **Excluded because they leak future information:** the number of amendments and extended deadlines.

**Out-of-time test** (train on awards through 2025-09-30, n = 1,251; test on FY2026 awards, n = 551, 14.2% single-offer):
- AUC 0.754 (95% interval 0.695–0.811), against 0.622 for the department-wide baseline.
- Brier score 0.1116 against 0.1196 (6.7% skill).
- Calibration is good below 0.2 (0.142 forecast vs 0.140 observed). The 0.2–0.3 bin was under-forecast (0.239 vs 0.394, n = 71).

**Largest effects** (odds ratios):
- no set-aside 1.57
- buying-office history 1.33 per standard deviation
- medical-supply product codes 1.19
- small-business set-aside 0.79
- service-disabled-veteran set-aside 0.83
- longer response window 0.89 per standard deviation

## 6. Live forecasts and how they will be scored

**Open solicitations** (pulled 2026-10-24): 2,473 open notices with deadlines between 2026-11-01 and 2026-12-15. 243 belong to the five modeled departments.

**Eligibility rule,** fixed in advance: the notice must be open, not canceled, of type Solicitation or Combined Synopsis/Solicitation, with a deadline in the window, first posted on or after 2026-06-01, and an original response window of 0–120 days.
- 224 notices met the rule. 15 were excluded, mostly for typo'd deadlines.
- **150 were registered**, by stratified random sample (seed 20261023): VA 69, USDA 33, Interior 21, DHS 14, HHS 13.

**Resolution:**
- Each registered solicitation number is matched (normalized) to USAspending base actions dated after the forecast. The earliest action wins.
- A forecast is scored only if the award was competed with 1–998 offers.
- The baseline is the department-wide rate.

**Expected resolution rate.** A backtest on 2,251 solicitations with March–April 2026 deadlines found that 70.8% matched an award and 66.3% were scoreable. The median time from deadline to award was 17 days (90th percentile 65 days). Expect about 100 of the 150 forecasts to resolve by 2027-03-31, with about 9–14 single-offer events. The first batch will measure calibration roughly, not skill.

**Threshold forecast (PIO-F2):** FY2027 Q1 bunching ratios, scored once that quarter's data is in USAspending (from 2027-02-15).

## 7. Known limitations

- Offer counts are entered by contracting staff. GAO found miscoding in 2010.
- FY2026 is a break year: the Oct 1 – Nov 12, 2025 shutdown, the move of reporting into SAM.gov, and staffing cuts. HHS and VA base-award counts fell 30–50% from FY2024.
- DoD is excluded because of its 90-day public reporting delay.
- The SAM.gov search endpoint is undocumented. Its `publishDate` field means the date of the *last amendment*, so original posting dates need a lookup on the parent notice. Some deadlines are typos.
- This is a first slice: five departments for the single-offer analysis and three fiscal years.
