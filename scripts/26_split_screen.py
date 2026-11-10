"""
26_split_screen.py - do awards come in clusters that look like split purchases?  A screening indicator about markets, by sector and
fiscal year.

What
  The FAR forbids splitting one requirement into several purchases just to stay under a threshold.  Bunching (20_threshold_history.py)
  shows single awards piling up just under the line.  This script asks a different question: does the same buying office buy the same
  kind of product from the same company several times in a short period, each purchase at or under the threshold but together above it?
  It cannot tell a prohibited split from legitimate repeat buying (monthly supplies, task-sized orders).  It measures how much more of
  this pattern sits at the real threshold than at fake lines where no rule applies.

Method
  * Universe: base actions of purchase orders (B) and definitive contracts (D) with a company id (vendor_id), a buying office and a
    positive base-and-all-options value.  Sectors: civilian agencies except GSA, GSA, Department of Defense.
  * Cluster: same company + same office + same 4-character product code, linked when consecutive awards are at most 30 days apart.
    Clusters are followed across fiscal-year boundaries when the next year's file is present (a cluster belongs to the fiscal year of
    its LAST award; one with a missing neighbor year is cut at the edge).
  * Line L for a cluster: the threshold in force on the date of its first award for its agency (THRESHOLDS, read from reference/thresholds.csv), times a
    multiple m.  Split-like at L: two or more awards, every award at or under L, cluster total above L.
  * Measures per sector and fiscal year: awards at or under L, the share of them in split-like clusters (and of their dollars), and
    the number of split-like clusters.
  * Placebo lines (the key comparison).  Repeat buying makes "split-like" clusters at any line.  The shares at the fake lines
    m = 0.5, 0.6, 0.7, 0.8, 1.25, 1.4, 1.6, 2.0 trace the smooth pattern.  A straight line through log(share) against log(m) at the
    fake lines gives the expected share at m = 1.  Excess ratio = actual share at the real line / expected share (1.0 = no excess).
    Fake lines that land within 8% of another real dollar line (MPT, SAT, $2,000, $2,500, $25,000) are not used for the fit.
    95% interval: 200 bootstrap resamples of companies within each sector and year (all of a company's clusters move together).
  * Second variant, "donut": every award at least 10% under the line (at or under 0.9 x L).  Single awards bunched just under the line
    make split-like clusters more common at the real line even without splitting; the donut variant removes that mechanical channel.
  * Natural experiment: for each threshold increase (old line X0 -> new line X1) the "band" measure counts clusters with every award at
    or under X0 and a total above X0 but at or under X1 (fake lines: the same with m x X0 and m x X1).  Once the line rises, such a
    requirement fits in one simplified purchase, so its excess ratio should fall.  Pooled over years:  sum(actual) / sum(expected).
  All of this is a pattern in public data.  It is not evidence that any purchase was split.

Privacy: company ids and office codes are used only inside the computation.  Outputs are counts and dollar totals at sector level by
fiscal year.  Dollar totals of split-like clusters are left blank when fewer than 3 clusters stand behind them.

Input : <DATA>/history/processed/base_actions_FY*.parquet (whatever years are present), reference/thresholds.csv,
        reference/threshold_adoption_2018.csv
Output: results/split_screen.csv, results/split_screen_summary.json, figures/split_screen.png, a printed summary
Usage : python -I scripts/26_split_screen.py
"""
import glob
import json
import os
import re
import sys

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA, PROJECT, RESULTS  # noqa: E402

# ---- thresholds by date ---------------------------------------------------------------------------------------------------------
# Read at run time from reference/thresholds.csv (rows simplified_acquisition and micro_purchase: value, effective date, and whom it
# applies to) and reference/threshold_adoption_2018.csv (each civilian agency's own date for the 2018 increase).  Each row becomes
# (kind, dollars, start, scope).  start "adoption" = each agency's own date in the adoption file, or the FAR rule date where no date is
# known (GSA, for example); a civilian row gets it when its value and date are those of the adoption file's "FAR (government-wide)" row.
# A blank effective date means "in force before FY2008".  scope: "all", "civilian" (not DoD) or "dod".  Rows are sorted by start date
# and later rows override earlier ones (end dates are implied by the next row).  FALLBACK_THRESHOLDS is the same table as read on
# 2026-11-10, used only when the reference files cannot be read.
REF_KINDS = {"simplified_acquisition": "SAT", "micro_purchase": "MPT"}
REF_SCOPES = {"all": "all", "civilian": "civilian", "dod": "dod"}
BEFORE = "1900-01-01"                                          # start of a row with a blank effective date
FALLBACK_FAR_DATE = "2020-08-31"
FALLBACK_THRESHOLDS = [
    ("SAT", 100_000, BEFORE, "all"),
    ("SAT", 150_000, "2010-10-01", "civilian"),
    ("SAT", 150_000, "2010-10-01", "dod"),
    ("SAT", 250_000, "2018-04-13", "dod"),
    ("SAT", 250_000, "adoption", "civilian"),
    ("SAT", 350_000, "2025-10-01", "all"),
    ("MPT", 2_500, BEFORE, "all"),
    ("MPT", 3_000, "2006-09-28", "all"),
    ("MPT", 3_500, "2015-10-01", "civilian"),
    ("MPT", 3_500, "2015-10-01", "dod"),
    ("MPT", 5_000, "2017-07-13", "dod"),        # DoD class deviation 2017-O0006; kept by 2018-O0013 until $10,000 on 2018-08-31
    ("MPT", 10_000, "2018-08-31", "dod"),
    ("MPT", 10_000, "adoption", "civilian"),
    ("MPT", 15_000, "2025-10-01", "civilian"),
    ("MPT", 15_000, "2025-10-01", "dod"),
]
# names in the adoption file that differ from USAspending's top-tier agency names
AGENCY_ALIAS = {"Chemical Safety and Hazard Investigation Board": "United States Chemical Safety Board"}


