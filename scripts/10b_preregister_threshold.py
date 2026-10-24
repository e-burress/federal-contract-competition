"""
10b_preregister_threshold.py - freeze forecast #2 (threshold pile-ups in FY2027 Q1) as JSON and record its SHA-256.

What is forecast (all for civilian purchase orders + definitive contracts, base actions dated 2026-10-01 .. 2026-12-31 = FY2027 Q1):
  1. bunching ratio just under $350,000   (observed / counterfactual awards in the four $5,000 bins below the line; 1.0 = no pile-up)
  2. bunching ratio just under $250,000
  3. share of reported purchase orders worth $10,000-$20,000 that fall in the $10,000-$15,000 half
     (the micro-purchase line moved from $10K to $15K on 2025-10-01; purchases under the line are mostly not reported, so this share fell)
Point forecast = mean of the two most recent fiscal quarters (FY2026 Q3 and Q4).  80% interval = point +/- 1.28 * sqrt(sampling_se^2 + level_se^2),
with sampling_se taken from the bootstrap errors of the two most recent Q1 quarters (small first-quarter samples) and level_se an allowance for the
quarter-to-quarter drift seen since the change.  Baselines: (a) persistence = the FY2026 Q4 value, (b) pre-change = the FY2025 full-year value.

Input : results/threshold_quarterly_bunching.csv, results/threshold_sat_bunching.csv, results/threshold_mpt_1k_bins.csv, universe zips
Output: forecasts/PIO-F2-threshold-fy2027q1-<date>.json, appended hash in forecasts/SHA256SUMS.txt
"""
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bunching_lib import load_universe  # noqa: E402
from common import FORECASTS, RESULTS  # noqa: E402

now = datetime.now(timezone.utc)
made_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
fid = f"PIO-F2-threshold-fy2027q1-{now.strftime('%Y%m%d')}"
path = os.path.join(FORECASTS, fid + ".json")
if os.path.exists(path):
    sys.exit(f"{path} already exists - a registered forecast is never overwritten.")

q = pd.read_csv(os.path.join(RESULTS, "threshold_quarterly_bunching.csv"))
yr = pd.read_csv(os.path.join(RESULTS, "threshold_sat_bunching.csv"))
quantities = []
for T, name, level_se in ((350_000, "bunching_ratio_350K", 0.30), (250_000, "bunching_ratio_250K", 0.15)):
    d = q[q.threshold == T].set_index("fyq")
    last2 = d.loc[["FY2026Q3", "FY2026Q4"], "ratio"].mean()
    samp = d.loc[["FY2025Q1", "FY2026Q1"], "se_boot"].mean()
    half = 1.28 * float(np.sqrt(samp ** 2 + level_se ** 2))
    pre = yr[(yr.threshold == T) & (yr.period == "FY2025_pre") & (yr.value == "base_and_all_options_value")].ratio.iloc[0]
    quantities.append({
        "name": name, "kind": "bunching_ratio", "threshold": T,
        "definition": f"observed / counterfactual number of civilian PO+definitive-contract base actions with base-and-all-options value in the four $5,000 bins up to ${T:,} (bins are right-closed), counterfactual = degree-5 Poisson polynomial fitted outside (T-20K, T+10K] over the window in SPEC of scripts/bunching_lib.py",
        "forecast": round(float(last2), 3), "interval80": [round(float(last2 - half), 3), round(float(last2 + half), 3)],
        "baseline": round(float(d.loc["FY2026Q4", "ratio"]), 3), "baseline_label": "persistence: FY2026 Q4 estimate",
        "baseline_pre_change": round(float(pre), 3), "baseline_pre_change_label": "FY2025 full-year estimate (before the 2025-10-01 change)",
        "recent_quarters": {k: round(float(v), 3) for k, v in d.loc[["FY2025Q1", "FY2025Q2", "FY2025Q3", "FY2025Q4", "FY2026Q1", "FY2026Q2", "FY2026Q3", "FY2026Q4"], "ratio"].items()},
    })

