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

Two kinds of flag, two units.  Share flags move in percentage POINTS; volume flags move in PERCENT.  They are never ranked together:
the flags table lists share flags first (largest move first), then volume flags (largest first), and the column `unit` says which unit
change_points is in.  The summary lists the largest of each kind separately.

Input : one or more tables with the columns agency (or awarding_agency_name), award_kind, action_date, competed, offers, offers_ok,
        eval_set.  Give files and/or folders (a folder means its base_actions_FY*.parquet files, or else all its .parquet files).
        Files are read one at a time, so the long history fits in little memory.
        Default: data/processed/base_awards_civilian.parquet.  `--history`: data/history/processed (FY2008 onward, with Defense).
Output: tables that name the agency `agency` (the FY2024-FY2026 snapshot):
          results/quality_flags.csv, results/quality_summary.json
        the history tables (`awarding_agency_name`, 18_history_build.py):
          results/quality_history_flags.csv, results/quality_history_summary.json
        `--prefix NAME` overrides the name (results/NAME_flags.csv, results/NAME_summary.json).
        flags columns: agency, award_kind, quarter, metric, before, after, change_points, z, n_before, n_after, unit
        Shares are in percent; for volume, before/after are award counts.
        summary: counts by metric, top_share_flags (points), top_volume_flags (percent), thresholds, inputs.  top_15 is kept for
        older readers and now holds the 15 largest SHARE flags only (same unit), so it is the same as top_share_flags.
Run   : python -I scripts/16_quality_monitor.py [--history] [--prefix NAME] [path ...]
"""
import glob
import json
import os
import sys
import time

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA, RESULTS  # noqa: E402

MIN_N = 200          # minimum awards on each side
Z_BAR = 6.0          # |z| must reach this
MIN_POINTS = 10.0    # share must move this many percentage points
VOLUME_PCT = 40.0    # volume must move this many percent vs the same quarter a year earlier
MIN_BASE_QUARTERS = 2  # at least this many of the previous four quarters must exist
TOP = 15             # flags listed in the summary, per kind
THRESHOLDS = {"min_n": MIN_N, "z_bar": Z_BAR, "min_points": MIN_POINTS, "volume_pct": VOLUME_PCT,
              "baseline": "previous four quarters pooled (at least %d present)" % MIN_BASE_QUARTERS,
              "volume_baseline": "same quarter one year earlier"}
UNITS = {"share": "percentage points (change_points = after minus before, shares in percent)",
         "volume": "percent (change_points = percent change in the number of awards)"}
# metric -> (numerator column, denominator column) in the counts table
SHARES = {"offers_1": ("o1", "n_ev"), "offers_2": ("o2", "n_ev"), "offers_3": ("o3", "n_ev"),
          "offers_unusable": ("bad", "n_comp"), "competed": ("n_comp", "n_all")}
COLUMNS = ["agency", "award_kind", "quarter", "metric", "before", "after", "change_points", "z", "n_before", "n_after", "unit"]
AGENCY_COLUMNS = ("agency", "awarding_agency_name")       # the snapshot tables use the first, the history tables the second
OTHER_COLUMNS = ["award_kind", "action_date", "competed", "offers", "offers_ok", "eval_set"]
NUM_COLS = ["n_all", "n_comp", "n_ev", "o1", "o2", "o3", "bad"]


def quarter_index(dates):
    """Fiscal quarter number counted continuously: fy*4 + (0..3).  Oct-Dec is quarter 0 (Q1) of the next calendar year's fiscal year."""
    m = dates.dt.month
    fy = dates.dt.year + (m >= 10).astype(int)
    q = ((m - 10) % 12) // 3
    return (fy * 4 + q).astype(int)


