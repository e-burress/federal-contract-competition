"""
18_history_build.py - turn ONE fiscal year of downloaded transactions (17_history_download.py) into two compact public tables.

  base_actions_FY<fy>.parquet   one row per award whose base action (modification 0) is dated in the year: what was bought, how it was
                                competed, offers received, set-aside, values at award, agency, buying-office code, a vendor_id (a short
                                company code, see below) with vendor_uei_code and vendor_duns_code, and whether it is a Department of
                                Defense award.  Same rules as 02_build_awards.py
                                (competed = A/D/E/F/CDO; usable offers = 1..998; eval set = both; single = exactly one offer).
  award_year_FY<fy>.parquet     one row per award with any action in the year: number of actions, dollars obligated, the sum of the
                                year's changes in potential value (value_sum) and in exercised value (exercised_sum), the largest and the
                                latest single change (value_max, value_last, exercised_last), last action date, and how many actions
                                were option exercises, scope changes (supplemental agreements, change orders) or terminations.  Joining
                                the years gives each award's history after it was made (cost growth, extensions, terminations), even
                                when that history spans many years.

On each transaction row, base_and_all_options_value and base_and_exercised_options_value are the CHANGE made by that action, not a
running total: the base action carries the initial value and a modification carries its increase or decrease (often 0).  So an award's
potential value at the end of year t is the sum of value_sum over the years up to t; value_max and value_last are single changes, not
totals.  (value_sum and exercised_sum were added in the second build of the tables; the first build, history-v1, does not have them.)

What is left out, as in the public snapshot: buying-office NAMES (codes stay), company names and raw company identifiers.  They are left
out only to keep the tables small and focused, not to hide anything.  vendor_id is a short code derived from the company's public
identifier (the first 16 hex digits of SHA-256("fcc-v1:" + UEI, or "duns:" + DUNS when there is no UEI)), so the same company can be
followed across awards.  It is not anonymous: anyone with the identifier can compute the same code, contract numbers are kept, and the
underlying records are public on USAspending.gov.  Known limit: a company whose records carry a UEI in some years and only a DUNS in
others (the switch from DUNS to UEI came in April 2022) gets two vendor_ids.  To link them, each base action also has two separate
codes made the same way: vendor_uei_code from the UEI alone (SHA-256("fcc-v1:" + UEI)) and vendor_duns_code from the DUNS alone
(SHA-256("fcc-v1:duns:" + DUNS)), each empty when that identifier is blank.  A row that carries both links the two.  vendor_id equals
vendor_uei_code when there is a UEI, and vendor_duns_code otherwise.

Usage:  python -I scripts/18_history_build.py FY
Input : data/history/FY<fy>/*.zip
Output: data/history/processed/base_actions_FY<fy>.parquet, award_year_FY<fy>.parquet, history_FY<fy>.json (counts)
"""
import glob
import hashlib
import json
import os
import sys
import zipfile

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA  # noqa: E402

COMPETED = {"A", "D", "E", "F", "CDO"}                       # as in 02_build_awards.py
OPTION, SCOPE, TERMINATION = {"G"}, {"B", "D", "L"}, {"E", "F", "X"}   # FPDS action type codes
BASE_COLS = ["contract_award_unique_key", "award_id_piid", "parent_award_id_piid", "awarding_agency_name", "awarding_sub_agency_name",
             "awarding_office_code", "award_type_code", "award_or_idv_flag", "idv_type_code", "multiple_or_single_award_idv_code",
             "action_date", "solicitation_identifier", "product_or_service_code", "naics_code", "extent_competed_code",
             "solicitation_procedures_code", "type_of_set_aside_code", "number_of_offers_received", "base_and_all_options_value",
             "federal_action_obligation", "base_and_exercised_options_value", "type_of_contract_pricing_code",
             "commercial_item_acquisition_procedures_code", "fair_opportunity_limited_sources_code",
             "other_than_full_and_open_competition_code", "purchase_card_as_payment_method_code", "cost_or_pricing_data_code",
             "period_of_performance_start_date", "period_of_performance_potential_end_date"]