# micro-purchase reporting share, by fiscal-year quarter
tx, base = load_universe()
po = base[base.kind == "PO"].copy()
po["fyq"] = "FY" + po.fy.astype(str) + "Q" + (((po.action_date.dt.month - 10) % 12) // 3 + 1).astype(str)
shares = {}
for k, d in po.groupby("fyq"):
    v = d.federal_action_obligation.dropna()
    a = ((v > 10_000) & (v <= 15_000)).sum()
    b = ((v > 15_000) & (v <= 20_000)).sum()
    shares[k] = a / (a + b)
last2 = (shares["FY2026Q3"] + shares["FY2026Q4"]) / 2
fy25 = po[po.fy == 2025].federal_action_obligation.dropna()
pre_share = ((fy25 > 10_000) & (fy25 <= 15_000)).sum() / (((fy25 > 10_000) & (fy25 <= 15_000)).sum() + ((fy25 > 15_000) & (fy25 <= 20_000)).sum())
half = 1.28 * float(np.sqrt(0.0096 ** 2 + 0.03 ** 2))
quantities.append({
    "name": "po_share_10_15K", "kind": "po_reporting_share", "threshold": 15_000,
    "definition": "among civilian purchase-order base actions with federal_action_obligation in ($10,000, $20,000], the share that lie in ($10,000, $15,000]",
    "forecast": round(float(last2), 4), "interval80": [round(float(last2 - half), 4), round(float(last2 + half), 4)],
    "baseline": round(float(shares["FY2026Q4"]), 4), "baseline_label": "persistence: FY2026 Q4 value",
    "baseline_pre_change": round(float(pre_share), 4), "baseline_pre_change_label": "FY2025 full-year value",
    "recent_quarters": {k: round(float(shares[k]), 4) for k in ["FY2025Q1", "FY2025Q2", "FY2025Q3", "FY2025Q4", "FY2026Q1", "FY2026Q2", "FY2026Q3", "FY2026Q4"]},
})

doc = {
    "id": fid,
    "made_at_utc": made_at,
    "project": "Federal Procurement Integrity Observatory - cycle-3 thin slice: threshold shifts (a market-level screening indicator, not an accusation about anyone)",
    "target": ("Whether the pile-up of civilian purchase orders and definitive contracts just under the simplified acquisition threshold keeps following the threshold "
               "($250,000 until 2025-09-30, $350,000 from 2025-10-01) and how far the micro-purchase reporting step has moved ($10,000 -> $15,000), "
               "in the first quarter of fiscal year 2027."),
    "period": {"start": "2026-10-01", "end": "2026-12-31"},
    "resolution_source": "USAspending.gov contract transactions (Advanced Search transaction download), civilian top-tier agencies (all except the Department of Defense), award types B and D, base actions (modification_number 0)",
    "resolution_source_url": ["https://api.usaspending.gov/api/v2/download/transactions/", "https://files.usaspending.gov/award_data_archive/"],
    "earliest_scoring_date": "2027-02-15",
    "resolution_date": "2027-02-28",
    "resolution_rule": ("Run scripts/12_resolve_threshold.py on a snapshot taken on or after 2027-02-15. It downloads the period, keeps base actions, and recomputes the three quantities with the estimator "
                        "frozen in scripts/bunching_lib.py (bins, window, exclusion zone, polynomial degree, Poisson fit). A forecast 'hits' if the actual value lies inside the 80% interval; "
                        "it 'beats the baseline' if its absolute error is smaller than that of the persistence baseline."),
    "quantities": quantities,
    "method": ("Point forecast = mean of the estimates for FY2026 Q3 and Q4, the two latest quarters after the 2025-10-01 threshold change (quarterly estimates in results/threshold_quarterly_bunching.csv). "
               "The pile-up at $350K rose every quarter since the change (1.51, 1.65, 1.89, 2.17) and the one at $250K fell (1.50, 1.24, 1.28, 1.18); the micro-purchase share fell (0.49, 0.45, 0.36, 0.36). "
               "Interval half-width = 1.28 * sqrt(sampling_se^2 + level_se^2) where sampling_se is the bootstrap error of the two most recent first-quarter samples (small, and depressed by the Oct-Nov 2025 shutdown in FY2026 Q1), "
               "and level_se allows for drift (0.30 and 0.15 ratio points; 0.03 for the share)."),
    "known_limitations": [
        "Only four quarters exist since the change and the trend may not have stopped; the interval allows for drift but not for a new policy shift.",
        "FY2027 Q1 data may be revised by late reporting; the earliest scoring date leaves six weeks after quarter end.",
        "Bunching is a market-level indicator; legitimate scoping to fit simpler rules produces the same signature. No office or person is implicated.",
        "The Department of Defense is excluded (its data are published with a 90-day delay).",
        "Poisson-polynomial counterfactuals are sensitive to specification; the specification is frozen in scripts/bunching_lib.py."],
}
text = json.dumps(doc, indent=1, ensure_ascii=True) + "\n"
open(path, "w", encoding="utf-8", newline="\n").write(text)
h = hashlib.sha256(open(path, "rb").read()).hexdigest()
with open(os.path.join(FORECASTS, "SHA256SUMS.txt"), "a", encoding="utf-8") as fh:
    fh.write(f"{h}  {os.path.basename(path)}  made_at={made_at}\n")
print("wrote", path)
print("SHA-256:", h)
for qq in quantities:
    print(qq["name"], "forecast", qq["forecast"], "interval80", qq["interval80"], "baseline", qq["baseline"], "pre-change", qq["baseline_pre_change"])
