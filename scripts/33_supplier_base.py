"""
33_supplier_base.py - who wins the awards: the supplier base, FY2008 onward, from the yearly history tables.

Questions (each per fiscal year and sector: civilian except GSA, GSA, Department of Defense, and all sectors together)
  1. Size.  How many different companies won at least one new award?  Two series: all awards, and awards above $25,000 (the headline
     series, because reporting of very small purchases changed over time, for example the FY2008 Veterans Affairs micro-purchases;
     dollars are not adjusted for inflation).  Also by award kind (purchase orders, definitive contracts, contract vehicles).
  2. New entrants.  A company is new in year t if it won no award in the three previous fiscal years (a fixed look-back, so every year is
     measured the same way; the first measurable year is the fourth year present).  Sensitivity: never seen in any earlier year present
     (since FY2008).  Reported: new companies, and their share of companies, of awards and of initial dollars.  In the above-$25,000
     series "won an award" means an award above $25,000 throughout (look-back and staying too).
  3. Staying.  Of the companies that won in year t, the share that won again in t+1, and within t+1..t+3 (only when those years are
     present and complete).
  4. Concentration.  Share of initial dollars won by the top 1% and the top 10% of companies.  Market concentration: for each 4-digit
     NAICS industry with at least 20 awards in the year, the Herfindahl-Hirschman index (HHI: the sum of the squared percentage shares
     of initial dollars, 0 to 10,000; the U.S. merger guidelines of 2023 call a market above 1,800 highly concentrated, and the older
     guidelines used 2,500); reported only as the award-weighted median and the share of awards in markets above 2,500.
     Dollar measures (these two, and the new companies' share of dollars) use purchase orders and definitive contracts only: a
     contract vehicle's initial value is a ceiling for future orders, often billions, not money to be spent.  Initial values above
     $10 billion are left out of dollar measures as likely recording errors (a few a year; counted in the concentration table), and
     the top shares are repeated with the dollars obligated at award as a check.
  5. Link to competition.  Single-offer share of competed awards (the eval set of 19_history_atlas.py) won by new companies versus
     established ones, with 95% Wilson intervals; the new-company share of awards by set-aside group (groups of 23_drivers.py); and the
     new-company share among awards with one offer versus several.

Company identity and the identifier switch
  Until April 2022 companies were identified by DUNS numbers, after that by UEI.  vendor_id (18_history_build.py) is made from the UEI
  when the record has one, otherwise from the DUNS, so one company could carry two ids and look "new" around 2022.  vendor_uei_code and
  vendor_duns_code are built with the same hash, so every vendor_id equals one of them.  Wherever a year's table has both columns, each
  row that carries both links the two codes (union-find over all such rows of all years: one company id per linked group); every
  vendor_id of every year, with or without the columns, is then mapped to its group.  The script measures how much is left to fix:
  for years with the columns, the share of awards whose company carries only a DUNS that no row links to a UEI.  A year whose look-back
  or follow-up window spans the switch is marked "not comparable" (column `note`, and on the chart) unless every year of the window
  has the columns and that unlinked share stays under 1%.  The unlinked series (raw vendor_id) is kept beside the linked one, so the
  size of the correction can be read directly.  No rows are dropped.

Privacy
  Company codes and buying-office codes are used inside the computation only.  No output names or codes a company or an office, no
  statistic describes one company, and every cell that rests on fewer than 10 companies is left blank (`note` says so).

Known limits (also in the summary): one company can have several identifiers (UEIs are per registered entity), and parents and
subsidiaries count as different companies; prime awards only; orders under existing contract vehicles (delivery and task orders) are
not in these tables, so companies that win only orders are missing; the latest Defense year can be incomplete (90-day delay).

Input : data/history/processed/base_actions_FY*.parquet (18_history_build.py), optional reference/uei_backfill_check.json
Output: results/supplier_base_by_fy.csv, results/supplier_base_concentration.csv, results/supplier_base_competition.csv,
        results/supplier_base_summary.json, figures/supplier_base.png
Usage : python -I scripts/33_supplier_base.py
"""
import glob
import importlib.util
import json
import math
import os
import re
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA, PROJECT, RESULTS  # noqa: E402


def _load(name, file):
    """import a numbered script of this folder by path (its file name starts with a digit)"""
    spec = importlib.util.spec_from_file_location(name, os.path.join(os.path.dirname(os.path.abspath(__file__)), file))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_drivers = _load("drivers23", "23_drivers.py")
_atlas = _load("atlas19", "19_history_atlas.py")
SA_MAP = _drivers.SA_MAP                      # set-aside groups, as in 23_drivers.py and 03b_atlas_civilian.py
dod_incomplete = _drivers.dod_incomplete      # a Defense year whose September is missing or thin
peak_mb = _drivers.peak_mb
KNOWN_ISSUES, MICRO = _atlas.KNOWN_ISSUES, _atlas.MICRO     # FY2008 Veterans Affairs micro-purchases (19_history_atlas.py)

GSA = "General Services Administration"
SECTORS = ["civilian except GSA", "GSA", "Department of Defense"]
ALL = "all sectors"
SCOPES = {"all": "all awards", "above_25k": "awards above $25,000"}
KINDS = ["all", "PO", "DEF", "IDV"]
CUT = 25_000
LOOKBACK = 3
MIN_COMPANIES = 10           # cells resting on fewer companies are left blank
MIN_MARKET_AWARDS = 20       # a 4-digit NAICS market needs this many awards in the year to get an HHI
HHI_HIGH = 2_500
SWITCH_FY = 2022             # UEI replaced DUNS in April 2022 (inside FY2022)
UNLINKED_OK = 0.01           # a year is "linked well enough" when under 1% of its awards carry a DUNS-only id that no row links
SUPPRESSED = "suppressed: fewer than 10 companies"
VALUE_CAP = 10e9            # dollar measures leave out initial values above $10 billion (mostly recording errors; counted)
DOLLAR_KINDS = ("PO", "DEF")  # contract vehicles (IDV) carry a ceiling, not dollars to be spent: left out of dollar measures
READ_COLS = ["awarding_agency_name", "is_dod", "award_kind", "base_and_all_options_value", "federal_action_obligation", "eval_set", "single",
             "type_of_set_aside_code", "naics_code", "vendor_id"]
LINK_COLS = ["vendor_uei_code", "vendor_duns_code"]
BY_FY_COLS = ["fy", "sector", "award_scope", "award_kind", "n_awards", "n_awards_no_company_id", "n_companies", "n_companies_unlinked_ids",
              "initial_dollars", "n_new_companies", "new_share_companies", "new_share_awards", "new_share_dollars",
              "new_share_companies_unlinked_ids", "new_share_companies_never_seen", "stay_share_next_year",
              "stay_share_next_year_unlinked_ids", "stay_share_within_3_years", "new_measures_comparable", "stay_next_year_comparable",
              "stay_3_years_comparable", "note"]