def read_thresholds(path=None, adoption_path=None):
    """(rows, FAR date) from reference/thresholds.csv and the adoption file; see the comment above.  Raises on a missing or odd file."""
    t = pd.read_csv(path or os.path.join(PROJECT, "reference", "thresholds.csv"), dtype=str)
    a = pd.read_csv(adoption_path or os.path.join(PROJECT, "reference", "threshold_adoption_2018.csv"), dtype=str)
    far = a[a.agency == "FAR (government-wide)"].iloc[0]
    adoption = {"SAT": (float(far.sat_new), str(far.sat_effective_date)), "MPT": (float(far.mpt_new), str(far.mpt_effective_date))}
    if adoption["SAT"][1] != adoption["MPT"][1]:
        raise ValueError("the FAR row of the adoption file has two different dates")
    rows = []
    for r in t[t.threshold.isin(list(REF_KINDS))].itertuples():
        kind, dollars = REF_KINDS[r.threshold], float(r.value_usd)
        scope = REF_SCOPES[str(r.applies_to).strip().lower()]
        start = str(r.effective_date) if isinstance(r.effective_date, str) and r.effective_date.strip() else BEFORE
        pd.Timestamp(start)                                       # a bad date raises here
        if scope == "civilian" and (dollars, start) == adoption[kind]:
            start = "adoption"
        rows.append((kind, int(dollars), start, scope))
    far_date = adoption["SAT"][1]
    rows.sort(key=lambda x: (x[0] == "MPT", far_date if x[2] == "adoption" else x[2], x[1]))
    for kind in ("SAT", "MPT"):
        if not any(k == kind for k, *_ in rows) or not any(k == kind and s == "adoption" for k, _, s, _ in rows):
            raise ValueError(f"reference/thresholds.csv: no usable {kind} rows")
    return rows, far_date


def load_thresholds(path=None, adoption_path=None):
    """(rows, FAR date, source) from the reference files, or the built-in fallback when they cannot be read"""
    try:
        rows, far = read_thresholds(path, adoption_path)
        return rows, far, "reference/thresholds.csv and reference/threshold_adoption_2018.csv"
    except (OSError, KeyError, ValueError, IndexError, TypeError, AttributeError) as e:
        print(f"note: reference threshold files not usable ({e}); using the built-in fallback table", file=sys.stderr)
        return list(FALLBACK_THRESHOLDS), FALLBACK_FAR_DATE, "built-in fallback (reference files not usable)"


THRESHOLDS, FAR_DATE, THRESHOLDS_SOURCE = load_thresholds()
GSA = "General Services Administration"
SECTORS = ["civilian_ex_gsa", "gsa", "defense"]

GAP_DAYS = 30                                                # consecutive awards at most this many days apart are linked
MULTIPLES = [0.5, 0.6, 0.7, 0.8, 1.0, 1.25, 1.4, 1.6, 2.0]   # 1.0 = the real line, the rest are placebo lines
DONUT = 0.9                                                  # donut variant: every award at or under 0.9 x line
OTHER_LINES = [2_000, 2_500, 3_000, 3_500, 5_000, 10_000, 15_000, 25_000, 100_000, 150_000, 250_000, 350_000]
NEAR = 1.08                                                  # a placebo line within 8% of a real line is not used for the fit
B = 200                                                      # bootstrap resamples
MIN_CLUSTERS_FOR_DOLLARS = 3                                 # small-cell rule for dollar totals
SEED = 20261110
# threshold increases: (kind, old line, new line, label, years before, years after).  Transition years 2018-2020 are left out.
CHANGES = [
    ("SAT", 100_000, 150_000, "FY2011", (2008, 2010), (2011, 2013)),
    ("SAT", 150_000, 250_000, "2018-2020", (2015, 2017), (2021, 2023)),
    ("SAT", 250_000, 350_000, "FY2026", (2023, 2025), (2026, 2026)),
    ("MPT", 3_000, 3_500, "FY2016", (2013, 2015), (2016, 2017)),
    ("MPT", 3_500, 10_000, "2018-2020", (2016, 2017), (2021, 2023)),
    ("MPT", 10_000, 15_000, "FY2026", (2023, 2025), (2026, 2026)),
]
COLS = ["awarding_agency_name", "awarding_office_code", "award_type_code", "action_date", "product_or_service_code",
        "base_and_all_options_value", "vendor_id", "is_dod"]
