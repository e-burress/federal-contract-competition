# Data dictionary

The public data snapshot (a GitHub release, for example `data-2026-11-09`) holds the processed tables behind every published result. `python run.py quick` downloads them into `data/processed/` and checks each file against its SHA-256 fingerprint in `SHA256SUMS`. `snapshot.json` records the download date and the columns of each table.

Source: USAspending.gov contract transactions (the Advanced Search download), every civilian top-tier agency (the Department of Defense is not included), actions dated October 1, 2023 to September 30, 2026. Buying-office names and company identifiers are left out, only to keep the tables small and focused. The tables are not anonymous: contract numbers are kept, so any award can be looked up on USAspending.gov, where every record is public.

Each table has **one row per award**, described by its **base action** (modification 0, the original award). Codes follow the Federal Procurement Data System (FPDS) data dictionary.

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
| value_all_options_last, value_all_options_max | Latest and highest potential value reported in the window |
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
| vendor_id | A short code derived from the company's public identifier, so the same company can be followed across awards: the first 16 hex digits of SHA-256("fcc-v1:" + UEI, or "duns:" + DUNS when there is no UEI). It is not anonymous: anyone with the identifier can compute the same code, contract numbers are kept, and the underlying records are public on USAspending.gov. Company names and raw identifiers are left out only to keep the tables small and focused. Known limit: a company whose records carry a UEI in some years and only a DUNS in others (the switch to UEI came in 2022) gets two codes. |
| is_dod | Awarded by the Department of Defense |

**award_year_FY<year>.parquet**: one row per award with any action in that fiscal year.

| Column | Meaning |
|---|---|
| contract_award_unique_key, fy | Award and fiscal year |
| n_tx | Number of actions (the award itself and its modifications) in the year |
| obligation_sum | Dollars obligated by those actions |
| value_max, value_last | Highest and latest base-and-all-options value reported in the year |
| exercised_last | Latest base-and-exercised-options value in the year |
| last_action_date | Date of the last action in the year |
| n_options, n_scope_changes, n_terminations | Actions that exercised an option (FPDS action type G), changed the scope (B, D, L) or terminated the contract (E, F, X) |

**history_FY<year>.json**: row counts and the files the year was built from.
