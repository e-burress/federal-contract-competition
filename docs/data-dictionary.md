# Data dictionary

The public data snapshot (a GitHub release, for example `data-2026-11-09`) holds the processed tables behind every published result. `python run.py quick` downloads them into `data/processed/` and checks each file against its SHA-256 fingerprint in `SHA256SUMS`. `snapshot.json` records the download date and the columns of each table.

Source: USAspending.gov contract transactions (the Advanced Search download), every civilian top-tier agency (the Department of Defense is not included), actions dated October 1, 2023 to September 30, 2026. Buying-office names and company identifiers are left out, only to keep the tables small and focused. The tables are not anonymous: contract numbers are kept, so any award can be looked up on USAspending.gov, where every record is public.

Each table has **one row per award**, described by its **base action** (modification 0, the original award). Codes follow the Federal Procurement Data System (FPDS) data dictionary.

**Values are changes, not running totals.** In USAspending's transaction data, `base_and_all_options_value`, `base_and_exercised_options_value` and `federal_action_obligation` on each row are the change made by that action. The base action carries the initial value; a modification carries its increase or decrease (often 0). An award's current total is the sum over its actions.

## base_awards_five.parquet

The five departments of the in-depth analysis (Veterans Affairs, Health and Human Services, Homeland Security, Interior, Agriculture). Built by `scripts/02_build_awards.py`; used by `scripts/03_atlas.py`.

