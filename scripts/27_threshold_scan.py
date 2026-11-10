"""
27_threshold_scan.py - a scan of EVERY FAR dollar threshold where awards could pile up just under the line.  For each threshold family
in reference/thresholds.csv (micro-purchase, simplified acquisition, the $25,000 posting line, certified cost or pricing data (TINA),
the simplified procedures for commercial items, the subcontracting plan, the justification approval levels and the 8(a) limits) it
measures the pile-up just under each value the line has had, fiscal year by fiscal year, and tests whether the pile-up follows the line
when the line moves.  A pile-up is a pattern in the data.  It does not show why it happens: budgets written to fit the rules, set-aside
rules tied to a line, or contracts split to stay under it all look the same here.

What it measures
  * Bunching ratio = awards in the zone just under a line / the awards a smooth curve predicts there (1.0 = no pile-up).
  * Bins scale with the line: 40 bins of 2% of the line over a window from 40% below to 40% above it.  The zone is the four bins just
    under the line, (0.92 L, L].  The two bins just above, (L, 1.04 L], are left out of the fit (awards that moved down leave a gap there).
  * The smooth curve is a cubic Poisson polynomial fitted to the other bins.  Left out of the fit, besides the zone: the bins near any
    dollar value that was a threshold of ANY family (thresholds.csv) during that fiscal year or the year before, and the bins that hold
    a multiple of the line's leading power of ten (for example $200,000 and $300,000 around $250,000), where awards heap on round values.
    Lines with fewer than 60 awards in the fitted bins, fewer than 12 fitted bins, or fewer than 3 fitted bins on either side get no ratio.
  * Intervals: 200 bootstrap replicates.  Each replicate redraws every count of a fine histogram from a Poisson distribution (the
    "Poisson bootstrap", equivalent to resampling awards for binned statistics), so only histograms are kept in memory across years.
    All lines of one family are measured on the same replicates, so their differences have correct intervals.
  * Round numbers: awards heap at round values everywhere, so a high ratio at a round line is not evidence on its own.  Two comparisons:
      - placebo values: for each line, the nearest round values (same roundness, within half to twice the line) that were never a
        threshold of any family and whose zone does not touch any threshold's zone;
      - the same line in years when it was NOT a threshold (and no other family used that value).
  * Each change (old value -> new value on a date): ratios under the old and the new line in the two years before and the two years
    after (cut short where another change of the same line intervenes).  Difference-in-differences = (new line: after - before) -
    (old line: after - before).  It is positive if the pile-up moved with the line.  For the 2018 micro-purchase and simplified-acquisition
    change, civilian agencies switched on their own dates (reference/threshold_adoption_2018.csv): each agency's awards are placed before
    or after ITS date.  Agencies without a documented date are left out of FY2018-FY2020 (transition years).

Universe (one per family, see FAMILIES below): base actions of purchase orders (B) and definitive contracts (D); contract vehicles and
orders under them are not in the comparison (delivery orders and BPA calls are not in the data).  Value = base and all options value.
Sectors: civilian, Defense, and both together ("all").

Input : <DATA>/history/processed/base_actions_FY*.parquet (18_history_build.py), reference/thresholds.csv,
        reference/threshold_adoption_2018.csv
Output: results/threshold_scan.csv           one row per family x sector x value (line or placebo) x fiscal year
        results/threshold_scan_changes.csv   one row per change x sector: before/after ratios and the difference-in-differences
        results/threshold_scan_summary.json  which changes show the pile-up moving, which do not, which could not be tested and why
        figures/threshold_scan.png           one panel per family
Usage : python -I scripts/27_threshold_scan.py            (add --boot 50 for a quick check)
Runs on whatever fiscal years are present and prints which.  Deterministic (fixed seeds per cell).
"""
import glob
import json
import math
import os
import re
import sys
import time
import zlib

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
from pandas.api.types import union_categoricals

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA, PROJECT, RESULTS  # noqa: E402

B = 200                     # bootstrap replicates
SEED = 20261112
DEG = 3                     # degree of the Poisson polynomial
NB = 40                     # bins per window (2% of the line each, from 0.6 L to 1.4 L)
Z0, Z1 = 16, 20             # zone = bins 16..19, (0.92 L, L]
A1 = 22                     # bins 20..21, (L, 1.04 L], also left out of the fit
MIN_FIT_AWARDS, MIN_FIT_BINS, MIN_SIDE_BINS = 60, 12, 3
SMALL_EXPECTED = 10         # flag ratios whose expected zone count is below this
ACTIVE_SHARE = 0.9          # a line is "active" in a fiscal year if it applied to at least 90% of the awards
FAR_2018 = pd.Timestamp("2020-08-31")
TRANSITION = (np.datetime64("2017-10-01"), np.datetime64("2020-09-30"))     # FY2018-FY2020
ALIASES = {"Chemical Safety and Hazard Investigation Board": "United States Chemical Safety Board"}
SECTORS = ("civilian", "defense")
HISTORY = range(2008, 2027)     # fiscal years the history tables cover (FY2008-FY2026)

# family: label, universe, why that universe
FAMILIES = {
    "micro_purchase": ("Micro-purchase", "all",
                       "all purchase orders and definitive contracts. The FPDS reporting floor moves with this line, so counts just under "
                       "it are thinned by non-reporting: read changes with care"),
    "simplified_acquisition": ("Simplified acquisition", "all", "all purchase orders and definitive contracts"),
    "synopsis": ("$25,000 posting line", "all", "all purchase orders and definitive contracts (posting applies to sole-source actions too)"),
    "cost_or_pricing_data": ("Certified cost or pricing data (TINA)", "tina",
                             "not competed (extent competed B, C, E, G) and not commercial (commercial procedures code D): where certified "
                             "data could apply. The 2026 move to $10M is Defense (and Coast Guard) only"),
    "commercial_simplified": ("Commercial simplified procedures (FAR 13.5)", "commercial",
                              "commercial items (commercial procedures code A, B or C)"),
    "subcontracting_plan": ("Subcontracting plan", "subk",
                            "not set aside for small business (no set-aside code), not construction (product or service code not Y or Z)"),
    "subcontracting_plan_construction": ("Subcontracting plan, construction", "subk_constr",
                                         "not set aside, construction or real-property repair (product or service code Y or Z)"),
    "justification_level1": ("Justification approval, level 1", "ja",
                             "other than full and open competition (extent competed B, C, E), excluding 8(a) awards (statutory exception)"),
    "justification_level2": ("Justification approval, level 2", "ja", "as level 1"),
    "justification_level3_civilian": ("Justification approval, level 3 (civilian)", "ja", "as level 1, civilian agencies only"),
    "justification_level3_dod": ("Justification approval, level 3 (Defense)", "ja", "as level 1, Defense only"),
    "8a_competitive_threshold_nonmfg": ("8(a) competitive line, non-manufacturing", "8an_nonmfg",
                                        "8(a) sole-source awards (set-aside code 8AN) outside manufacturing (NAICS not 31-33)"),
    "8a_competitive_threshold_mfg": ("8(a) competitive line, manufacturing", "8an_mfg",
                                     "8(a) sole-source awards (set-aside code 8AN) in manufacturing (NAICS 31-33)"),
    "8a_sole_source_ja": ("8(a) sole-source justification line", "8an", "8(a) sole-source awards (set-aside code 8AN)"),
}
AGENCY_DATED = {"micro_purchase": ("mpt_effective_date", 3500.0, 10000.0),          # civilian 2018 change, agency by agency
                "simplified_acquisition": ("sat_effective_date", 150000.0, 250000.0)}
