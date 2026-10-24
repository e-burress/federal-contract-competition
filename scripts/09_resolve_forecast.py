"""
09_resolve_forecast.py - SCORE a pre-registered single-offer forecast once USAspending has posted the awards.

Resolution rule (written into the forecast file before any outcome existed):
  For each forecast solicitation number S:
    1. Normalise S (upper case, letters and digits only).
    2. Look at USAspending BASE ACTIONS (modification_number 0) of purchase orders, definitive contracts and IDV awards whose
       normalised solicitation_identifier equals S and whose base action date is on or after the forecast's made_at date.
    3. If several match, use the one with the EARLIEST action date (ties: larger base-and-all-options value).
    4. The solicitation is RESOLVED only if that award was competed (extent competed A, D, E, F or CDO) and
       number_of_offers_received is a number from 1 to 998.  outcome = 1 if offers == 1 else 0.
    5. Everything else is reported as UNRESOLVED with its reason (no award yet / not competed / offers blank or 999).
  Scores use resolved solicitations only: Brier score, log loss, ROC-AUC, a calibration table, and skill versus the baseline.

Usage
    python -I scripts/09_resolve_forecast.py forecasts/<forecast>.json [YYYY-MM-DD snapshot date]
         downloads (live) the recent base actions for the forecast's agencies, then scores.
    python -I scripts/09_resolve_forecast.py forecasts/<forecast>.json --from-processed
         dry run: use data/processed/base_awards.parquet instead of downloading (tests the code on old data).
Output: results/resolution_<forecast id>_<date>.json and .csv
"""
import csv
import glob
import json
import os
import sys
import time
import zipfile
from datetime import datetime, timezone

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA, RESULTS, UA_USASPENDING, download_file, http_json  # noqa: E402

API = "https://api.usaspending.gov/api/v2"
COMPETED = {"A", "D", "E", "F", "CDO"}
COLS = ["contract_award_unique_key", "award_id_piid", "modification_number", "action_date", "award_type_code", "award_or_idv_flag",
        "base_and_all_options_value", "awarding_agency_name", "awarding_office_code", "solicitation_identifier",
        "extent_competed_code", "number_of_offers_received"]


def norm(s):
    return "".join(ch for ch in (s or "").upper() if ch.isalnum())


def fetch_recent_base_actions(agencies, start, end):
    """Download base-action rows for the given agencies from USAspending (Advanced Search transaction download, a few columns only)."""
    out_dir = os.path.join(DATA, "resolve_tmp")
    os.makedirs(out_dir, exist_ok=True)
    frames = []
    for ag in agencies:
        body = {"filters": {"award_type_codes": ["B", "D", "IDV_A", "IDV_B", "IDV_B_A", "IDV_B_B", "IDV_B_C", "IDV_C", "IDV_D", "IDV_E"],
                            "agencies": [{"type": "awarding", "tier": "toptier", "name": ag}],
                            "time_period": [{"start_date": start, "end_date": end, "date_type": "action_date"}]},
                "columns": COLS, "file_format": "csv"}
        resp = http_json(f"{API}/download/transactions/", method="POST", body=body, ua=UA_USASPENDING)
        if not resp:
            print("could not submit for", ag)
            continue
        for _ in range(100):
            st = http_json(f"{API}/download/status?file_name={resp['file_name']}", ua=UA_USASPENDING) or {}
            if st.get("status") in ("finished", "failed"):
                break
            time.sleep(8)
        if st.get("status") == "finished" and float(st.get("total_size") or 0) <= 25_000:
            dest = os.path.join(out_dir, resp["file_name"])
            if download_file(resp["file_url"], dest, ua=UA_USASPENDING):
                z = zipfile.ZipFile(dest)
                for n in z.namelist():
                    if "Transactions" in n and n.endswith(".csv"):
                        frames.append(pd.read_csv(z.open(n), dtype=str, keep_default_na=False))
        time.sleep(3)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=COLS)


