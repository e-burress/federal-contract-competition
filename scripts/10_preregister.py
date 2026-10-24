"""
10_preregister.py - freeze forecast #1 (open-solicitation single-offer probabilities) as a JSON file and record its SHA-256.

A pre-registration only counts if it is written BEFORE the outcomes exist and cannot be quietly changed afterwards.  So:
  1. this script writes forecasts/<id>.json once (refuses to overwrite an existing file),
  2. computes the SHA-256 hash of the file's exact bytes,
  3. appends "<hash>  <file>  <made_at>" to forecasts/SHA256SUMS.txt.
Anyone can later run `sha256sum forecasts/<id>.json` and compare.  Publishing the hash (or the file) before the outcomes are known
is Ellie's decision - nothing is published by this project.

Input : forecasts/open_solicitations_forecast_registered.csv, results/model_card.json, results/resolution_backtest_summary.csv
Output: forecasts/PIO-F1-open-solicitations-single-offer-<date>.json, forecasts/SHA256SUMS.txt
"""
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import FORECASTS, RESULTS  # noqa: E402

now = datetime.now(timezone.utc)
made_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
fid = f"PIO-F1-open-solicitations-single-offer-{now.strftime('%Y%m%d')}"
path = os.path.join(FORECASTS, fid + ".json")
if os.path.exists(path):
    sys.exit(f"{path} already exists - a registered forecast is never overwritten.")

reg = pd.read_csv(os.path.join(FORECASTS, "open_solicitations_forecast_registered.csv"), dtype={"office": str, "psc": str, "naics": str})
card = json.load(open(os.path.join(RESULTS, "model_card.json")))
bt = pd.read_csv(os.path.join(RESULTS, "resolution_backtest_summary.csv"))
n_not = int(bt.n_notices.sum())
resolvable_share = float(bt.n_resolvable.sum() / n_not)

def clean(x):
    """JSON has no NaN: turn missing values into null"""
    return None if pd.isna(x) else x


items = []
for r in reg.itertuples():
    items.append({
        "solicitation_number": r.solicitation_number,
        "sam_notice_id": r.notice_id,
        "agency": r.agency_full,
        "agency_short": r.agency,
        "office_code": r.office,
        "office_name": clean(r.office_chain),
        "response_deadline_utc": r.response_deadline_utc,
        "notice_type": r.notice_type,
        "psc": clean(r.psc),
        "naics": clean(r.naics),
        "set_aside": clean(r.set_aside),
        "p_single_offer": round(float(r.p_model), 4),
        "p_baseline_agency": round(float(r.p_baseline_agency), 4),
        "p_office_history": round(float(r.p_office_history), 4),
    })

