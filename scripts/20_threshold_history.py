"""
20_threshold_history.py - does the pile-up follow the line, every time?  Bunching just under each simplified-acquisition threshold the
government has used since 2008, fiscal year by fiscal year, for civilian agencies and the Department of Defense separately.

The simplified acquisition threshold (SAT) moved three times in this period:
  $100,000 -> $150,000  on 2010-10-01 (inflation adjustment), so FY2011 onward
  $150,000 -> $250,000  in 2018 (FY2018 National Defense Authorization Act); agencies adopted it on different dates through class
                        deviations (reference/threshold_adoption_2018.csv), so FY2018-FY2019 are transition years
  $250,000 -> $350,000  on 2025-10-01 (inflation adjustment), so FY2026 onward
For every fiscal year and each of the four lines, the bunching ratio = awards in the four $5,000 bins just under the line, divided by the
awards a smooth curve predicts there.  The curve is the same as in 04_threshold.py (a degree-5 Poisson polynomial over the line's window)
but fitted outside the bins near ANY of the four lines, so one line's pile-up cannot distort another line's baseline.  The windows for
$250K and $350K are the ones fixed in bunching_lib.SPEC.  Uncertainty: 200 bootstrap resamples of the awards.
If the rule shapes behavior, each line's ratio should rise when it becomes the threshold and fade after it stops being one.  This is a
pattern in the data, not proof of why it happens (budgets written to fit the rules, small-business rules tied to the line, or splitting).

Universe: base actions (modification 0) of purchase orders and definitive contracts, as in 04_threshold.py.
Input : data/history/processed/base_actions_FY*.parquet (18_history_build.py; `python run.py get-history` downloads them)
Output: results/threshold_history.csv, figures/threshold_history.png
"""
import glob
import os
import re
import sys

import numpy as np
import pandas as pd
import statsmodels.api as sm

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bunching_lib import SPEC, count_bins  # noqa: E402
from common import DATA, PROJECT, RESULTS  # noqa: E402

LINES = [100_000, 150_000, 250_000, 350_000]
# window (low edge, high edge) for each line; $250K and $350K as fixed in bunching_lib.SPEC
WINDOW = {100_000: (40_000, 200_000), 150_000: (80_000, 240_000),
          250_000: SPEC[250_000][:2], 350_000: SPEC[350_000][:2]}
WIDTH, BELOW, ABOVE = 5_000, 20_000, 10_000
ACTIVE = {100_000: (2008, 2010), 150_000: (2011, 2018), 250_000: (2018, 2025), 350_000: (2026, 2100)}   # fiscal years each line applied
B = 200
rng = np.random.default_rng(20261110)


def ratio(vals, T):
    lo, hi = WINDOW[T]
    edges, counts = count_bins(vals, lo, hi, WIDTH)
    upper, centers = edges[1:], (edges[:-1] + edges[1:]) / 2
    below = (upper > T - BELOW) & (upper <= T)
    excl = np.zeros(len(upper), bool)
    for L in LINES:                                         # skip the bins near every line inside this window
        excl |= (upper > L - BELOW) & (upper <= L + ABOVE)
    z = (centers - T) / (hi - lo)
    X = np.vander(z, 6, increasing=True)
    if counts[~excl].sum() < 50 or (~excl).sum() < 8:
        return np.nan, int(counts[below].sum()), np.nan
    try:
        fit = sm.GLM(counts[~excl], X[~excl], family=sm.families.Poisson()).fit(maxiter=100)
        cf = float(np.exp(X @ fit.params)[below].sum())
    except Exception:
        return np.nan, int(counts[below].sum()), np.nan
    return counts[below].sum() / cf, int(counts[below].sum()), cf


def main():
    files = sorted(glob.glob(os.path.join(DATA, "history", "processed", "base_actions_FY*.parquet")))
    if not files:
        sys.exit("no history tables: run `python run.py get-history` (or the history workflow) first")
    rows = []
    for p in files:
        fy = int(re.search(r"FY(\d{4})", p).group(1))
        b = pd.read_parquet(p, columns=["award_type_code", "base_and_all_options_value", "is_dod"])
        b = b[b.award_type_code.isin(["B", "D"])]
        for sector, d in (("civilian", b[~b.is_dod]), ("defense", b[b.is_dod])):
            v = d.base_and_all_options_value.dropna().to_numpy()
            for T in LINES:
                r, obs, cf = ratio(v, T)
                boots = [ratio(v[rng.integers(0, len(v), len(v))], T)[0] for _ in range(B)] if np.isfinite(r) else [np.nan]
                lo, hi = WINDOW[T]
                rows.append(dict(fy=fy, sector=sector, line=T, ratio=r, ci_lo=np.nanpercentile(boots, 2.5), ci_hi=np.nanpercentile(boots, 97.5),
                                 obs_below=obs, cf_below=cf, n_window=int(((v > lo) & (v <= hi)).sum()), n_base=int(len(v)),
                                 active=ACTIVE[T][0] <= fy <= ACTIVE[T][1]))
        print(f"FY{fy}: done", flush=True)
    out = pd.DataFrame(rows)
    out.to_csv(os.path.join(RESULTS, "threshold_history.csv"), index=False)
    plot(out)
    piv = out.pivot_table(index="fy", columns=["sector", "line"], values="ratio").round(2)
    print(piv.to_string())


def plot(out):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    colors = {100_000: "#8c8c8c", 150_000: "#eb6834", 250_000: "#2a78d6", 350_000: "#1baf7a"}
    sectors = [s for s in ("civilian", "defense") if s in set(out.sector)]
    fig, axes = plt.subplots(1, len(sectors), figsize=(5.6 * len(sectors), 4.2), sharey=True, squeeze=False)
    for ax, sector in zip(axes[0], sectors):
        d = out[out.sector == sector]
        for T in LINES:
            e = d[d.line == T].sort_values("fy")
            ax.plot(e.fy, e.ratio, color=colors[T], lw=2, label=f"${T // 1000}K line")
            act = e[e.active]
            ax.scatter(act.fy, act.ratio, color=colors[T], s=18, zorder=3)
        for x in (2010.5, 2017.5, 2025.5):
            ax.axvline(x, color="#555555", lw=1)
        ax.axhline(1, color="#999999", lw=1)
        ax.set_title({"civilian": "Civilian agencies", "defense": "Department of Defense"}[sector], fontsize=10)
        ax.set_xlabel("fiscal year (dots: years the line was the limit)")
        ax.spines[["top", "right"]].set_visible(False)
    axes[0][0].set_ylabel("bunching ratio (1.0 = no pile-up)")
    axes[0][0].legend(frameon=False, fontsize=8)
    fig.suptitle("Pile-ups just under each simplified-acquisition limit, FY2008 onward (vertical lines: limit changes)", fontsize=10)
    fig.tight_layout()
    fig.savefig(os.path.join(PROJECT, "figures", "threshold_history.png"), dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    main()