# ======================================================================================================================================
# small pure helpers (the tests call them on synthetic data)
# ======================================================================================================================================
def hex_codes(x):
    """a Series of 16-hex-digit codes (or blanks) -> uint64 array; 0 for blank or missing (no real code is 0 in practice)."""
    c = x.astype("category") if not isinstance(x.dtype, pd.CategoricalDtype) else x
    cats = [int(s, 16) if isinstance(s, str) and re.fullmatch(r"[0-9a-fA-F]{1,16}", s.strip() or "-") else 0
            for s in c.cat.categories.astype(str)]
    lut = np.array(cats + [0], dtype=np.uint64)
    codes = c.cat.codes.to_numpy().astype(np.int64)
    return lut[np.where(codes < 0, len(cats), codes)]


def link_groups(a, b):
    """union-find over pairs (a[i], b[i]) of uint64 codes: returns (nodes, root) sorted by node, where root is the smallest code of the
    node's linked group.  Pairs with a 0 are ignored."""
    a, b = np.asarray(a, dtype=np.uint64), np.asarray(b, dtype=np.uint64)
    ok = (a != 0) & (b != 0)
    a, b = a[ok], b[ok]
    if not len(a):
        return np.array([], dtype=np.uint64), np.array([], dtype=np.uint64)
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    nodes, inv = np.unique(np.concatenate([a, b]), return_inverse=True)
    m = len(nodes)
    g = coo_matrix((np.ones(len(a), dtype=np.int8), (inv[:len(a)], inv[len(a):])), shape=(m, m))
    _, lab = connected_components(g, directed=False)
    root_of_label = np.full(lab.max() + 1, np.iinfo(np.uint64).max, dtype=np.uint64)
    np.minimum.at(root_of_label, lab, nodes)
    return nodes, root_of_label[lab]


def canon(ids, nodes, root):
    """map codes to their linked group's root; codes not in any link map to themselves."""
    ids = np.asarray(ids, dtype=np.uint64)
    if not len(nodes):
        return ids.copy()
    pos = np.clip(np.searchsorted(nodes, ids), 0, len(nodes) - 1)
    hit = nodes[pos] == ids
    return np.where(hit, root[pos], ids)


def window_years(t, years, back=True):
    """the three years before t (back) or after t; None when any of them is missing."""
    w = [t - k for k in range(1, LOOKBACK + 1)] if back else [t + k for k in range(1, LOOKBACK + 1)]
    return w if all(y in years for y in w) else None


def unique_pairs(a, b):
    """the distinct (a, b) pairs of two uint64 arrays"""
    if not len(a):
        return a, b
    p = np.unique(np.stack([a, b], axis=1), axis=0)
    return p[:, 0].copy(), p[:, 1].copy()


def union_sets(sets):
    return np.unique(np.concatenate(sets)) if sets else np.array([], dtype=np.uint64)


def top_share(dollars, frac):
    """share of the total held by the top `frac` of companies (at least one company); NaN when the total is not positive.
    Returns (share, number of companies in the top group)."""
    d = np.sort(np.asarray(dollars, dtype=float))[::-1]
    tot = d.sum()
    k = max(1, math.ceil(frac * len(d)))
    return (float(d[:k].sum() / tot) if tot > 0 else float("nan")), k


def hhi_markets(market, company, dollars, min_awards=MIN_MARKET_AWARDS):
    """per market with at least min_awards awards and positive dollars: HHI of dollars (0-10,000), awards, companies.
    market and company are integer arrays (market < 0 = unknown, left out)."""
    df = pd.DataFrame({"m": market, "c": company, "d": np.clip(np.nan_to_num(np.asarray(dollars, dtype=float)), 0, None)})
    df = df[df.m >= 0]
    if not len(df):
        return pd.DataFrame(columns=["market", "n_awards", "n_companies", "hhi"])
    n = df.groupby("m").size()
    keep = n[n >= min_awards].index
    df = df[df.m.isin(keep)]
    by = df.groupby(["m", "c"], sort=False).d.sum().reset_index()
    tot = by.groupby("m").d.transform("sum")
    by = by[tot > 0]
    by["s2"] = (100 * by.d / tot[tot > 0]) ** 2
    out = by.groupby("m").agg(hhi=("s2", "sum"), n_companies=("c", "size")).reset_index().rename(columns={"m": "market"})
    out["n_awards"] = out.market.map(n).astype(int)
    return out[["market", "n_awards", "n_companies", "hhi"]]


def weighted_median(x, w):
    x, w = np.asarray(x, dtype=float), np.asarray(w, dtype=float)
    if not len(x) or w.sum() <= 0:
        return float("nan")
    o = np.argsort(x)
    cw = np.cumsum(w[o])
    return float(x[o][np.searchsorted(cw, 0.5 * cw[-1])])


def wilson(k, n):
    """95% Wilson interval (the same calculation as atlas_lib.share_table)."""
    from statsmodels.stats.proportion import proportion_confint
    lo, hi = proportion_confint(np.asarray(k), np.asarray(n), alpha=0.05, method="wilson")
    return lo, hi


def sector_codes(agency, is_dod):
    """0 = civilian except GSA, 1 = GSA, 2 = Department of Defense"""
    ag = agency.astype(str).to_numpy() if not isinstance(agency.dtype, pd.CategoricalDtype) else None
    if ag is None:
        cats = np.asarray(agency.cat.categories.astype(str))
        codes = agency.cat.codes.to_numpy()
        is_gsa = np.append(cats == GSA, False)[np.where(codes < 0, len(cats), codes)]
    else:
        is_gsa = ag == GSA
    return np.where(np.asarray(is_dod, dtype=bool), 2, np.where(is_gsa, 1, 0)).astype(np.int8)


def setaside_group(x):
    c = x.astype("category") if not isinstance(x.dtype, pd.CategoricalDtype) else x
    lab = pd.Series(c.cat.categories.astype(str)).str.strip().map(SA_MAP).fillna("other/blank").tolist() + ["other/blank"]
    codes = c.cat.codes.to_numpy().astype(np.int64)
    return np.asarray(lab, dtype=object)[np.where(codes < 0, len(lab) - 1, codes)]


def naics4_codes(x):
    """integer code of the 4-digit NAICS industry (-1 when blank or not numeric)"""
    c = x.astype("category") if not isinstance(x.dtype, pd.CategoricalDtype) else x
    s = pd.Series(c.cat.categories.astype(str)).str.strip().str[:4]
    lab = np.where(s.str.fullmatch(r"\d{4}").fillna(False), pd.to_numeric(s, errors="coerce").fillna(-1), -1).astype(np.int64).tolist() + [-1]
    codes = c.cat.codes.to_numpy().astype(np.int64)
    return np.asarray(lab, dtype=np.int64)[np.where(codes < 0, len(lab) - 1, codes)]


def year_of(path):
    return int(re.search(r"FY(\d{4})", os.path.basename(path)).group(1))