NOT_SCANNED = {
    "micro_purchase_construction / micro_purchase_services_sca": "never changed in the period, and the data cannot tell which "
                                                                 "purchases fall under Davis-Bacon or the Service Contract Act",
    "micro_purchase_contingency_us / _abroad / _higher_ed": "apply only to contingency, emergency or university purchases, which "
                                                             "the data does not identify",
    "synopsis_display_floor": "a notice-display rule with little reason to shape award values; moves on the same days as the "
                              "micro-purchase line (left in as an excluded value in every fit)",
    "commercial_simplified_13_500c": "applies only to special purchases (contingency, defense against attack) the data does not identify",
    "cost_or_pricing_data_pre2018_contracts": "applies to modifications of contracts entered into before July 2018, not to new awards",
    "justification_dod_statute_*": "statute-only change of 2025-12-18 that FAR 6.304 has not implemented; at most part of one "
                                   "Defense year after it, with Defense data published 90 days late",
    "small_business_reservation / fpds_reporting_floor / other_note_cas": "no dollar value (bands or notes)",
}

COLS = ["award_type_code", "action_date", "awarding_agency_name", "is_dod", "base_and_all_options_value", "extent_competed_code",
        "type_of_set_aside_code", "commercial_item_acquisition_procedures_code", "product_or_service_code", "naics_code"]


# ----------------------------------------------------------------------------------------------------- reference tables
def load_thresholds(path=None):
    """thresholds.csv as one row per family x sector x value period (start and end inclusive, as numpy days)"""
    t = pd.read_csv(path or os.path.join(PROJECT, "reference", "thresholds.csv"))
    t = t[t.value_usd.notna()]
    rows = []
    for r in t.itertuples():
        start = np.datetime64(r.effective_date) if isinstance(r.effective_date, str) else np.datetime64("1900-01-01")
        end = np.datetime64(r.end_date) if isinstance(r.end_date, str) else np.datetime64("2100-12-31")
        secs = SECTORS if r.applies_to == "all" else ("civilian",) if r.applies_to == "civilian" else ("defense",)
        for s in secs:
            rows.append(dict(family=r.threshold, sector=s, value=float(r.value_usd), start=start, end=end))
    out = pd.DataFrame(rows)
    out["start"], out["end"] = pd.to_datetime(out.start), pd.to_datetime(out.end)
    return out.sort_values(["family", "sector", "start"]).reset_index(drop=True)


def load_adoption(path=None):
    """{column: {agency name as in the data: numpy day}} for the civilian 2018 change (dated rows only; DoD comes from thresholds.csv)"""
    a = pd.read_csv(path or os.path.join(PROJECT, "reference", "threshold_adoption_2018.csv"))
    a = a[~a.agency.isin(["FAR (government-wide)", "Department of Defense"])]
    out = {}
    for col in ("mpt_effective_date", "sat_effective_date"):
        d = a[a[col].notna()]
        out[col] = {ALIASES.get(n, n): np.datetime64(v) for n, v in zip(d.agency, d[col])}
    return out


def periods(T, family, sector):
    d = T[(T.family == family) & (T.sector == sector)]
    return list(zip(d.value, d.start.to_numpy().astype("datetime64[D]"), d.end.to_numpy().astype("datetime64[D]")))


def changes(T, family, sector):
    """consecutive value periods of one family in one sector -> list of changes (old, new, date, previous change, next change)"""
    if sector == "all":
        c = [x for x in changes(T, family, "civilian") if x in changes(T, family, "defense")]
        return c
    p = periods(T, family, sector)
    out = []
    for i in range(1, len(p)):
        if p[i][0] == p[i - 1][0]:
            continue
        prev = p[i - 1][1] if p[i - 1][1] > np.datetime64("1901-01-01") else np.datetime64("1900-01-01")
        nxt = p[i + 1][1] if i + 1 < len(p) else np.datetime64("2100-12-31")
        out.append(dict(old=p[i - 1][0], new=p[i][0], date=p[i][1], prev=prev, next=nxt,
                        agency_dated=bool(sector == "civilian" and family in AGENCY_DATED and p[i][1] == np.datetime64(FAR_2018.date()))))
    return out


def in_force(T, family, dates, is_dod, agency, adoption):
    """the value of `family` that applied to each award (by date, sector, and for the 2018 civilian change by agency).
    0 = no such line at that date; NaN = unknown (civilian agencies without a documented 2018 date, FY2018-FY2020)"""
    out = np.zeros(len(dates))
    for sector, sm in (("civilian", ~is_dod), ("defense", is_dod)):
        for val, s, e in periods(T, family, sector):
            out[sm & (dates >= s) & (dates <= e)] = val
    if family in AGENCY_DATED:
        col, old, new = AGENCY_DATED[family]
        ad = agency_dates(agency, adoption[col])
        tr = ~is_dod & (dates >= TRANSITION[0]) & (dates <= TRANSITION[1])
        known = tr & ~np.isnat(ad)
        out[known] = np.where(dates[known] >= ad[known], new, old)
        out[tr & np.isnat(ad)] = np.nan
    return out


def agency_dates(agency, table):
    """each award's agency adoption date (NaT if none); works on category codes, so no per-award Python objects are made"""
    cat = agency if isinstance(agency, pd.Categorical) else pd.Categorical(np.asarray(agency, dtype=object))
    per_cat = np.array([table.get(c, np.datetime64("NaT", "D")) for c in cat.categories] + [np.datetime64("NaT", "D")], dtype="datetime64[D]")
    return per_cat[cat.codes]                                          # code -1 (missing) picks the NaT at the end


