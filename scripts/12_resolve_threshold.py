"""
12_resolve_threshold.py - SCORE the pre-registered threshold forecast (F2) once USAspending has posted the forecast period.

What it does
  1. Reads the forecast JSON (period, quantities, point forecasts, 80% intervals, baselines).
  2. Downloads the civilian purchase-order + definitive-contract transactions for that period (same request as
     01b_download_threshold_universe.py: 110 civilian top-tier agencies, 14 columns), keeps BASE actions (modification 0).
     A window that reaches USAspending's 500,000-row cap (or the 25 MB rule) is split into months and the months combined;
     a failed or cut-off download stops the script with an error, so nothing is scored from incomplete data.
  3. Re-computes the bunching ratio at $250K and $350K with EXACTLY the estimator in bunching_lib.py (same bins, window,
     exclusion zone, degree-5 Poisson polynomial) and a 300-resample bootstrap interval.
  4. Reports, for each quantity: actual, forecast, whether it fell inside the 80% interval, absolute error of the forecast and of the baseline.

Usage:  python -I scripts/12_resolve_threshold.py forecasts/<threshold forecast>.json
Output: results/threshold_resolution_<forecast id>.json
"""
import calendar
import importlib.util
import json
import os
import sys
import time
import zipfile
from datetime import date, timedelta

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bunching_lib import SPEC, boot, bunch, count_bins  # noqa: E402
from common import DATA, RESULTS, UA_USASPENDING, download_file, http_json  # noqa: E402

# reuse the column list and agency lookup of the downloader (its file name starts with a digit, so import it by path)
spec_ = importlib.util.spec_from_file_location("dl01b", os.path.join(os.path.dirname(os.path.abspath(__file__)), "01b_download_threshold_universe.py"))
dl = importlib.util.module_from_spec(spec_)
spec_.loader.exec_module(dl)


ROW_CAP = 499_000          # USAspending cuts a download off at 500,000 rows: at or above this a window is split into months
SIZE_CAP_KB = 25_000       # the 25 MB rule, as in 01c_download_civilian.py


def months(start, end):
    """the calendar-month windows inside start..end (YYYY-MM-DD), the last one cut at `end`"""
    out, d, last = [], date.fromisoformat(start), date.fromisoformat(end)
    while d <= last:
        month_end = date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])
        out.append((d.isoformat(), min(month_end, last).isoformat()))
        d = month_end + timedelta(days=1)
    return out


def transactions(start, end, agencies):
    """All transactions of one window.  A window that hits the row cap or the size cap is split into months and the months
    are combined (as 01c_download_civilian.py does).  Stops with an error rather than score from an incomplete download."""
    body = {"filters": {"award_type_codes": ["B", "D"], "agencies": [{"type": "awarding", "tier": "toptier", "name": n} for n in agencies],
                        "time_period": [{"start_date": start, "end_date": end, "date_type": "action_date"}]},
            "columns": dl.COLUMNS, "file_format": "csv"}
    resp = http_json(f"{dl.API}/download/transactions/", method="POST", body=body, ua=UA_USASPENDING)
    if not resp or "file_name" not in resp:
        sys.exit(f"could not submit the download request for {start}..{end}")
    st = {}
    for _ in range(120):
        st = http_json(f"{dl.API}/download/status?file_name={resp['file_name']}", ua=UA_USASPENDING) or {}
        if st.get("status") in ("finished", "failed"):
            break
        time.sleep(10)
    if st.get("status") != "finished":
        sys.exit(f"download not usable for {start}..{end}: {st}")
    if int(st.get("total_rows") or 0) >= ROW_CAP or float(st.get("total_size") or 0) > SIZE_CAP_KB:
        windows = months(start, end)
        if len(windows) < 2:
            sys.exit(f"{start}..{end} alone reaches the 500,000-row or 25 MB cap ({st.get('total_rows')} rows, "
                     f"{st.get('total_size')} KB): refusing to score from a cut-off download")
        print(f"  {start}..{end}: {st.get('total_rows')} rows, too many for one file: split into {len(windows)} months", flush=True)
        return pd.concat([transactions(ms, me, agencies) for ms, me in windows], ignore_index=True)
    out_dir = os.path.join(DATA, "resolve_tmp")
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, resp["file_name"])
    n = download_file(resp["file_url"], dest, ua=UA_USASPENDING)
    if not n or not zipfile.is_zipfile(dest):
        sys.exit(f"the download of {start}..{end} failed (no usable ZIP file); nothing was scored. Run again later.")
    with zipfile.ZipFile(dest) as z:
        frames = [pd.read_csv(z.open(f), dtype=str, keep_default_na=False) for f in z.namelist() if "Transactions" in f and f.endswith(".csv")]
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
    out = {"forecast_id": fc["id"], "period": per, "n_base_actions": int(len(base)), "n_values_used": int(len(v)), "results": results}
    write_atomic(os.path.join(RESULTS, f"threshold_resolution_{fc['id']}.json"), out)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