def base_actions_from_tx(tx):
    tx = tx.copy()
    tx["action_date"] = pd.to_datetime(tx["action_date"], errors="coerce")
    tx["value"] = pd.to_numeric(tx["base_and_all_options_value"], errors="coerce")
    tx["offers"] = pd.to_numeric(tx["number_of_offers_received"], errors="coerce")
    b = tx[tx.modification_number.isin(["0", "00", "000", ""])].copy()
    b["sol_norm"] = b.solicitation_identifier.map(norm)
    return b[b.sol_norm != ""]


def score(df, pcol):
    from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
    y, p = df["outcome"].values, df[pcol].clip(0.001, 0.999).values
    res = {"brier": float(brier_score_loss(y, p)), "log_loss": float(log_loss(y, p, labels=[0, 1]))}
    res["auc"] = float(roc_auc_score(y, p)) if len(set(y)) > 1 else None
    return res


def main():
    fpath = sys.argv[1]
    fc = json.load(open(fpath, encoding="utf-8"))
    made_at = pd.Timestamp(fc["made_at_utc"][:10])
    snap = next((a for a in sys.argv[2:] if not a.startswith("--")), datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    items = pd.DataFrame(fc["forecasts"])
    items["sol_norm"] = items["solicitation_number"].map(norm)
    if "--from-processed" in sys.argv:
        b = pd.read_parquet(os.path.join(DATA, "processed", "base_awards.parquet"))
        b = b.assign(sol_norm=b.solicitation_identifier.map(norm), value=b.value0, offers=b.offers)
        b = b[b.sol_norm != ""]
        made_at = pd.Timestamp("1900-01-01")                      # dry run: ignore the date rule
    else:
        agencies = sorted(items["agency"].unique())
        tx = fetch_recent_base_actions(agencies, (made_at - pd.Timedelta(days=7)).strftime("%Y-%m-%d"), snap)
        b = base_actions_from_tx(tx)
    b = b[(b.action_date >= made_at) & b.sol_norm.isin(set(items.sol_norm))]
    b = b.sort_values(["sol_norm", "action_date", "value"], ascending=[True, True, False]).drop_duplicates("sol_norm", keep="first")
    m = items.merge(b[["sol_norm", "award_id_piid", "action_date", "extent_competed_code", "offers"]], on="sol_norm", how="left")
    m["competed"] = m.extent_competed_code.isin(COMPETED)
    m["offers_ok"] = m.offers.between(1, 998)
    m["status"] = np.select(
        [m.award_id_piid.isna(), ~m.competed, ~m.offers_ok],
        ["unresolved: no matching award yet", "unresolved: award not competed", "unresolved: offers blank/999"], default="resolved")
    m["outcome"] = np.where(m.status == "resolved", (m.offers == 1).astype(float), np.nan)
    r = m[m.status == "resolved"].copy()
    summary = {"forecast_id": fc["id"], "snapshot_date": snap, "n_forecasts": int(len(m)),
               "status_counts": m.status.value_counts().to_dict(), "n_resolved": int(len(r))}
    if len(r) >= 5:
        summary["observed_single_offer_share"] = float(r.outcome.mean())
        summary["model"] = score(r, "p_single_offer")
        summary["baseline_agency_rate"] = score(r, "p_baseline_agency")
        summary["brier_skill_vs_baseline"] = 1 - summary["model"]["brier"] / summary["baseline_agency_rate"]["brier"]
        r["bin"] = pd.cut(r.p_single_offer, [0, .1, .2, .3, .4, .5, .7, 1.0])
        summary["calibration"] = [{"bin": str(k), "n": int(len(g)), "mean_forecast": float(g.p_single_offer.mean()), "observed": float(g.outcome.mean())}
                                  for k, g in r.groupby("bin", observed=True)]
    tag = f"{fc['id']}_{snap}".replace(" ", "_")
    json.dump(summary, open(os.path.join(RESULTS, f"resolution_{tag}.json"), "w"), indent=1)
    m.drop(columns=["sol_norm"]).to_csv(os.path.join(RESULTS, f"resolution_{tag}.csv"), index=False)
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
