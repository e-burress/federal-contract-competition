"""
13_track_threshold.py - the weekly check on the threshold forecast (F2): what the FY2027 Q1 numbers look like SO FAR.

The official score comes from 12_resolve_threshold.py after the resolution date.  Until then this script gives a provisional
reading, so the dashboard can show how the forecast is tracking:
  1. Downloads the civilian purchase-order + definitive-contract base actions from the start of the forecast period to yesterday
     (same request as 12_resolve_threshold.py).
  2. Computes the same three quantities with the same estimator (bunching_lib.py), with a 300-resample bootstrap interval.
  3. Writes results/tracking_<forecast id>.json and puts one row per quantity in results/tracking_<forecast id>_history.csv
     (a second run on the same day replaces that day's rows).
  4. On or after the forecast's resolution date, runs 12_resolve_threshold.py once to produce the official score
     (skipped if results/threshold_resolution_<forecast id>.json already exists).

It never touches the sealed forecast file.  Early readings are noisy (few awards, and agencies keep reporting for weeks).

Usage:  python -I scripts/13_track_threshold.py [forecasts/<threshold forecast>.json]
Output: results/tracking_<forecast id>.json, results/tracking_<forecast id>_history.csv
"""
import csv
import importlib.util
import json
import os
import subprocess
import sys
from datetime import date, timedelta

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bunching_lib import SPEC, boot, bunch, count_bins  # noqa: E402
from common import CONFIG, PROJECT, RESULTS  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
spec_ = importlib.util.spec_from_file_location("resolve12", os.path.join(HERE, "12_resolve_threshold.py"))
r12 = importlib.util.module_from_spec(spec_)
spec_.loader.exec_module(r12)


def main():
    fc_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(PROJECT, CONFIG["forecasts"]["F2"]["file"])
    fc = json.load(open(fc_path, encoding="utf-8"))
    fid, per = fc["id"], fc["period"]
    today = date.today()

    official = os.path.join(RESULTS, f"threshold_resolution_{fid}.json")
    if today >= date.fromisoformat(fc["resolution_date"]) and not os.path.exists(official):
        print("resolution date reached: running the official score")
        subprocess.run([sys.executable, "-I", os.path.join(HERE, "12_resolve_threshold.py"), fc_path], check=True)

    end = min(date.fromisoformat(per["end"]), today - timedelta(days=1))
    if end < date.fromisoformat(per["start"]):
        sys.exit("the forecast period has not started yet")
    base = r12.fetch(per["start"], end.isoformat())
    v = base.v.dropna().values
    print(f"{len(base):,} civilian base actions, {per['start']} .. {end}")

    out_q = []
    for q in fc["quantities"]:
        T = q["threshold"]
        if q["kind"] == "po_reporting_share":
            po = base[base.award_type_code == "B"].obl.dropna()
            a = int(((po > 10_000) & (po <= 15_000)).sum())
            b = int(((po > 15_000) & (po <= 20_000)).sum())
            est = a / (a + b) if a + b else float("nan")
            rng_ = np.random.default_rng(1)
            bs = np.array([rng_.binomial(a + b, est) / (a + b) for _ in range(300)]) if a + b else np.array([np.nan])
        else:
            spec = SPEC[T]
            e, c = count_bins(v, spec[0], spec[1], spec[2])
            est = bunch(c, e, T, spec[3], spec[4])["ratio"]
            bs = boot(v, T, spec, B=300)
        lo, hi = q["interval80"]
        ok = bool(np.isfinite(est))
        out_q.append({"name": q["name"], "threshold": T, "so_far": float(est) if ok else None,
                      "so_far_95ci": [float(np.nanpercentile(bs, 2.5)), float(np.nanpercentile(bs, 97.5))] if np.isfinite(bs).any() else None,
                      "forecast": q["forecast"], "interval80": q["interval80"], "inside_interval80_so_far": bool(lo <= est <= hi) if ok else None})

    out = {"forecast_id": fid, "as_of": today.isoformat(), "data_from": per["start"], "data_to": end.isoformat(),
           "period": per, "provisional": True, "n_base_actions": int(len(base)), "n_values_used": int(len(v)), "quantities": out_q}
    json.dump(out, open(os.path.join(RESULTS, f"tracking_{fid}.json"), "w", encoding="utf-8"), indent=1)

    rows = []
    for q in out_q:
        ci = q["so_far_95ci"] or [None, None]
        r4 = lambda x: None if x is None else round(x, 4)
        rows.append([out["as_of"], out["data_to"], out["n_base_actions"], q["name"], r4(q["so_far"]), r4(ci[0]), r4(ci[1])])
    update_history(os.path.join(RESULTS, f"tracking_{fid}_history.csv"), out["as_of"], rows)
    print(json.dumps(out, indent=1))


HEADER = ["as_of", "data_to", "n_base_actions", "name", "so_far", "lo95", "hi95"]


def update_history(hist, as_of, rows):
    """Put today's rows in the history CSV, replacing any rows with the same as_of date (so a second run on the same day
    does not add a duplicate block).  The file is written to a temporary name first and then renamed."""
    kept = []
    if os.path.exists(hist):
        with open(hist, newline="", encoding="utf-8") as f:
            r = csv.reader(f)
            next(r, None)                                   # header
            kept = [row for row in r if row and row[0] != as_of]
    tmp = hist + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(HEADER)
        w.writerows(kept)
        w.writerows(rows)                                   # None is written as an empty field
    os.replace(tmp, hist)


if __name__ == "__main__":
    main()