def active_values(T, sector, t0, t1, adoption_start=None):
    """every threshold value (any family) in force at any time in [t0, t1] for the sector ('all' = either sector)"""
    secs = SECTORS if sector == "all" else (sector,)
    d = T[T.sector.isin(secs)]
    start = d.start.to_numpy().copy()
    if adoption_start is not None:        # civilian agencies used the 2018 values from their own dates, the earliest in Feb 2018
        m = (d.sector.to_numpy() == "civilian") & d.family.isin(list(AGENCY_DATED)).to_numpy() & (start == np.datetime64(FAR_2018.date()))
        start[m] = adoption_start
    keep = (start <= t1) & (d.end.to_numpy() >= t0)
    return sorted(set(d.value.to_numpy()[keep]))


# ----------------------------------------------------------------------------------------------------- grids and placebos
def edges_of(L):
    """41 bin edges, 2% of the line apart, from 0.6 L to 1.4 L (exact whole dollars for lines that are multiples of $50)"""
    return np.round(L * np.arange(30, 30 + NB + 1) / 50.0)


def roundness(x):
    """log10 of the largest 1, 2.5 or 5 x 10^p that divides x (100,000 -> 5.0; 250,000 -> 4.7; 3,500 -> 2.7)"""
    x = int(round(x))
    best = 0.0
    for p in range(0, 11):
        for m in (1, 2.5, 5):
            u = m * 10 ** p
            if u >= 1 and float(u).is_integer() and x % int(u) == 0:
                best = max(best, math.log10(u))
    return best


def zones_overlap(a, b):
    return 0.92 * a < 1.04 * b and 0.92 * b < 1.04 * a


def placebos_for(L, all_values):
    """nearest round value below and above L (within L/2 to 2L) that was never a threshold and whose zone touches no threshold's zone.
    Same roundness first (within one step, for example 50,000s for 250,000); if none on a side, two steps."""
    step = 10 ** math.floor(math.log10(L)) / 4
    cands = [k * step for k in range(math.ceil(0.5 * L / step), math.floor(2 * L / step) + 1)]
    free = [c for c in cands if c > 0 and not any(zones_overlap(c, V) for V in all_values)]
    out = []
    for side in (lambda c: c < L, lambda c: c > L):
        for tol in (0.31, 0.61):
            s = [c for c in free if side(c) and abs(roundness(c) - roundness(L)) <= tol]
            if s:
                out.append(min(s, key=lambda c: abs(math.log(c / L))))
                break
    return sorted(out)


class Grid:
    """one fine histogram grid for a family: the union of the bin edges of all its lines and placebos"""

    def __init__(self, values):
        self.values = sorted(set(values))
        self.U = np.unique(np.concatenate([edges_of(v) for v in self.values]))
        self.idx = {v: np.searchsorted(self.U, edges_of(v)) for v in self.values}

    def hist(self, vals):
        i = np.searchsorted(self.U, vals, side="left") - 1         # fine bin i covers (U[i], U[i+1]]
        ok = (i >= 0) & (i < len(self.U) - 1)
        return np.bincount(i[ok], minlength=len(self.U) - 1).astype(np.int64)

    def line_counts(self, fine, v):
        """counts in the 40 bins of value v from fine counts (last axis)"""
        cum = np.concatenate([np.zeros(fine.shape[:-1] + (1,), dtype=fine.dtype), np.cumsum(fine, axis=-1)], axis=-1)
        k = self.idx[v]
        return cum[..., k[1:]] - cum[..., k[:-1]]


# ----------------------------------------------------------------------------------------------------- the estimator
def masks(L, excl_values):
    """zone (4 bins under L) and the bins used to fit the smooth curve"""
    e = edges_of(L)
    lo, hi = e[:-1], e[1:]
    j = np.arange(NB)
    zone = (j >= Z0) & (j < Z1)
    out = (j >= Z0) & (j < A1)
    for V in excl_values:
        if V != L:
            out |= (hi > 0.92 * V + 1e-6) & (lo < 1.04 * V - 1e-6)
    p10 = 10 ** math.floor(math.log10(L))
    out |= np.floor(hi / p10) * p10 > lo                           # bins holding a multiple of the leading power of ten
    return zone, ~out


def design(L):
    e = edges_of(L)
    z = ((e[:-1] + e[1:]) / 2 - L) / (e[-1] - e[0])
    return np.vander(z, DEG + 1, increasing=True)


def poisson_expected(Y, X, fit, zone):
    """batched Poisson regression (Newton steps) of the fitted bins of each row of Y on X; returns the expected zone count per row"""
    Y = np.atleast_2d(Y).astype(float)
    Xf, Yf = X[fit], Y[:, fit]
    beta = np.linalg.lstsq(Xf, np.log(Yf + 0.5).T, rcond=None)[0].T
    ridge = 1e-9 * np.eye(X.shape[1])
    nrm = np.full(len(Y), np.inf)
    for _ in range(60):
        mu = np.exp(np.clip(beta @ Xf.T, -50, 50))
        g = (Yf - mu) @ Xf
        H = np.einsum("rn,nj,nk->rjk", mu, Xf, Xf) + ridge
        step = np.linalg.solve(H, g[..., None])[..., 0]
        nrm = np.abs(step).max(axis=1)
        beta += step * np.minimum(1.0, 5.0 / np.maximum(nrm, 1e-12))[:, None]
        if np.all(nrm < 1e-9):
            break
    pred = np.exp(np.clip(beta @ X[zone].T, -50, 50)).sum(axis=1)
    pred[~(nrm < 1e-6) | ~np.isfinite(pred)] = np.nan
    return pred


def bunching(Y, L, excl_values=()):
    """bunching ratio for the 40-bin counts Y (one row or a batch of rows) under line L.
    Returns dict(ratio, obs, expected, n_window, n_fit, note) with arrays (one entry per row)."""
    Y = np.atleast_2d(Y)
    zone, fit = masks(L, excl_values)
    obs = Y[:, zone].sum(axis=1).astype(float)
    n_fit = Y[:, fit].sum(axis=1)
    j = np.arange(NB)
    note = ""
    if fit.sum() < MIN_FIT_BINS or (fit & (j < Z0)).sum() < MIN_SIDE_BINS or (fit & (j >= A1)).sum() < MIN_SIDE_BINS:
        note = "too many bins left out near other thresholds"
    exp = np.full(len(Y), np.nan)
    if not note:
        exp = poisson_expected(Y, design(L), fit, zone)
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = obs / exp
    return dict(ratio=ratio, obs=obs, expected=exp, n_window=Y.sum(axis=1), n_fit=n_fit, note=note)