def read_cols(path, cols):
    """the columns of one yearly table that exist, text columns as categoricals (compact)."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    have = set(pq.ParquetFile(path).schema_arrow.names)
    out = {}
    for c in [c for c in cols if c in have]:          # one column at a time, as in 23_drivers.py: keeps the peak low
        t = pq.read_table(path, columns=[c], read_dictionary=[c] if c in ("vendor_id", "naics_code", *LINK_COLS) else None)
        out[c] = t.column(c).to_pandas()
        del t
        pa.default_memory_pool().release_unused()
    return pd.DataFrame(out)


# ======================================================================================================================================
# pass 1: company ids of every year, the links between DUNS and UEI codes, and what the switch leaves unlinked
# ======================================================================================================================================
def scan_ids(paths):
    """per year: raw vendor_id codes (uint64) per row, the value of each row, whether the year has the link columns, the link pairs,
    and simple diagnostics.  Returns (per-year dict, pairs a, pairs b)."""
    info, pa, pb = {}, [], []
    for y, p in sorted(paths.items()):
        b = read_cols(p, ["vendor_id", *LINK_COLS, "base_and_all_options_value", "action_date", "is_dod"])
        vid = hex_codes(b["vendor_id"]) if "vendor_id" in b else np.zeros(len(b), dtype=np.uint64)
        has_cols = all(c in b.columns for c in LINK_COLS)
        rec = {"rows": int(len(b)), "has_link_columns": has_cols, "rows_without_company_id": int((vid == 0).sum()),
               "incomplete_defense": bool(dod_incomplete(b.action_date, b.is_dod.fillna(False).to_numpy(bool)))}
        if has_cols:
            u, d = hex_codes(b["vendor_uei_code"]), hex_codes(b["vendor_duns_code"])
            both = (u != 0) & (d != 0)
            pairs = unique_pairs(u[both], d[both])        # distinct pairs only: keeps memory low when every year has them
            pa.append(pairs[0])
            pb.append(pairs[1])
            rec.update(share_with_uei=float((u != 0).mean()) if len(b) else None,
                       share_with_duns=float((d != 0).mean()) if len(b) else None,
                       share_duns_only=float(((d != 0) & (u == 0)).mean()) if len(b) else None)
            rec["_duns_only_ids"] = np.unique(d[(d != 0) & (u == 0)])
        v = pd.to_numeric(b.base_and_all_options_value, errors="coerce").to_numpy()
        rec["_vid"] = vid
        rec["_above"] = np.nan_to_num(v, nan=0.0) > CUT
        info[y] = rec
        del b
    a, bb = unique_pairs(np.concatenate(pa), np.concatenate(pb)) if pa else (np.array([], dtype=np.uint64),) * 2
    return info, a, bb


def switch_diagnostics(info, nodes, root, uei_codes):
    """per year: share of awards whose id is a DUNS-only code that no row links to a UEI (years with the columns), and for years without
    the columns the share of awards whose id matches a known DUNS code (a DUNS-based id) or a known UEI code."""
    out = {}
    duns_known = np.unique(np.concatenate([r["_duns_only_ids"] for r in info.values() if "_duns_only_ids" in r] +
                                          [np.array([], dtype=np.uint64)]))
    linked_duns = nodes[np.isin(nodes, uei_codes, invert=True)] if len(nodes) else nodes
    for y, r in info.items():
        vid = r["_vid"]
        d = {"rows": r["rows"], "has_link_columns": r["has_link_columns"], "rows_without_company_id": r["rows_without_company_id"],
             "incomplete_defense": r["incomplete_defense"]}
        if r["has_link_columns"]:
            unl = np.isin(vid, r["_duns_only_ids"]) & ~np.isin(vid, linked_duns)
            d.update(share_with_uei=r["share_with_uei"], share_with_duns=r["share_with_duns"],
                     share_duns_only=r["share_duns_only"], share_duns_only_unlinked=float(unl.mean()) if len(vid) else 0.0)
        else:
            dmatch = np.isin(vid, np.union1d(duns_known, linked_duns))
            umatch = np.isin(vid, uei_codes)
            d.update(share_id_matching_a_known_duns_code=float(dmatch.mean()) if len(vid) else 0.0,
                     share_id_matching_a_known_uei_code=float(umatch.mean()) if len(vid) else 0.0)
        out[y] = d
    return out


def spans_switch(window):
    """True when a window of fiscal years has years on both sides of the switch (it contains FY2022 and another year)"""
    return bool(window) and min(window) <= SWITCH_FY <= max(window) and min(window) < max(window)


def window_ok(window, diag):
    """(True, '') when a window of years can be compared across the identifier switch; otherwise (False, reason).  Years after FY2022
    carry only UEIs, so only the window's years up to FY2022 need the link columns and a low unlinked share."""
    if not spans_switch(window):
        return True, ""
    early = [y for y in window if y <= SWITCH_FY]
    missing = [y for y in early if not diag.get(y, {}).get("has_link_columns")]
    if missing:
        return False, ("not comparable: company identifiers changed from DUNS to UEI in April 2022, and the tables of "
                       + ", ".join(f"FY{y}" for y in sorted(missing)) + " cannot link the two yet")
    bad = [y for y in early if (diag[y].get("share_duns_only_unlinked") or 0) >= UNLINKED_OK]
    if bad:
        return False, ("not comparable: company identifiers changed from DUNS to UEI in April 2022, and in "
                       + ", ".join(f"FY{y} {100 * diag[y]['share_duns_only_unlinked']:.1f}%" for y in bad)
                       + " of awards carry a DUNS that no record links to a UEI")
    return True, ""


# ======================================================================================================================================
# pass 2: one year at a time
# ======================================================================================================================================
def cell_stats(cid, rid, dollars, newf, newf_raw, ever_new, keep, next1, next3, next1_raw):
    """company and award counts for the rows in `keep` (boolean mask).  newf etc. are per-row flags; next1/next3 are sorted id sets
    (or None)."""
    c = cid[keep]
    has = c != 0
    c_ok = c[has]
    comp, first = np.unique(c_ok, return_index=True)
    n_comp = len(comp)
    v = dollars[keep]
    out = {"n_awards": int(keep.sum()), "n_awards_no_company_id": int((~has).sum()), "n_companies": int(n_comp),
           "n_companies_unlinked_ids": int(len(np.unique(rid[keep][rid[keep] != 0]))),
           "initial_dollars": float(v.sum())}
    if newf is not None:
        nf = newf[keep]
        out["n_new_companies"] = int(nf[has][first].sum()) if n_comp else 0
        out["new_share_companies"] = out["n_new_companies"] / n_comp if n_comp else np.nan
        out["new_share_awards"] = float(nf[has].mean()) if has.any() else np.nan
        dv = v[has]
        out["new_share_dollars"] = float(dv[nf[has]].sum() / dv.sum()) if dv.sum() > 0 else np.nan
        r = rid[keep]
        rh = r != 0
        ru, rfirst = np.unique(r[rh], return_index=True)
        out["new_share_companies_unlinked_ids"] = float(newf_raw[keep][rh][rfirst].mean()) if len(ru) else np.nan
    if ever_new is not None:
        ef = ever_new[keep]
        out["new_share_companies_never_seen"] = float(ef[has][first].mean()) if n_comp else np.nan
    if next1 is not None:
        out["stay_share_next_year"] = float(np.isin(comp, next1).mean()) if n_comp else np.nan
        r = rid[keep]
        ru = np.unique(r[r != 0])
        out["stay_share_next_year_unlinked_ids"] = float(np.isin(ru, next1_raw).mean()) if len(ru) else np.nan
    if next3 is not None:
        out["stay_share_within_3_years"] = float(np.isin(comp, next3).mean()) if n_comp else np.nan
    return out