| Column | Meaning |
|---|---|
| contract_award_unique_key | USAspending's unique award identifier |
| award_id_piid, parent_award_id_piid | Contract number, and the contract vehicle it was ordered under (if any) |
| agency | Department (VA, HHS, DHS, DOI, USDA) |
| awarding_sub_agency_name | Component, for example Veterans Health Administration |
| awarding_office_code | Code of the buying office (names are left out) |
| award_kind | PO = purchase order, DEF = definitive contract, IDV = contract vehicle |
| idv_type_code, multiple_or_single_award_idv_code | Type of contract vehicle |
| action_date, fy | Date of the base action and its federal fiscal year (October to September) |
| solicitation_identifier, has_sol_id | Solicitation number on the award, and whether there is one |
| product_or_service_code, naics_code | What was bought, and the seller's industry |
| extent_competed_code | How it was competed (A, D, E, F and CDO count as competed) |
| solicitation_procedures_code | For example SP1 = simplified acquisition |
| type_of_set_aside_code | Small-business or other set-aside (NONE = open to all) |
| offers | Number of offers received (999 is a placeholder, not a count) |
| competed, offers_ok, eval_set, single | Competed; offer count usable (1 to 998); both (the base of every single-offer share); exactly one offer |
| value0, obl0 | Base-and-all-options value and dollars obligated at the base action |
| base_and_exercised_options_value | Value of the base plus exercised options at the base action |
| n_tx, obligation_sum_window | Number of transactions on the award in the window, and their total obligations |
| value_all_options_last, value_all_options_max | Per-action figures, not totals: the change in potential value made by the latest action in the window (often 0), and the largest single change (usually the base action's own value). Do not use them to measure growth. |
| value_all_options_sum | Sum of the per-action changes over the award's actions in the window: the award's potential value at the end of the window. Only in tables built from the raw download by the current `02_build_awards.py`; the snapshot `data-2026-11-09` does not have it. |
| last_action_date, months_at_risk | Last transaction date, and months from the base action to the end of the window |
| type_of_contract_pricing_code, commercial_item_acquisition_procedures_code | Pricing type and commercial-item procedures |
| fair_opportunity_limited_sources_code, other_than_full_and_open_competition_code | Reasons for limited competition, where given |
| purchase_card_as_payment_method_code, cost_or_pricing_data_code | Paid by purchase card; certified cost or pricing data obtained |

## base_awards_civilian.parquet

Every civilian agency, with fewer columns. Built by `scripts/02_build_awards.py --civilian`; used by `scripts/03b_atlas_civilian.py`. The columns have the same meanings as above; `agency` is the full agency name for agencies outside the five departments.

## threshold_base_actions.parquet

Base actions of civilian purchase orders and definitive contracts: the universe of the threshold test. Built by `scripts/bunching_lib.py`; used by `scripts/04_threshold.py` and `scripts/04b_threshold_quarterly.py`.

| Column | Meaning |
|---|---|
| contract_award_unique_key, modification_number, action_date, fy | Award, modification number (always 0 here), date and fiscal year |
| action_type_code, award_type_code, kind | Action type; B = purchase order, D = definitive contract (kind: PO or DEF) |
| base_and_all_options_value | Potential value at the base action (the value used for bunching) |
| federal_action_obligation | Dollars obligated by the base action (used for the micro-purchase check and as a robustness check) |
| awarding_agency_name | Agency |
| extent_competed_code, type_of_set_aside_code, number_of_offers_received | As above |
| purchase_card_as_payment_method_code | Paid by purchase card |

## agencies.json

The 110 civilian top-tier agencies requested, with USAspending's abbreviation and agency code.

## History tables (release `history-v1`)

Built on GitHub by `.github/workflows/history.yml` (scripts `17_history_download.py` and `18_history_build.py`), one pair of files per fiscal year from FY2008, covering civilian agencies and the Department of Defense (every year is always built from both). Buying-office names, company names and raw company identifiers are left out, only to keep the tables small; contract numbers are kept, and every award is public on USAspending.gov. `python run.py history` downloads them into `data/history/processed/` and rebuilds the history results. Defense data are published 90 days late, so the latest Defense year is incomplete until its last quarter is out.

**base_actions_FY<year>.parquet**: one row per award whose base action is dated in that fiscal year (purchase orders, definitive contracts and contract vehicles). Columns as in `base_awards_five.parquet`, with the original USAspending names (`awarding_agency_name`, `base_and_all_options_value`, `federal_action_obligation`, `base_and_exercised_options_value`, `period_of_performance_start_date`, `period_of_performance_potential_end_date`, ...), plus:

| Column | Meaning |
|---|---|
| vendor_id | A short code derived from the company's public identifier, so the same company can be followed across awards: the first 16 hex digits of SHA-256("fcc-v1:" + UEI, or "duns:" + DUNS when there is no UEI). It is not anonymous: anyone with the identifier can compute the same code, contract numbers are kept, and the underlying records are public on USAspending.gov. Company names and raw identifiers are left out only to keep the tables small and focused. Known limit: a company whose records carry a UEI in some years and only a DUNS in others (the switch to UEI came in 2022) gets two codes; use the two columns below to link them. |
| vendor_uei_code, vendor_duns_code | The same kind of code from one identifier alone: SHA-256("fcc-v1:" + UEI) and SHA-256("fcc-v1:duns:" + DUNS), first 16 hex digits, each empty when that identifier is blank. A row that carries both links a company's UEI years to its DUNS-only years. `vendor_id` equals `vendor_uei_code` when there is a UEI, and `vendor_duns_code` otherwise. Added in the second build of the tables. |
| is_dod | Awarded by the Department of Defense |

**award_year_FY<year>.parquet**: one row per award with any action in that fiscal year.

| Column | Meaning |
|---|---|
| contract_award_unique_key, fy | Award and fiscal year |
| n_tx | Number of actions (the award itself and its modifications) in the year |
| obligation_sum | Dollars obligated by those actions |
| value_max, value_last | Per-action figures, not totals: the largest single change in base-and-all-options value in the year, and the change made by the year's last action |
| exercised_last | The change in base-and-exercised-options value made by the year's last action |
| value_sum, exercised_sum | Sums of the year's per-action changes in base-and-all-options value and in base-and-exercised-options value. Summed over the years up to t (from the base year), they give the award's potential and exercised value at the end of year t. Added in the second build of the tables; `history-v1` does not have them. |
| last_action_date | Date of the last action in the year |
| n_options, n_scope_changes, n_terminations | Actions that exercised an option (FPDS action type G), changed the scope (B, D, L) or terminated the contract (E, F, X) |

**history_FY<year>.json**: row counts and the files the year was built from.

## Result files: who wins the awards (supplier base)

Built by `scripts/33_supplier_base.py` from the history tables (`python run.py supplier-base`). Sector level or coarser only: no file names or codes a company or a buying office, and every cell resting on fewer than 10 companies is blank, with the reason in `note`. Two award scopes: `all` (every new award) and `above_25k` (initial potential value above $25,000, the headline series). Dollars are initial potential value (base and all options), not adjusted for inflation.

**supplier_base_by_fy.csv**: one row per fiscal year, sector (`civilian except GSA`, `GSA`, `Department of Defense`, `all sectors`), award scope and award kind (`all`, `PO`, `DEF`, `IDV`).

| Column | Meaning |
|---|---|
| n_awards, n_awards_no_company_id | New awards in the cell, and those without any company identifier |
| n_companies, n_companies_unlinked_ids | Different companies that won at least one award: with DUNS and UEI codes linked, and with the raw `vendor_id` |
| initial_dollars | Initial value of the cell's purchase orders and definitive contracts (contract vehicles and values above $10 billion left out) |
| n_new_companies, new_share_companies, new_share_awards, new_share_dollars | Companies that won no award (in the same scope) in the three previous fiscal years: their number, and their share of companies, awards and initial dollars. Blank when the three previous years are not all present |
| new_share_companies_unlinked_ids | The same share computed with the raw `vendor_id` (no linking), to show the size of the identifier correction |
| new_share_companies_never_seen | Sensitivity: share of companies with no award in any earlier year present |
| stay_share_next_year, stay_share_next_year_unlinked_ids, stay_share_within_3_years | Share of the cell's companies that won again (in the same scope) in the next year, or within the next three years; blank when those years are missing or incomplete |
| new_measures_comparable, stay_next_year_comparable, stay_3_years_comparable | False when the measure spans the 2022 switch from DUNS to UEI identifiers and the switch could not be checked; blank when the measure is not computed |
| note | Known reporting changes (FY2008 Veterans Affairs micro-purchases), incomplete Defense years, the identifier switch, suppressed cells |

**supplier_base_concentration.csv**: one row per fiscal year, sector and award scope; purchase orders and definitive contracts only.

| Column | Meaning |
|---|---|
| n_companies, n_awards, initial_dollars | Companies, awards and initial dollars counted |
| awards_over_cap_left_out | Awards with an initial value above $10 billion, left out as likely recording errors |
| top1pct_companies, top1pct_share_dollars, top10pct_companies, top10pct_share_dollars | Size of the top 1% and top 10% of companies (ranked by initial dollars) and their share of initial dollars; blank when the group has fewer than 10 companies |
| top1pct_share_obligated_at_award, top10pct_share_obligated_at_award | The same shares with dollars obligated at award, as a check |
| n_markets, awards_in_markets, awards_without_naics | 4-digit NAICS industries with at least 20 awards, the awards in them, and awards without a usable NAICS code |
| hhi_median_award_weighted, share_awards_in_markets_hhi_above_2500 | Herfindahl-Hirschman index of initial dollars per industry (0 to 10,000): the award-weighted median, and the share of awards in industries above 2,500. No single industry is published |
| note | As above |

**supplier_base_competition.csv**: competed awards with a usable offer count, by new and established companies. One row per period (a fiscal year, or the comparable years pooled), sector, award scope, measure and group.

| Column | Meaning |
|---|---|
| period, fy | `FY2013` and 2013, or `FY2011-FY2021 pooled` and blank |
| measure, group | `single_offer_share` (groups: new companies, established companies; k = awards with one offer), `new_company_share_by_setaside` (groups: set-aside groups as in `23_drivers.py`; all awards; k = awards won by new companies), `new_company_share_by_offers` (groups: one offer, two or more offers; k = awards won by new companies) |
| k, n, share, ci_lo, ci_hi | Count, total, share and 95% Wilson interval |
| n_companies | Companies behind the cell (pooled rows: the largest yearly count); cells under 10 are blank |
| note | As above |

**supplier_base_summary.json**: the headline numbers the pages quote, the definitions, the identifier-switch checks by year (`identifier_switch.by_year`: share of awards with a UEI, with a DUNS, with only an unlinked DUNS; for tables without the linking columns, the share of company ids that match a DUNS code seen in the newer tables), the known reporting changes, the caveats and the run's time and memory.

## Result files: market lookup for buyers

Built by `scripts/34_market_lookup.py` from the history tables (`python run.py market-lookup`). Competed awards with a usable offer count (the eval set) in the recent window: the last five complete fiscal years, the same for every scope (`market_lookup_summary.json` gives the years). Industry and product level only: no file names or codes a buying office, a company or a solicitation. A value resting on fewer than 100 competed awards is blank, not zero.

**market_lookup_naics4.csv**: one row per industry group (first 4 digits of the NAICS code) and scope (`all sectors`, `civilian except GSA`, `GSA`, `Department of Defense`). **market_lookup_psc2.csv**: the same per product or service group (first 2 characters of the product or service code). Groups with a blank code are left out of both files but counted in the summary's totals.

| Column | Meaning |
|---|---|
| naics4 / psc2, title | The group code and its title: for industries the official 2022 NAICS title (`reference/naics4_titles.csv`), "<title> (2017 code)" for a code of the earlier 2017 list, or "industry <code> (older code)" for a code found in no list; for products the federal supply group name; for services the name of the first letter's category |
| older_code | (Industries only.) True when the code is not in the 2022 NAICS list. The same industry can then appear under two codes; neither holds the whole market |
| scope | Sector, or all sectors together |
| n_eval, k_single | Competed awards with a usable offer count in the recent window, and those with exactly one offer |
| share, ci_lo, ci_hi | Single-offer share and its 95% Wilson interval |
| offers_p25, offers_median, offers_p75 | 25th, 50th and 75th percentiles of the number of offers (linear interpolation, so a value can fall between whole numbers) |
| share_one_or_two | Share of awards with one or two offers |
| n_setaside, share_setaside, ci_lo_setaside, ci_hi_setaside | Awards under a small-business set-aside of any type (small business, 8(a), HUBZone, SDVOSB, WOSB/EDWOSB, VOSB), their single-offer share and its interval |
| n_no_setaside, share_no_setaside, ci_lo_no_setaside, ci_hi_no_setaside | The same for awards with no set-aside (other set-asides and blanks are in neither) |
| n_<band>, share_<band> | Awards and single-offer share in each size band of initial value (base and all options): <25K, 25-100K, 100-250K, 250K-1M, 1-10M, >10M |
| n_eval_earlier, share_earlier | The same population in the earlier window (the five years before) |
| change_points, change_lo, change_hi | Recent minus earlier single-offer share, in percentage points, with a 95% interval; blank unless both windows have at least 100 awards and every year of both windows is present |
| change_note | "Compare with care" when the group's awards changed more than three times between the windows, for IT product and service groups redefined in FY2021, and for GSA |
| note | On `all sectors` rows: GSA holds more than half of the awards |
| registered_primary | (Industries only.) SAM.gov registrations whose main NAICS code is in the group: distinct companies |
| registered_any_largest, registered_any_listings | Registrations that list a code of the group: at least the first (the largest single 6-digit code) and at most the second (the sum over the group's codes, which counts a company once per code) |
| registered_codes | 6-digit codes of the group in `reference/market_depth_naics.csv` |

**market_lookup_summary.json**: the window and the rule that chose it (`window`), the cell minimum, the number of groups, every-market figures per scope (`all_markets`), the thinnest and deepest industry and product groups with at least 2,000 competed awards (`markets_naics4`, `markets_psc2`; groups where GSA holds more than half of the awards are left out), the yearly totals by sector (`eval_by_year`) and their check against `history_by_fy.csv` (`totals_check`), the SAM.gov extract date and the caveats.

**reference/naics4_titles.csv**: official NAICS titles of the 4-digit industry groups, from the Census Bureau's code files (`source_url`), with the same columns as `reference/naics3_titles.csv`: the 2022 list, plus the codes of the 2017 list that the awards still carry (`vintage` 2017 NAICS). `reference/naics3_titles.csv` has the 2017 rows for its older 3-digit codes in the same way.

## Result files: completeness check

Built by `scripts/28_reconcile.py` (`python run.py reconcile`) from the history tables and USAspending's public search API. These files depend on the day they were made, so `python run.py verify` does not compare them.

**reconciliation.csv**: one row per fiscal year and comparison. `sector` is `download_agencies` (USAspending filtered to the same top-tier agencies as our downloads: the like-for-like check) or `all_agencies` (no agency filter). Columns: `our_transactions`, `api_transactions`, `diff_transactions_pct` (ours minus theirs, in percent of theirs), `our_obligations`, `api_obligations`, `diff_obligations_pct`, `checked_utc`, `endpoint`.

**reconciliation_summary.json**: the years checked and not answered, the largest differences, `all_agencies_largest_transaction_gap_pct` (the largest share of a year's transactions held by awarding agencies that are not downloaded), the date of each yearly table, a plain-English `verdict` and the caveats.

## Result files: SAM.gov notices, day by day

Built by `scripts/14_sam_daily_snapshot.py` from SAM.gov's Get Opportunities API ([`sam-api-key.md`](sam-api-key.md)). These files depend on the day they were made, so `python run.py verify` does not compare them.

**sam_api_pulls.csv**: one row per pull of one posted day, in the order made. `posted_day`, `pulled_utc`, `attempt` (1 for the first pull of that day), `requests` (requests this pull made), `total_records` (SAM.gov's count of notices posted that day), `notices_received` (distinct notice ids received), `status` (`complete`; `incomplete`: cut short, pulled again in full next time; `short`: fell short twice for another reason, or needs more requests than one run may make; kept as received), `offset_mode` (how the API counts its `offset`, once known: `page` or `record`).

**sam_new_notices.csv**: one row per posted day, department (as SAM.gov writes it; `(blank)` if missing) and notice type, for every finished day. `n_notices` (notices posted that day, new and amended), `n_sol_numbers` (distinct solicitation numbers among them).

**sam_daily_latest.json**: the dashboard summary. Since November 11, 2026 (`"schema": 2`): the newest finished day (`through`), the 7-day window, solicitation notices posted in the window (all, civilian, Defense; notices and distinct solicitation numbers), the ten civilian departments with the most solicitations, the newest day by notice type, the store's size and first day, the requests used, and warnings. Before that it described SAM.gov's nightly file of active notices.

**sam_daily_counts.csv**: open notices by department and type from SAM.gov's nightly file of active notices, November 9 and 10, 2026 only. No longer updated.

## Daily sealed forecasts

Built by `scripts/31_daily_forecast.py` and `scripts/32_score_daily.py`. Guide: [`daily-forecasts.md`](daily-forecasts.md). The batches themselves are private. These files depend on the day they were made, so `python run.py verify` does not compare them.

**forecasts/daily/ledger.csv** (public, one row per sealed batch): `batch_id`, `forecast_date`, `made_at_utc`, `snapshot_date`, `model_version`, `model_sha256`, `n_forecasts`, forecasts by sector (`n_civilian_except_gsa`, `n_gsa`, `n_defense`, `n_unknown`), `n_fresh` (first posted at most 3 days before the forecast), `mean_forecast` (blank under 5 forecasts) and `batch_sha256`. `SHA256SUMS.txt` holds the seal of each batch and `seen.txt` the short hashes of the solicitation numbers already forecast (see [`forecasts/daily/README.md`](../forecasts/daily/README.md)).

**results/daily_forecast_scores.json** (public, totals only): counts of batches, forecasts and resolved forecasts, by sector and by freshness, the reasons forecasts are unresolved, and `scores_published`. Scores (Brier score, log loss and AUC for the model and its three baselines, a calibration table, and rows by sector, month and freshness) appear only once enough forecasts have resolved; small cells are hidden. `results/daily_forecast_scores.csv` holds the same in rows. The full list of keys is in the docstring of `32_score_daily.py`.