doc = {
    "id": fid,
    "made_at_utc": made_at,
    "project": "Federal Procurement Integrity Observatory - cycle-3 thin slice (a research risk indicator about markets and offices, not an accusation about anyone)",
    "target": ("For each listed OPEN SAM.gov solicitation (Solicitation or Combined Synopsis/Solicitation notice, civilian agencies VA, HHS, DHS, Interior, USDA, "
              "response deadline between 2026-11-01 and 2026-12-15): the probability that the award that resolves it will have EXACTLY ONE OFFER "
              "(number_of_offers_received == 1), conditional on that award being competed (extent competed A, D, E, F or CDO) with a usable offers count (1..998)."),
    "population": {"n_forecasts": len(items), "n_eligible_before_sampling": int(len(pd.read_csv(os.path.join(FORECASTS, "open_solicitations_forecast_all_eligible.csv")))), "pulled_from": "https://sam.gov/api/prod/sgs/v1/search/ (keyless search used by the sam.gov website)",
                   "pulled_at_utc": str(reg.pulled_at.iloc[0]), "eligibility": "open, not canceled, notice type o/k, first posted on or after 2026-06-01 with an original response window of 0-120 days; at most 150, stratified random sample by agency (seed 20261023)"},
    "resolution_source": "USAspending.gov contract award data (fed by FPDS), base actions (modification_number 0) of purchase orders, definitive contracts and IDV awards",
    "resolution_source_url": ["https://api.usaspending.gov/api/v2/download/transactions/", "https://files.usaspending.gov/award_data_archive/", "https://www.usaspending.gov/"],
    "resolution_date": "2027-03-31",
    "interim_scoring_dates": ["2027-01-31", "2027-02-28"],
    "resolution_rule": ("Normalise the solicitation number (upper case, letters and digits only). Find USAspending base actions whose normalised solicitation_identifier equals it "
                        "and whose action date is on or after this forecast's made_at date; if several, take the earliest action date (ties: larger base_and_all_options_value). "
                        "The forecast is RESOLVED only if that award has extent_competed_code in {A, D, E, F, CDO} and number_of_offers_received between 1 and 998; outcome = 1 if offers == 1 else 0. "
                        "Solicitations with no matching award by the resolution date, with a non-competed award, or with blank/999 offers are reported as unresolved and are not scored. "
                        "Prefix or fuzzy matches are NOT used for scoring (they may be reported separately). Orders under IDVs and BPA calls are out of scope. Code: scripts/09_resolve_forecast.py."),
    "expected_resolution": {"backtest_resolvable_share_of_notices": round(resolvable_share, 3),
                            "note": "Measured on 2026-03-01..2026-04-15 deadlines of the same five departments (scripts/11_resolution_backtest.py): share of notices that became a competed award with usable offers by 2026-09-30. Expect roughly this share of the forecasts to be scoreable."},
    "forecasts": items,
    "method": ("L2-regularised logistic regression (scikit-learn, C=%s) on %d historical solicitations of the same five departments (competed awards with usable offers and a solicitation identifier, FY2024-FY2026 "
               "USAspending base actions) whose SAM.gov notice could be found (%d). Features known at posting: agency, PSC (first 2 characters), NAICS (first 3), set-aside group, solicitation-number letter (Q/R/B), "
               "notice type, the buying office's past single-offer rate (shrunk toward its agency rate, out-of-fold during training) and its volume, the ORIGINAL response window in days and a <=14-day flag, and whether presolicitation or "
               "sources-sought notices exist. Out-of-time test (train through 2025-09-30, test FY2026): see model_card. Probabilities are NOT recalibrated after the fit."
               % (card["C"], card["n_population"], card["n_sample_L"])),
    "baseline": {"description": "Naive forecast: the agency-wide single-offer share among FY2026 competed base awards with a solicitation identifier (same population as the model); stored per item as p_baseline_agency.",
                 "values_by_agency_fy2026": {k: round(v, 4) for k, v in card["baseline_agency_rate_fy2026"].items()},
                 "secondary_baseline": "p_office_history = the buying office's shrunk historical single-offer rate (no other features)."},
    "model_card": card,
    "scoring_plan": "Brier score, log loss and ROC-AUC on resolved forecasts; Brier skill score versus the agency baseline; calibration table in 7 probability bins; bootstrap 95% intervals. Report the resolved share and reasons for unresolved items.",
    "known_limitations": [
        "About two thirds of the forecasts are expected to resolve (66% of comparable March-April 2026 notices did; 39% for HHS to 79% for USDA and Interior); unresolved items are excluded (selection on a competed award with a reported offers count).",
        "number_of_offers_received is entered by contracting staff; GAO has found miscoding in the past.",
        "Solicitation numbers can change between SAM and the award record (suffixes); such items stay unresolved.",
        "A high probability is a risk indicator for the market of this requirement, not a statement about any person or office's conduct.",
        "SAM.gov search endpoint used here is undocumented and may change."],
}
text = json.dumps(doc, indent=1, ensure_ascii=True) + "\n"
open(path, "w", encoding="utf-8", newline="\n").write(text)
h = hashlib.sha256(open(path, "rb").read()).hexdigest()
with open(os.path.join(FORECASTS, "SHA256SUMS.txt"), "a", encoding="utf-8") as fh:
    fh.write(f"{h}  {os.path.basename(path)}  made_at={made_at}\n")
print("wrote", path)
print("SHA-256:", h)
print("forecasts:", len(items), "| mean P(single) model", round(sum(i['p_single_offer'] for i in items) / len(items), 4),
      "| baseline", round(sum(i['p_baseline_agency'] for i in items) / len(items), 4))
