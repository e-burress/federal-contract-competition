"""
16_quality_monitor.py - finds quarters where an agency changed HOW it records contract data, so readers know which numbers to treat with care.

Why: when an agency starts coding "2 or 3 offers" where it used to code "1 offer", its single-offer share falls even though nothing
about real competition changed.  Likewise, a sudden drop in award counts may mean late or missing reporting.  This script looks for
such breaks automatically and lists them.  A flag does NOT say which is true (coding change, late reporting, or a real change);
it says "check before you trust numbers from this agency and quarter".

What is checked, for every agency x award kind (PO / DEF / IDV) x federal fiscal quarter (Q1 = Oct-Dec, from action_date):
  offers_1, offers_2, offers_3   share of eval-set awards (competed, usable offer count) with exactly 1, 2, 3 offers
  offers_unusable                share of competed awards whose offer count is blank or 999 (not usable)
  competed                       share of all awards marked competed
  volume                         number of awards, against the SAME quarter one year earlier (avoids seasonality)

Rules (kept simple on purpose):
  1. Share metrics compare a quarter ("after") with the previous four quarters pooled ("before"; at least 2 of those 4 must exist).
  2. Both sides need at least MIN_N = 200 awards in the metric's base (eval set, competed awards, or all awards).
  3. Two-proportion z-test (pooled standard error).  Flag only if |z| >= Z_BAR = 6 (a high bar, since there are thousands of
     comparisons) AND the share moved by at least MIN_POINTS = 10 percentage points.  Both must hold.
  4. Volume: flag if the count moved 40% or more versus the same quarter a year earlier, both quarters having at least 200 awards.
     No z-test (counts are not shares); change_points is then the percent change and z is blank.
  5. The newest quarter is skipped for volume if the data stop before its last day (it would look like a false drop).

Input : data/processed/base_awards_civilian.parquet (or any table with the same columns, given as the first argument)
        columns used: agency, award_kind, action_date, competed, offers, offers_ok, eval_set
Output: results/quality_flags.csv (agency, award_kind, quarter, metric, before, after, change_points, z, n_before, n_after; biggest first)
        results/quality_summary.json (counts by metric, the 15 largest flags, thresholds)
        Shares are in percent; for volume, before/after are award counts.
Run   : python -I scripts/16_quality_monitor.py [path/to/table.parquet]
"""
import json
import os
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA, RESULTS  # noqa: E402

MIN_N = 200          # minimum awards on each side
Z_BAR = 6.0          # |z| must reach this
MIN_POINTS = 10.0    # share must move this many percentage points
VOLUME_PCT = 40.0    # volume must move this many percent vs the same quarter a year earlier
MIN_BASE_QUARTERS = 2  # at least this many of the previous four quarters must exist
THRESHOLDS = {"min_n": MIN_N, "z_bar": Z_BAR, "min_points": MIN_POINTS, "volume_pct": VOLUME_PCT,
              "baseline": "previous four quarters pooled (at least %d present)" % MIN_BASE_QUARTERS,
              "volume_baseline": "same quarter one year earlier"}
# metric -> (numerator column, denominator column) in the counts table
SHARES = {"offers_1": ("o1", "n_ev"), "offers_2": ("o2", "n_ev"), "offers_3": ("o3", "n_ev"),
          "offers_unusable": ("bad", "n_comp"), "competed": ("n_comp", "n_all")}
COLUMNS = ["agency", "award_kind", "quarter", "metric", "before", "after", "change_points", "z", "n_before", "n_after"]


def quarter_index(dates):
    """Fiscal quarter number counted continuously: fy*4 + (0..3).  Oct-Dec is quarter 0 (Q1) of the next calendar year's fiscal year."""
    m = dates.dt.month
    fy = dates.dt.year + (m >= 10).astype(int)
    q = ((m - 10) % 12) // 3
    return (fy * 4 + q).astype(int)