ALL_YEARS = list(range(2008, 2027))


# ---- thresholds ------------------------------------------------------------------------------------------------------------------
def load_adoption(path=None):
    """{"SAT": {agency: date}, "MPT": {...}} from the adoption file (rows with a date only; the FAR row is the fallback)"""
    a = pd.read_csv(path or os.path.join(PROJECT, "reference", "threshold_adoption_2018.csv"))
    a = a[a.agency != "FAR (government-wide)"]
    out = {}
    for kind, col in (("SAT", "sat_effective_date"), ("MPT", "mpt_effective_date")):
        d = a.dropna(subset=[col])
        out[kind] = {AGENCY_ALIAS.get(n, n): np.datetime64(x, "D") for n, x in zip(d.agency, d[col])}
    return out


def thresholds_at(kind, agency, date, is_dod, adopt, table=None, far_date=None):
    """threshold of `kind` in force for each (agency, date, is_dod); arrays of equal length; date as datetime64[D]"""
    date = np.asarray(date, dtype="datetime64[D]")
    is_dod = np.asarray(is_dod, dtype=bool)
    far_date = far_date or FAR_DATE
    T = np.full(len(date), np.nan)
    for k, dollars, start, scope in (THRESHOLDS if table is None else table):
        if k != kind:
            continue
        if start == "adoption":
            inv, uniq = pd.factorize(pd.Series(agency, dtype="str"))
            s_u = np.array([adopt[kind].get(str(u), np.datetime64(far_date, "D")) for u in uniq], dtype="datetime64[D]")
            s = s_u[inv]
        else:
            s = np.datetime64(start, "D")
        ok = date >= s
        if scope == "civilian":
            ok &= ~is_dod
        elif scope == "dod":
            ok &= is_dod
        T = np.where(ok, float(dollars), T)
    return T


# ---- data and clusters -----------------------------------------------------------------------------------------------------------
def find_years(data_dir):
    files = {}
    for p in sorted(glob.glob(os.path.join(data_dir, "history", "processed", "base_actions_FY*.parquet"))):
        m = re.search(r"base_actions_FY(\d{4})\.parquet$", p)
        if m:
            files[int(m.group(1))] = p
    return files


def load_year(path, max_date=None):
    """The universe of one year's file, slim columns only (optionally only actions on or before max_date).
    Read one column at a time with pyarrow to keep peak memory low."""
    f = pq.ParquetFile(path)

    def col(name):
        return f.read(columns=[name]).column(0)

    keep = pc.is_in(col("award_type_code").cast(pa.string()), value_set=pa.array(["B", "D"]))
    keep = pc.and_(keep, pc.greater(col("base_and_all_options_value"), 0))
    for name in ("vendor_id", "awarding_office_code", "product_or_service_code", "action_date"):
        c = col(name)
        keep = pc.and_(keep, pc.is_valid(c))
        if name == "vendor_id":
            keep = pc.and_(keep, pc.not_equal(c, ""))
        if name == "action_date" and max_date is not None:
            keep = pc.and_(keep, pc.less_equal(c, pa.scalar(pd.Timestamp(max_date).to_pydatetime(), type=c.type)))
        del c
    keep = pc.fill_null(keep, False)

    def text(name, width=None):
        c = col(name).filter(keep)
        if pa.types.is_dictionary(c.type):
            c = c.cast(pa.string())
        if width:
            c = pc.utf8_slice_codeunits(c, 0, width)
        return pd.Series(pd.array(c.combine_chunks(), dtype="str"))     # pandas' compact string type, no Python objects

    out = pd.DataFrame({
        "agency": text("awarding_agency_name"), "office": text("awarding_office_code"),
        "psc": text("product_or_service_code", 4), "vendor": text("vendor_id"),
        "date": col("action_date").filter(keep).to_numpy().astype("datetime64[D]"),
        "value": col("base_and_all_options_value").filter(keep).to_numpy().astype(float),
        "is_dod": col("is_dod").filter(keep).to_numpy(zero_copy_only=False).astype(bool),
    })
    del keep, f
    pa.default_memory_pool().release_unused()   # hand the reader's buffers back to the system (memory budget)
    return out