def ratio_from_values(vals, L, excl_values=()):
    """convenience: bunching ratio of raw award values under line L (point estimate)"""
    g = Grid([L])
    r = bunching(g.line_counts(g.hist(np.asarray(vals, float)), L), L, excl_values)
    return float(r["ratio"][0]) if r["n_fit"][0] >= MIN_FIT_AWARDS else np.nan


def measure(grid, fine, values, excl_values, rng, boot):
    """point estimate and bootstrap replicates for every value of the grid from one cell's fine counts"""
    draws = rng.poisson(fine, size=(boot, len(fine))) if boot else None
    out = {}
    for v in values:
        r0 = bunching(grid.line_counts(fine, v), v, excl_values)
        ok = r0["note"] == "" and r0["n_fit"][0] >= MIN_FIT_AWARDS and np.isfinite(r0["ratio"][0])
        reps = np.full(boot, np.nan)
        if ok and boot:
            reps = bunching(grid.line_counts(draws, v), v, excl_values)["ratio"]
        note = r0["note"] or ("" if ok else ("too few awards in the window" if r0["n_fit"][0] < MIN_FIT_AWARDS else "fit failed"))
        out[v] = dict(ratio=float(r0["ratio"][0]) if ok else np.nan, obs=int(r0["obs"][0]),
                      expected=float(r0["expected"][0]) if ok else np.nan, n_window=int(r0["n_window"][0]),
                      n_fit=int(r0["n_fit"][0]), note=note, reps=reps)
    return out


def ci(reps):
    reps = np.asarray(reps, float)
    if np.isfinite(reps).sum() < max(10, 0.8 * len(reps)):
        return np.nan, np.nan
    return float(np.nanpercentile(reps, 2.5)), float(np.nanpercentile(reps, 97.5))


def cell_rng(*key):
    return np.random.default_rng([SEED, zlib.crc32(repr(key).encode())])


# ----------------------------------------------------------------------------------------------------- data
def year_files():
    files = sorted(glob.glob(os.path.join(DATA, "history", "processed", "base_actions_FY*.parquet")))
    return {int(re.search(r"FY(\d{4})", p).group(1)): p for p in files}


def read_year(path, batch_size=150_000):
    """one fiscal year: purchase orders and definitive contracts with a positive value, codes cut to what the universes need.
    Read in batches so the peak memory stays small; codes are kept as categoricals, never as millions of Python strings."""
    parts = {k: [] for k in ("value", "date", "dod", "agency", "ec", "sa", "comm", "psc1", "naics2")}
    for t in pq.ParquetFile(path).iter_batches(batch_size=batch_size, columns=COLS):
        atc = pc.cast(t.column("award_type_code"), pa.string())
        t = t.filter(pc.and_(pc.is_in(atc, value_set=pa.array(["B", "D"])), pc.greater(t.column("base_and_all_options_value"), 0)))
        parts["value"].append(t.column("base_and_all_options_value").to_numpy())
        parts["date"].append(t.column("action_date").to_numpy().astype("datetime64[D]"))
        parts["dod"].append(pc.fill_null(t.column("is_dod"), False).to_numpy(zero_copy_only=False).astype(bool))
        cols = (("agency", t.column("awarding_agency_name")), ("ec", t.column("extent_competed_code")),
                ("sa", t.column("type_of_set_aside_code")), ("comm", t.column("commercial_item_acquisition_procedures_code")),
                ("psc1", pc.utf8_slice_codeunits(pc.cast(t.column("product_or_service_code"), pa.string()), 0, 1)),
                ("naics2", pc.utf8_slice_codeunits(pc.cast(t.column("naics_code"), pa.string()), 0, 2)))
        for name, col in cols:
            if not pa.types.is_dictionary(col.type):
                col = pc.dictionary_encode(col)
            c = pd.Series(col.to_pandas()).astype("category")
            if "" not in c.cat.categories:
                c = c.cat.add_categories([""])
            parts[name].append(pd.Categorical(c.fillna("")))
        del t
    d = {k: np.concatenate(parts[k]) if parts[k] else np.array([]) for k in ("value", "date", "dod")}
    for k in ("agency", "ec", "sa", "comm", "psc1", "naics2"):
        d[k] = union_categoricals(parts[k]) if parts[k] else pd.Categorical([])
    del parts
    pa.default_memory_pool().release_unused()
    return d


def universe(name, d):
    ec, sa, comm, psc1, nai = (pd.Series(d[k]) for k in ("ec", "sa", "comm", "psc1", "naics2"))
    n = len(ec)
    if name == "all":
        m = np.ones(n, bool)
    elif name == "tina":
        m = ec.isin(["B", "C", "E", "G"]) & comm.isin(["D", ""])
    elif name == "commercial":
        m = comm.isin(["A", "B", "C"])
    elif name == "subk":
        m = sa.isin(["NONE", ""]) & ~psc1.isin(["Y", "Z"])
    elif name == "subk_constr":
        m = sa.isin(["NONE", ""]) & psc1.isin(["Y", "Z"])
    elif name == "ja":
        m = ec.isin(["B", "C", "E"]) & ~sa.isin(["8A", "8AN"])
    elif name == "8an":
        m = sa.eq("8AN")
    elif name == "8an_nonmfg":
        m = sa.eq("8AN") & ~nai.isin(["31", "32", "33"])
    elif name == "8an_mfg":
        m = sa.eq("8AN") & nai.isin(["31", "32", "33"])
    else:
        raise ValueError(name)
    return np.asarray(m, bool)


def family_sectors(T, family):
    s = [x for x in SECTORS if len(periods(T, family, x))]
    return s + (["all"] if len(s) == 2 else [])


def fy_bounds(fy):
    return np.datetime64(f"{fy - 1}-10-01"), np.datetime64(f"{fy}-09-30")


def add_years(day, k):
    return np.datetime64((pd.Timestamp(day) + pd.DateOffset(years=k)).date())


