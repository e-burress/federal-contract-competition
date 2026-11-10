"""
24_outcomes.py - "follow the money": do competed awards that drew ONE offer turn out differently AFTER award than awards with two or more?

What
  Federal award data has no unit prices, so a price premium cannot be measured directly.  The closest available evidence is what happens
  to an award after it is made: how much money is obligated against it, whether its scope is changed, whether it is terminated, whether
  its options are exercised.  This script compares single-offer awards with multi-offer awards on those outcomes.

Method
  Cohorts   purchase orders and definitive contracts (not contract vehicles, whose values are ceilings) that were competed, have a usable
            offer count, and were made in fiscal year t with an initial potential value (base and all options) of $25,000 or more.
            $25,000 is the line above which a solicitation must be posted publicly, and it did not move between FY2008 and FY2026.
            Below it, awards are rarely changed after award.  (A $10,000 floor and higher floors are shown as checks.)
  Follow-up fiscal years t to t+3 from the award_year tables, and t to t+1 so that recent cohorts can be shown.  A cohort is used for a
            follow-up only when every one of those years is in the folder.
  Outcomes  oblig_ratio_w     dollars obligated over the follow-up, divided by the initial potential value (limited to 0..3)
            overrun25         obligated 125% or more of the initial potential value
            value_jump_w      the largest single increase in potential value, divided by the initial value (limited to 0..3)
            value_jump25      at least one modification raised the potential value by 25% or more of the initial value
            any_scope         any scope-change modification (supplemental agreement, change order)
            n_scope_w         number of scope-change modifications (limited to 10)
            any_termination   any termination
            option_exercised  an option was exercised, among awards that had options left at award
            Outliers are handled with fixed limits, not percentiles, so the numbers do not depend on which years are present.
            The award_year tables hold each year's LARGEST and LAST change in potential value, not the sum of the changes, so total
            growth of potential value cannot be built from them.  The two value_jump outcomes are a lower bound on it: they miss growth
            made of several smaller steps, and a change made in the award's own fiscal year is seen only if it was the last action of
            that year or larger than the award itself.  If a later build of the tables adds a `value_sum` column (the sum of the
            changes), this script also reports value_growth_w and value_growth25.
  Compare   single-offer versus multi-offer awards WITHIN cells of buying office x fiscal year and quarter x award kind x 4-character
            product code x value bin, by subtracting cell means (as fe_ols in 03_atlas.py does).  Cells in which every award, or no
            award, had one offer carry no information and drop out.  Standard errors are clustered two ways, by buying office and
            by cell (see cluster_estimate for why office alone is not enough when cells cut across offices); intervals use Student's
            t with (offices - 1) degrees of freedom.  Three changes from the first design, each shown as a check:
              * the buying office inside the cell, in place of the department.  With department cells, and with bureau cells (Army,
                Navy, Defense Logistics Agency ...), the comparison failed its own checks on FY2008 and FY2009: single-offer
                awards were signed on even days 1.6 percentage points more often and had shorter planned lengths.  Offices differ
                in what they buy and how they record it.  Inside the same office both checks pass;
              * the quarter of the award inside the cell, because single-offer awards are made later in the fiscal year (in FY2018,
                7 percentage points more often in the last quarter), which would give them less follow-up time;
              * fixed value bins ($25K, $50K, $100K, $250K ... each about a doubling) in place of deciles, because the top decile
                spans from about a million dollars to billions, and fixed bins make the size bands and size floors exact subsets.
  Checks    two placebos that competition cannot affect: whether the award was signed on an even day of the month, and a coin flip
            tied to the award's ID number.  The coin tests the intervals.  The even day is a real trait of the award, so a
            difference there means the two groups are signed or recorded differently;
            balance tests on things fixed at award (value, planned length, options, month).  A difference there means single-offer
            awards differ in ways the cells do not capture;
            other size floors; bureau cells and department cells; cells without the product code; value deciles; cells without the
            quarter; cells of five or more awards only.
  Memory    one cohort at a time.  Each cohort is reduced to sums per buying office (never written to disk), so all 19 years fit in a
            few hundred megabytes.

What this can and cannot show
  These are signals about markets, not accusations.  Single-offer awards may differ from multi-offer awards in ways the cells do not
  capture (urgency, specialised requirements, incumbents).  Extra obligations and scope changes are often legitimate.  Values are typed
  in by staff and contain errors.  Defense data arrive 90 days late, so a follow-up that ends in the newest year is short for Defense.

Input : data/history/processed/base_actions_FY<year>.parquet and award_year_FY<year>.parquet (18_history_build.py)
Output: results/outcomes_estimates.csv   one row per outcome x scope x follow-up (coefficient on "single", 95% interval, raw means).
                                         coef = single-offer minus multi-offer; for a "share" multiply by 100 for percentage points.
                                         n_awards, n_cells, n_offices and mean_multi_offer describe the awards that carry the
                                         estimate (cells with both kinds of award); n_all and the raw means describe every award.
        results/outcomes_by_cohort.csv   the same by cohort year and sector
        results/outcomes_summary.json    headline numbers, the cohorts used, the caveats
        figures/outcomes.png             pooled estimates with intervals, by sector
        Nothing below agency level is written: office codes and bureaus are used only inside the computation.
Usage : python -I scripts/24_outcomes.py
"""
import gc
import glob
import json
import os
import re
import sys
import zlib

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
from scipy import stats

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA, PROJECT, RESULTS  # noqa: E402

GSA = "General Services Administration"
SECTORS = ["civilian except GSA", "GSA", "Department of Defense"]      # codes 0, 1, 2 (as in 19_history_atlas.py)
FLOOR = 25_000                    # main size floor: the public-posting threshold, unchanged FY2008-FY2026
LOW_FLOOR = 10_000                # the lowest floor tried in the checks (awards below it are never read)
BIN_EDGES = np.array([1e4, 2.5e4, 5e4, 1e5, 2.5e5, 5e5, 1e6, 2.5e6, 5e6, 1e7, 2.5e7, 5e7, 1e8, np.inf])   # value bins inside the cells
BAND_EDGES = np.array([1e4, 2.5e4, 1e5, 2.5e5, 1e6, 1e7, np.inf])
BANDS = ["$10K-$25K", "$25K-$100K", "$100K-$250K", "$250K-$1M", "$1M-$10M", "$10M and up"]   # band 0 is used only by the $10K-floor check
MAIN_BANDS = [1, 2, 3, 4, 5]
FOLLOW_UPS = {"t to t+3": 3, "t to t+1": 1}
FALLBACK = {"t only (same fiscal year)": 0}       # used only when no cohort has a later year in the folder yet
MIN_CELL = 2                      # a cell needs a single-offer and a multi-offer award; that is the only requirement
CAP_RATIO, CAP_SCOPE, CAP_YEARS = 3.0, 10, 10.0