def year_tables(y, b, cid, rid, sets, years, complete, diag, issue_notes):
    """all output rows of one fiscal year.  sets[(year, scope, 'canon'|'raw')] = sorted unique ids."""
    sector = sector_codes(b.awarding_agency_name, b.is_dod.fillna(False))
    kind = b.award_kind.astype(str).to_numpy()
    val = pd.to_numeric(b.base_and_all_options_value, errors="coerce").to_numpy(dtype=float)
    above = np.nan_to_num(val, nan=0.0) > CUT
    dollars, obl, podef, over_cap = dollar_columns(kind, val, b)
    ev = b.eval_set.fillna(False).to_numpy(bool)
    single = b.single.fillna(False).to_numpy(bool)
    sa = setaside_group(b.type_of_set_aside_code)
    n4 = naics4_codes(b.naics_code)
    back, fwd = window_years(y, years, True), window_years(y, years, False)
    fwd_ok = fwd is not None and all(complete.get(t, False) for t in fwd)
    one_ok = (y + 1) in years and complete.get(y + 1, False)
    bok, bwhy = window_ok(back + [y] if back else None, diag)
    f1ok, f1why = window_ok([y, y + 1], diag)
    f3ok, f3why = window_ok([y] + fwd if fwd else None, diag)
    earlier = [t for t in years if t < y]
    by_fy, conc, comp_rows = [], [], []
    for scope in SCOPES:
        in_scope = above if scope == "above_25k" else np.ones(len(b), dtype=bool)
        if back:
            prior = union_sets([sets[(t, scope, "canon")] for t in back])
            prior_raw = union_sets([sets[(t, scope, "raw")] for t in back])
            newf, newf_raw = ~np.isin(cid, prior), ~np.isin(rid, prior_raw)
        else:
            newf = newf_raw = None
        ever = ~np.isin(cid, union_sets([sets[(t, scope, "canon")] for t in earlier])) if earlier else None
        n1 = sets[(y + 1, scope, "canon")] if one_ok else None
        n1r = sets[(y + 1, scope, "raw")] if one_ok else None
        n3 = union_sets([sets[(t, scope, "canon")] for t in fwd]) if fwd_ok else None
        for si, sname in enumerate(SECTORS + [ALL]):
            in_sec = in_scope & ((sector == si) if si < 3 else True)
            for k in KINDS:
                keep = in_sec & ((kind == k) if k != "all" else True)
                if not keep.any():
                    continue
                st = cell_stats(cid, rid, dollars, newf, newf_raw, ever, keep, n1, n3, n1r)
                notes = []
                if sname in (SECTORS[2], ALL) and not complete.get(y, True):
                    notes.append("incomplete year: Defense data for the last months are not published yet")
                notes += issue_notes.get((y, scope, sname), [])
                if newf is not None and not bok:
                    notes.append("new-company measures " + bwhy)
                if one_ok and not f1ok:
                    notes.append("next-year staying " + f1why)
                if fwd_ok and not f3ok:
                    notes.append("three-year staying " + f3why)
                row = {"fy": y, "sector": sname, "award_scope": scope, "award_kind": k, **st}
                row["new_measures_comparable"] = bool(bok) if newf is not None else None
                row["stay_next_year_comparable"] = bool(f1ok) if one_ok else None
                row["stay_3_years_comparable"] = bool(f3ok) if fwd_ok else None
                row["note"] = "; ".join(notes)
                by_fy.append(row)
            # ---- concentration (purchase orders and definitive contracts; initial value at or under the cap) -----------------------
            keep = in_sec & (cid != 0) & podef
            c = cid[keep]
            if not len(c):
                continue
            comp, inv = np.unique(c, return_inverse=True)
            dol = np.bincount(inv, weights=dollars[keep])
            t1, k1 = top_share(dol, 0.01)
            t10, k10 = top_share(dol, 0.10)
            ob = np.bincount(inv, weights=obl[keep])
            o1, _ = top_share(ob, 0.01)
            o10, _ = top_share(ob, 0.10)
            mk = hhi_markets(n4[keep], inv, dollars[keep])
            crow = {"fy": y, "sector": sname, "award_scope": scope, "n_companies": int(len(comp)), "n_awards": int(keep.sum()),
                    "awards_over_cap_left_out": int((in_sec & over_cap).sum()), "initial_dollars": float(dol.sum()),
                    "top1pct_companies": k1, "top1pct_share_dollars": t1 if k1 >= MIN_COMPANIES else np.nan,
                    "top10pct_companies": k10, "top10pct_share_dollars": t10 if k10 >= MIN_COMPANIES else np.nan,
                    "top1pct_share_obligated_at_award": o1 if k1 >= MIN_COMPANIES else np.nan,
                    "top10pct_share_obligated_at_award": o10 if k10 >= MIN_COMPANIES else np.nan,
                    "n_markets": int(len(mk)), "awards_in_markets": int(mk.n_awards.sum()) if len(mk) else 0,
                    "awards_without_naics": int((n4[keep] < 0).sum()),
                    "hhi_median_award_weighted": weighted_median(mk.hhi, mk.n_awards) if len(mk) else np.nan,
                    "share_awards_in_markets_hhi_above_2500": float(mk.n_awards[mk.hhi > HHI_HIGH].sum() / mk.n_awards.sum())
                    if len(mk) else np.nan}
                    # markets are summarized only: no single market is ever written out
            notes = []
            if k1 < MIN_COMPANIES:
                notes.append("top 1% share " + SUPPRESSED)
            if len(comp) < MIN_COMPANIES:
                crow.update(top10pct_share_dollars=np.nan, top10pct_share_obligated_at_award=np.nan, initial_dollars=np.nan, hhi_median_award_weighted=np.nan, share_awards_in_markets_hhi_above_2500=np.nan)
                notes.append(SUPPRESSED)
            if sname in (SECTORS[2], ALL) and not complete.get(y, True):
                notes.append("incomplete year: Defense data for the last months are not published yet")
            notes += [n for n in issue_notes.get((y, scope, sname), []) if not n.startswith("new-company")]
            crow["note"] = "; ".join(notes)
            conc.append(crow)
            # ---- link to competition (needs the look-back) ------------------------------------------------------------------------
            if newf is None:
                continue
            note_c = "" if bok else "new-company measures " + bwhy
            if sname in (SECTORS[2], ALL) and not complete.get(y, True):
                note_c = "; ".join(x for x in (note_c, "incomplete year: Defense data for the last months are not published yet") if x)
            keep = in_sec & (cid != 0)                   # every award kind, as in the single-offer history
            m = keep & ev
            for grp, gm in (("new companies", newf), ("established companies", ~newf)):
                sel = m & gm
                comp_rows.append(dict(fy=y, sector=sname, award_scope=scope, measure="single_offer_share", group=grp,
                                      k=int(single[sel].sum()), n=int(sel.sum()), n_companies=int(len(np.unique(cid[sel]))), note=note_c))
            for g in sorted(set(sa[keep])):
                sel = keep & (sa == g)
                comp_rows.append(dict(fy=y, sector=sname, award_scope=scope, measure="new_company_share_by_setaside", group=g,
                                      k=int(newf[sel].sum()), n=int(sel.sum()), n_companies=int(len(np.unique(cid[sel]))), note=note_c))
            for grp, gm in (("one offer", single), ("two or more offers", ~single)):
                sel = m & gm
                comp_rows.append(dict(fy=y, sector=sname, award_scope=scope, measure="new_company_share_by_offers", group=grp,
                                      k=int(newf[sel].sum()), n=int(sel.sum()), n_companies=int(len(np.unique(cid[sel]))), note=note_c))
    return by_fy, conc, comp_rows