def quarter_label(i):
    return "FY%dQ%d" % (i // 4, i % 4 + 1)


def agency_column(columns):
    """the name of the agency column in a table with these columns ('agency' or 'awarding_agency_name')"""
    for c in AGENCY_COLUMNS:
        if c in columns:
            return c
    raise KeyError("no agency column: expected one of %s" % ", ".join(AGENCY_COLUMNS))


def count_table(df):
    """One row per agency x award kind x quarter with the counts every check needs."""
    comp = df["competed"].astype(bool)
    ev = df["eval_set"].astype(bool)
    off = pd.to_numeric(df["offers"], errors="coerce")
    d = pd.DataFrame({
        "agency": df[agency_column(df.columns)].astype(str), "award_kind": df["award_kind"].astype(str),
        "qi": quarter_index(pd.to_datetime(df["action_date"])),
        "n_all": np.ones(len(df), np.int32), "n_comp": comp.astype(np.int32), "n_ev": ev.astype(np.int32),
        "o1": (ev & (off == 1)).astype(np.int32), "o2": (ev & (off == 2)).astype(np.int32), "o3": (ev & (off == 3)).astype(np.int32),
        "bad": (comp & ~df["offers_ok"].astype(bool)).astype(np.int32),
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


def order_flags(fl):
    """share flags first (largest move in points first), then volume flags (largest move in percent first); adds `unit`"""
    if not len(fl):
        return fl.reindex(columns=COLUMNS)
    fl = fl.copy()
    fl["unit"] = np.where(fl["metric"] == "volume", "percent", "points")
    key = fl["change_points"].abs()
    fl["_grp"] = (fl["metric"] == "volume").astype(int)
    fl["_key"] = -key
    fl = fl.sort_values(["_grp", "_key"], kind="stable").drop(columns=["_grp", "_key"]).reset_index(drop=True)
    return fl[COLUMNS]


def flags_from_counts(ct, data_end, min_n=MIN_N, z_bar=Z_BAR, min_points=MIN_POINTS, volume_pct=VOLUME_PCT):
    """The flags table from a counts table (count_table, possibly several summed) and the last action date in the data."""
    data_end = pd.Timestamp(data_end)
    last_q = int(quarter_index(pd.Series([data_end])).iloc[0])
    # a quarter is complete if the data reach its last month (Dec, Mar, Jun, Sep) and its last days
    qend_month = [12, 3, 6, 9][last_q % 4]
    last_q_complete = data_end.month == qend_month and data_end.day >= 28
    out = []
    for (ag, kind), g in ct.groupby(["agency", "award_kind"]):
        g = g.set_index("qi")[NUM_COLS].astype(float)
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
    return order_flags(pd.DataFrame(out, columns=COLUMNS[:-1]))


def find_flags(df, min_n=MIN_N, z_bar=Z_BAR, min_points=MIN_POINTS, volume_pct=VOLUME_PCT, data_end=None):
    """Return the flags table for the awards table df (see header for the rules)."""
    if data_end is None:
        data_end = pd.to_datetime(df["action_date"]).max()
    return flags_from_counts(count_table(df), data_end, min_n, z_bar, min_points, volume_pct)


def input_files(paths):
    """Expand files and folders into a sorted list of parquet files (a folder: its base_actions_FY*.parquet, else all *.parquet)."""
    files = []
    for p in paths:
        if os.path.isdir(p):
            found = sorted(glob.glob(os.path.join(p, "base_actions_FY*.parquet"))) or sorted(glob.glob(os.path.join(p, "*.parquet")))
            files += found
        else:
            files.append(p)
    return list(dict.fromkeys(files))


def read_counts(path):
    """(counts table, last action date, number of rows, agency column name) for one parquet file, read with only the needed columns"""
    ag = agency_column(pq.ParquetFile(path).schema_arrow.names)
    df = pd.read_parquet(path, columns=[ag] + OTHER_COLUMNS)
    return count_table(df), pd.to_datetime(df["action_date"]).max(), len(df), ag


def flags_for_files(files):
    """(flags, number of rows, last action date, agency column names) over several files, one file in memory at a time"""
    cts, ends, n, ags = [], [], 0, set()
    for p in files:
        ct, end, k, ag = read_counts(p)
        cts.append(ct)
        ends.append(end)
        n += k
        ags.add(ag)
    ct = pd.concat(cts, ignore_index=True).groupby(["agency", "award_kind", "qi"]).sum().reset_index()
    data_end = max(e for e in ends if pd.notna(e))
    return flags_from_counts(ct, data_end), n, data_end, ags


def records(fl):
    return fl.astype(object).where(fl.notna(), None).to_dict("records")


def summarize(fl, top=TOP, inputs=None, data_end=None):
    """Counts by metric and the largest flags, listed SEPARATELY for shares (points) and volume (percent)."""
    share = fl[fl["metric"] != "volume"]
    vol = fl[fl["metric"] == "volume"]
    share = share.reindex(share["change_points"].abs().sort_values(ascending=False, kind="stable").index)
    vol = vol.reindex(vol["change_points"].abs().sort_values(ascending=False, kind="stable").index)
    top_share = records(share.head(top))
    out = {"n_flags": int(len(fl)), "n_share_flags": int(len(share)), "n_volume_flags": int(len(vol)),
           "by_metric": {k: int(v) for k, v in fl["metric"].value_counts().items()},
           "units": UNITS,
           "top_share_flags": top_share, "top_volume_flags": records(vol.head(top)),
           "top_15": top_share[:15],
           "top_15_note": "kept for older readers: the largest share flags only (percentage points); volume flags are in top_volume_flags",
           "thresholds": THRESHOLDS}
    if inputs is not None:
        out["inputs"] = [os.path.basename(p) for p in inputs]
    if data_end is not None:
        out["data_end"] = str(pd.Timestamp(data_end).date())
    return out


def output_prefix(agency_columns, prefix=None):
    """'quality' for tables with an `agency` column (the snapshot), 'quality_history' for the history tables"""
    if prefix:
        return prefix
    return "quality_history" if "awarding_agency_name" in agency_columns else "quality"


def parse_args(argv):
    paths, prefix, i = [], None, 1
    while i < len(argv):
        a = argv[i]
        if a == "--history":
            paths.append(os.path.join(DATA, "history", "processed"))
        elif a == "--prefix" and i + 1 < len(argv):
            i += 1
            prefix = argv[i]
        else:
            paths.append(a)
        i += 1
    return paths or [os.path.join(DATA, "processed", "base_awards_civilian.parquet")], prefix


def main(argv, results_dir=RESULTS, verbose=True):
    t0 = time.time()
    paths, prefix = parse_args(argv)
    files = input_files(paths)
    if not files:
        sys.exit("no input tables found in: " + ", ".join(paths))
    fl, n, data_end, ags = flags_for_files(files)
    name = output_prefix(ags, prefix)
    os.makedirs(results_dir, exist_ok=True)
    fl.round({"before": 2, "after": 2, "change_points": 2, "z": 1}).to_csv(os.path.join(results_dir, f"{name}_flags.csv"), index=False)
    summ = summarize(fl, inputs=files, data_end=data_end)
    with open(os.path.join(results_dir, f"{name}_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summ, f, indent=2, default=float)
    if not verbose:
        return fl, summ, name
    print("Quality monitor: %d file(s), %d awards read, data through %s, %d flags (rules: |z| >= %g and >= %g points; volume >= %g%% vs a "
          "year earlier; min %d awards)" % (len(files), n, summ["data_end"], len(fl), Z_BAR, MIN_POINTS, VOLUME_PCT, MIN_N))
    for k, v in summ["by_metric"].items():
        print("  %-16s %d" % (k, v))
    for title, lst, unit in (("Largest share flags (percentage points):", summ["top_share_flags"], "pts"),
                             ("Largest volume flags (percent):", summ["top_volume_flags"], "%")):
        print(title)
        for r in lst[:10]:
            u = "awards" if r["metric"] == "volume" else "%"
            print("  %-40s %-3s %s %-15s %.1f -> %.1f %s (%+.1f %s)" % (str(r["agency"])[:40], r["award_kind"], r["quarter"], r["metric"],
                                                                      r["before"], r["after"], u, r["change_points"], unit))
    print("Done in %.1f s.  Wrote results/%s_flags.csv and results/%s_summary.json" % (time.time() - t0, name, name))
    return fl, summ, name


if __name__ == "__main__":
    main(sys.argv)
