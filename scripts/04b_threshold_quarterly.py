"""
04b_threshold_quarterly.py - the bunching ratio quarter by quarter (US fiscal-year quarters), for the threshold forecast.

Same estimator and settings as 04_threshold.py (bunching_lib.py): ratio = observed / counterfactual awards in the four $5,000 bins under the line.
Output: results/threshold_quarterly_bunching.csv
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bunching_lib import SPEC, boot, bunch, count_bins, load_universe  # noqa: E402
from common import RESULTS  # noqa: E402

tx, base = load_universe()
base["fyq"] = "FY" + base.fy.astype(str) + "Q" + (((base.action_date.dt.month - 10) % 12) // 3 + 1).astype(str)
rows = []
for q, d in base.groupby("fyq"):
    v = d.base_and_all_options_value.dropna().values
    for T in (250_000, 350_000, 200_000, 450_000):
        spec = SPEC[T]
        e, c = count_bins(v, spec[0], spec[1], spec[2])
        r = bunch(c, e, T, spec[3], spec[4])
        bs = boot(v, T, spec, B=300)
        rows.append(dict(fyq=q, threshold=T, n_base=len(d), n_window=int(c.sum()), obs_below=r["obs_below"], cf_below=round(r["cf_below"], 1), ratio=r["ratio"],
                         se_boot=np.nanstd(bs), lo95=np.nanpercentile(bs, 2.5), hi95=np.nanpercentile(bs, 97.5)))
out = pd.DataFrame(rows)
out.to_csv(os.path.join(RESULTS, "threshold_quarterly_bunching.csv"), index=False)
print(out.pivot(index="fyq", columns="threshold", values="ratio").round(3).to_string())
print(out[out.threshold.isin([250_000, 350_000])].round(3).to_string(index=False))