def change_windows(ch, dates=None, agency=None, adoption=None):
    """before and after windows [start, end) of a change; per award for the agency-dated 2018 change (NaT = transition, left out)"""
    if not ch["agency_dated"]:
        d = ch["date"]
        return (max(add_years(d, -2), ch["prev"]), d), (d, min(add_years(d, 2), ch["next"]))
    col = AGENCY_DATED[ch["family"]][0]
    ad = agency_dates(agency, adoption[col])
    known = ~np.isnat(ad)
    b0 = np.full(len(dates), np.datetime64("2015-10-01"), dtype="datetime64[D]")
    b1 = np.full(len(dates), np.datetime64("2017-10-01"), dtype="datetime64[D]")
    a0 = np.full(len(dates), np.datetime64("2020-10-01"), dtype="datetime64[D]")
    a1 = np.full(len(dates), np.datetime64("2022-10-01"), dtype="datetime64[D]")
    if known.any():
        u, inv = np.unique(ad[known], return_inverse=True)                # a few dozen distinct agency dates
        b0[known] = np.array([max(add_years(x, -2), ch["prev"]) for x in u], dtype="datetime64[D]")[inv]
        b1[known] = ad[known]
        a0[known] = ad[known]
        a1[known] = np.array([min(add_years(x, 2), ch["next"]) for x in u], dtype="datetime64[D]")[inv]
    b0 = np.maximum(b0, ch["prev"])
    a1 = np.minimum(a1, ch["next"])
    return (b0, b1), (a0, a1)


def change_years(ch):
    """fiscal years each window touches (for the coverage check)"""
    def fys(a, b):
        a, b = pd.Timestamp(a), pd.Timestamp(b) - pd.Timedelta(days=1)
        return list(range(a.year + (a.month >= 10), b.year + (b.month >= 10) + 1))
    if ch["agency_dated"]:
        return list(range(2016, 2021)), list(range(2018, 2023))
    (b0, b1), (a0, a1) = change_windows(ch)
    return fys(b0, b1), fys(a0, a1)


# ----------------------------------------------------------------------------------------------------- main
def setup(T):
    all_values = sorted(set(T.value))
    fams = {}
    for f in FAMILIES:
        lines = sorted(set(T[T.family == f].value))
        pl = {L: placebos_for(L, all_values) for L in lines}
        fams[f] = dict(lines=lines, placebos=pl, grid=Grid(lines + [p for v in pl.values() for p in v]), sectors=family_sectors(T, f),
                       changes={s: [dict(c, family=f) for c in changes(T, f, s)] for s in family_sectors(T, f)})
    return fams, all_values


def accumulate(files, T, adoption, fams):
    scan, meta, chg = {}, {}, {}
    for fy, path in sorted(files.items()):
        t0 = time.time()
        d = read_year(path)
        for f, (_, uni, _) in FAMILIES.items():
            F = fams[f]
            m = universe(uni, d)
            v, dates, dod, agency = d["value"][m], d["date"][m], d["dod"][m], d["agency"][m]
            inf = in_force(T, f, dates, dod, agency, adoption)
            for s in F["sectors"]:
                sm = np.ones(len(v), bool) if s == "all" else (dod if s == "defense" else ~dod)
                scan[(f, s, fy)] = F["grid"].hist(v[sm])
                meta[(f, s, fy)] = dict(n=int(sm.sum()), unknown=int(np.isnan(inf[sm]).sum()),
                                        active={L: int((inf[sm] == L).sum()) for L in F["lines"]})
                for i, ch in enumerate(F["changes"][s]):
                    (b0, b1), (a0, a1) = change_windows(ch, dates[sm], agency[sm], adoption)
                    for per, (w0, w1) in (("before", (b0, b1)), ("after", (a0, a1))):
                        pm = (dates[sm] >= w0) & (dates[sm] < w1)
                        if pm.any():
                            key = (f, s, i, per)
                            h = F["grid"].hist(v[sm][pm])
                            chg[key] = chg[key] + h if key in chg else h
                            meta[key] = meta.get(key, 0) + int(pm.sum())
        del d
        pa.default_memory_pool().release_unused()
        print(f"  FY{fy}: read and binned in {time.time() - t0:.0f}s", flush=True)
    return scan, meta, chg


def status_of(n, n_active, n_unknown):
    if n == 0:
        return "no data", np.nan
    share = n_active / n
    if share >= ACTIVE_SHARE:
        return "active", share
    if n_active == 0 and n_unknown / n < 0.1:
        return "inactive", share
    return "partial", share


def other_family_active(T, family, sector, value, t0, t1):
    """other SCANNED families whose value in force during [t0, t1] has a zone touching this value's zone (niche variants such as the
    contingency micro-purchase lines are left out here, but they are still left out of every fit)"""
    secs = SECTORS if sector == "all" else (sector,)
    d = T[(T.family != family) & T.family.isin(list(FAMILIES)) & T.sector.isin(secs) & (T.start <= t1) & (T.end >= t0)]
    return sorted({f for f, V in zip(d.family, d.value) if zones_overlap(value, V)})


def scan_rows(T, fams, scan, meta, adoption, boot):
    adopt0 = min(min(adoption[c].values()) for c in adoption)
    rows, reps = [], {}
    for (f, s, fy), fine in sorted(scan.items()):
        F = fams[f]
        fy0, fy1 = fy_bounds(fy)
        excl = active_values(T, s, fy_bounds(fy - 1)[0], fy1, adopt0)
        vals = F["grid"].values
        res = measure(F["grid"], fine, vals, excl, cell_rng(f, s, fy), boot)
        mt = meta[(f, s, fy)]
        for v in vals:
            r = res[v]
            is_line = v in F["lines"]
            unknown = mt["unknown"] if (f not in AGENCY_DATED or v in AGENCY_DATED[f][1:]) else 0   # unknown = old or new 2018 value
            st, share = status_of(mt["n"], mt["active"][v], unknown) if is_line else ("placebo", np.nan)
            lo, hi = ci(r["reps"])
            reps[(f, s, fy, v)] = r["reps"]
            rows.append(dict(family=f, sector=s, kind="line" if is_line else "placebo", value=v,
                             placebo_for=";".join(str(int(L)) for L in F["lines"] if v in F["placebos"][L]) if not is_line else "",
                             fy=fy, status=st, active_share=round(share, 3) if np.isfinite(share) else np.nan,
                             other_family_active=";".join(other_family_active(T, f, s, v, fy0, fy1)),
                             ratio=r["ratio"], ci_lo=lo, ci_hi=hi, obs_below=r["obs"], exp_below=r["expected"],
                             n_window=r["n_window"], n_fit=r["n_fit"], n_universe=mt["n"],
                             small=bool(np.isfinite(r["expected"]) and r["expected"] < SMALL_EXPECTED), note=r["note"]))
    return pd.DataFrame(rows), reps


def same_day(T, family, sector, date):
    secs = SECTORS if sector == "all" else (sector,)
    d = T[(T.family != family) & T.sector.isin(secs) & (T.start == date)]
    return sorted(set(d.family))


