"""
02_build_awards.py - turn the downloaded transaction ZIPs into ONE TABLE OF BASE AWARDS (one row per award).

Background for a learner
  * A "transaction" is one action on a contract: the original award (modification_number 0) or a later
    modification (P00001, P00002 ...).  USAspending gives us one row per transaction.
  * A "base award" is the transaction with modification_number 0.  Its fields (offers received, how competed,
    set-aside, value) describe the award AT THE TIME IT WAS MADE, which is what we want for a single-offer atlas.
  * For each award we also add what happened later (sum of obligations, sum of the changes in potential value) using all of
    the award's transactions that fall inside our download window (Oct 2023 - Sep 2026).
  * On each transaction row, base_and_all_options_value (and federal_action_obligation) is the CHANGE made by that action,
    not a running total: the base action carries the initial value, and a modification carries its increase or decrease
    (often 0).  USAspending's award-level "current total value" is the sum of these changes.  So:
        value_all_options_sum   sum of the per-action values over the award's actions in the window = the award's potential
                                value at the end of the window (true cumulative potential value)
        value_all_options_max   the largest single per-action value (usually the base action's own value)
        value_all_options_last  the per-action value of the latest action (often 0)
    max and last are per-action figures.  They are kept for reference, but they are NOT the award's total, and growth must
    not be measured with them.  (An earlier version of 03_atlas.py did, and that measure was withdrawn.)

Labels
  competed    = extent_competed_code in A (full and open), D (full and open after exclusion of sources, i.e. set-asides),
                E (follow-on to competed), F (competed under simplified acquisition procedures), CDO (competitive delivery order)
  offers_ok   = number_of_offers_received is a number from 1 to 998 (blank and 999 are not usable; 999 is a sentinel value)
  eval_set    = competed AND offers_ok     <- the denominator for every single-offer share in this project
  single      = offers == 1

Input : the five departments' rows of data/usaspending_civilian/*.zip (from 01c_download_civilian.py), or
        data/usaspending/*.zip if you downloaded the five departments separately with 01_download_usaspending.py
Output: data/processed/base_awards.parquet  and a printed summary

With --civilian it builds the same table for every civilian agency instead (03b_atlas_civilian.py):
Input : data/usaspending_civilian/*.zip   (from 01c_download_civilian.py)
Output: data/processed/base_awards_civilian.parquet
In that mode only the columns the civilian-wide atlas needs are read, to keep memory low; the rules are the same.
"""
import glob
import json
import os
import sys
import zipfile

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import AGENCIES, DATA, RESULTS, WINDOW_END  # noqa: E402

SRC = os.path.join(DATA, "usaspending", "*.zip")
OUT_DIR = os.path.join(DATA, "processed")
os.makedirs(OUT_DIR, exist_ok=True)

COMPETED = {"A", "D", "E", "F", "CDO"}
# columns read in --civilian mode (everything the atlas and the rules below use)
CIVILIAN_COLS = [
    "contract_transaction_unique_key", "contract_award_unique_key", "modification_number", "action_date",
    "award_type_code", "award_or_idv_flag", "awarding_agency_name", "extent_competed_code", "number_of_offers_received",
    "type_of_set_aside_code", "solicitation_procedures_code", "solicitation_identifier",
    "federal_action_obligation", "base_and_all_options_value", "base_and_exercised_options_value",
]
SHORT = {
    "Department of Veterans Affairs": "VA",
    "Department of Health and Human Services": "HHS",
    "Department of Homeland Security": "DHS",
    "Department of the Interior": "DOI",
    "Department of Agriculture": "USDA",
}


def read_all(src=SRC, usecols=None, agencies=None):
    """read every transaction CSV in the ZIPs matching `src`; keep only `agencies` (awarding agency names) if given"""
    frames = []
    for path in sorted(glob.glob(src)):
        z = zipfile.ZipFile(path)
        for name in z.namelist():
            if "Transactions" in name and name.endswith(".csv"):
                df = pd.read_csv(z.open(name), dtype=str, keep_default_na=False, usecols=usecols)
                if agencies is not None:
                    df = df[df["awarding_agency_name"].isin(agencies)]
                df["src_file"] = os.path.basename(path)
                frames.append(df)
    return pd.concat(frames, ignore_index=True)


def fiscal_year(dt):
    return dt.dt.year + (dt.dt.month >= 10).astype(int)