# name: (role, unit, plain-English label)
OUTCOMES = {
    "oblig_ratio_w":    ("outcome", "ratio", "Dollars obligated over the follow-up, as a multiple of the initial potential value (0 to 3)"),
    "overrun25":        ("outcome", "share", "Obligated 125% or more of the initial potential value"),
    "value_jump_w":     ("outcome", "ratio", "Largest single increase in potential value, as a multiple of the initial value (0 to 3)"),
    "value_jump25":     ("outcome", "share", "One modification raised the potential value by 25% or more"),
    "value_growth_w":   ("outcome", "ratio", "Growth of potential value over the follow-up (-1 to 3; needs value_sum)"),
    "value_growth25":   ("outcome", "share", "Potential value grew 25% or more (needs value_sum)"),
    "any_scope":        ("outcome", "share", "Any scope-change modification"),
    "n_scope_w":        ("outcome", "count", "Number of scope-change modifications (0 to 10)"),
    "any_termination":  ("outcome", "share", "Any termination"),
    "option_exercised": ("outcome", "share", "An option was exercised (awards with options left at award)"),
    "placebo_even_day": ("placebo", "share", "Placebo: award signed on an even day of the month"),
    "placebo_coin":     ("placebo", "share", "Placebo: a coin flip tied to the award's ID number"),
    "bal_log_value":    ("balance", "log", "Balance: log of the initial potential value"),
    "bal_planned_years": ("balance", "years", "Balance: planned length at award in years (0 to 10)"),
    "bal_has_options":  ("balance", "share", "Balance: award had options left at award"),
    "bal_month":        ("balance", "months", "Balance: month of the fiscal year in which the award was made (1 to 12)"),
}
# name of the check: (minimum cell size, what it changes)
SPECS = {
    "main":                 (MIN_CELL, "buying office x fiscal year and quarter x award kind x 4-character product code x value bin"),
    "bureau cells":         (MIN_CELL, "bureau in place of buying office"),
    "department cells":     (MIN_CELL, "department in place of buying office (first design)"),
    "no product code":      (MIN_CELL, "without the product code"),
    "value deciles":        (MIN_CELL, "deciles of initial value in place of fixed value bins (first design)"),
    "no quarter":           (MIN_CELL, "fiscal year only, not split by quarter of award (first design)"),
    "cells of 5 or more":   (5, "only cells with five or more awards (the rule in 03_atlas.py)"),
}
STATS = ["n", "n1", "sxx", "sxy", "n0", "sy0"]
BASE_COLS = ["contract_award_unique_key", "awarding_agency_name", "awarding_sub_agency_name", "awarding_office_code", "award_kind",
             "eval_set", "single", "is_dod", "action_date", "product_or_service_code", "base_and_all_options_value",
             "base_and_exercised_options_value", "period_of_performance_start_date", "period_of_performance_potential_end_date"]
YEAR_COLS = ["contract_award_unique_key", "n_tx", "obligation_sum", "value_max", "value_last", "n_options", "n_scope_changes",
             "n_terminations"]


# ---- which years are there -------------------------------------------------------------------------------------------------------------
def years_present(hist):
    """Fiscal years with a base_actions table and with an award_year table in the folder."""
    def years(prefix):
        found = (re.search(r"FY(\d{4})\.parquet$", p) for p in glob.glob(os.path.join(hist, f"{prefix}_FY*.parquet")))
        return sorted(int(m.group(1)) for m in found if m)
    return years("base_actions"), years("award_year")


def cohort_plan(base_years, year_years, follow_ups=None):
    """For each follow-up, the cohorts that are complete: the award year and every follow-up year are in the folder."""
    follow_ups = FOLLOW_UPS if follow_ups is None else follow_ups
    have = set(year_years)
    return {label: [t for t in base_years if all(y in have for y in range(t, t + h + 1))] for label, h in follow_ups.items()}


# ---- one cohort ------------------------------------------------------------------------------------------------------------------------
def read_rows(path, columns, keep):
    """Read a parquet table 100,000 rows at a time and keep only the rows for which keep(batch) is true (this keeps memory low)."""
    parts = []
    for batch in pq.ParquetFile(path).iter_batches(batch_size=100_000, columns=columns):
        t = pa.Table.from_batches([batch])
        parts.append(t.filter(keep(t)))
    return pa.concat_tables(parts).to_pandas()


def load_cohort(hist, fy):
    """Competed purchase orders and definitive contracts of fiscal year `fy` with a usable offer count and an initial value of
    LOW_FLOOR or more: one row per award, with the codes the cells need.  Names stay in memory only."""
    b = read_rows(os.path.join(hist, f"base_actions_FY{fy}.parquet"), BASE_COLS,
                  lambda t: pc.and_(t["eval_set"], pc.greater_equal(t["base_and_all_options_value"], LOW_FLOOR)))
    b = b[b.award_kind.astype(str).isin(["PO", "DEF"])].reset_index(drop=True)
    agency = b.awarding_agency_name.astype(str)
    office = b.awarding_office_code.astype(str).where(b.awarding_office_code.notna() & (b.awarding_office_code != ""), "none:" + agency)
    v0 = b.base_and_all_options_value.to_numpy(float)
    exercised0 = b.base_and_exercised_options_value.to_numpy(float)
    exercised0 = np.where(np.isnan(exercised0), v0, exercised0)
    when = pd.to_datetime(b.action_date)
    start = pd.to_datetime(b.period_of_performance_start_date, errors="coerce")
    end = pd.to_datetime(b.period_of_performance_potential_end_date, errors="coerce")
    kind = (b.award_kind.astype(str) == "DEF").to_numpy().astype(np.int64)
    d = pd.DataFrame({
        "key": b.contract_award_unique_key.astype(str),
        "office": office,
        "office_code": pd.factorize(office)[0],
        "agency": pd.factorize(agency)[0],
        "bureau": pd.factorize(agency + "|" + b.awarding_sub_agency_name.astype(str))[0],
        "sector": np.where(b.is_dod.to_numpy(bool), 2, np.where(agency == GSA, 1, 0)),
        "kind": kind,
        "psc4": pd.factorize(b.product_or_service_code.astype(str).str[:4])[0],
        "value0": v0,
        "bin": np.searchsorted(BIN_EDGES, v0, side="right") - 1,
        "band": np.searchsorted(BAND_EDGES, v0, side="right") - 1,
        "single": b.single.to_numpy(bool).astype(float),
        "month": (when.dt.month.to_numpy() - 10) % 12 + 1,          # month of the fiscal year, 1 = October
        "has_options": (v0 > 1.001 * exercised0).astype(float),
        "even_day": (when.dt.day.to_numpy() % 2 == 0).astype(float),
        "coin": np.array([zlib.crc32(k.encode()) & 1 for k in b.contract_award_unique_key.astype(str)], dtype=float),
        "planned_years": ((end - start).dt.days / 365.25).clip(0, CAP_YEARS).to_numpy(float),
    })
    # deciles of initial value among awards at or above the main floor, within award kind (used by one check only)
    d["quarter"] = (d.month - 1) // 3
    d["decile"] = 0
    for k in (0, 1):
        m = ((d.kind == k) & (d.value0 >= FLOOR)).to_numpy()
        if m.any():
            rank = d.value0[m].rank(method="first").to_numpy()
            d.loc[m, "decile"] = np.minimum((10 * (rank - 1) / m.sum()).astype(int), 9)
    return d