def change_rows(T, fams, chg, meta, files, adoption, boot):
    adopt0 = min(min(adoption[c].values()) for c in adoption)
    years = set(files)
    rows, details = [], []
    for f, F in fams.items():
        for s in F["sectors"]:
            for i, ch in enumerate(F["changes"][s]):
                old, new = ch["old"], ch["new"]
                pl = sorted(set(F["placebos"][old]) | set(F["placebos"][new]))
                vals = [old, new] + pl
                bys, ays = change_years(ch)
                miss_b, miss_a = [y for y in bys if y not in years and y in HISTORY], [y for y in ays if y not in years and y in HISTORY]
                beyond = sorted(y for y in set(bys + ays) if y not in HISTORY)
                if ch["agency_dated"]:
                    date_txt, bw, aw = "by agency, 2018-2020", "2 years before each agency's date (FY2016-17 if undated)", \
                        "2 years after each agency's date (FY2021-22 if undated)"
                    ex0, ex1 = np.datetime64("2014-10-01"), np.datetime64("2022-09-30")
                else:
                    (b0, b1), (a0, a1) = change_windows(ch)
                    date_txt, bw, aw = str(ch["date"]), f"{b0} to {b1 - np.timedelta64(1, 'D')}", f"{a0} to {a1 - np.timedelta64(1, 'D')}"
                    ex0, ex1 = add_years(b0, -1), a1
                excl = active_values(T, s, ex0, ex1, adopt0)
                res = {}
                for per in ("before", "after"):
                    fine = chg.get((f, s, i, per))
                    res[per] = measure(F["grid"], fine, vals, excl, cell_rng(f, s, i, per), boot) if fine is not None else None
                row = dict(family=f, sector=s, old_line=old, new_line=new, change_date=date_txt, before_window=bw, after_window=aw,
                           years_missing=";".join(f"FY{y}" for y in sorted(set(miss_b + miss_a))), data_complete=not (miss_b or miss_a),
                           years_outside_data=";".join(f"FY{y}" for y in beyond),
                           n_before=meta.get((f, s, i, "before"), 0), n_after=meta.get((f, s, i, "after"), 0),
                           placebos=";".join(str(int(p)) for p in pl),
                           same_day_changes=";".join(same_day(T, f, s, ch["date"])) if not ch["agency_dated"] else
                           ("simplified_acquisition" if f == "micro_purchase" else "micro_purchase") + " (most agencies, same memo)")
                reason = ""
                if res["before"] is None or res["after"] is None:
                    reason = "no awards in the " + ("before" if res["before"] is None else "after") + " window in the years present" + \
                             (f" (missing {row['years_missing']})" if row["years_missing"] else "")
                for per in ("before", "after"):
                    for tag, v in (("old", old), ("new", new)):
                        r = res[per][v] if res[per] else None
                        row[f"{tag}_{per}"] = r["ratio"] if r else np.nan
                        row[f"{tag}_{per}_obs"] = r["obs"] if r else 0
                        row[f"{tag}_{per}_exp"] = r["expected"] if r else np.nan
                        row[f"{tag}_{per}_n_window"] = r["n_window"] if r else 0
                        if r and not np.isfinite(r["ratio"]) and not reason:
                            reason = f"{r['note']} ({tag} line, {per})"
                if not reason:
                    rb, ra = res["before"], res["after"]
                    d_old = ra[old]["reps"] - rb[old]["reps"]
                    d_new = ra[new]["reps"] - rb[new]["reps"]
                    pdeltas = [ra[p]["reps"] - rb[p]["reps"] for p in pl if np.isfinite(ra[p]["ratio"]) and np.isfinite(rb[p]["ratio"])]
                    pts = dict(d_old=ra[old]["ratio"] - rb[old]["ratio"], d_new=ra[new]["ratio"] - rb[new]["ratio"])
                    pts["did"] = pts["d_new"] - pts["d_old"]
                    repd = dict(d_old=d_old, d_new=d_new, did=d_new - d_old)
                    if pdeltas:
                        pd_rep = np.nanmean(np.vstack(pdeltas), axis=0)
                        pts["d_placebo"] = float(np.mean([ra[p]["ratio"] - rb[p]["ratio"] for p in pl
                                                          if np.isfinite(ra[p]["ratio"]) and np.isfinite(rb[p]["ratio"])]))
                        repd["d_placebo"] = pd_rep
                    for k, val in pts.items():
                        row[k] = val
                        row[k + "_lo"], row[k + "_hi"] = ci(repd[k])
                    if not np.isfinite(row["did_lo"]):
                        reason = "too few awards for a stable interval"
                if not reason and not row["data_complete"]:
                    reason = f"waiting for {row['years_missing']} (not present yet); the numbers shown use part of the windows"
                if not reason and beyond:
                    row["note"] = f"window reaches {row['years_outside_data']}, outside the data: shortened"
                row["verdict"], row["reason"] = verdict(row, reason)
                rows.append(row)
    cols_first = ["family", "sector", "old_line", "new_line", "change_date", "verdict", "did", "did_lo", "did_hi", "d_old", "d_old_lo",
                  "d_old_hi", "d_new", "d_new_lo", "d_new_hi", "d_placebo", "d_placebo_lo", "d_placebo_hi", "old_before", "old_after",
                  "new_before", "new_after", "reason"]
    out = pd.DataFrame(rows)
    for c in cols_first:
        if c not in out:
            out[c] = np.nan
    return out[cols_first + [c for c in out.columns if c not in cols_first]]


def verdict(row, reason):
    if reason:
        return "not testable", reason
    lo, hi = row["did_lo"], row["did_hi"]
    rose = row["d_new_lo"] > 0
    fell = row["d_old_hi"] < 0
    shape = "the new line's pile-up grew and the old one's shrank" if rose and fell else \
        "the new line's pile-up grew" if rose else "the old line's pile-up shrank" if fell else "neither side alone is clear"
    if lo > 0:
        return "moved", shape
    if hi < 0:
        return "moved the wrong way", shape
    return "no clear move", shape


