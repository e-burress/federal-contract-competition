"""
18_history_build.py - turn ONE fiscal year of downloaded transactions (17_history_download.py) into two compact public tables.

  base_actions_FY<fy>.parquet   one row per award whose base action (modification 0) is dated in the year: what was bought, how it was
                                competed, offers received, set-aside, values at award, agency, buying-office code, a pseudonymous vendor
                                id, and whether it is a Department of Defense award.  Same rules as 02_build_awards.py
                                (competed = A/D/E/F/CDO; usable offers = 1..998; eval set = both; single = exactly one offer).
  award_year_FY<fy>.parquet     one row per award with any action in the year: number of actions, dollars obligated, highest and latest
                                potential value, last action date, and how many actions were option exercises, scope changes
                                (supplemental agreements, change orders) or terminations.  Joining the years gives each award's history
                                after it was made (cost growth, extensions, terminations), even when that history spans many years.

Privacy, as in the public snapshot: buying-office NAMES are left out (codes stay), and company identifiers (UEI, or DUNS before 2022) are
replaced by vendor_id = the first 16 hex digits of SHA-256("fcc-v1:" + identifier), so awards to the same company can be linked without
publishing the identifier.

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
        vend = b.apply(lambda r: vendor_id(r.get("recipient_uei"), r.get("recipient_duns")), axis=1) if len(b) else []
        b = b[keep].assign(vendor_id=vend)
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
            "n_options": g["is_option"].sum(), "n_scope_changes": g["is_scope"].sum(), "n_terminations": g["is_term"].sum()}))
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
        "n_options": g["n_options"].sum(), "n_scope_changes": g["n_scope_changes"].sum(), "n_terminations": g["n_terminations"].sum()})
    ay = ay.reset_index().assign(fy=fy)

    base.to_parquet(os.path.join(out_dir, f"base_actions_FY{fy}.parquet"), index=False, compression="zstd")
    ay.to_parquet(os.path.join(out_dir, f"award_year_FY{fy}.parquet"), index=False, compression="zstd")
    counts = {"fiscal_year": fy, "transactions": int(n_rows), "base_actions": int(len(base)), "defense_base_actions": int(base.is_dod.sum()),
              "competed": int(base.competed.sum()), "eval_set": int(base.eval_set.sum()), "single": int((base.eval_set & base.single).sum()),
              "awards_with_actions": int(len(ay)), "files": [os.path.basename(z) for z in zips]}
    json.dump(counts, open(os.path.join(out_dir, f"history_FY{fy}.json"), "w", encoding="utf-8"), indent=1)
    print(json.dumps({k: v for k, v in counts.items() if k != "files"}, indent=1))


if __name__ == "__main__":
    main()