def follow_up(hist, d, fy, horizons):
    """Add up each award's later history from the award_year tables of fiscal years fy .. fy + max(horizons).
    Returns {horizon: table of raw totals}, one row per award of the cohort, in the cohort's order."""
    keys = pd.Index(d.key)
    v0 = d.value0.to_numpy()
    n = len(d)
    acc = {c: np.zeros(n) for c in ("obligations", "scope", "terminations", "options", "jump", "value_sum")}
    has_sum = True
    out = {}
    wanted = pa.array(d.key.to_numpy(object), type=pa.large_string())
    for y in range(fy, fy + max(horizons) + 1):
        path = os.path.join(hist, f"award_year_FY{y}.parquet")
        schema = pq.read_schema(path)
        cols = YEAR_COLS + (["value_sum"] if "value_sum" in schema.names else [])
        has_sum = has_sum and "value_sum" in cols
        mine = wanted.cast(schema.field("contract_award_unique_key").type)
        a = read_rows(path, cols, lambda t: pc.is_in(t["contract_award_unique_key"], value_set=mine))
        pos = keys.get_indexer(a.contract_award_unique_key.astype(str))
        acc["obligations"][pos] += a.obligation_sum.fillna(0).to_numpy()
        acc["scope"][pos] += a.n_scope_changes.to_numpy()
        acc["terminations"][pos] += a.n_terminations.to_numpy()
        acc["options"][pos] += a.n_options.to_numpy()
        if has_sum:
            acc["value_sum"][pos] += a.value_sum.fillna(0).to_numpy()
        vmax, vlast = a.value_max.fillna(0).to_numpy(), a.value_last.fillna(0).to_numpy()
        if y == fy:
            # the award's own year: the yearly maximum includes the award itself, so a change is visible only if it is larger than
            # the award, or if it is the last action of the year (and is not the award itself)
            base = v0[pos]
            jump = np.where(vmax > base, vmax, np.where((a.n_tx.to_numpy() >= 2) & (vlast < base), vlast, 0.0))
        else:
            jump = vmax
        acc["jump"][pos] = np.maximum(acc["jump"][pos], jump)
        del a
        if y - fy in horizons:
            snap = pd.DataFrame({c: v.copy() for c, v in acc.items() if c != "value_sum" or has_sum})
            out[y - fy] = snap
    return out


def outcome_table(d, fu):
    """The outcomes, placebo and balance measures of one cohort for one follow-up, as {name: array}.  NaN = not defined for that award."""
    v0 = d.value0.to_numpy()
    ratio = fu.obligations.to_numpy() / v0
    jump = fu.jump.to_numpy() / v0
    scope = fu.scope.to_numpy()
    y = {
        "oblig_ratio_w": np.clip(ratio, 0, CAP_RATIO),
        "overrun25": (ratio >= 1.25).astype(float),
        "value_jump_w": np.clip(jump, 0, CAP_RATIO),
        "value_jump25": (jump >= 0.25).astype(float),
        "any_scope": (scope > 0).astype(float),
        "n_scope_w": np.minimum(scope, CAP_SCOPE),
        "any_termination": (fu.terminations.to_numpy() > 0).astype(float),
        "option_exercised": np.where(d.has_options.to_numpy() > 0, (fu.options.to_numpy() > 0).astype(float), np.nan),
        "placebo_even_day": d.even_day.to_numpy(),
        "placebo_coin": d.coin.to_numpy(),
        "bal_log_value": np.log(v0),
        "bal_planned_years": d.planned_years.to_numpy(),
        "bal_has_options": d.has_options.to_numpy(),
        "bal_month": d.month.to_numpy(float),
    }
    if "value_sum" in fu:
        growth = fu.value_sum.to_numpy() / v0 - 1
        y["value_growth_w"] = np.clip(growth, -1, CAP_RATIO)
        y["value_growth25"] = (growth >= 0.25).astype(float)
    return y


def cell_codes(d):
    """The cell number (0, 1, 2, ...) of every award for each cell definition in SPECS (the fiscal year is the cohort itself)."""
    def mix(*cols):
        code = np.zeros(len(d), dtype=np.int64)
        for c in cols:
            c = np.asarray(c, dtype=np.int64)
            code = code * (int(c.max()) + 1 if len(c) else 1) + c
        return np.unique(code, return_inverse=True)[1]
    main = mix(d.office_code, d.quarter, d.kind, d.psc4, d.bin)
    return {
        "main": main,
        "bureau cells": mix(d.bureau, d.quarter, d.kind, d.psc4, d.bin),
        "department cells": mix(d.agency, d.quarter, d.kind, d.psc4, d.bin),
        "no product code": mix(d.office_code, d.quarter, d.kind, d.bin),
        "value deciles": mix(d.office_code, d.quarter, d.kind, d.psc4, d.decile),
        "no quarter": mix(d.office_code, d.kind, d.psc4, d.bin),
        "cells of 5 or more": main,
    }