def dollar_columns(kind, val, b):
    """dollars used by the dollar measures: initial value of purchase orders and definitive contracts, negative as zero, values above
    the cap left out; the same for dollars obligated at award (a robustness check).  Returns (dollars, obligated, PO/DEF mask, over-cap
    mask)."""
    podef = np.isin(kind, DOLLAR_KINDS)
    v = np.nan_to_num(val, nan=0.0)
    over = podef & (v > VALUE_CAP)
    dollars = np.where(podef & ~over, np.clip(v, 0, None), 0.0)
    o = pd.to_numeric(b.federal_action_obligation, errors="coerce").to_numpy(dtype=float) if "federal_action_obligation" in b else np.zeros(len(v))
    o = np.nan_to_num(o, nan=0.0)
    obl = np.where(podef & ~over & (o <= VALUE_CAP), np.clip(o, 0, None), 0.0)
    return dollars, obl, podef & ~over, over


def known_issue_notes(paths, years):
    """{(fy, scope, sector): [note]} for the known reporting changes of 19_history_atlas.py, computed from the data.  The all-awards rows
    of the affected year get the note (counts and staying), and the all-awards new-company measures of the three years after it
    (their look-back includes the affected year)."""
    notes, info = {}, []
    for k in KNOWN_ISSUES:
        ag, fy = k["agency"], k["fy"]
        if fy not in years:
            continue
        cnt = {}
        for t in (fy, fy + 1):
            if t in years:
                b = read_cols(paths[t], ["awarding_agency_name", "award_kind", "base_and_all_options_value"])
                a = b[b.awarding_agency_name.astype(str) == ag]
                cnt[t] = (int((a.award_kind.astype(str) == "PO").sum()),
                          int((pd.to_numeric(a.base_and_all_options_value, errors="coerce") <= MICRO).sum()))
                del b, a
        if fy not in cnt:
            continue
        sector = SECTORS[2] if ag == "Department of Defense" else SECTORS[1] if ag == GSA else SECTORS[0]
        nxt = f" (FY{fy + 1}: {cnt[fy + 1][0]:,} and {cnt[fy + 1][1]:,})" if fy + 1 in cnt else ""
        info.append({"agency": ag, "fy": fy, "purchase_orders": cnt[fy][0], "at_or_under_micro_purchase_threshold": cnt[fy][1],
                     "micro_purchase_threshold": MICRO, "next_year": ({"purchase_orders": cnt[fy + 1][0],
                                                                       "at_or_under_micro_purchase_threshold": cnt[fy + 1][1]}
                                                                      if fy + 1 in cnt else None)})
        text = (f"FY{fy} reporting change: {ag} reported {cnt[fy][0]:,} purchase orders, {cnt[fy][1]:,} of them at or under "
                f"${MICRO:,}{nxt}; counts of all awards are not comparable with later years; use the above-$25,000 series")
        for s in (sector, ALL):
            notes.setdefault((fy, "all", s), []).append(text)
            for t in range(fy + 1, fy + LOOKBACK + 1):
                if t in years:
                    notes.setdefault((t, "all", s), []).append(
                        f"new-company measures: the look-back includes the FY{fy} reporting change of {ag}; use the above-$25,000 series")
    return notes, info


def suppress_small(df, comp_cols, share_cols):
    """blank the share and count columns of rows resting on fewer than MIN_COMPANIES companies, and say so in `note`"""
    small = df[comp_cols] < MIN_COMPANIES
    if small.any():
        df.loc[small, share_cols] = np.nan
        df.loc[small, "note"] = [("; ".join(x for x in (n, SUPPRESSED) if x)) for n in df.loc[small, "note"]]
    return df


def years_label(years):
    """[2011, 2012, 2013, 2025] -> 'FY2011-FY2013 and FY2025'"""
    runs = []
    for y in sorted(set(int(v) for v in years)):
        if runs and y == runs[-1][1] + 1:
            runs[-1][1] = y
        else:
            runs.append([y, y])
    parts = [f"FY{a}" if a == b else f"FY{a}-FY{b}" for a, b in runs]
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def pooled_competition(cp, years_ok):
    """sum the yearly competition rows over the years whose new-company measures are comparable"""
    p = cp[cp.fy.isin(years_ok)]
    g = p.groupby(["sector", "award_scope", "measure", "group"], as_index=False)[["k", "n"]].sum()
    # companies behind a pooled cell: at least the largest yearly count (a company can appear in several years)
    nc = p.groupby(["sector", "award_scope", "measure", "group"], as_index=False).n_companies.max()
    g = g.merge(nc, on=["sector", "award_scope", "measure", "group"])
    g["fy"] = np.nan
    g["period"] = (years_label(years_ok) + " pooled") if years_ok else ""
    g["note"] = ""
    return g


def add_shares(cp):
    cp = cp.copy()
    ok = cp.n > 0
    cp["share"] = np.where(ok, cp.k / cp.n.where(ok, 1), np.nan)
    lo, hi = wilson(cp.k.where(ok, 0), cp.n.where(ok, 1))
    cp["ci_lo"], cp["ci_hi"] = np.where(ok, lo, np.nan), np.where(ok, hi, np.nan)
    return cp