def build(src=SRC, usecols=None, window_end=WINDOW_END, agencies=None):
    """Read the transaction ZIPs that match `src` and return the table of base awards (rules in the header).
    `window_end` is the last day of the download window (used for months_at_risk); `agencies` keeps only those agencies."""
    tx = read_all(src, usecols, agencies)
    print("transaction rows read:", len(tx))
    n0 = len(tx)
    tx = tx.drop_duplicates("contract_transaction_unique_key")
    print("after dropping duplicate transaction keys:", len(tx), f"(dropped {n0 - len(tx)})")
    tx["action_date"] = pd.to_datetime(tx["action_date"], errors="coerce")
    for c in ("federal_action_obligation", "base_and_all_options_value", "base_and_exercised_options_value"):
        tx[c] = pd.to_numeric(tx[c], errors="coerce")
    tx["offers"] = pd.to_numeric(tx["number_of_offers_received"], errors="coerce")
    tx["award_kind"] = np.where(tx["award_or_idv_flag"] == "IDV", "IDV",
                                np.where(tx["award_type_code"] == "B", "PO", "DEF"))
    tx["agency"] = tx["awarding_agency_name"].map(SHORT).fillna(tx["awarding_agency_name"])

    # ---- award-level aggregates over every transaction we downloaded --------------------------------
    tx = tx.sort_values(["contract_award_unique_key", "action_date", "modification_number"])
    g = tx.groupby("contract_award_unique_key", sort=False)
    agg = pd.DataFrame({
        "n_tx": g.size(),
        "obligation_sum_window": g["federal_action_obligation"].sum(),
        "last_action_date": g["action_date"].max(),
        "value_all_options_last": g["base_and_all_options_value"].last(),   # per-action change of the latest action
        "value_all_options_max": g["base_and_all_options_value"].max(),     # largest per-action change (NOT the award's total)
        "value_all_options_sum": g["base_and_all_options_value"].sum(),     # sum of the changes = cumulative potential value
    })

    # ---- base action = modification_number 0 (earliest if there are several) ------------------------
    base = tx[tx["modification_number"].isin(["0", "00", "000", ""])].copy()
    base = base.sort_values(["contract_award_unique_key", "action_date"]).drop_duplicates("contract_award_unique_key", keep="first")
    build.counts = {"transaction_rows": int(n0), "duplicate_transactions_dropped": int(n0 - len(tx)),
                    "awards_seen": int(tx["contract_award_unique_key"].nunique()), "awards_with_base_action": int(len(base))}
    print("awards seen:", tx["contract_award_unique_key"].nunique(), "| with a base action in window:", len(base),
          f"({100 * len(base) / tx['contract_award_unique_key'].nunique():.1f}%)")
    base = base.merge(agg, left_on="contract_award_unique_key", right_index=True, how="left")

    base["fy"] = fiscal_year(base["action_date"])
    base["competed"] = base["extent_competed_code"].isin(COMPETED)
    base["offers_ok"] = base["offers"].between(1, 998)
    base["eval_set"] = base["competed"] & base["offers_ok"]
    base["single"] = (base["offers"] == 1)
    base["has_sol_id"] = base["solicitation_identifier"].str.strip() != ""
    base["value0"] = base["base_and_all_options_value"]
    base["obl0"] = base["federal_action_obligation"]
    # months between the base action and the end of our window (time the award had to grow)
    base["months_at_risk"] = ((pd.Timestamp(window_end) - base["action_date"]).dt.days / 30.44).round(1)

    keep = ["contract_award_unique_key", "award_id_piid", "parent_award_id_piid", "agency", "awarding_sub_agency_name",
            "awarding_office_code", "awarding_office_name", "award_kind", "idv_type_code", "multiple_or_single_award_idv_code",
            "action_date", "fy", "solicitation_identifier", "has_sol_id", "product_or_service_code", "naics_code",
            "extent_competed_code", "solicitation_procedures_code", "type_of_set_aside_code", "offers",
            "competed", "offers_ok", "eval_set", "single", "value0", "obl0", "base_and_exercised_options_value",
            "n_tx", "obligation_sum_window", "value_all_options_last", "value_all_options_max",
            "value_all_options_sum", "last_action_date", "months_at_risk",
            "type_of_contract_pricing_code", "commercial_item_acquisition_procedures_code",
            "fair_opportunity_limited_sources_code", "other_than_full_and_open_competition_code",
            "recipient_uei", "recipient_parent_uei", "purchase_card_as_payment_method_code", "cost_or_pricing_data_code"]
    base = base[[c for c in keep if c in base.columns]]
    for c in ("agency", "awarding_sub_agency_name", "award_kind", "idv_type_code", "multiple_or_single_award_idv_code",
              "extent_competed_code", "solicitation_procedures_code", "type_of_set_aside_code"):
        if c in base.columns:
            base[c] = base[c].astype("category")
    return base


def main(civilian=False):
    civ = os.path.join(DATA, "usaspending_civilian", "*.zip")
    if civilian:
        base = build(civ, CIVILIAN_COLS)
    elif glob.glob(SRC):
        base = build()                                   # a separate five-department download (01_download_usaspending.py)
    else:
        base = build(civ, agencies=AGENCIES)             # the five departments' rows of the civilian download
    out = os.path.join(OUT_DIR, "base_awards_civilian.parquet" if civilian else "base_awards.parquet")
    base.to_parquet(out, index=False)
    print("saved", out, base.shape)
    # the counts behind the methodology's coverage line (results/ keeps them, so `run.py quick` needs no raw files)
    counts = dict(build.counts, competed=int(base.competed.sum()), eval_set=int(base.eval_set.sum()),
                  offers_999_among_competed=int((base[base.competed].offers == 999).sum()))
    with open(os.path.join(RESULTS, f"build_summary_{'civilian' if civilian else 'five'}.json"), "w", encoding="utf-8") as f:
        json.dump(counts, f, indent=1)

    # ---- quick summary ---------------------------------------------------------------------------
    print("\nBase awards by agency x kind:")
    print(base.groupby(["agency", "award_kind"], observed=True).size().unstack(fill_value=0))
    print("\nBase awards by agency x fiscal year:")
    print(base.groupby(["agency", "fy"], observed=True).size().unstack(fill_value=0))
    print("\nShare competed / offers usable / eval set (all base awards):",
          f"{base.competed.mean():.3f} / {base.offers_ok.mean():.3f} / {base.eval_set.mean():.3f}")


if __name__ == "__main__":
    main(civilian="--civilian" in sys.argv)