def level_contrasts(df, reps, fams, boot):
    """per family x sector: (1) ratio in years the line applied minus the same line's ratio in years when no family used that value,
    averaged over lines; (2) ratio in applied years minus the mean of the line's placebo values in the same year"""
    out = []
    for (f, s), d in df.groupby(["family", "sector"], sort=False):
        F = fams[f]
        lines = d[d.kind == "line"]
        diffs, dpts, exc, epts, n_act, n_in = [], [], [], [], 0, 0
        for L, dl in lines.groupby("value"):
            ok = dl[np.isfinite(dl.ratio)]
            act = ok[ok.status == "active"]
            ina = ok[(ok.status == "inactive") & (ok.other_family_active == "")]
            n_act += len(act)
            if len(act) and len(ina):
                n_in += len(ina)
                ra = np.nanmean(np.vstack([reps[(f, s, y, L)] for y in act.fy]), axis=0)
                ri = np.nanmean(np.vstack([reps[(f, s, y, L)] for y in ina.fy]), axis=0)
                diffs.append(ra - ri)
                dpts.append(act.ratio.mean() - ina.ratio.mean())
            for y, r in zip(act.fy, act.ratio):
                pv = [p for p in F["placebos"][L] if (f, s, y, p) in reps and np.isfinite(df_ratio(d, p, y))]
                if pv:
                    exc.append(reps[(f, s, y, L)] - np.nanmean(np.vstack([reps[(f, s, y, p)] for p in pv]), axis=0))
                    epts.append(r - np.mean([df_ratio(d, p, y) for p in pv]))
        row = dict(family=f, sector=s, active_cells=n_act, inactive_cells=n_in)
        if diffs:
            row["active_minus_inactive"] = round(float(np.mean(dpts)), 3)
            row["active_minus_inactive_ci"] = [round(x, 3) for x in ci(np.nanmean(np.vstack(diffs), axis=0))]
        if exc:
            row["active_minus_placebo"] = round(float(np.mean(epts)), 3)
            row["active_minus_placebo_ci"] = [round(x, 3) for x in ci(np.nanmean(np.vstack(exc), axis=0))]
        out.append(row)
    return out


def df_ratio(d, v, y):
    r = d[(d.value == v) & (d.fy == y)].ratio
    return float(r.iloc[0]) if len(r) else np.nan


def summary(df, ch, levels, files, fams, boot):
    def lab(r):
        miss = "" if r.data_complete else f" [incomplete: {r.years_missing} not present]"
        return f"{r.family} {r.sector}: ${r.old_line:,.0f} -> ${r.new_line:,.0f} ({r.change_date}){miss}"
    def why(r):
        if isinstance(r.years_missing, str) and r.years_missing and not r.reason.startswith(("too", "fit")):
            return "fiscal years not present yet"
        if "window in the years present" in r.reason:
            return "before or after window outside FY2008-FY2026"
        return "too few awards near the line"

    def stat(r):
        return f"DiD {r.did:+.2f} ({r.did_lo:+.2f} to {r.did_hi:+.2f}); {r.reason}"
    out = dict(
        generated_by="scripts/27_threshold_scan.py",
        fiscal_years_present=sorted(files), fiscal_years_missing=[y for y in range(2008, 2027) if y not in files],
        method=("Bunching ratio = awards in the four 2%-of-the-line bins just under a line / a cubic Poisson curve fitted outside the "
                "bins near any threshold value in force that year or the year before and outside bins holding a multiple of the line's "
                "leading power of ten. Window 0.6 to 1.4 times the line. Poisson bootstrap, " + f"{boot} replicates. Changes: two years "
                "before vs two years after (each agency's own date for the 2018 civilian change); DiD = change at the new line minus "
                "change at the old line."),
        moved=[lab(r) + " | " + stat(r) for r in ch[ch.verdict == "moved"].itertuples()],
        moved_wrong_way=[lab(r) + " | " + stat(r) for r in ch[ch.verdict == "moved the wrong way"].itertuples()],
        no_clear_move=[lab(r) + " | " + stat(r) for r in ch[ch.verdict == "no clear move"].itertuples()],
        not_testable=[dict(change=lab(r), why=why(r), reason=r.reason) for r in ch[ch.verdict == "not testable"].itertuples()],
        not_testable_counts=pd.Series([why(r) for r in ch[ch.verdict == "not testable"].itertuples()], dtype=object).value_counts().to_dict(),
        levels=levels,
        universes={f: FAMILIES[f][2] for f in FAMILIES},
        placebo_values={f: {str(int(L)): [int(p) for p in P] for L, P in F["placebos"].items()} for f, F in fams.items()},
        not_scanned=NOT_SCANNED,
        caveats=[
            "A pile-up is a pattern, not proof of why it happens: budgets written to fit the rules, set-aside rules tied to a line, "
            "and contract splitting all produce it.",
            "Many lines move on the same day (1 October of 2010, 2015, 2020 and 2025): see same_day_changes. A change test on one "
            "line is confounded by the others that moved with it, and the first day of a fiscal year has its own pattern.",
            "Justification level 1 and the subcontracting plan have had the same value since 2010 and change on the same days; "
            "their universes overlap, so their results are not independent.",
            "The FPDS reporting floor moves with the micro-purchase line, so counts just under it change for reporting reasons too.",
            "Samples near the larger lines (above about $10 million) are small; level 3 justifications and the 8(a) justification "
            "line are mostly too thin to test.",
            "Delivery orders and BPA calls are not in the data; contract vehicles are left out. Values are base and all options at "
            "award, which is close to but not the same as each rule's measure.",
            "Defense data are published 90 days late, so the latest Defense fiscal year is incomplete.",
            "Placebo values are round numbers that were never thresholds; near $3 million to $15 million thresholds are so dense "
            "that no free value exists, and those lines rely on the active-versus-inactive comparison only.",
        ])
    return out


