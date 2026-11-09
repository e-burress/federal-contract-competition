"""
21_threshold_staggered.py - the 2018 natural experiment: agencies raised the simplified acquisition threshold from $150,000 to $250,000 on
DIFFERENT dates (reference/threshold_adoption_2018.csv: class deviations from February 2018 to 2020).  If the rule itself drives the
pile-up, each agency's pile-up should move from $150K to $250K when THAT agency switched, not when others did.

Method
  * Awards: base actions of civilian purchase orders and definitive contracts (and DoD separately), FY2016-FY2021, of agencies with a
    documented adoption date (confidence high or medium; agencies without a date are left out, never guessed).
  * Event time: quarters (91.31 days) between the award date and the agency's own SAT adoption date; quarter 0 starts on adoption day.
  * For each event quarter from -8 to +8, all agencies' awards in that quarter are pooled and the bunching ratio is computed under $150K
    and under $250K with the estimator of 20_threshold_history.py (four $5,000 bins under the line versus a smooth curve fitted outside
    the bins near every threshold), with 200 bootstrap resamples.
  * Placebo: the same calculation with every adoption date moved one year EARLIER.  A real effect shows a break at quarter 0 with the
    true dates and at quarter +4 (not 0) with the placebo dates.
This is a pattern test: it cannot tell budgets written to fit the rules from contracts split to stay under them.

Input : data/history/processed/base_actions_FY2016..FY2021.parquet, reference/threshold_adoption_2018.csv
Output: results/threshold_staggered.csv, figures/threshold_staggered.png
"""
import importlib.util
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA, PROJECT, RESULTS  # noqa: E402

_spec = importlib.util.spec_from_file_location("th20", os.path.join(os.path.dirname(os.path.abspath(__file__)), "20_threshold_history.py"))
th = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(th)
YEARS = range(2016, 2022)
EVENT = range(-8, 9)
B = 200
rng = np.random.default_rng(20261111)


def load():
    dates = pd.read_csv(os.path.join(PROJECT, "reference", "threshold_adoption_2018.csv"))
    dates = dates[dates.confidence.isin(["high", "medium"]) & dates.sat_effective_date.notna()]
    dates = dates[dates.agency != "FAR (government-wide)"]
    adopt = dict(zip(dates.agency, pd.to_datetime(dates.sat_effective_date)))
    frames = []
    for fy in YEARS:
        p = os.path.join(DATA, "history", "processed", f"base_actions_FY{fy}.parquet")
        if not os.path.exists(p):
            sys.exit(f"missing {p}: run `python run.py get-history` (or the history workflow) first")
        b = pd.read_parquet(p, columns=["awarding_agency_name", "award_type_code", "action_date", "base_and_all_options_value", "is_dod"])
        frames.append(b[b.award_type_code.isin(["B", "D"])])
    b = pd.concat(frames, ignore_index=True)
    b["agency"] = b.awarding_agency_name.astype(str)
    b = b[b.agency.isin(adopt)].copy()
    b["adopt"] = b.agency.map(adopt)
    return b, adopt


def event_table(b, shift_years=0):
    days = (b.action_date - (b.adopt - pd.DateOffset(years=shift_years))).dt.days
    q = np.floor(days / 91.31).astype(int)
    rows = []
    for sector, mask in (("civilian", ~b.is_dod), ("defense", b.is_dod)):
        for k in EVENT:
            v = b.loc[mask & (q == k), "base_and_all_options_value"].dropna().to_numpy()
            for T in (150_000, 250_000):
                r, obs, cf = th.ratio(v, T) if len(v) else (np.nan, 0, np.nan)
                boots = [th.ratio(v[rng.integers(0, len(v), len(v))], T)[0] for _ in range(B)] if np.isfinite(r) else [np.nan]
                rows.append(dict(sector=sector, event_quarter=k, line=T, ratio=r, ci_lo=np.nanpercentile(boots, 2.5),
                                 ci_hi=np.nanpercentile(boots, 97.5), obs_below=obs, cf_below=cf, n_awards=int(len(v)),
                                 dates="placebo, one year earlier" if shift_years else "actual"))
    return rows


def main():
    b, adopt = load()
    print(f"{len(adopt)} agencies with a documented date; {len(b):,} base actions FY2016-FY2021", flush=True)
    out = pd.DataFrame(event_table(b) + event_table(b, shift_years=1))
    out.to_csv(os.path.join(RESULTS, "threshold_staggered.csv"), index=False)
    for dates in ("actual", "placebo, one year earlier"):
        d = out[(out.dates == dates) & (out.sector == "civilian")]
        print(f"\ncivilian, {dates} dates:")
        print(d.pivot(index="event_quarter", columns="line", values="ratio").round(2).to_string())
    plot(out)


def plot(out):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    colors = {150_000: "#eb6834", 250_000: "#2a78d6"}
    fig, axes = plt.subplots(1, 2, figsize=(11, 4), sharey=True)
    for ax, dates in zip(axes, ("actual", "placebo, one year earlier")):
        d = out[(out.dates == dates) & (out.sector == "civilian")]
        for T, color in colors.items():
            e = d[d.line == T].sort_values("event_quarter")
            ax.plot(e.event_quarter, e.ratio, color=color, lw=2, marker="o", ms=3, label=f"under ${T // 1000}K")
            ax.fill_between(e.event_quarter, e.ci_lo, e.ci_hi, color=color, alpha=0.12, lw=0)
        ax.axvline(-0.5, color="#555555", lw=1)
        ax.axhline(1, color="#999999", lw=1)
        ax.set_title("Each agency's own adoption date" if dates == "actual" else "Placebo: dates moved one year earlier", fontsize=10)
        ax.set_xlabel("quarters from the agency's switch to $250K")
        ax.spines[["top", "right"]].set_visible(False)
    axes[0].set_ylabel("bunching ratio (1.0 = no pile-up)")
    axes[0].legend(frameon=False, fontsize=8)
    fig.suptitle("Civilian agencies, 2018 change: pile-ups under $150K and $250K around each agency's own switch date", fontsize=10)
    fig.tight_layout()
    fig.savefig(os.path.join(PROJECT, "figures", "threshold_staggered.png"), dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    main()