VENDOR_COLS = ["recipient_uei", "recipient_duns"]


def vendor_id(uei, duns):
    key = uei if isinstance(uei, str) and uei.strip() else (f"duns:{duns}" if isinstance(duns, str) and duns.strip() else "")
    return hashlib.sha256(("fcc-v1:" + key).encode()).hexdigest()[:16] if key else ""


def vendor_code(ident, prefix=""):
    """A code from ONE identifier, with the vendor_id scheme: vendor_code(uei) for the UEI, vendor_code(duns, "duns:") for the DUNS.
    Empty when the identifier is blank."""
    return hashlib.sha256(("fcc-v1:" + prefix + ident).encode()).hexdigest()[:16] if isinstance(ident, str) and ident.strip() else ""


def vendor_columns(b):
    """vendor_id, vendor_uei_code and vendor_duns_code for each row of a frame of base actions."""
    uei = b["recipient_uei"] if "recipient_uei" in b.columns else pd.Series(None, index=b.index, dtype=object)
    duns = b["recipient_duns"] if "recipient_duns" in b.columns else pd.Series(None, index=b.index, dtype=object)
    return pd.DataFrame({"vendor_id": [vendor_id(u, d) for u, d in zip(uei, duns)],
                         "vendor_uei_code": [vendor_code(u) for u in uei],
                         "vendor_duns_code": [vendor_code(d, "duns:") for d in duns]}, index=b.index)


