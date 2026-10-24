"""
bunching_lib.py - the small statistical toolkit shared by 04_threshold.py (analysis) and 12_resolve_threshold.py (scoring).

  load_universe()   read the civilian B + D transaction zips and return (all transactions, base actions)
  count_bins()      histogram with right-closed bins (a, b]
  bunch()           Poisson-polynomial counterfactual and the bunching ratio observed / predicted just below a threshold
  boot()            bootstrap distribution of that ratio
  SPEC              window, bin width and exclusion zone used for each threshold (fixed before looking at post-period results)
"""
import glob
import os
import sys
import zipfile

import numpy as np
import pandas as pd
import statsmodels.api as sm

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA  # noqa: E402

rng = np.random.default_rng(20261023)


def load_universe():
    frames = []
    for p in sorted(glob.glob(os.path.join(DATA, "usaspending_universe", "*.zip"))):
        z = zipfile.ZipFile(p)
        for n in z.namelist():
            if "Transactions" in n and n.endswith(".csv"):
                frames.append(pd.read_csv(z.open(n), dtype=str, keep_default_na=False))
    tx = pd.concat(frames, ignore_index=True).drop_duplicates("contract_transaction_unique_key")
    tx["action_date"] = pd.to_datetime(tx["action_date"])
    for c in ("base_and_all_options_value", "federal_action_obligation"):
        tx[c] = pd.to_numeric(tx[c], errors="coerce")
    base = tx[tx.modification_number.isin(["0", "00", "000", ""])].sort_values("action_date").drop_duplicates("contract_award_unique_key")
    base["fy"] = base.action_date.dt.year + (base.action_date.dt.month >= 10).astype(int)
    base["kind"] = np.where(base.award_type_code == "B", "PO", "DEF")
    return tx, base


# --------------------------------------------------------------------------------------------------- bunching estimator
def count_bins(vals, lo_edge, hi_edge, w):
    edges = np.arange(lo_edge, hi_edge + w / 2, w)
    idx = np.searchsorted(edges, vals, side="left") - 1          # bin i covers (edges[i], edges[i+1]]
    ok = (idx >= 0) & (idx < len(edges) - 1)
    return edges, np.bincount(idx[ok], minlength=len(edges) - 1)


def bunch(counts, edges, T, below_excl, above_excl, deg=5):
    upper = edges[1:]
    centers = (edges[:-1] + edges[1:]) / 2
    below = (upper > T - below_excl) & (upper <= T)
    above = (upper > T) & (upper <= T + above_excl)
    excl = below | above
    z = (centers - T) / (edges[-1] - edges[0])
    X = np.vander(z, deg + 1, increasing=True)
    try:
        fit = sm.GLM(counts[~excl], X[~excl], family=sm.families.Poisson()).fit(maxiter=100)
        pred = np.exp(X @ fit.params)
    except Exception:
        return dict(ratio=np.nan, obs_below=counts[below].sum(), cf_below=np.nan, obs_above=counts[above].sum(), cf_above=np.nan)
    return dict(ratio=counts[below].sum() / pred[below].sum(), obs_below=int(counts[below].sum()), cf_below=float(pred[below].sum()),
                obs_above=int(counts[above].sum()), cf_above=float(pred[above].sum()))


def boot(vals, T, spec, B=300):
    """bootstrap the bunching ratio by resampling awards"""
    lo_edge, hi_edge, w, bx, ax = spec
    out = []
    n = len(vals)
    for _ in range(B):
        v = vals[rng.integers(0, n, n)]
        e, c = count_bins(v, lo_edge, hi_edge, w)
        out.append(bunch(c, e, T, bx, ax)["ratio"])
    out = np.array(out, dtype=float)
    return out


SPEC = {  # threshold: (window_low_edge, window_high_edge, bin width, exclude below, exclude above)
    250_000: (150_000, 330_000, 5_000, 20_000, 10_000),
    350_000: (290_000, 450_000, 5_000, 20_000, 10_000),
    200_000: (110_000, 225_000, 5_000, 20_000, 10_000),
    450_000: (370_000, 530_000, 5_000, 20_000, 10_000),
    10_000: (6_000, 14_000, 250, 1_000, 500),
    15_000: (11_000, 20_000, 250, 1_000, 500),
}