# ---- the estimator ---------------------------------------------------------------------------------------------------------------------
def cell_design(x, ok, cell, min_cell=MIN_CELL):
    """Which awards carry information, and x (1 = single offer) minus its cell mean.  `cell` holds whole numbers from 0 (the cell of
    each award); `ok` marks the awards whose outcome is known.  Returns (x_demeaned, keep, good_cells, cell_sizes).
    A cell is good when it has at least `min_cell` usable awards with both a single-offer and a multi-offer award among them.
    Cells without that variation are dropped: `keep` marks the usable awards in good cells."""
    cell = np.asarray(cell, dtype=np.int64)
    k = int(cell.max()) + 1 if len(cell) else 0
    weight = ok.astype(float)
    n_c = np.bincount(cell, weights=weight, minlength=k)
    x_c = np.bincount(cell, weights=x * weight, minlength=k)
    good = (n_c >= min_cell) & (x_c > 0) & (x_c < n_c)
    return x - (x_c / np.maximum(n_c, 1))[cell], ok & good[cell], good, n_c


def within_cells(x, y, cell, min_cell=MIN_CELL, use=None):
    """Subtract cell means from x and y.  Returns (x_demeaned, y_demeaned, keep, good_cells); see cell_design.
    Awards with an unknown y (NaN), or outside `use`, take no part."""
    x, y, cell = np.asarray(x, dtype=float), np.asarray(y, dtype=float), np.asarray(cell, dtype=np.int64)
    ok = ~np.isnan(y) if use is None else (~np.isnan(y) & use)
    xd, keep, good, n_c = cell_design(x, ok, cell, min_cell)
    y = np.where(ok, y, 0.0)
    y_c = np.bincount(cell, weights=y, minlength=len(n_c))
    return xd, y - (y_c / np.maximum(n_c, 1))[cell], keep, good


def squares(sxx, sxy, block=None, n_blocks=1):
    """For a set of groups (cells, or office-by-cell pairs) with sums sxx and sxy: the three totals from which the sum over groups of
    (sxy - b * sxx) squared can be rebuilt for any coefficient b, as A - 2 b B + b b C.  Returned per block as a dictionary."""
    block = np.zeros(len(sxx), dtype=np.int64) if block is None else block
    return {"A": np.bincount(block, weights=sxy * sxy, minlength=n_blocks), "B": np.bincount(block, weights=sxy * sxx, minlength=n_blocks),
            "C": np.bincount(block, weights=sxx * sxx, minlength=n_blocks)}


def cluster_estimate(sxx, sxy, cells=None, pairs=None):
    """The coefficient on `single` and its standard error from sums of x*x and x*y per buying office (x, y already demeaned).

    With `cells` and `pairs` left out this is an OLS of y on x with standard errors clustered by office.  That is not enough here.
    Subtracting cell means ties the awards of a cell together (in a cell of two awards the two residuals are mirror images), and
    the awards of one cell often sit in different offices, so clustering by office alone gives intervals that are too narrow when
    cells are small.  The standard errors are therefore clustered two ways, by office and by cell (Cameron, Gelbach and Miller
    2011): variance = by office + by cell - by office-and-cell pair, and never less than by office alone.
    `cells` and `pairs` are dictionaries with the totals A, B, C from squares() and the number of groups G.
    Intervals use Student's t with (offices - 1) degrees of freedom, or (cells - 1) if there are fewer cells than offices.
    Working from sums lets cohorts be added up without keeping every award in memory."""
    sxx, sxy = np.asarray(sxx, dtype=float), np.asarray(sxy, dtype=float)
    g, total = len(sxx), float(np.sum(sxx))
    if g < 2 or total <= 0:
        return dict(coef=np.nan, se=np.nan, lo=np.nan, hi=np.nan, p=np.nan, n_offices=g)
    coef = float(np.sum(sxy)) / total
    by_office = g / (g - 1) * float(np.sum((sxy - coef * sxx) ** 2))
    var, df = by_office, g - 1
    if cells is not None and cells["G"] >= 2:
        def part(t):
            return t["G"] / (t["G"] - 1) * max(float(t["A"] - 2 * coef * t["B"] + coef * coef * t["C"]), 0.0) if t["G"] >= 2 else 0.0
        var = max(by_office, by_office + part(cells) - part(pairs))
        df = min(df, int(cells["G"]) - 1)
    se = float(np.sqrt(var)) / total
    crit = stats.t.ppf(0.975, df)
    p = float(2 * stats.t.sf(abs(coef / se), df)) if se > 0 else np.nan
    return dict(coef=coef, se=se, lo=coef - crit * se, hi=coef + crit * se, p=p, n_offices=g)


def fe_estimate(df, y, cells, cluster, x="single", min_cell=MIN_CELL):
    """All in one for a small table: the within-cell coefficient on `x` for outcome `y`, clustered by `cluster` and by cell.
    (The pipeline does the same from sums, cohort by cohort; this is the readable version and the one the tests check.)"""
    cell = df.groupby(cells, sort=False, observed=True).ngroup().to_numpy()
    xd, yd, keep, good = within_cells(df[x].to_numpy(float), df[y].to_numpy(float), cell, min_cell)
    t = pd.DataFrame({"office": df[cluster].to_numpy()[keep], "cell": cell[keep], "sxx": xd[keep] ** 2, "sxy": xd[keep] * yd[keep]})
    by_office, by_cell, by_pair = (t.groupby(k)[["sxx", "sxy"]].sum() for k in ("office", "cell", ["office", "cell"]))
    two_way = [dict(squares(g.sxx.to_numpy(), g.sxy.to_numpy()), G=len(g)) for g in (by_cell, by_pair)]
    two_way = [{k: (float(v[0]) if k != "G" else v) for k, v in t2.items()} for t2 in two_way]
    multi = keep & (df[x].to_numpy(float) == 0)
    return dict(cluster_estimate(by_office.sxx.to_numpy(), by_office.sxy.to_numpy(), *two_way), n=int(keep.sum()), n_cells=int(good.sum()),
                n_single=int(df[x].to_numpy(float)[keep].sum()), mean_multi_offer=float(df[y].to_numpy(float)[multi].mean()))