def read_zip(path):
    z = zipfile.ZipFile(path)
    frames = []
    for name in z.namelist():
        if "Transactions" in name and name.endswith(".csv"):
            frames.append(pd.read_csv(z.open(name), dtype=str, keep_default_na=False))
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return df.drop_duplicates("contract_transaction_unique_key") if len(df) else df


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    fy = int(sys.argv[1])
    src = os.path.join(DATA, "history", f"FY{fy}")
    out_dir = os.path.join(DATA, "history", "processed")
    os.makedirs(out_dir, exist_ok=True)
    zips = sorted(glob.glob(os.path.join(src, "*.zip")))
    if not zips:
        sys.exit(f"no downloads in {src}: run 17_history_download.py {fy} first")

    bases, parts, n_rows = [], [], 0
    for p in zips:
        tx = read_zip(p)
        if not len(tx):
            continue
        n_rows += len(tx)
        tx["action_date"] = pd.to_datetime(tx["action_date"], errors="coerce")
        for c in ("federal_action_obligation", "base_and_all_options_value", "base_and_exercised_options_value"):
            tx[c] = pd.to_numeric(tx[c], errors="coerce")
        # base actions of this file (one per award: the earliest modification 0)
        b = tx[tx["modification_number"].isin(["0", "00", "000", ""])]
        b = b.sort_values("action_date").drop_duplicates("contract_award_unique_key")
        keep = [c for c in BASE_COLS if c in b.columns]
        b = pd.concat([b[keep], vendor_columns(b)], axis=1)
        bases.append(b)
        # per-award totals of every action in this file
        tx = tx.sort_values(["contract_award_unique_key", "action_date"])
        a = tx["action_type_code"]
        tx["is_option"], tx["is_scope"], tx["is_term"] = a.isin(OPTION), a.isin(SCOPE), a.isin(TERMINATION)
        g = tx.groupby("contract_award_unique_key", sort=False)
        parts.append(pd.DataFrame({
            "n_tx": g.size(), "obligation_sum": g["federal_action_obligation"].sum(),
            "value_max": g["base_and_all_options_value"].max(), "value_last": g["base_and_all_options_value"].last(),
            "exercised_last": g["base_and_exercised_options_value"].last(), "last_action_date": g["action_date"].max(),
            "n_options": g["is_option"].sum(), "n_scope_changes": g["is_scope"].sum(), "n_terminations": g["is_term"].sum(),
            # each action's value is a change, so the sums are the year's net change (for the base year: the initial value too)
            "value_sum": g["base_and_all_options_value"].sum(), "exercised_sum": g["base_and_exercised_options_value"].sum()}))
        print(f"  {os.path.basename(p)}: {len(tx):,} transactions", flush=True)

    base = pd.concat(bases, ignore_index=True).sort_values("action_date").drop_duplicates("contract_award_unique_key")
    base["fy"] = base.action_date.dt.year + (base.action_date.dt.month >= 10).astype(int)
    base = base[base.fy == fy].copy()                      # the year's own base actions
    base["is_dod"] = base["awarding_agency_name"].eq("Department of Defense")
    base["offers"] = pd.to_numeric(base["number_of_offers_received"], errors="coerce")
    base["award_kind"] = np.where(base["award_or_idv_flag"] == "IDV", "IDV", np.where(base["award_type_code"] == "B", "PO", "DEF"))
    base["competed"] = base["extent_competed_code"].isin(COMPETED)
    base["offers_ok"] = base["offers"].between(1, 998)
    base["eval_set"] = base["competed"] & base["offers_ok"]
    base["single"] = base["offers"] == 1
    base = base.drop(columns=["number_of_offers_received", "award_or_idv_flag"])
    for c in ("awarding_agency_name", "awarding_sub_agency_name", "award_type_code", "award_kind", "extent_competed_code",
              "solicitation_procedures_code", "type_of_set_aside_code", "idv_type_code", "type_of_contract_pricing_code"):
        if c in base.columns:
            base[c] = base[c].astype("category")
    # awards split across files (windows): combine their per-file totals
    allp = pd.concat(parts).reset_index().rename(columns={"index": "contract_award_unique_key"})
    allp = allp.sort_values("last_action_date")
    g = allp.groupby("contract_award_unique_key", sort=False)
    ay = pd.DataFrame({
        "n_tx": g["n_tx"].sum(), "obligation_sum": g["obligation_sum"].sum(), "value_max": g["value_max"].max(),
        "value_last": g["value_last"].last(), "exercised_last": g["exercised_last"].last(), "last_action_date": g["last_action_date"].max(),
        "n_options": g["n_options"].sum(), "n_scope_changes": g["n_scope_changes"].sum(), "n_terminations": g["n_terminations"].sum(),
        "value_sum": g["value_sum"].sum(), "exercised_sum": g["exercised_sum"].sum()})
    ay = ay.reset_index().assign(fy=fy)

    counts = {"fiscal_year": fy, "transactions": int(n_rows), "base_actions": int(len(base)), "defense_base_actions": int(base.is_dod.sum()),
              "competed": int(base.competed.sum()), "eval_set": int(base.eval_set.sum()), "single": int((base.eval_set & base.single).sum()),
              "awards_with_actions": int(len(ay)), "files": [os.path.basename(z) for z in zips]}
    # write each table under a temporary name and swap it in, so a reader never sees a half-written file
    for name, write in ((f"base_actions_FY{fy}.parquet", lambda p: base.to_parquet(p, index=False, compression="zstd")),
                        (f"award_year_FY{fy}.parquet", lambda p: ay.to_parquet(p, index=False, compression="zstd")),
                        (f"history_FY{fy}.json", lambda p: write_json(counts, p))):
        swap_in(write, os.path.join(out_dir, name))
    print(json.dumps({k: v for k, v in counts.items() if k != "files"}, indent=1))


def write_json(obj, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1)


def swap_in(write, dest, tries=30, wait=2.0):
    """write(tmp) then replace dest with tmp.  On Windows the swap fails while another program has dest open, so it is retried."""
    import time
    tmp = dest + ".tmp"
    write(tmp)
    for i in range(tries):
        try:
            os.replace(tmp, dest)
            return
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(wait)


if __name__ == "__main__":
    main()