def link_clusters(df, gap=GAP_DAYS):
    """Sort awards by (company, office, product code, date) and link consecutive awards at most `gap` days apart.
    Returns the sorted awards (with column cid) and one row per cluster: start row, n, max, sum, first and last date."""
    key = np.zeros(len(df), np.int64)
    for col in ("vendor", "office", "psc"):
        codes, uniq = pd.factorize(df[col], sort=True)
        key = key * (len(uniq) + 1) + codes
    d = df["date"].to_numpy().astype("datetime64[D]").astype(np.int64)
    v = df["value"].to_numpy(dtype=float)
    order = np.lexsort((v, d, key))
    df = df.reset_index(drop=True)
    for c in list(df.columns):                  # reorder one column at a time (lower peak memory than a whole-frame copy)
        df[c] = df[c].take(order).reset_index(drop=True)
    key, d, v = key[order], d[order], v[order]
    new = np.ones(len(df), bool)
    if len(df) > 1:
        new[1:] = (key[1:] != key[:-1]) | (d[1:] - d[:-1] > gap)
    starts = np.flatnonzero(new)
    df["cid"] = np.cumsum(new) - 1
    ends = np.append(starts[1:], len(df)) - 1
    cl = pd.DataFrame({"start": starts, "n": ends - starts + 1,
                       "vmax": np.maximum.reduceat(v, starts) if len(v) else np.array([]),
                       "vsum": np.add.reduceat(v, starts) if len(v) else np.array([]),
                       "first": d[starts].astype("datetime64[D]"), "last": d[ends].astype("datetime64[D]")})
    return df, cl


def continues_next(df, cl, next_path, fy, gap=GAP_DAYS):
    """True for clusters that link to an award in the first days of the next fiscal year (read from the next year's file)"""
    cont = np.zeros(len(cl), bool)
    nstart = np.datetime64(f"{fy}-10-01", "D")
    open_ = np.flatnonzero(cl["last"].to_numpy() >= nstart - np.timedelta64(gap, "D"))
    if not len(open_):
        return cont
    head = load_year(next_path, max_date=nstart + np.timedelta64(gap - 1, "D"))
    if not len(head):
        return cont
    nxt = head.groupby(["vendor", "office", "psc"], sort=True)["date"].min().rename("next_date").reset_index()
    rows = cl["start"].to_numpy()[open_]
    o = pd.DataFrame({"vendor": df["vendor"].iloc[rows].to_numpy(), "office": df["office"].iloc[rows].to_numpy(),
                      "psc": df["psc"].iloc[rows].to_numpy(), "last": cl["last"].to_numpy()[open_], "i": open_})
    o = o.merge(nxt, on=["vendor", "office", "psc"], how="inner")
    link = (o["next_date"].to_numpy().astype("datetime64[D]") - o["last"].to_numpy().astype("datetime64[D]")).astype(int) <= gap
    cont[o["i"].to_numpy()[link]] = True
    return cont


# ---- the screen --------------------------------------------------------------------------------------------------------------------
def screen_line(value, starts, n, vmax, vsum, L, cap=1.0, upper=None):
    """For per-cluster lines L (awards sorted by cluster, `starts` = first row of each cluster):
    awards at or under L per cluster, their dollars, and whether each cluster is split-like
    (2+ awards, every award at or under cap x L, total above L, and at or under `upper` when given)."""
    L = np.round(np.asarray(L, dtype=float), 2)
    under = value <= np.repeat(L, n)
    a_under = np.add.reduceat(under.astype(np.int64), starts)
    d_under = np.add.reduceat(np.where(under, value, 0.0), starts)
    split = (n >= 2) & (vmax <= np.round(cap * L, 2)) & (vsum > L)
    if upper is not None:
        split &= vsum <= np.round(np.asarray(upper, dtype=float), 2)
    return a_under, d_under, split


def placebo_used(ref):
    """which multiples serve as placebo lines for a real line `ref` (not 1.0, not within NEAR of another real line)"""
    out = []
    for m in MULTIPLES:
        far = all(abs(np.log(m * ref / R)) > np.log(NEAR) for R in OTHER_LINES)
        out.append(m != 1.0 and far)
    return np.array(out)


