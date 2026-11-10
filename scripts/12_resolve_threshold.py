"""
12_resolve_threshold.py - SCORE the pre-registered threshold forecast (F2) once USAspending has posted the forecast period.

What it does
  1. Reads the forecast JSON (period, quantities, point forecasts, 80% intervals, baselines).
  2. Downloads the civilian purchase-order + definitive-contract transactions for that period (same request as
     01b_download_threshold_universe.py: 110 civilian top-tier agencies, 14 columns), keeps BASE actions (modification 0).
     The download goes through the shared verified download (01_download_usaspending.fetch_verified): every file's rows must
     match USAspending's own count for the same filters and dates.  A window that reaches the 500,000-row cap or the 25 MB rule, or
     whose file comes back cut off, is split into smaller windows and the pieces combined.  STRICT: a failed, cut-off or unchecked
     download (also when the count service cannot be reached) stops the script with an error, so nothing is scored from incomplete
     data.  The verification (expected and received rows) is written into the result file.
  3. Re-computes the bunching ratio at $250K and $350K with EXACTLY the estimator in bunching_lib.py (same bins, window,
     exclusion zone, degree-5 Poisson polynomial) and a 300-resample bootstrap interval.
  4. Reports, for each quantity: actual, forecast, whether it fell inside the 80% interval, absolute error of the forecast and of the baseline.

Usage:  python -I scripts/12_resolve_threshold.py forecasts/<threshold forecast>.json
Output: results/threshold_resolution_<forecast id>.json
"""
import importlib.util
import json
import os
import shutil
import sys
import zipfile

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bunching_lib import SPEC, boot, bunch, count_bins  # noqa: E402
from common import DATA, RESULTS  # noqa: E402

# reuse the column list and agency lookup of the downloader (its file name starts with a digit, so import it by path)
spec_ = importlib.util.spec_from_file_location("dl01b", os.path.join(os.path.dirname(os.path.abspath(__file__)), "01b_download_threshold_universe.py"))
dl = importlib.util.module_from_spec(spec_)
spec_.loader.exec_module(dl)


# the shared verified download (its file name starts with a digit, so import it by path)
spec_ = importlib.util.spec_from_file_location("dl01", os.path.join(os.path.dirname(os.path.abspath(__file__)), "01_download_usaspending.py"))
DL = importlib.util.module_from_spec(spec_)
spec_.loader.exec_module(DL)
LAST_VERIFICATION = {}       # the verification of the latest download (expected and received rows), written into the result


def transactions(start, end, agencies):
    """All transactions of one window, downloaded fresh through the shared verified download (split when too big or cut off).
    Stops with an error rather than score from an incomplete or unchecked download."""
    out_dir = os.path.join(DATA, "resolve_tmp", "f2_threshold")
    shutil.rmtree(out_dir, ignore_errors=True)                # always fresh: never mix in files of an earlier run
    os.makedirs(out_dir)
    failed = DL.fetch_verified("F2", start, end, agencies, out_dir, prefix="CIVILIAN", columns=dl.COLUMNS,
                               award_types=["B", "D"], force=True)
    summary, leaves = DL.verification(out_dir, ["F2"])
    if failed or not summary["verified"]:
        sys.exit(f"the download of {start}..{end} could not be verified against USAspending's own count "
                 f"({'; '.join(summary['problems']) or failed}): refusing to score from a cut-off or unchecked download. "
                 "Nothing was written. Run again later.")
    LAST_VERIFICATION.clear()
    LAST_VERIFICATION.update({k: summary[k] for k in ("verified", "rule", "expected_rows", "received_rows", "windows")})
    frames = []
    for lf in leaves:
        with zipfile.ZipFile(lf["zip"]) as z:
            frames += [pd.read_csv(z.open(f), dtype=str, keep_default_na=False) for f in z.namelist() if "Transactions" in f and f.endswith(".csv")]
    if not frames:
        sys.exit(f"the download of {start}..{end} holds no transaction file; nothing was scored")
    return pd.concat(frames, ignore_index=True)


def fetch(start, end):
    """Base actions (modification 0, one per award) of the civilian purchase orders and definitive contracts dated start..end."""
    tx = transactions(start, end, dl.civilian_agencies()).drop_duplicates("contract_transaction_unique_key")
    tx["action_date"] = pd.to_datetime(tx["action_date"])
    tx["v"] = pd.to_numeric(tx["base_and_all_options_value"], errors="coerce")
    tx["obl"] = pd.to_numeric(tx["federal_action_obligation"], errors="coerce")
    return tx[tx.modification_number.isin(["0", "00", "000", ""])].drop_duplicates("contract_award_unique_key")


def write_atomic(path, obj):
    """Write JSON to a temporary name, then rename it: a crash cannot leave a half-written file (which would block re-scoring)."""
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=1)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def main():
    fc = json.load(open(sys.argv[1], encoding="utf-8"))
    per = fc["period"]
    base = fetch(per["start"], per["end"])
    print(f"{len(base):,} civilian base actions with action dates {base.action_date.min().date()} .. {base.action_date.max().date()}")
    v = base.v.dropna().values
    results = []
    for q in fc["quantities"]:
        T = q["threshold"]
        if q["kind"] == "po_reporting_share":
            po = base[base.award_type_code == "B"].obl.dropna()
            a = ((po > 10_000) & (po <= 15_000)).sum()
            b = ((po > 15_000) & (po <= 20_000)).sum()
            est = a / (a + b)
            rng_ = np.random.default_rng(1)
            bs = np.array([rng_.binomial(a + b, est) / (a + b) for _ in range(300)])      # simple binomial sampling error
        else:
            spec = SPEC[T]
            e, c = count_bins(v, spec[0], spec[1], spec[2])
            est = bunch(c, e, T, spec[3], spec[4])["ratio"]
            bs = boot(v, T, spec, B=300)
        lo, hi = q["interval80"]
        results.append({"name": q["name"], "threshold": T, "actual_ratio": float(est), "actual_95ci": [float(np.nanpercentile(bs, 2.5)), float(np.nanpercentile(bs, 97.5))],
                        "forecast": q["forecast"], "interval80": q["interval80"], "inside_interval80": bool(lo <= est <= hi),
                        "abs_error_forecast": float(abs(est - q["forecast"])), "baseline": q["baseline"], "abs_error_baseline": float(abs(est - q["baseline"])),
                        "forecast_beats_baseline": bool(abs(est - q["forecast"]) < abs(est - q["baseline"]))})
    out = {"forecast_id": fc["id"], "period": per, "n_base_actions": int(len(base)), "n_values_used": int(len(v)), "results": results,
           "download_verification": dict(LAST_VERIFICATION)}
    write_atomic(os.path.join(RESULTS, f"threshold_resolution_{fc['id']}.json"), out)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