def cohort_sums(d, ys, cells):
    """Reduce one cohort to sums.  Returns
       tables[spec]  per buying office (for "main": per office x sector x size band), the sums the estimator needs for every outcome
       extras[spec]  per outcome: how many cells and office-by-cell pairs carry the estimate, and their totals for the two-way
                     standard errors (for "main": per sector x size band; every main cell lies inside one sector and one band)
       raw           count and sum of every outcome by sector x size band x single, over ALL awards (for the raw means)."""
    x = d.single.to_numpy()
    sector, band = d.sector.to_numpy(), d.band.to_numpy()
    at_floor = d.value0.to_numpy() >= FLOOR
    offices = pd.factorize(d.office)
    group_main = (offices[0].astype(np.int64) * 3 + sector) * len(BANDS) + band
    tables, extras = {}, {}
    for spec, (min_cell, _) in SPECS.items():
        main = spec == "main"
        codes, g = np.unique(group_main if main else offices[0], return_inverse=True)
        if main:
            index = pd.MultiIndex.from_arrays([np.asarray(offices[1])[codes // (3 * len(BANDS))], (codes // len(BANDS)) % 3,
                                               codes % len(BANDS)], names=["office", "sector", "band"])
        else:
            index = pd.Index(np.asarray(offices[1])[codes], name="office")
        cell = cells[spec]
        k = int(cell.max()) + 1
        n_blocks = 3 * len(BANDS) if main else 1
        block_of_cell = np.zeros(k, dtype=np.int64)
        if main:
            block_of_cell[cell] = sector * len(BANDS) + band
        cols, extra = {}, {}
        shared = None       # most outcomes are known for every award: they share the same cells, so that part is done once
        for o, y in ys.items():
            known = ~np.isnan(y)
            if shared is None or not known.all():
                xd, keep, good, n_c = cell_design(x, known if main else known & at_floor, cell, min_cell)
                gk, ck, xk, xdk = g[keep], cell[keep], x[keep], xd[keep]
                pair, pk = np.unique(gk * k + ck, return_inverse=True)           # office-by-cell pairs
                design = dict(keep=keep, gk=gk, ck=ck, xk=xk, xdk=xdk, pk=pk, size=np.maximum(n_c, 1),
                              block_of_pair=block_of_cell[pair % k],
                              sxx_cell=np.bincount(ck, weights=xdk ** 2, minlength=k), sxx_pair=np.bincount(pk, weights=xdk ** 2),
                              same={"n": np.bincount(gk, minlength=len(codes)).astype(float),
                                    "n1": np.bincount(gk, weights=xk, minlength=len(codes)),
                                    "sxx": np.bincount(gk, weights=xdk ** 2, minlength=len(codes)),
                                    "n0": np.bincount(gk, weights=1 - xk, minlength=len(codes))})
                design["n_cells"] = np.bincount(block_of_cell[good], minlength=n_blocks)
                design["n_pairs"] = np.bincount(design["block_of_pair"], minlength=n_blocks)
                if known.all():
                    shared = design
            else:
                design = shared
            gk, ck, xk, xdk, pk = (design[c] for c in ("gk", "ck", "xk", "xdk", "pk"))
            yk = y[design["keep"]]
            ydk = yk - (np.bincount(ck, weights=yk, minlength=k) / design["size"])[ck]      # y minus its cell mean
            xy = xdk * ydk
            for s in STATS:
                cols[(o, s)] = (design["same"][s] if s in design["same"] else
                                np.bincount(gk, weights=xy if s == "sxy" else (1 - xk) * yk, minlength=len(codes)))
            extra[(o, "n_cells")], extra[(o, "n_pairs")] = design["n_cells"], design["n_pairs"]
            by_cell = squares(design["sxx_cell"], np.bincount(ck, weights=xy, minlength=k), block_of_cell, n_blocks)
            by_pair = squares(design["sxx_pair"], np.bincount(pk, weights=xy, minlength=len(design["sxx_pair"])),
                              design["block_of_pair"], n_blocks)
            for name, t in (("cell", by_cell), ("pair", by_pair)):
                for c in "ABC":
                    extra[(o, f"{name}_{c}")] = t[c]
        tables[spec] = pd.DataFrame(cols, index=index)
        extras[spec] = pd.DataFrame(extra, index=pd.MultiIndex.from_product([range(3), range(len(BANDS))], names=["sector", "band"])
                                    if main else pd.Index(["all"]))
    code = (sector * len(BANDS) + band) * 2 + x.astype(np.int64)
    raw = {}
    for o, y in ys.items():
        ok = ~np.isnan(y)
        raw[(o, "n")] = np.bincount(code[ok], minlength=6 * len(BANDS))
        raw[(o, "sum")] = np.bincount(code[ok], weights=y[ok], minlength=6 * len(BANDS))
    raw = pd.DataFrame(raw, index=pd.MultiIndex.from_product([range(3), range(len(BANDS)), range(2)], names=["sector", "band", "single"]))
    return tables, extras, raw


def add(total, new):
    """Add one cohort's sums to the running totals (rows are matched by office / sector / band)."""
    if total is None:
        return new
    return total.add(new, fill_value=0)


def estimates(per_office, extra):
    """{outcome: estimate and counts} from sums per buying office (a table with one block of STATS columns per outcome) and the
    totals over cells and office-by-cell pairs (a series with the same outcomes)."""
    out = {}
    for o in per_office.columns.get_level_values(0).unique():
        g, e = per_office[o], extra[o]
        g = g[g.n.to_numpy() > 0]
        r = cluster_estimate(g.sxx.to_numpy(), g.sxy.to_numpy(), dict(A=e["cell_A"], B=e["cell_B"], C=e["cell_C"], G=e["n_cells"]),
                             dict(A=e["pair_A"], B=e["pair_B"], C=e["pair_C"], G=e["n_pairs"]))
        r.update(n_awards=int(g.n.sum()), n_single=int(g.n1.sum()), n_cells=int(e["n_cells"]),
                 mean_multi_offer=float(g.sy0.sum() / g.n0.sum()) if g.n0.sum() else np.nan)
        out[o] = r
    return out


def summarise(table, extra, raw, sectors=(0, 1, 2), bands=MAIN_BANDS):
    """Estimates for every outcome on a part of the main-design sums (some sectors, some size bands), with the raw means."""
    def part(t):
        return t[t.index.get_level_values("sector").isin(sectors) & t.index.get_level_values("band").isin(bands)]
    out = estimates(part(table).groupby(level="office").sum(), part(extra).sum())
    rr = part(raw).groupby(level="single").sum()
    for o, r in out.items():
        n0, n1 = rr.loc[0, (o, "n")], rr.loc[1, (o, "n")]
        r.update(n_all=int(n0 + n1), n_all_single=int(n1), raw_mean_single=float(rr.loc[1, (o, "sum")] / n1) if n1 else np.nan,
                 raw_mean_multi=float(rr.loc[0, (o, "sum")] / n0) if n0 else np.nan)
    return out


# ---- the run ---------------------------------------------------------------------------------------------------------------------------
def span(years):
    """[2008, 2009, 2010, 2014] -> 'FY2008-FY2010, FY2014'"""
    parts, years = [], sorted(years)
    while years:
        end = 0
        while end + 1 < len(years) and years[end + 1] == years[end] + 1:
            end += 1
        parts.append(f"FY{years[0]}" if end == 0 else f"FY{years[0]}-FY{years[end]}")
        years = years[end + 1:]
    return ", ".join(parts) if parts else "none"


def run(hist, results_dir, figure_path=None, quiet=False):
    """Build every output from the yearly tables in `hist`.  Returns (estimates, by_cohort, summary)."""
    def say(*a):
        if not quiet:
            print(*a, flush=True)

    pa.set_memory_pool(pa.system_memory_pool())        # gives memory back to Windows sooner than the default pool
    base_years, year_years = years_present(hist)
    if not base_years:
        sys.exit("no history tables: run `python run.py get-history` (or the history workflow) first")
    follow_ups = dict(FOLLOW_UPS)
    plan = cohort_plan(base_years, year_years, follow_ups)
    say(f"base_actions tables: {span(base_years)}\naward_year tables  : {span(year_years)}")
    if not any(plan.values()):
        follow_ups = dict(FALLBACK)
        plan = cohort_plan(base_years, year_years, follow_ups)
        say("NOTE: no cohort has a later fiscal year in the folder yet. Falling back to the award's own fiscal year, which is a few "
            "months of follow-up at most. Treat these numbers as a test of the script, not as findings.")
    for label, cohorts in plan.items():
        say(f"complete cohorts, follow-up {label}: {span(cohorts)} ({len(cohorts)})")
    latest = max(year_years) if year_years else None

    totals = {label: {"tables": {s: None for s in SPECS}, "cells": {s: None for s in SPECS}, "raw": None} for label in follow_ups}
    cohort_rows = []
    for fy in sorted({t for cohorts in plan.values() for t in cohorts}):
        labels = [label for label in follow_ups if fy in plan[label]]
        d = load_cohort(hist, fy)
        fus = follow_up(hist, d, fy, sorted({follow_ups[label] for label in labels}))
        cells = cell_codes(d)
        say(f"  FY{fy}: {int((d.value0 >= FLOOR).sum()):,} awards of ${FLOOR:,} or more "
            f"({100 * d.single[d.value0 >= FLOOR].mean():.1f}% single offer); follow-up: {', '.join(labels)}")
        for label in labels:
            ys = outcome_table(d, fus[follow_ups[label]])
            tables, n_cells, raw = cohort_sums(d, ys, cells)
            tot = totals[label]
            for s in SPECS:
                tot["tables"][s] = add(tot["tables"][s], tables[s])
                tot["cells"][s] = add(tot["cells"][s], n_cells[s])
            tot["raw"] = add(tot["raw"], raw)
            for scope, sectors in [("all sectors", (0, 1, 2))] + [(name, (i,)) for i, name in enumerate(SECTORS)]:
                for o, r in summarise(tables["main"], n_cells["main"], raw, sectors).items():
                    cohort_rows.append(dict(cohort_fy=fy, follow_up=label, scope=scope, outcome=o, role=OUTCOMES[o][0], **r,
                                            window_ends_in_latest_year=bool(fy + follow_ups[label] == latest)))
            del ys, tables, n_cells, raw
        del d, fus, cells
        gc.collect()

    rows = []
    for label in follow_ups:
        if not plan[label]:
            continue
        tot = totals[label]
        main, mcells, raw = tot["tables"]["main"], tot["cells"]["main"], tot["raw"]
        outcomes = [o for o in OUTCOMES if o in main.columns.get_level_values(0)]
        scopes = [("pooled", "all sectors", dict())]
        scopes += [("sector", name, dict(sectors=(i,))) for i, name in enumerate(SECTORS)]
        scopes += [("size band", BANDS[b], dict(bands=[b])) for b in MAIN_BANDS]
        scopes += [("check", "size floor $10,000", dict(bands=[0, 1, 2, 3, 4, 5])), ("check", "size floor $100,000", dict(bands=[2, 3, 4, 5])),
                   ("check", "size floor $250,000", dict(bands=[3, 4, 5])), ("check", "size floor $1,000,000", dict(bands=[4, 5]))]
        found = [(scope_type, scope, summarise(main, mcells, raw, **kw)) for scope_type, scope, kw in scopes]
        for spec in SPECS:
            if spec != "main":      # the other cell designs: same awards as the pooled row, so the same raw means
                r = estimates(tot["tables"][spec], tot["cells"][spec].sum())
                for o in r:
                    r[o].update({k: found[0][2][o][k] for k in ("n_all", "n_all_single", "raw_mean_single", "raw_mean_multi")})
                found.append(("check", f"cells: {SPECS[spec][1]}", r))
        for o in outcomes:
            for scope_type, scope, r in found:
                rows.append(dict(outcome=o, follow_up=label, scope_type=scope_type, scope=scope, **r[o]))
    est = pd.DataFrame(rows)
    est.insert(1, "role", est.outcome.map(lambda o: OUTCOMES[o][0]))
    est.insert(2, "unit", est.outcome.map(lambda o: OUTCOMES[o][1]))
    est.insert(3, "label", est.outcome.map(lambda o: OUTCOMES[o][2]))
    est["cohorts"] = est.follow_up.map(lambda f: span(plan[f]))
    est["n_cohorts"] = est.follow_up.map(lambda f: len(plan[f]))
    cols = ["outcome", "role", "unit", "label", "follow_up", "scope_type", "scope", "cohorts", "n_cohorts", "coef", "se", "lo", "hi", "p",
            "mean_multi_offer", "n_awards", "n_single", "n_cells", "n_offices", "n_all", "n_all_single", "raw_mean_single", "raw_mean_multi"]
    est = est[cols]
    by_cohort = pd.DataFrame(cohort_rows)[["cohort_fy", "follow_up", "scope", "outcome", "role", "coef", "se", "lo", "hi", "p",
                                           "mean_multi_offer", "n_awards", "n_single", "n_cells", "n_offices", "n_all", "n_all_single",
                                           "raw_mean_single", "raw_mean_multi", "window_ends_in_latest_year"]]
    by_cohort = by_cohort.sort_values(["follow_up", "scope", "outcome", "cohort_fy"])
    os.makedirs(results_dir, exist_ok=True)
    est.to_csv(os.path.join(results_dir, "outcomes_estimates.csv"), index=False, float_format="%.6g")
    by_cohort.to_csv(os.path.join(results_dir, "outcomes_by_cohort.csv"), index=False, float_format="%.6g")

    summary = build_summary(est, plan, follow_ups, base_years, year_years, latest)
    with open(os.path.join(results_dir, "outcomes_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=1)
    if figure_path:
        plot(est, summary["headline_follow_up"], figure_path)
    if not quiet:
        print_summary(est, summary)
    return est, by_cohort, summary


CAVEATS = [
    "These are signals about markets, not accusations. A difference after award does not show that anyone did anything wrong.",
    "Selection: single-offer awards can differ from multi-offer awards in ways the cells do not capture, such as urgency, specialised "
    "requirements or an incumbent supplier. The balance rows show how different they are on things fixed at award. The bureau and "
    "department rows among the checks show how much the answer depends on comparing inside the same buying office.",
    "Only awards in cells that hold both a single-offer and a multi-offer award carry the estimate. That is a minority of awards, "
    "and offices that never or always get one offer are not in it.",
    "Extra obligations, scope changes and exercised options are often legitimate: needs change and options are planned.",
    "Total growth of potential value cannot be built from the yearly tables, which keep each year's largest and last change, not the "
    "sum. The value_jump outcomes are a lower bound.",
    "Values and offer counts are typed in by staff. Fixed limits (0 to 3 times the initial value) keep single typing errors from "
    "driving the averages, but they do not remove them.",
    "Defense data are published 90 days late, so a follow-up that ends in the newest fiscal year is short for Defense.",
    "Awards are compared within the same quarter, so follow-up time differs by up to three months inside a cell.",
    "No unit prices exist in the data, so none of this is a price comparison.",
]


def build_summary(est, plan, follow_ups, base_years, year_years, latest):
    """The headline numbers as a dictionary (written to results/outcomes_summary.json)."""
    used = [label for label in follow_ups if plan[label]]
    headline = used[0]

    def pick(label, scope_type, scope=None, roles=("outcome",)):
        e = est[(est.follow_up == label) & (est.scope_type == scope_type) & est.role.isin(roles)]
        if scope is not None:
            e = e[e.scope == scope]
        return e

    def records(e, key="outcome"):
        keep = ["coef", "lo", "hi", "se", "p", "mean_multi_offer", "raw_mean_single", "raw_mean_multi", "n_awards", "n_cells", "n_offices"]
        return {r[key]: {k: (None if pd.isna(r[k]) else (int(r[k]) if k.startswith("n_") else round(float(r[k]), 6))) for k in keep}
                for r in e.to_dict("records")}

    out = {
        "question": "Among competed awards, do those that received exactly one offer turn out differently after award than those "
                    "with two or more?",
        "reading": "coef is single-offer minus multi-offer within cells. For outcomes with unit 'share', multiply by 100 for "
                   "percentage points. lo and hi are the 95% interval.",
        "years_present": {"base_actions": base_years, "award_year": year_years},
        "latest_fiscal_year": latest,
        "design": {
            "awards": "competed purchase orders and definitive contracts with a usable offer count",
            "size_floor": FLOOR, "size_bands": [BANDS[b] for b in MAIN_BANDS], "value_bin_edges": [float(e) for e in BIN_EDGES[:-1]],
            "cells": SPECS["main"][1], "minimum_cell": "at least one single-offer and one multi-offer award",
            "standard_errors": "clustered two ways, by buying office and by cell, and never smaller than by office alone; "
                               "Student's t intervals with (offices - 1) degrees of freedom",
            "limits": {"ratios": [0, CAP_RATIO], "scope_changes": CAP_SCOPE, "planned_years": CAP_YEARS},
        },
        "headline_follow_up": headline,
        "follow_ups": {},
        "caveats": CAVEATS,
    }
    for label in used:
        p = pick(label, "pooled", roles=("outcome", "placebo", "balance")).set_index("outcome")
        first = p.loc["any_scope"]
        out["follow_ups"][label] = {
            "cohorts": plan[label], "cohorts_text": span(plan[label]),
            "cohorts_ending_in_latest_year": [t for t in plan[label] if t + follow_ups[label] == latest],
            "awards_at_or_above_floor": int(first.n_all),
            "single_offer_share": round(float(first.n_all_single / first.n_all), 4) if first.n_all else None,
            "awards_in_cells_with_both_kinds": int(first.n_awards),
            "share_of_awards_carrying_the_estimate": round(float(first.n_awards / first.n_all), 4) if first.n_all else None,
            "cells": int(first.n_cells), "buying_offices": int(first.n_offices),
            "pooled": records(pick(label, "pooled")),
            "by_sector": {s: records(pick(label, "sector", s)) for s in SECTORS},
            "by_size_band": {BANDS[b]: records(pick(label, "size band", BANDS[b])) for b in MAIN_BANDS},
            "placebo": records(pick(label, "pooled", roles=("placebo",))),
            "balance": records(pick(label, "pooled", roles=("balance",))),
            "checks": {o: records(pick(label, "check")[pick(label, "check").outcome == o], key="scope")
                       for o in ("oblig_ratio_w", "overrun25", "any_scope", "placebo_even_day", "bal_planned_years")},
        }
    return out


def fmt(r, pct):
    """'+1.23 (+0.80 to +1.66)' in percentage points for shares, in the outcome's own unit otherwise."""
    if pd.isna(r.coef) or not r.se > 0:
        return "not enough data"
    if pct:
        return f"{100 * r.coef:+.2f} ({100 * r.lo:+.2f} to {100 * r.hi:+.2f})"
    return f"{r.coef:+.3f} ({r.lo:+.3f} to {r.hi:+.3f})"


def print_summary(est, summary):
    print("\n" + "=" * 110)
    print("SINGLE-OFFER MINUS MULTI-OFFER AWARDS, WITHIN CELLS  (95% interval; shares in percentage points, others in their own unit)")
    for label, info in summary["follow_ups"].items():
        print(f"\nFollow-up {label}; cohorts {info['cohorts_text']}")
        print(f"  {info['awards_at_or_above_floor']:,} awards of ${FLOOR:,} or more; {info['awards_in_cells_with_both_kinds']:,} "
              f"({100 * info['share_of_awards_carrying_the_estimate']:.0f}%) are in the {info['cells']:,} cells that hold both kinds "
              f"of award; {info['buying_offices']:,} buying offices")
        if info["cohorts_ending_in_latest_year"]:
            print(f"  cohorts whose follow-up ends in the newest year (short for Defense): {span(info['cohorts_ending_in_latest_year'])}")
        e = est[est.follow_up == label]
        for title, scope_types in (("pooled and by sector", ("pooled", "sector")), ("by size band", ("size band",))):
            print(f"\n  {title}")
            t = e[e.scope_type.isin(scope_types)]
            for o in t.outcome.unique():
                pct = OUTCOMES[o][1] == "share"
                print(f"   {OUTCOMES[o][2]}")
                for r in t[t.outcome == o].itertuples():
                    k = 100 if pct else 1
                    print(f"      {r.scope:<22} {fmt(r, pct):<30} multi-offer mean {k * r.mean_multi_offer:7.2f}   "
                          f"raw means: single {k * r.raw_mean_single:.2f}, multi {k * r.raw_mean_multi:.2f}   n={r.n_awards:,}")
        print("\n  checks (pooled, all sectors)")
        t = e[(e.scope_type == "check") & e.outcome.isin(["oblig_ratio_w", "overrun25", "any_scope", "placebo_even_day", "bal_planned_years"])]
        for o in t.outcome.unique():
            pct = OUTCOMES[o][1] == "share"
            print(f"   {OUTCOMES[o][2]}")
            for r in t[t.outcome == o].itertuples():
                print(f"      {r.scope:<82} {fmt(r, pct):<30} n={r.n_awards:,} cells={r.n_cells:,}")
    print("\nWhat this can and cannot show")
    for c in CAVEATS:
        print("  - " + c)


def plot(est, label, path):
    """One chart: the pooled estimates with 95% intervals for the yes/no outcomes, all sectors together and each sector."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    colors = {"all sectors": "#222222", "civilian except GSA": "#2a78d6", "Department of Defense": "#eb6834", "GSA": "#8c8c8c"}
    names = {"overrun25": "Obligated 125% or more\nof the initial potential value",
             "value_jump25": "One modification raised the\npotential value by 25% or more",
             "value_growth25": "Potential value grew\n25% or more", "any_scope": "Any scope-change\nmodification",
             "any_termination": "Any termination", "option_exercised": "An option was exercised\n(awards with options)",
             "placebo_even_day": "Placebo: signed on an\neven day of the month"}
    e = est[(est.follow_up == label) & est.scope_type.isin(["pooled", "sector"]) & est.outcome.isin(list(names))]
    shown = [o for o in names if o in set(e.outcome) and not (o == "value_jump25" and "value_growth25" in set(e.outcome))]
    e = e[e.outcome.isin(shown) & e.coef.notna() & (e.se > 0)]          # no estimate, or no variation at all: nothing to draw
    # the axis is set by the three scopes with many awards; GSA has few awards of this size, and where its interval is wider
    # than the chart it is cut at the edge and marked with an arrow
    reach = [abs(100 * v) for r in e[e.scope != "GSA"].itertuples() for v in (r.lo, r.hi)]
    lim = 1.15 * max(reach) if reach else 1.0
    fig, ax = plt.subplots(figsize=(8, 0.9 * len(shown) + 2.0))
    cut = False
    for i, o in enumerate(shown):
        for j, scope in enumerate(colors):
            r = e[(e.outcome == o) & (e.scope == scope)]
            if not len(r):
                continue
            r = r.iloc[0]
            y = i + (j - 1.5) * 0.19
            lo, hi, c = 100 * r.lo, 100 * r.hi, 100 * r.coef
            ax.plot([max(lo, -lim), min(hi, lim)], [y, y], color=colors[scope], lw=2, solid_capstyle="round")
            for edge, over in ((-lim, lo < -lim), (lim, hi > lim)):
                if over:
                    cut = True
                    ax.plot([edge], [y], marker="<" if edge < 0 else ">", color=colors[scope], ms=6, clip_on=False)
            if -lim <= c <= lim:
                ax.plot([c], [y], "o", color=colors[scope], ms=7, mec="white", mew=1)
        if i:
            ax.axhline(i - 0.5, color="#e6e6e6", lw=0.8)
    for scope, color in colors.items():
        ax.plot([], [], "o-", color=color, lw=2, ms=7, mec="white", mew=1, label=scope)
    ax.axvline(0, color="#666666", lw=1)
    ax.set_xlim(-lim, lim)
    ax.set_ylim(len(shown) - 0.5, -0.5)
    ax.set_yticks(range(len(shown)))
    ax.set_yticklabels([names[o] for o in shown], fontsize=8.5)
    ax.tick_params(axis="y", length=0)
    ax.tick_params(axis="x", labelsize=8.5, colors="#444444")
    ax.set_xlabel("difference in percentage points, single-offer minus multi-offer awards (95% interval)"
                  + ("\narrow: the interval runs past the edge of the chart" if cut else ""), fontsize=8.5)
    ax.text(0.0, 1.01, "less common after one offer", transform=ax.transAxes, fontsize=8, color="#666666", ha="left", va="bottom")
    ax.text(1.0, 1.01, "more common after one offer", transform=ax.transAxes, fontsize=8, color="#666666", ha="right", va="bottom")
    info = est[(est.follow_up == label) & (est.scope_type == "pooled")].iloc[0]
    years = {"t to t+3": "the award year and the three years after", "t to t+1": "the award year and the year after",
             "t only (same fiscal year)": "the rest of the award year only"}.get(label, label)
    fig.suptitle("After award: competed awards that drew one offer, compared with similar awards that drew two or more\n"
                 f"Awards of ${FLOOR:,} or more made in {info.cohorts}, followed over {years}",
                 fontsize=9.5, x=0.02, ha="left")
    ax.spines[["top", "right", "left"]].set_visible(False)
    fig.legend(frameon=False, fontsize=8.5, ncol=4, loc="lower center", handletextpad=0.4, columnspacing=1.6)
    fig.tight_layout(rect=(0, 0.045, 1, 0.985))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fig.savefig(path, dpi=150)
    plt.close(fig)


def main():
    run(os.path.join(DATA, "history", "processed"), RESULTS, os.path.join(PROJECT, "figures", "outcomes.png"))
    print("\nsaved results/outcomes_estimates.csv, outcomes_by_cohort.csv, outcomes_summary.json and figures/outcomes.png")


if __name__ == "__main__":
    main()