def baseline_share(shares, used):
    """Expected share at m = 1 from a straight line in log(share) on log(m) through the placebo lines.
    shares: (len(MULTIPLES),) or (len(MULTIPLES), reps).  NaN when the fit is not possible."""
    S = np.asarray(shares, dtype=float)
    one = S.ndim == 1
    S = S.reshape(len(MULTIPLES), -1)
    m = np.array(MULTIPLES)[used]
    out = np.full(S.shape[1], np.nan)
    if len(m) >= 3 and (m < 1).any() and (m > 1).any():
        Y = S[used]
        ok = np.all(np.isfinite(Y) & (Y > 0), axis=0)
        if ok.any():
            A = np.column_stack([np.ones(len(m)), np.log(m)])
            coef = np.linalg.lstsq(A, np.log(Y[:, ok]), rcond=None)[0]
            out[ok] = np.exp(coef[0])
    return float(out[0]) if one else out


def bases_for(cl_T_sat, cl_T_mpt, n):
    """the lines to screen: the threshold in force (SAT and MPT) and one band per threshold increase"""
    out = []
    for kind, T in (("SAT", cl_T_sat), ("MPT", cl_T_mpt)):
        ref = float(pd.Series(n).groupby(T).sum().idxmax()) if len(T) else np.nan
        out.append(dict(basis="in_force", kind=kind, X=T, upper=None, ref=ref, line_high=np.nan,
                        share_at_ref=float(n[T == ref].sum() / max(n.sum(), 1)) if len(T) else np.nan))
    for kind, x0, x1, label, _, _ in CHANGES:
        out.append(dict(basis="band", kind=kind, X=np.full(len(n), float(x0)), upper=float(x1), ref=float(x0), line_high=float(x1),
                        share_at_ref=1.0, change=label))
    return out


def sector_year(value, n, vmax, vsum, vendor, T_sat, T_mpt, seed, reps=B):
    """All lines for one sector and fiscal year.  Returns (rows, boot) where boot[(basis, kind, line, variant)] holds the
    actual and expected split-like award counts at m = 1 for the point estimate and for every bootstrap resample.
    Bootstrap: companies are drawn with replacement and carry all their clusters.  The same seed is used for every line, so all
    lines see the same resampled companies; one line is done at a time to keep memory small."""
    starts = np.concatenate([[0], np.cumsum(n)[:-1]]).astype(np.int64) if len(n) else np.array([], np.int64)
    vendor_codes, vendor_uniq = pd.factorize(pd.Series(vendor, dtype="str").reset_index(drop=True), sort=True)
    nv = len(vendor_uniq)
    i1 = MULTIPLES.index(1.0)
    rows, boot = [], {}
    for bs in bases_for(T_sat, T_mpt, n):
        used = placebo_used(bs["ref"]) if np.isfinite(bs["ref"]) else np.zeros(len(MULTIPLES), bool)
        V = np.zeros((nv, 3 * len(MULTIPLES)), np.float32)   # per company and multiple: [under the line, split-like all, donut]
        brows = []
        for mi, m in enumerate(MULTIPLES):
            L = m * bs["X"]
            upper = None if bs["upper"] is None else m * bs["upper"]
            a_under, d_under, split = screen_line(value, starts, n, vmax, vsum, L, upper=upper)
            _, _, split_d = screen_line(value, starts, n, vmax, vsum, L, cap=DONUT, upper=upper)
            V[:, 3 * mi] = np.bincount(vendor_codes, weights=a_under, minlength=nv)
            for vi, (variant, sp) in enumerate((("all", split), ("donut", split_d))):
                V[:, 3 * mi + 1 + vi] = np.bincount(vendor_codes, weights=n * sp, minlength=nv)
                k = int(sp.sum())
                under, n_split = int(a_under.sum()), int((n * sp).sum())
                dollars_under, dsplit = float(d_under.sum()), float(vsum[sp].sum()) if k >= MIN_CLUSTERS_FOR_DOLLARS else np.nan
                brows.append(dict(basis=bs["basis"], threshold_type=bs["kind"], line_low=bs["ref"], line_high=bs["line_high"],
                                  variant=variant, multiple=m, line_dollars=round(m * bs["ref"], 2), placebo_line=bool(used[mi]),
                                  share_awards_at_line_low=round(bs["share_at_ref"], 4), awards_under=under,
                                  awards_split_like=n_split, share_awards=n_split / under if under else np.nan,
                                  dollars_under=dollars_under, dollars_split_like=dsplit,
                                  share_dollars=dsplit / dollars_under if dollars_under and np.isfinite(dsplit) else np.nan,
                                  clusters_split_like=k, _m=mi))
        rng = np.random.default_rng(seed)
        R = np.empty((reps, V.shape[1]))
        for r in range(reps):
            R[r] = np.bincount(rng.integers(0, nv, nv), minlength=nv).astype(np.float32) @ V
        tot = V.sum(axis=0, dtype=np.float64)
        del V
        for vi, variant in enumerate(("all", "donut")):
            den, num = tot[0::3], tot[1 + vi::3]
            rden, rnum = R[:, 0::3].T, R[:, 1 + vi::3].T
            with np.errstate(invalid="ignore", divide="ignore"):
                share, rshare = num / den, rnum / rden
                base = baseline_share(share, used)
                rratio = rshare[i1] / baseline_share(rshare, used)
                rexp = baseline_share(rshare, used) * rden[i1]
            ratio = share[i1] / base if np.isfinite(base) else np.nan
            okr = np.isfinite(rratio).sum() >= 20
            boot[(bs["basis"], bs["kind"], bs["ref"] if bs["basis"] == "band" else None, variant)] = dict(
                obs=num[i1], exp=base * den[i1] if np.isfinite(base) else np.nan, robs=rnum[i1], rexp=rexp)
            for row in brows:
                if row["variant"] == variant and row["_m"] == i1:
                    row.update(n_placebo_lines=int(used.sum()), baseline_share=base, excess_ratio=ratio,
                               ci_lo=np.nanpercentile(rratio, 2.5) if okr else np.nan,
                               ci_hi=np.nanpercentile(rratio, 97.5) if okr else np.nan)
        for row in brows:
            row.pop("_m")
        rows += brows
    return rows, boot