def quarter_label(i):
    return "FY%dQ%d" % (i // 4, i % 4 + 1)


def count_table(df):
    """One row per agency x award kind x quarter with the counts every check needs."""
    comp = df["competed"].astype(bool)
    ev = df["eval_set"].astype(bool)
    off = pd.to_numeric(df["offers"], errors="coerce")
    d = pd.DataFrame({
        "agency": df["agency"].astype(str), "award_kind": df["award_kind"].astype(str),
        "qi": quarter_index(pd.to_datetime(df["action_date"])),
        "n_all": 1, "n_comp": comp.astype(int), "n_ev": ev.astype(int),
        "o1": (ev & (off == 1)).astype(int), "o2": (ev & (off == 2)).astype(int), "o3": (ev & (off == 3)).astype(int),
        "bad": (comp & ~df["offers_ok"].astype(bool)).astype(int),
    })
    return d.groupby(["agency", "award_kind", "qi"]).sum().reset_index()


def two_prop_z(x1, n1, x2, n2):
    """z for 'share 2 differs from share 1' with pooled standard error (arrays)."""
    p1, p2 = x1 / n1, x2 / n2
    p = (x1 + x2) / (n1 + n2)
    se = np.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    with np.errstate(divide="ignore", invalid="ignore"):
        z = np.where(se > 0, (p2 - p1) / se, 0.0)
    return z


def find_flags(df, min_n=MIN_N, z_bar=Z_BAR, min_points=MIN_POINTS, volume_pct=VOLUME_PCT, data_end=None):
    """Return the flags table for the awards table df (see header for the rules)."""
    ct = count_table(df)
    if data_end is None:
        data_end = pd.to_datetime(df["action_date"]).max()
    last_q = int(quarter_index(pd.Series([data_end])).iloc[0])
    # a quarter is complete if the data reach its last month (Dec, Mar, Jun, Sep) and its last days
    qend_month = [12, 3, 6, 9][last_q % 4]
    last_q_complete = data_end.month == qend_month and data_end.day >= 28
    num_cols = ["n_all", "n_comp", "n_ev", "o1", "o2", "o3", "bad"]
    out = []
    for (ag, kind), g in ct.groupby(["agency", "award_kind"]):
        g = g.set_index("qi")[num_cols]
        g = g.reindex(range(int(g.index.min()), int(g.index.max()) + 1))
        present = g["n_all"].notna()
        g = g.fillna(0)
        base = g.rolling(4, min_periods=1).sum().shift(1)
        nbase = present.astype(int).rolling(4, min_periods=1).sum().shift(1)
        ok = present & (nbase >= MIN_BASE_QUARTERS)
        for metric, (nc, dc) in SHARES.items():
            n1, n2 = base[dc], g[dc]
            sel = ok & (n1 >= min_n) & (n2 >= min_n)
            if not sel.any():
                continue
            x1, x2 = base.loc[sel, nc].to_numpy(float), g.loc[sel, nc].to_numpy(float)
            a, b = n1[sel].to_numpy(float), n2[sel].to_numpy(float)
            z = two_prop_z(x1, a, x2, b)
            pts = (x2 / b - x1 / a) * 100
            hit = (np.abs(z) >= z_bar) & (np.abs(pts) >= min_points)
            qs = sel.index[sel]
            for j in np.flatnonzero(hit):
                out.append((ag, kind, quarter_label(int(qs[j])), metric, x1[j] / a[j] * 100, x2[j] / b[j] * 100,
                            pts[j], z[j], int(a[j]), int(b[j])))
        prev = g["n_all"].shift(4)
        pprev = present.shift(4, fill_value=False).astype(bool)
        for qi in g.index[present & pprev]:
            if qi == last_q and not last_q_complete:
                continue
            n1, n2 = prev[qi], g.loc[qi, "n_all"]
            if n1 < min_n or n2 < min_n:
                continue
            pct = (n2 / n1 - 1) * 100
            if abs(pct) >= volume_pct:
                out.append((ag, kind, quarter_label(int(qi)), "volume", n1, n2, pct, np.nan, int(n1), int(n2)))
    fl = pd.DataFrame(out, columns=COLUMNS)
    if len(fl):
        fl = fl.reindex(fl["change_points"].abs().sort_values(ascending=False, kind="stable").index).reset_index(drop=True)
    return fl


def summarize(fl):
    top = fl.head(15).astype(object).where(fl.head(15).notna(), None).to_dict("records")
    return {"n_flags": int(len(fl)), "by_metric": {k: int(v) for k, v in fl["metric"].value_counts().items()},
            "top_15": top, "thresholds": THRESHOLDS}


def main(argv):
    t0 = time.time()
    path = argv[1] if len(argv) > 1 else os.path.join(DATA, "processed", "base_awards_civilian.parquet")
    cols = ["agency", "award_kind", "action_date", "competed", "offers", "offers_ok", "eval_set"]
    df = pd.read_parquet(path, columns=cols)
    fl = find_flags(df)
    os.makedirs(RESULTS, exist_ok=True)
    fl.round({"before": 2, "after": 2, "change_points": 2, "z": 1}).to_csv(os.path.join(RESULTS, "quality_flags.csv"), index=False)
    summ = summarize(fl)
    with open(os.path.join(RESULTS, "quality_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summ, f, indent=2, default=float)
    print("Quality monitor: %d awards read, %d flags (rules: |z| >= %g and >= %g points; volume >= %g%% vs a year earlier; min %d awards)"
          % (len(df), len(fl), Z_BAR, MIN_POINTS, VOLUME_PCT, MIN_N))
    for k, v in summ["by_metric"].items():
        print("  %-16s %d" % (k, v))
    print("Largest flags:")
    for r in fl.head(10).itertuples():
        unit = "awards" if r.metric == "volume" else "%"
        print("  %-40s %-3s %s %-15s %.1f -> %.1f %s (%+.1f %s)" % (r.agency[:40], r.award_kind, r.quarter, r.metric, r.before, r.after,
                                                                  unit, r.change_points, "%" if r.metric == "volume" else "pts"))
    print("Done in %.1f s.  Wrote results/quality_flags.csv and results/quality_summary.json" % (time.time() - t0))


if __name__ == "__main__":
    main(sys.argv)