def main():
    boot = B
    if "--boot" in sys.argv:
        boot = int(sys.argv[sys.argv.index("--boot") + 1])
    files = year_files()
    if not files:
        sys.exit("no history tables: run `python run.py get-history` (or the history workflow) first")
    print(f"fiscal years present: {', '.join(f'FY{y}' for y in sorted(files))}")
    missing = [y for y in range(2008, 2027) if y not in files]
    if missing:
        print(f"fiscal years not present (yet): {', '.join(f'FY{y}' for y in missing)}")
    T = load_thresholds()
    adoption = load_adoption()
    fams, _ = setup(T)
    t0 = time.time()
    scan, meta, chg = accumulate(files, T, adoption, fams)
    df, reps = scan_rows(T, fams, scan, meta, adoption, boot)
    df.to_csv(os.path.join(RESULTS, "threshold_scan.csv"), index=False)
    ch = change_rows(T, fams, chg, meta, files, adoption, boot)
    ch.to_csv(os.path.join(RESULTS, "threshold_scan_changes.csv"), index=False)
    levels = level_contrasts(df, reps, fams, boot)
    summ = summary(df, ch, levels, files, fams, boot)
    with open(os.path.join(RESULTS, "threshold_scan_summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summ, fh, indent=1, default=float)
    plot(df, T, fams)
    print_summary(df, ch, levels)
    print(f"done in {time.time() - t0:.0f}s: results/threshold_scan.csv, results/threshold_scan_changes.csv, "
          "results/threshold_scan_summary.json, figures/threshold_scan.png")


def print_summary(df, ch, levels):
    print("\nPile-up at the line in force (civilian / Defense ratio, by fiscal year):")
    for f in FAMILIES:
        d = df[(df.family == f) & (df.kind == "line") & (df.status == "active") & df.sector.isin(SECTORS)]
        if d.empty:
            continue
        parts = []
        for fy, g in d.groupby("fy"):
            txt = "/".join(f"{g[g.sector == s].ratio.iloc[0]:.2f}" if (g.sector == s).any() and np.isfinite(g[g.sector == s].ratio.iloc[0])
                           else "-" for s in SECTORS)
            parts.append(f"FY{fy} {txt}")
        print(f"  {FAMILIES[f][0]}: " + "; ".join(parts))
    print("\nLevels (active years minus inactive years of the same lines; active years minus placebo values):")
    for r in levels:
        a = f"{r['active_minus_inactive']:+.2f} {r['active_minus_inactive_ci']}" if "active_minus_inactive" in r else "n/a"
        p = f"{r['active_minus_placebo']:+.2f} {r['active_minus_placebo_ci']}" if "active_minus_placebo" in r else "n/a"
        print(f"  {r['family']:34s} {r['sector']:9s} vs inactive {a:28s} vs placebo {p}")
    print("\nChanges:")
    for v in ("moved", "moved the wrong way", "no clear move", "not testable"):
        d = ch[ch.verdict == v]
        print(f"  {v}: {len(d)}")
        for r in d.itertuples():
            extra = f"DiD {r.did:+.2f} ({r.did_lo:+.2f} to {r.did_hi:+.2f})" if np.isfinite(r.did) else r.reason
            print(f"    {r.family} {r.sector} ${r.old_line:,.0f} -> ${r.new_line:,.0f} ({r.change_date}): {extra}")


# ----------------------------------------------------------------------------------------------------- figure
def plot(df, T, fams):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    colors = {"civilian": "#2a78d6", "defense": "#eb6834"}
    fl = [f for f in FAMILIES if (df.family == f).any()]
    ncol = 4
    nrow = math.ceil(len(fl) / ncol)
    fig, axes = plt.subplots(nrow, ncol, figsize=(4.3 * ncol, 3.0 * nrow + 0.9), squeeze=False)
    years = list(range(int(df.fy.min()), int(df.fy.max()) + 1))          # missing years leave gaps in the lines
    names = {"civilian": "Civilian", "defense": "Defense"}
    for ax, f in zip(axes.flat, fl):
        d = df[df.family == f]
        top = 2.0
        for s in SECTORS:
            e = d[(d.sector == s) & (d.kind == "line")]
            if e.empty:
                continue
            cur = e[e.status.isin(["active", "partial"])].sort_values(["fy", "active_share"]).groupby("fy").tail(1)
            cur = cur.set_index("fy").reindex(years)
            ax.plot(years, cur.ratio, color=colors[s], lw=2, label=f"{names[s]}: line in force")
            full = cur.status == "active"
            ax.scatter(np.array(years)[full], cur.ratio[full], color=colors[s], s=16, zorder=3)
            ax.scatter(np.array(years)[~full], cur.ratio[~full], facecolor="white", edgecolor=colors[s], s=16, zorder=3, lw=1.2)
            band = (cur.ci_hi - cur.ci_lo) < 3                                  # very wide intervals would hide everything else
            ax.fill_between(years, cur.ci_lo.where(band), cur.ci_hi.where(band), color=colors[s], alpha=0.13, lw=0)
            # the value in force just before: shows whether the old pile-up fades
            prev = []
            for y in years:
                p = periods(T, f, s)
                now = cur.loc[y, "value"] if np.isfinite(cur.loc[y, "value"]) else np.nan
                vals = [v for v, st, en in p if en < fy_bounds(y)[1] and v != now]
                pv = vals[-1] if vals else np.nan
                r = e[(e.fy == y) & (e.value == pv)].ratio
                prev.append(float(r.iloc[0]) if len(r) else np.nan)
            ax.plot(years, prev, color=colors[s], lw=1, ls="--", label=f"{names[s]}: previous value")
            pl = d[(d.sector == s) & (d.kind == "placebo")].groupby("fy").ratio.mean().reindex(years)
            ax.plot(years, pl, color=colors[s], lw=0.9, ls=":", label=f"{names[s]}: never-threshold round values")
            top = max(top, np.nanmax(np.concatenate([cur.ratio.to_numpy(float), [0]])) * 1.1)
        if not np.isfinite(d[d.sector.isin(SECTORS)].ratio).any():
            ax.text(0.5, 0.55, "too few awards near these lines\nto measure a pile-up", transform=ax.transAxes, ha="center",
                    fontsize=8, color="#555555")
        for s in SECTORS:
            for c in fams[f]["changes"].get(s, []):
                x = pd.Timestamp(c["date"])
                xf = x.year + (x.month >= 10) - 0.5 + ((x.month - 10) % 12) / 12
                if c["agency_dated"]:
                    ax.axvspan(2017.5, 2020.5, color="#999999", alpha=0.12, lw=0)
                elif years[0] - 0.5 <= xf <= years[-1] + 0.5:
                    ax.axvline(xf, color="#777777", lw=0.7, ls="-" if s == "civilian" else "-.")
        ax.axhline(1, color="#aaaaaa", lw=0.8)
        ax.set_ylim(0, min(top, 8))
        ax.set_xlim(years[0] - 0.6, years[-1] + 0.6)
        ax.set_title(FAMILIES[f][0], fontsize=9)
        ax.tick_params(labelsize=7)
        ax.spines[["top", "right"]].set_visible(False)
    for ax in axes.flat[len(fl):]:
        ax.axis("off")
    h, lab = axes.flat[0].get_legend_handles_labels()
    fig.legend(h, lab, loc="lower center", ncol=6, frameon=False, fontsize=8)
    fig.suptitle("Pile-ups just under each FAR dollar threshold, by fiscal year (bunching ratio; 1.0 = no pile-up)", fontsize=11)
    fig.text(0.5, 0.035, "Solid: the value in force (filled dot: all year; open dot: part of the year; band: 95% interval). Dashed: the value "
             "in force before it. Dotted: round values that were never thresholds. Grey lines: the line moved (dash-dot: Defense only). "
             "Grey band: 2018-2020 civilian transition.", ha="center", fontsize=8)
    fig.tight_layout(rect=(0, 0.06, 1, 0.97))
    os.makedirs(os.path.join(PROJECT, "figures"), exist_ok=True)
    fig.savefig(os.path.join(PROJECT, "figures", "threshold_scan.png"), dpi=130)
    plt.close(fig)


if __name__ == "__main__":
    main()