def pooled(entries):
    """sum(actual) / sum(expected) over years, with a percentile interval from the per-year resamples added up"""
    entries = [e for e in entries if np.isfinite(e["exp"]) and e["exp"] > 0]
    if not entries:
        return dict(ratio=None, ci_lo=None, ci_hi=None, years=0), None
    obs = sum(e["obs"] for e in entries)
    exp = sum(e["exp"] for e in entries)
    with np.errstate(invalid="ignore", divide="ignore"):
        r = sum(e["robs"] for e in entries) / sum(e["rexp"] for e in entries)
    good = np.isfinite(r).sum() >= 20
    out = dict(ratio=round(obs / exp, 3), ci_lo=round(float(np.nanpercentile(r, 2.5)), 3) if good else None,
               ci_hi=round(float(np.nanpercentile(r, 97.5)), 3) if good else None, years=len(entries),
               actual_awards=int(obs), expected_awards=round(float(exp), 1))
    return out, r


# ---- the pipeline ------------------------------------------------------------------------------------------------------------------
def run(data_dir=DATA, results_dir=RESULTS, fig_dir=None, reps=B, verbose=True):
    fig_dir = fig_dir or os.path.join(PROJECT, "figures")
    files = find_years(data_dir)
    if not files:
        sys.exit("no history tables: run `python run.py get-history` (or the history workflow) first")
    years = sorted(files)
    say = print if verbose else (lambda *a, **k: None)
    say(f"fiscal years present ({len(years)}): {', '.join(map(str, years))}")
    missing = [y for y in ALL_YEARS if y not in files]
    if missing:
        say(f"not present yet: {', '.join(map(str, missing))}")
    adopt = load_adoption()
    rows, boots, universe = [], {}, []
    carry = None
    for fy in years:
        df = load_year(files[fy])
        n_file = len(df)
        if carry is not None and len(carry):
            df = pd.concat([carry, df], ignore_index=True)
        df, cl = link_clusters(df)
        cont = continues_next(df, cl, files[fy + 1], fy) if (fy + 1) in files else np.zeros(len(cl), bool)
        move = np.repeat(cont, cl["n"].to_numpy())
        carry = df.loc[move, ["agency", "office", "psc", "vendor", "date", "value", "is_dod"]].reset_index(drop=True)
        df = df.loc[~move].reset_index(drop=True)
        cl = cl.loc[~cont].reset_index(drop=True)
        n = cl["n"].to_numpy()
        first = np.concatenate([[0], np.cumsum(n)[:-1]]).astype(np.int64)     # first row of each remaining cluster
        agency = df["agency"].iloc[first].reset_index(drop=True)              # stays in the compact string type
        is_dod = df["is_dod"].to_numpy()[first]
        fdate = cl["first"].to_numpy().astype("datetime64[D]")
        T_sat = thresholds_at("SAT", agency, fdate, is_dod, adopt)
        T_mpt = thresholds_at("MPT", agency, fdate, is_dod, adopt)
        sector = np.where(is_dod, 2, np.where((agency == GSA).to_numpy(dtype=bool), 1, 0))
        value = df["value"].to_numpy()
        vendor = df["vendor"].iloc[first].reset_index(drop=True)
        vmax, vsum = cl["vmax"].to_numpy(), cl["vsum"].to_numpy()
        aw_sector = np.repeat(sector, n)
        for si, sname in enumerate(SECTORS):
            k = sector == si
            if not k.any():
                continue
            r, b = sector_year(value[aw_sector == si], n[k], vmax[k], vsum[k], vendor[k], T_sat[k], T_mpt[k], [SEED, fy, si], reps)
            meta = dict(sector=sname, fy=fy, awards=int(n[k].sum()), clusters=int(k.sum()), multi_award_clusters=int((n[k] >= 2).sum()))
            universe.append(meta)
            rows += [{**meta, **x} for x in r]
            for key, e in b.items():
                boots.setdefault((sname,) + key, {})[fy] = e
        say(f"FY{fy}: {n_file:,} awards in file, {len(cl):,} clusters closed, {int(cont.sum()):,} carried into FY{fy + 1}", flush=True)
        del df, cl, agency, vendor, value, fdate, is_dod, T_sat, T_mpt, sector, aw_sector, n, first, vmax, vsum, move, cont
    out = pd.DataFrame(rows)
    order = ["sector", "fy", "threshold_type", "basis", "line_low", "line_high", "variant", "multiple", "line_dollars", "placebo_line",
             "awards", "clusters", "multi_award_clusters", "share_awards_at_line_low", "awards_under", "awards_split_like",
             "share_awards", "dollars_under", "dollars_split_like", "share_dollars", "clusters_split_like", "n_placebo_lines",
             "baseline_share", "excess_ratio", "ci_lo", "ci_hi"]
    out = out[order].sort_values(["sector", "fy", "threshold_type", "basis", "line_low", "variant", "multiple"]).reset_index(drop=True)
    for c in ("share_awards", "share_dollars", "baseline_share", "share_awards_at_line_low"):
        out[c] = out[c].round(6)
    for c in ("excess_ratio", "ci_lo", "ci_hi"):
        out[c] = out[c].astype(float).round(4)
    for c in ("dollars_under", "dollars_split_like"):
        out[c] = out[c].round(0)
    os.makedirs(results_dir, exist_ok=True)
    out.to_csv(os.path.join(results_dir, "split_screen.csv"), index=False)
    summary = summarize(out, boots, years, missing, universe)
    with open(os.path.join(results_dir, "split_screen_summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=1)
    os.makedirs(fig_dir, exist_ok=True)
    plot(out, years, os.path.join(fig_dir, "split_screen.png"))
    if verbose:
        print_summary(out, summary)
    return out, summary


def summarize(out, boots, years, missing, universe):
    head = {}
    for sector in SECTORS:
        for kind in ("SAT", "MPT"):
            for variant in ("all", "donut"):
                e = boots.get((sector, "in_force", kind, None, variant), {})
                head[f"{sector}|{kind}|{variant}"] = pooled(list(e.values()))[0]
    nat = []
    for kind, x0, x1, label, before, after in CHANGES:
        for sector in SECTORS:
            for variant in ("all", "donut"):
                e = boots.get((sector, "band", kind, float(x0), variant), {})
                pb, rb = pooled([v for y, v in e.items() if before[0] <= y <= before[1]])
                pa, ra = pooled([v for y, v in e.items() if after[0] <= y <= after[1]])
                diff = dict(change=None, ci_lo=None, ci_hi=None)
                if pb["ratio"] is not None and pa["ratio"] is not None:
                    d = ra - rb
                    good = np.isfinite(d).sum() >= 20
                    diff = dict(change=round(pa["ratio"] - pb["ratio"], 3),
                                ci_lo=round(float(np.nanpercentile(d, 2.5)), 3) if good else None,
                                ci_hi=round(float(np.nanpercentile(d, 97.5)), 3) if good else None)
                nat.append(dict(threshold_type=kind, old_line=x0, new_line=x1, change_label=label, sector=sector, variant=variant,
                                before_years=list(before), after_years=list(after), before=pb, after=pa, after_minus_before=diff))
    return dict(
        what="Split-like clusters: same company, office and 4-character product code, awards at most 30 days apart, every award at or "
             "under the line, total above it. Excess ratio = share of at-or-under-line awards in such clusters at the real line, divided "
             "by the share expected from placebo lines (1.0 = no excess). A screening indicator; it cannot tell a prohibited split from "
             "legitimate repeat buying.",
        years_used=years, years_not_present=missing,
        universe=universe,
        design=dict(gap_days=GAP_DAYS, multiples=MULTIPLES, donut_cap=DONUT, near_other_line=NEAR, bootstrap=B,
                    bootstrap_unit="company within sector and fiscal year", baseline="straight line in log(share) on log(multiple)",
                    thresholds=[dict(kind=k, dollars=d, start=s, scope=sc) for k, d, s, sc in THRESHOLDS],
                    thresholds_source=THRESHOLDS_SOURCE, far_rule_date=FAR_DATE),
        excess_ratio_pooled=head,
        natural_experiment=nat,
    )


def print_summary(out, summary):
    print("\nExcess ratio at the threshold in force (1.0 = no more split-like clustering than at placebo lines), pooled over "
          f"FY{summary['years_used'][0]}-FY{summary['years_used'][-1]}:")
    for k, v in summary["excess_ratio_pooled"].items():
        if v["ratio"] is not None:
            print(f"  {k:<28} {v['ratio']:.2f}  (95% interval {v['ci_lo']} to {v['ci_hi']}; {v['years']} years)")
    m1 = out[(out.basis == "in_force") & (out.multiple == 1.0) & (out.variant == "all")]
    print("\nBy fiscal year (all clusters):")
    print(m1.pivot_table(index="fy", columns=["sector", "threshold_type"], values="excess_ratio").round(2).to_string())
    s = m1.groupby(["sector", "threshold_type"])[["awards_under", "awards_split_like", "clusters_split_like"]].sum()
    s["share"] = (s.awards_split_like / s.awards_under).round(4)
    print("\nAt-or-under-threshold awards in split-like clusters (all years):")
    print(s.to_string())
    nat = [x for x in summary["natural_experiment"] if x["variant"] == "all" and x["before"]["ratio"] is not None
           and x["after"]["ratio"] is not None]
    if nat:
        print("\nNatural experiment (band between old and new line; excess ratio before -> after):")
        for x in nat:
            d = x["after_minus_before"]
            print(f"  {x['threshold_type']} {x['old_line']:,}->{x['new_line']:,} {x['sector']:<16} {x['before']['ratio']:.2f} -> "
                  f"{x['after']['ratio']:.2f}  change {d['change']} ({d['ci_lo']} to {d['ci_hi']})")
    else:
        print("\nNatural experiment: not enough years present yet for any before/after comparison.")


def plot(out, years, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    colors = {"SAT": "#2a78d6", "MPT": "#eb6834"}
    names = {"civilian_ex_gsa": "Civilian agencies except GSA", "gsa": "GSA", "defense": "Department of Defense"}
    d = out[(out.basis == "in_force") & (out.multiple == 1.0) & (out.variant == "all")]
    fig, axes = plt.subplots(1, 3, figsize=(13.5, 4.2), sharey=True)
    for ax, sector in zip(axes, SECTORS):
        for kind, dx in (("SAT", -0.12), ("MPT", 0.12)):
            e = d[(d.sector == sector) & (d.threshold_type == kind)].sort_values("fy")
            ok = e.excess_ratio.notna()
            e = e[ok]
            lab = "simplified acquisition threshold" if kind == "SAT" else "micro-purchase threshold"
            if len(e):
                lo = (e.excess_ratio - e.ci_lo).clip(lower=0).fillna(0)
                hi = (e.ci_hi - e.excess_ratio).clip(lower=0).fillna(0)
                ax.errorbar(e.fy + dx, e.excess_ratio, yerr=[lo, hi], color=colors[kind], lw=0, marker="o", ms=5, elinewidth=1,
                            capsize=0, label=lab)
                full = e.set_index("fy").excess_ratio.reindex(range(years[0], years[-1] + 1))   # gaps in years break the line
                ax.plot(full.index + dx, full.to_numpy(), color=colors[kind], lw=2)
        ax.axhline(1, color="#999999", lw=1)
        for x, txt in ((2010.5, "SAT up"), (2015.5, "MPT up"), (2025.5, "both up")):
            if years[0] - 1 <= x <= years[-1] + 1:
                ax.axvline(x, color="#555555", lw=0.8)
                ax.text(x + 0.1, 0.02, txt, transform=ax.get_xaxis_transform(), fontsize=7, color="#555555")
        if years[0] <= 2020 and years[-1] >= 2018:
            ax.axvspan(2017.5, 2020.5, color="#dddddd", alpha=0.5, lw=0)
        ax.set_xlim(years[0] - 0.8, years[-1] + 0.8)
        ax.set_xticks(years if len(years) <= 10 else years[::2])
        ax.set_title(names[sector], fontsize=10)
        ax.set_xlabel("fiscal year")
        ax.spines[["top", "right"]].set_visible(False)
    axes[0].set_ylabel("excess ratio at the real line\n(1.0 = same as placebo lines)")
    axes[0].legend(frameon=False, fontsize=8, loc="upper left")
    yr = f"FY{years[0]}" if len(years) == 1 else f"FY{years[0]}-FY{years[-1]}"
    fig.suptitle(f"Split-like clusters (same company, office and product, each award under the line, together over it), {yr}.\n"
                 "Excess over placebo lines at the threshold in force. Bars: 95% intervals. Grey: 2018-2020 transition. "
                 "A screening pattern, not evidence of splitting.", fontsize=9)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def main():
    run()


if __name__ == "__main__":
    main()