# ======================================================================================================================================
# main
# ======================================================================================================================================
def run(paths, out_dir=RESULTS, fig_path=None, backfill=None, verbose=True):
    t0 = time.time()
    years = sorted(paths)
    say = print if verbose else (lambda *a, **k: None)
    say(f"fiscal years present: {', '.join(map(str, years))}")
    info, la, lb = scan_ids(paths)
    nodes, root = link_groups(la, lb)
    uei_codes = np.unique(la) if len(la) else np.array([], dtype=np.uint64)
    diag = switch_diagnostics(info, nodes, root, uei_codes)
    link_years = [y for y in years if info[y]["has_link_columns"]]
    group_sizes = np.unique(root, return_counts=True)[1] if len(root) else np.array([], dtype=int)
    say(f"years with both identifier codes: {', '.join(map(str, link_years)) or 'none'}; "
        f"{int((group_sizes >= 2).sum()):,} DUNS-UEI links; linked groups with more than two codes: {int((group_sizes > 2).sum()):,}")
    # incomplete years: a Defense year whose September is missing or thin
    complete = {y: not info[y]["incomplete_defense"] for y in years}
    # id sets per year and scope (canonical and raw)
    sets = {}
    for y in years:
        vid, ab = info[y]["_vid"], info[y]["_above"]
        cid = canon(vid, nodes, root)
        for scope, m in (("all", np.ones(len(vid), dtype=bool)), ("above_25k", ab)):
            sets[(y, scope, "canon")] = np.unique(cid[m & (cid != 0)])
            sets[(y, scope, "raw")] = np.unique(vid[m & (vid != 0)])
    for r in info.values():
        for k in [k for k in r if k.startswith("_")]:
            del r[k]
    issue, issue_info = known_issue_notes(paths, years)
    by_fy, conc, comp = [], [], []
    for y in years:
        b = read_cols(paths[y], READ_COLS)
        rid = hex_codes(b["vendor_id"])
        cid = canon(rid, nodes, root)
        a, c, p = year_tables(y, b, cid, rid, sets, years, complete, diag, issue)
        by_fy += a
        conc += c
        comp += p
        say(f"  FY{y}: {len(b):,} awards, {len(np.unique(cid[cid != 0])):,} companies  [{time.time() - t0:.0f}s, peak {peak_mb():.0f} MB]",
            flush=True)
        del b, rid, cid

    # ---- tables --------------------------------------------------------------------------------------------------------------------
    t = pd.DataFrame(by_fy)
    t = t.reindex(columns=BY_FY_COLS)       # the same columns whatever the years present (a measure without its years stays blank)
    share_cols = [c for c in t.columns if "share" in c or c in ("n_new_companies", "initial_dollars")]
    t = suppress_small(t, "n_companies", share_cols)
    order = {"award_scope": list(SCOPES), "sector": SECTORS + [ALL], "award_kind": KINDS}
    t = t.sort_values(["award_scope", "sector", "award_kind", "fy"],
                      key=lambda c: c.map({v: i for i, v in enumerate(order[c.name])}) if c.name in order else c).reset_index(drop=True)
    t.to_csv(os.path.join(out_dir, "supplier_base_by_fy.csv"), index=False)
    cdf = pd.DataFrame(conc)
    cdf.to_csv(os.path.join(out_dir, "supplier_base_concentration.csv"), index=False)
    cp = pd.DataFrame(comp, columns=["fy", "sector", "award_scope", "measure", "group", "k", "n", "n_companies", "note"])
    comparable_years = sorted({int(r["fy"]) for r in by_fy if r.get("new_measures_comparable") and r["sector"] == ALL
                               and r["award_scope"] == "above_25k" and r["award_kind"] == "all" and complete.get(int(r["fy"]), True)})
    cp["period"] = [f"FY{int(y)}" for y in cp.fy]
    pooled = pooled_competition(cp, comparable_years) if comparable_years else cp.iloc[0:0]
    cp = add_shares(pd.concat([cp, pooled], ignore_index=True)) if len(cp) else cp.assign(share=[], ci_lo=[], ci_hi=[])
    small = cp.n_companies < MIN_COMPANIES
    cp.loc[small, ["k", "n", "share", "ci_lo", "ci_hi"]] = np.nan
    cp.loc[small, "note"] = [("; ".join(x for x in (n, SUPPRESSED) if x)) for n in cp.loc[small, "note"]]
    cp = cp[["period", "fy", "sector", "award_scope", "measure", "group", "k", "n", "share", "ci_lo", "ci_hi", "n_companies", "note"]]
    cp.to_csv(os.path.join(out_dir, "supplier_base_competition.csv"), index=False)

    summary = build_summary(t, cdf, cp, years, link_years, diag, group_sizes, comparable_years, complete, backfill, t0)
    summary["known_reporting_changes"] = issue_info
    with open(os.path.join(out_dir, "supplier_base_summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=1)
    if fig_path:
        plot(t, cp, summary, fig_path)
    return t, cdf, cp, summary


def _f(x, d=4):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(float(x), d)


def build_summary(t, cdf, cp, years, link_years, diag, group_sizes, comparable_years, complete, backfill, t0):
    head = t[(t.sector == ALL) & (t.award_kind == "all")]
    h25 = head[head.award_scope == "above_25k"].set_index("fy")
    hall = head[head.award_scope == "all"].set_index("fy")
    full = [y for y in years if complete.get(y, True)]
    first, last = (min(full), max(full)) if full else (None, None)
    s = {"question": "Who wins new federal awards each year: how many companies, how many are new, how many stay, how concentrated the "
                     "dollars are, and how new companies relate to single-offer awards.",
         "years_present": years, "complete_years": full, "incomplete_years": [y for y in years if not complete.get(y, True)],
         "headline_series": "awards above $25,000 (initial potential value; dollars not adjusted for inflation)",
         "definitions": {
             "company": "one company id: vendor_id, with DUNS-based and UEI-based codes linked into one id wherever a record carries both",
             "new_company": f"won no award in the {LOOKBACK} previous fiscal years (in the above-$25,000 series: no award above $25,000)",
             "new_company_never_seen": f"won no award in any earlier year present (since FY{years[0]})" if years else "",
             "staying": "won again in the next fiscal year, or within the next three fiscal years",
             "top_shares": "share of initial dollars won by the top 1% and top 10% of companies, ranked by initial dollars",
             "hhi": "Herfindahl-Hirschman index of initial dollars within a 4-digit NAICS industry with at least 20 awards in the year "
                    "(sum of squared percentage shares, 0 to 10,000); summarized as the award-weighted median and the share of awards "
                    "in industries above 2,500"},
         "identifier_switch": {
             "years_with_link_columns": link_years,
             "years_without_link_columns": [y for y in years if y not in link_years],
             "duns_uei_links": int((group_sizes >= 2).sum()) if len(group_sizes) else 0,
             "linked_groups_with_more_than_two_codes": int((group_sizes > 2).sum()) if len(group_sizes) else 0,
             "linked_groups_with_more_than_five_codes": int((group_sizes > 5).sum()) if len(group_sizes) else 0,
             "by_year": {str(y): {k: (_f(v) if isinstance(v, float) else v) for k, v in d.items()} for y, d in diag.items()},
             "usaspending_sample": backfill},
         "comparable_years_new_measures": comparable_years}
    if first is not None and first in h25.index and last in h25.index:
        a, b = h25.loc[first], h25.loc[last]
        s["companies_above_25k"] = {"first_year": first, "first": int(a.n_companies), "last_year": last, "last": int(b.n_companies),
                                    "change": _f(b.n_companies / a.n_companies - 1),
                                    "peak_year": int(h25.n_companies.idxmax()), "peak": int(h25.n_companies.max())}
        a2, b2 = hall.loc[first], hall.loc[last]
        s["companies_all_awards"] = {"first_year": first, "first": int(a2.n_companies), "last_year": last, "last": int(b2.n_companies),
                                     "change": _f(b2.n_companies / a2.n_companies - 1),
                                     "note_first_year": str(a2.note or "")}
        s["awards_above_25k"] = {"first": int(a.n_awards), "last": int(b.n_awards), "change": _f(b.n_awards / a.n_awards - 1)}
        s["companies_above_25k_by_sector"] = {
            sec: {str(int(r.fy)): int(r.n_companies) for r in t[(t.sector == sec) & (t.award_scope == "above_25k") & (t.award_kind == "all")
                                                                  & t.fy.isin([first, last])].itertuples()} for sec in SECTORS}
    ok = h25[h25.index.isin(comparable_years)]
    # how much linking changes the new-company share, over every year that has it (also the years around the switch)
    gap = (h25.new_share_companies_unlinked_ids - h25.new_share_companies).abs().dropna()
    if len(ok):
        y_last = int(ok.index.max())
        s["new_companies_above_25k"] = {
            "years": [int(y) for y in ok.index],
            "share_of_companies_first": _f(ok.new_share_companies.iloc[0]), "first_year": int(ok.index[0]),
            "share_of_companies_last": _f(ok.new_share_companies.iloc[-1]), "last_year": y_last,
            "share_of_companies_mean": _f(ok.new_share_companies.mean()), "share_of_awards_mean": _f(ok.new_share_awards.mean()),
            "share_of_dollars_mean": _f(ok.new_share_dollars.mean()),
            "share_of_companies_never_seen_last": _f(ok.new_share_companies_never_seen.iloc[-1]),
            "largest_gap_linked_vs_unlinked_points": _f(100 * gap.max(), 2) if len(gap) else None,
            "largest_gap_year": int(gap.idxmax()) if len(gap) else None}
    stay = h25[h25.stay_share_next_year.notna() & (h25.stay_next_year_comparable == True)]  # noqa: E712
    if len(stay):
        s["staying_above_25k"] = {"years": [int(y) for y in stay.index], "next_year_mean": _f(stay.stay_share_next_year.mean()),
                                  "next_year_last": _f(stay.stay_share_next_year.iloc[-1]), "next_year_last_year": int(stay.index[-1])}
        s3 = h25[h25.stay_share_within_3_years.notna() & (h25.stay_3_years_comparable == True)]  # noqa: E712
        if len(s3):
            s["staying_above_25k"].update(within_3_years_mean=_f(s3.stay_share_within_3_years.mean()),
                                          within_3_years_last=_f(s3.stay_share_within_3_years.iloc[-1]),
                                          within_3_years_last_year=int(s3.index[-1]))
        # staying among new companies is not computed per company group here; the per-year rows have the overall rate
    c = cdf[(cdf.sector == ALL) & (cdf.award_scope == "above_25k") & cdf.fy.isin(full)].set_index("fy")
    if len(c):
        y0, y1 = int(c.index.min()), int(c.index.max())
        s["concentration_above_25k"] = {"first_year": y0, "last_year": y1,
                                        "top1pct_first": _f(c.top1pct_share_dollars[y0]), "top1pct_last": _f(c.top1pct_share_dollars[y1]),
                                        "top10pct_first": _f(c.top10pct_share_dollars[y0]), "top10pct_last": _f(c.top10pct_share_dollars[y1]),
                                        "hhi_median_first": _f(c.hhi_median_award_weighted[y0], 0),
                                        "hhi_median_last": _f(c.hhi_median_award_weighted[y1], 0),
                                        "share_awards_hhi_above_2500_first": _f(c.share_awards_in_markets_hhi_above_2500[y0]),
                                        "share_awards_hhi_above_2500_last": _f(c.share_awards_in_markets_hhi_above_2500[y1])}
    pooled = cp[cp.fy.isna() & (cp.sector == ALL) & (cp.award_scope == "above_25k")]
    if len(pooled):
        def g(measure, group):
            r = pooled[(pooled.measure == measure) & (pooled.group == group)]
            return None if not len(r) or pd.isna(r.share.iloc[0]) else {
                "share": _f(r.share.iloc[0]), "ci_lo": _f(r.ci_lo.iloc[0]), "ci_hi": _f(r.ci_hi.iloc[0]), "n": int(r.n.iloc[0])}
        sa = pooled[(pooled.measure == "new_company_share_by_setaside") & pooled.share.notna() & (pooled.n >= 1_000)]
        sa = sa[~sa.group.isin(["other", "other/blank"])].sort_values("share")
        s["competition_above_25k_pooled"] = {
            "period": str(pooled.period.iloc[0]),
            "single_offer_share_new": g("single_offer_share", "new companies"),
            "single_offer_share_established": g("single_offer_share", "established companies"),
            "new_share_one_offer": g("new_company_share_by_offers", "one offer"),
            "new_share_several_offers": g("new_company_share_by_offers", "two or more offers"),
            "new_share_by_setaside": {r.group: {"share": _f(r.share), "ci_lo": _f(r.ci_lo), "ci_hi": _f(r.ci_hi), "n": int(r.n)}
                                      for r in sa.itertuples()}}
    s["notes_in_tables"] = sorted({n for n in t.note if n})[:20]
    s["caveats"] = [
        "A company is one identifier. A company can hold several identifiers (one per registered entity), and parents and subsidiaries "
        "count as different companies, so the counts are counts of registered entities, not of corporate families.",
        "Only prime awards: subcontractors are not counted.",
        "Only new awards (base actions): orders placed under existing contract vehicles (delivery and task orders) are not in these "
        "tables, so a company that wins only such orders does not appear.",
        "Dollars are the initial potential value (base and all options) of purchase orders and definitive contracts, not adjusted for "
        "inflation; negative values count as zero. Contract vehicles are left out of dollar measures because their value is a ceiling, "
        "and values above $10 billion are left out as likely recording errors.",
        "Counts of all awards depend on how very small purchases were reported, which changed over time (for example the FY2008 "
        "Veterans Affairs micro-purchases and the higher micro-purchase limits from FY2016 and FY2018); the series of awards above "
        "$25,000 is the headline for that reason.",
        "Until April 2022 companies were identified by DUNS numbers, after that by UEI. The tables link the two where a record carries "
        "both; rows marked 'not comparable' could not be checked.",
        "The latest Defense year is incomplete until its last quarter is published, 90 days late.",
        "These are patterns in public data, not accusations and not proof of causes."]
    s["privacy"] = ("No company or buying office is named or coded in any output; no statistic describes a single company; cells resting "
                    f"on fewer than {MIN_COMPANIES} companies are blank.")
    s["runtime_seconds"] = round(time.time() - t0, 1)
    s["peak_memory_mb"] = round(peak_mb())
    return s


def plot(t, cp, summary, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    colors = {"civilian except GSA": "#2a78d6", "Department of Defense": "#eb6834", "GSA": "#8c8c8c", ALL: "#222222"}
    d = t[(t.award_scope == "above_25k") & (t.award_kind == "all")]
    fig, axs = plt.subplots(2, 2, figsize=(10, 7.2))
    ax = axs[0, 0]
    for sec, col in colors.items():
        e = d[d.sector == sec].sort_values("fy")
        if len(e):
            g = e.set_index("fy").n_companies.reindex(range(int(e.fy.min()), int(e.fy.max()) + 1))    # gaps break the line
            ax.plot(g.index, g.astype(float) / 1000, color=col, lw=2 if sec != ALL else 2.4, label=sec)
            inc = e[e.note.str.contains("incomplete year", na=False)]
            ax.scatter(inc.fy, inc.n_companies / 1000, s=50, facecolors="white", edgecolors=col, zorder=4)
    ax.set_title("Companies winning at least one new award above \\$25,000", fontsize=9)
    ax.set_ylabel("thousands of companies")
    ax.legend(frameon=False, fontsize=7)

    def notcomp_line(ax, e, col, ycol, flag, label=None, ls="-"):
        """the line over comparable years (broken at not-comparable or missing years), hollow markers at the not-comparable ones"""
        e = e.sort_values("fy")
        bad = e[e[flag] == False]  # noqa: E712
        y = e.set_index("fy")[ycol].where(e.set_index("fy")[flag] == True)  # noqa: E712
        y = y.reindex(range(int(e.fy.min()), int(e.fy.max()) + 1))
        ax.plot(y.index, 100 * y.astype(float), color=col, lw=2, ls=ls, marker="o", ms=2.5, label=label)   # dots: a lone year shows
        if len(bad):
            ax.scatter(bad.fy, 100 * bad[ycol], s=40, facecolors="white", edgecolors=col, lw=1.3, zorder=4)
        return bad

    ax = axs[0, 1]
    anybad = False
    for sec, col in colors.items():
        e = d[(d.sector == sec) & d.new_share_companies.notna()]
        if len(e):
            anybad |= len(notcomp_line(ax, e, col, "new_share_companies", "new_measures_comparable", sec)) > 0
    e = d[(d.sector == ALL) & d.new_share_companies_never_seen.notna() & (d.fy >= (d.fy.min() + LOOKBACK))]
    if len(e):
        ax.plot(e.fy, 100 * e.new_share_companies_never_seen, color="#222222", lw=1, ls=":", label="all sectors, never seen since FY%d" % d.fy.min())
    ax.set_title("New companies: no award above \\$25,000 in the 3 previous years", fontsize=9)
    ax.set_ylabel("% of the year's companies")
    if anybad:
        ax.scatter([], [], s=40, facecolors="white", edgecolors="#222222", label="not comparable (DUNS to UEI switch)")
    ax.legend(frameon=False, fontsize=7)

    ax = axs[1, 0]
    for sec, col in colors.items():
        e = d[(d.sector == sec) & d.stay_share_next_year.notna()]
        if len(e):
            notcomp_line(ax, e, col, "stay_share_next_year", "stay_next_year_comparable", sec)
    ax.set_title("Companies that won again the next year (above \\$25,000)", fontsize=9)
    ax.set_ylabel("% of the year's companies")

    ax = axs[1, 1]
    q = cp[(cp.sector == ALL) & (cp.award_scope == "above_25k") & (cp.measure == "single_offer_share") & cp.fy.notna() & cp.share.notna()]
    for grp, col in (("new companies", "#c0392b"), ("established companies", "#2a78d6")):
        e = q[q.group == grp].sort_values("fy")
        if len(e):
            ok = ~e.note.str.contains("not comparable", na=False)
            g = e[ok].set_index("fy").reindex(range(int(e.fy.min()), int(e.fy.max()) + 1))    # gaps break the line
            ax.plot(g.index, 100 * g.share.astype(float), color=col, lw=2, marker="o", ms=2.5, label=f"won by {grp}")
            ax.fill_between(g.index, 100 * g.ci_lo.astype(float), 100 * g.ci_hi.astype(float), color=col, alpha=0.15, lw=0)
            ax.scatter(e.fy[~ok], 100 * e.share[~ok], s=40, facecolors="white", edgecolors=col, lw=1.3, zorder=4)
    ax.set_title("Single-offer share of competed awards above \\$25,000, all sectors", fontsize=9)
    ax.set_ylabel("% with one offer (95% interval)")
    ax.legend(frameon=False, fontsize=7)
    years = sorted(int(y) for y in t.fy.unique())
    for a in axs.flat:
        a.set_xlabel("fiscal year", fontsize=8)
        a.set_xlim(min(years) - 0.5, max(years) + 0.5)
        a.set_xticks(years if len(years) <= 10 else years[::2])
        a.tick_params(labelsize=7)
        a.spines[["top", "right"]].set_visible(False)
    fig.suptitle("Who wins new federal awards: the supplier base (patterns, not causes; hollow markers: incomplete or not comparable)",
                 fontsize=10)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def load_backfill():
    """the result of the one-off USAspending sample (reference/uei_backfill_check.json), or None"""
    p = os.path.join(PROJECT, "reference", "uei_backfill_check.json")
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def main():
    files = sorted(glob.glob(os.path.join(DATA, "history", "processed", "base_actions_FY*.parquet")))
    if not files:
        sys.exit("no history tables: run `python run.py get-history` (or the history workflow) first")
    paths = {year_of(p): p for p in files}
    t, cdf, cp, s = run(paths, fig_path=os.path.join(PROJECT, "figures", "supplier_base.png"), backfill=load_backfill())
    show = t[(t.sector == ALL) & (t.award_kind == "all")]
    for scope in SCOPES:
        e = show[show.award_scope == scope].set_index("fy")
        print(f"\nALL SECTORS, {SCOPES[scope].upper()}")
        print(e[["n_awards", "n_companies", "new_share_companies", "new_share_companies_unlinked_ids", "new_share_companies_never_seen",
                 "stay_share_next_year", "stay_share_within_3_years"]].round(3).to_string())
    c = cdf[(cdf.sector == ALL) & (cdf.award_scope == "above_25k")].set_index("fy")
    print("\nCONCENTRATION, ALL SECTORS, AWARDS ABOVE $25,000")
    print(c[["n_companies", "top1pct_share_dollars", "top10pct_share_dollars", "n_markets", "hhi_median_award_weighted",
             "share_awards_in_markets_hhi_above_2500"]].round(3).to_string())
    p = cp[cp.fy.isna() & (cp.award_scope == "above_25k")]
    if len(p):
        print(f"\nLINK TO COMPETITION ({p.period.iloc[0]}, awards above $25,000)")
        print(p[["sector", "measure", "group", "n", "share", "ci_lo", "ci_hi"]].round(3).to_string(index=False))
    notes = sorted({n for n in t.note if n})
    if notes:
        print("\nNOTES IN THE TABLES:")
        for n in notes:
            print("  - " + n)
    print(f"\ndone in {s['runtime_seconds']:.0f}s, peak memory {s['peak_memory_mb']} MB")


if __name__ == "__main__":
    main()
