"""
34_market_lookup.py - a market lookup for buyers: in each industry group and product or service group, how many offers do competed
awards usually get, how often only one, does a set-aside change that, and how many companies are registered?

Market research is the first step of a federal purchase.  This script answers its first questions at industry level, from the yearly
tables of the long history.  It describes markets, not buyers or sellers: a single offer is a risk indicator, not proof of a problem.

Groups
  naics4  industry group: the first 4 digits of the award's NAICS code, with official titles from reference/naics4_titles.csv (the
          2022 list, plus the codes of earlier lists that the awards still carry: such a code is labeled "<title> (2017 code)" and
          marked older_code; a code in none of the lists is labeled "industry <code> (older code)").  Codes are not merged: when the
          list changed, the same industry can appear under two codes, each holding only the awards recorded under it.
  psc2    product or service group: the first 2 characters of the product or service code, as in 03_atlas.py (products: the federal
          supply group, for example 65 medical supplies; services: the letter and the next character, for example R4)
Scopes: all sectors together, civilian except GSA, GSA, and the Department of Defense (sectors as in 19_history_atlas.py).

Window
  The recent window is the last WINDOW (5) complete fiscal years.  A year is complete when it has ended and, for Defense, is not cut
  off by the 90-day publication delay (19_history_atlas.py `defense_incomplete`).  One window for every scope: when the newest year is
  incomplete for Defense, it is left out for every scope, so "all sectors" always covers the same years.  The earlier window is the
  WINDOW years before it.  A year missing from the tables is listed in the summary; a change between windows is given only when every
  year of both windows is present.

Measures (competed awards with a usable offer count, the eval set of 19_history_atlas.py: competed A/D/E/F/CDO, offers 1..998)
  n_eval, k_single, share (exactly one offer) with a 95% Wilson interval; the 25th, 50th and 75th percentiles of the number of offers
  (linear interpolation, as pandas' quantile); the share with one or two offers; the single-offer share under a small-business
  set-aside (every small-business type together: small business, 8(a), HUBZone, SDVOSB, WOSB/EDWOSB, VOSB; the groups of 23_drivers.py)
  and with no set-aside; the share in each size band of 23_drivers.py (initial value, base and all options); the same share in the
  earlier window and the change in percentage points, with a 95% interval (normal approximation for a difference of two shares).

Cells
  A cell (a share, a percentile, a set-aside or size-band share, a change) is published only when it rests on at least MIN_CELL (100)
  competed awards; fewer means blank, not zero.  Nothing below agency level: no buying office, company or solicitation is read into
  the outputs.  Only industry and product codes, titles and counts are written.

Change notes (column change_note): the change is marked "compare with care" when the group's awards grew or shrank more than VOLUME_RATIO
  (3) times between windows (codes or reporting may have changed); for product groups whose IT codes were redefined in FY2021 (the
  October 2020 product and service code manual: groups starting with 7 or D); and for GSA, whose offer coding changed several times
  (16_quality_monitor.py --history).  The earlier window also holds the 2018 rise of the micro-purchase and simplified-acquisition
  limits, so the mix of small purchases differs; the summary says so.
  Column note: an "all sectors" row where GSA holds more than half of the competed awards says so (GSA's very small supply orders can
  set the whole group's share).  The summary's thinnest and deepest large markets (at least LARGE_MARKET competed awards, all sectors)
  leave those groups out.

Registered companies (naics4 only), from reference/market_depth_naics.csv (SAM.gov public extract, 25_market_depth.py):
  registered_primary         companies whose PRIMARY NAICS code is in the group.  Each company has one primary code, so this is a
                             count of distinct companies.
  registered_any_largest     the largest n_any of one 6-digit code in the group: at least this many distinct companies list a code of
                             the group.  The true number of distinct companies listing any code of the group is between this and
                             registered_any_listings (a company that lists several codes of the group is counted once per code there).
                             The extract itself is not kept on disk, so distinct companies cannot be recounted without a new download.
  registered_any_listings    the sum of n_any over the group's 6-digit codes (listings, not companies).

Input : data/history/processed/base_actions_FY*.parquet (18_history_build.py), one fiscal year at a time with explicit columns;
        reference/naics4_titles.csv, reference/market_depth_naics.csv (both optional); results/history_by_fy.csv (optional, for a
        check of the yearly totals)
Output: results/market_lookup_naics4.csv, results/market_lookup_psc2.csv, results/market_lookup_summary.json
Usage : python -I scripts/34_market_lookup.py
"""
import csv
import glob
import importlib.util
import json
import math
import os
import re
import sys
from datetime import date, datetime, timezone

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


_atlas = _load("atlas19", "19_history_atlas.py")
_drivers = _load("drivers23", "23_drivers.py")
defense_incomplete = _atlas.defense_incomplete
SA_MAP, SIZE_BINS, SIZE_LABELS = _drivers.SA_MAP, _drivers.SIZE_BINS, _drivers.SIZE_LABELS
PSC_PRODUCT, PSC_SERVICE = _drivers.PSC_PRODUCT, _drivers.PSC_SERVICE

WINDOW = 5
MIN_CELL = 100
VOLUME_RATIO = 3.0
LARGE_MARKET = 2_000         # competed awards (all sectors, recent window) for the "thinnest and deepest large markets" lists
GSA = "General Services Administration"
DEFENSE = "Department of Defense"
SECTORS = ["civilian except GSA", "GSA", DEFENSE]
ALL = "all sectors"
SCOPES = [ALL] + SECTORS
SB_GROUPS = {"small business", "8(a)", "HUBZone", "SDVOSB", "WOSB/EDWOSB", "VOSB"}
COLS = ["naics_code", "product_or_service_code", "type_of_set_aside_code", "offers", "eval_set", "single", "is_dod",
        "awarding_agency_name", "base_and_all_options_value"]
DIMS = {"naics4": "naics_code", "psc2": "product_or_service_code"}
WIDTH = {"naics4": 4, "psc2": 2}
REFERENCE = os.path.join(PROJECT, "reference")
GSA_NOTE = "GSA's offer coding changed several times in these years (see the quality monitor): compare with care"
IT_NOTE = "IT product and service codes were redefined in FY2021 (October 2020 code manual): compare with care"


# ---- the window ---------------------------------------------------------------------------------------------------------------------
def year_of(path):
    m = re.search(r"FY(\d{4})", os.path.basename(path))
    return int(m.group(1)) if m else None


def year_ended(fy, today):
    """a fiscal year ends on September 30"""
    return today >= date(fy, 10, 1)


def choose_window(years, incomplete, n=WINDOW):
    """the recent window: the n fiscal years ending with the newest year that is present and complete; the earlier window: the n years
    before.  Returns a dict with the two windows, the years present in each and the years missing."""
    complete = sorted(y for y in years if y not in set(incomplete))
    if not complete:
        return None
    end = complete[-1]
    recent, earlier = list(range(end - n + 1, end + 1)), list(range(end - 2 * n + 1, end - n + 1))
    have = set(years)
    return {"end": end, "recent": recent, "earlier": earlier,
            "recent_present": [y for y in recent if y in have], "earlier_present": [y for y in earlier if y in have],
            "missing": [y for y in recent + earlier if y not in have],
            "left_out_incomplete": sorted(y for y in incomplete if y > end),
            "change_ok": all(y in have for y in recent + earlier)}


# ---- one year's counts --------------------------------------------------------------------------------------------------------------
def sector_codes(agency, is_dod):
    """0 civilian except GSA, 1 GSA, 2 Defense"""
    a = agency.astype(str).to_numpy()
    d = pd.Series(is_dod).fillna(False).to_numpy(bool)
    return np.where(d, 2, np.where(a == GSA, 1, 0)).astype(np.int8)


def group_codes(codes, width):
    """the first `width` characters of a code; blank when the code is missing or shorter"""
    c = pd.Series(codes, dtype=object).fillna("").astype(str).str.strip().str.upper()
    g = c.str[:width]
    return g.where(c.str.len() >= width, "")


def setaside_group(codes):
    """'sb' small-business set-aside (any type), 'none' no set-aside, 'other' (other set-asides, blank)"""
    lab = pd.Series(codes, dtype=object).fillna("").astype(str).str.strip().map(SA_MAP).fillna("other/blank")
    return np.where(lab.isin(SB_GROUPS), "sb", np.where(lab == "no set-aside", "none", "other"))


def size_band(values):
    v = pd.to_numeric(pd.Series(values), errors="coerce").clip(lower=0)
    return pd.cut(v, bins=SIZE_BINS, labels=SIZE_LABELS, right=False).astype(object).fillna("unknown").to_numpy()


def year_counts(b):
    """counts of one fiscal year's eval-set awards.  b: the base actions (any rows; only eval_set rows are used).
    Returns {"offers": DataFrame(dim, group, sector, offers, n), "sa": (dim, group, sector, sa, n, k), "size": (dim, group, sector, size,
    n, k), "totals": {sector: n}}.  Office, company and solicitation columns are never read."""
    e = b[b.eval_set.fillna(False).to_numpy(bool)]
    sec = sector_codes(e.awarding_agency_name, e.is_dod)
    offers = pd.to_numeric(e.offers, errors="coerce").round().astype("int64").to_numpy()
    single = e.single.fillna(False).to_numpy(bool)
    if len(e) and not np.array_equal(single, offers == 1):
        print(f"  warning: {int((single != (offers == 1)).sum())} eval-set rows where 'single' is not 'offers == 1'; 'single' is used")
    sa, size = setaside_group(e.type_of_set_aside_code), size_band(e.base_and_all_options_value)
    out = {"offers": [], "sa": [], "size": []}
    for dim, col in DIMS.items():
        g = group_codes(e[col].to_numpy(), WIDTH[dim]).to_numpy()
        base = pd.DataFrame({"dim": dim, "group": g, "sector": sec, "offers": offers, "single": single, "sa": sa, "size": size})
        out["offers"].append(base.groupby(["dim", "group", "sector", "offers"]).size().rename("n").reset_index())
        for key in ("sa", "size"):
            t = base.groupby(["dim", "group", "sector", key])["single"].agg(["count", "sum"]).reset_index()
            out[key].append(t.rename(columns={"count": "n", "sum": "k"}))
    res = {k: pd.concat(v, ignore_index=True) for k, v in out.items()}
    res["totals"] = {SECTORS[s]: int((sec == s).sum()) for s in range(3)}
    return res


def collect(files, windows):
    """sum the yearly counts of each window.  files: {fy: path}.  windows: {"recent": [...], "earlier": [...]}.
    Returns ({window: {"offers", "sa", "size"}}, {fy: {sector: n}})."""
    acc = {w: {"offers": [], "sa": [], "size": []} for w in windows}
    totals = {}
    for w, years in windows.items():
        for fy in years:
            if fy not in files:
                continue
            b = pd.read_parquet(files[fy], columns=COLS)
            c = year_counts(b)
            del b
            totals[fy] = c.pop("totals")
            for k in acc[w]:
                acc[w][k].append(c[k])
            print(f"  FY{fy}: {sum(totals[fy].values()):,} competed awards with a usable offer count", flush=True)
    out = {}
    for w, parts in acc.items():
        out[w] = {}
        for k, keys in (("offers", ["dim", "group", "sector", "offers"]), ("sa", ["dim", "group", "sector", "sa"]),
                        ("size", ["dim", "group", "sector", "size"])):
            df = pd.concat(parts[k], ignore_index=True) if parts[k] else pd.DataFrame(columns=keys + ["n", "k"])
            out[w][k] = df.groupby(keys, as_index=False).sum(numeric_only=True)
    return out, totals


def with_scopes(df, keys):
    """add 'all sectors' rows and name the sectors.  df has a numeric 'sector' column (0, 1, 2)."""
    df = df.copy()
    df["sector"] = df.sector.map(dict(enumerate(SECTORS)))
    allrows = df.groupby([k for k in keys if k != "sector"], as_index=False).sum(numeric_only=True)
    allrows["sector"] = ALL
    return pd.concat([allrows, df], ignore_index=True).rename(columns={"sector": "scope"})


# ---- statistics ----------------------------------------------------------------------------------------------------------------------
def wilson(k, n, z=1.959963984540054):
    if n <= 0:
        return (math.nan, math.nan)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


def hist_quantile(values, counts, q):
    """the q-quantile of the data in which values[i] appears counts[i] times, with linear interpolation (pandas' default)"""
    order = np.argsort(values)
    v, c = np.asarray(values, float)[order], np.asarray(counts, np.int64)[order]
    n = int(c.sum())
    if n == 0:
        return math.nan
    h = (n - 1) * q
    lo, hi = int(math.floor(h)), int(math.ceil(h))
    cum = np.cumsum(c)
    x_lo, x_hi = v[np.searchsorted(cum, lo + 1)], v[np.searchsorted(cum, hi + 1)]
    return float(x_lo + (h - lo) * (x_hi - x_lo))


def cell_share(k, n, min_n=MIN_CELL):
    """(share, lo, hi) or NaNs when n < min_n"""
    if n < min_n:
        return (math.nan, math.nan, math.nan)
    lo, hi = wilson(k, n)
    return (k / n, lo, hi)


def diff_ci(p1, n1, p0, n0, z=1.959963984540054):
    se = math.sqrt(p1 * (1 - p1) / n1 + p0 * (1 - p0) / n0)
    d = p1 - p0
    return (100 * d, 100 * (d - z * se), 100 * (d + z * se))


def change_note(dim, group, scope, n1, n0):
    notes = []
    if n0 > 0 and n1 > 0 and max(n1 / n0, n0 / n1) > VOLUME_RATIO:
        notes.append(f"awards in this group changed from {n0:,} to {n1:,} between the windows; codes or reporting may have changed: "
                     "compare with care")
    if dim == "psc2" and group[:1] in ("7", "D"):
        notes.append(IT_NOTE)
    if scope == "GSA":
        notes.append(GSA_NOTE)
    return "; ".join(notes)


def market_table(dim, counts, window, min_n=MIN_CELL):
    """one row per group and scope with every measure.  counts = collect()[0]."""
    rec, ear = counts["recent"], counts.get("earlier")
    off = with_scopes(rec["offers"][rec["offers"].dim == dim], ["group", "sector", "offers"])
    rows = {}
    for (g, sc), t in off.groupby(["group", "scope"], sort=False):
        if g == "":
            continue
        n = int(t.n.sum())
        k = int(t.n[t.offers == 1].sum())
        r = {"group": g, "scope": sc, "n_eval": n, "k_single": k}
        r["share"], r["ci_lo"], r["ci_hi"] = cell_share(k, n, min_n)
        ok = n >= min_n
        r["offers_p25"] = hist_quantile(t.offers, t.n, 0.25) if ok else math.nan
        r["offers_median"] = hist_quantile(t.offers, t.n, 0.5) if ok else math.nan
        r["offers_p75"] = hist_quantile(t.offers, t.n, 0.75) if ok else math.nan
        r["share_one_or_two"] = int(t.n[t.offers <= 2].sum()) / n if ok else math.nan
        rows[(g, sc)] = r
    sa = with_scopes(rec["sa"][rec["sa"].dim == dim], ["group", "sector", "sa"])
    for (g, sc, s), n, k in zip(zip(sa.group, sa.scope, sa.sa), sa.n, sa.k):
        if (g, sc) not in rows or s == "other":
            continue
        name = "setaside" if s == "sb" else "no_setaside"
        r = rows[(g, sc)]
        r[f"n_{name}"] = int(n)
        r[f"share_{name}"], r[f"ci_lo_{name}"], r[f"ci_hi_{name}"] = cell_share(int(k), int(n), min_n)
    sz = with_scopes(rec["size"][rec["size"].dim == dim], ["group", "sector", "size"])
    for (g, sc, s), n, k in zip(zip(sz.group, sz.scope, sz["size"]), sz.n, sz.k):
        if (g, sc) not in rows or s == "unknown":
            continue
        rows[(g, sc)][f"share_{s}"] = cell_share(int(k), int(n), min_n)[0]
        rows[(g, sc)][f"n_{s}"] = int(n)
    if ear is not None:
        eo = with_scopes(ear["offers"][ear["offers"].dim == dim], ["group", "sector", "offers"])
        eg = eo.assign(k=np.where(eo.offers == 1, eo.n, 0)).groupby(["group", "scope"])[["n", "k"]].sum()
        for (g, sc), r in rows.items():
            n0, k0 = (int(eg.n.get((g, sc), 0)), int(eg.k.get((g, sc), 0))) if len(eg) else (0, 0)
            r["n_eval_earlier"] = n0
            r["share_earlier"] = k0 / n0 if n0 >= min_n else math.nan
            if window["change_ok"] and n0 >= min_n and r["n_eval"] >= min_n:
                r["change_points"], r["change_lo"], r["change_hi"] = diff_ci(r["share"], r["n_eval"], r["share_earlier"], n0)
                r["change_note"] = change_note(dim, g, sc, r["n_eval"], n0)
    cols = (["group", "scope", "n_eval", "k_single", "share", "ci_lo", "ci_hi", "offers_p25", "offers_median", "offers_p75",
             "share_one_or_two", "n_setaside", "share_setaside", "ci_lo_setaside", "ci_hi_setaside", "n_no_setaside", "share_no_setaside",
             "ci_lo_no_setaside", "ci_hi_no_setaside"]
            + [c for s in SIZE_LABELS for c in (f"n_{s}", f"share_{s}")]
            + ["n_eval_earlier", "share_earlier", "change_points", "change_lo", "change_hi", "change_note"])
    df = pd.DataFrame(list(rows.values()))
    for c in cols:
        if c not in df:
            df[c] = "" if c == "change_note" else (0 if c.startswith("n_") else math.nan)
    df["change_note"] = df.change_note.fillna("")
    for c in [c for c in cols if c.startswith("n_")]:
        df[c] = df[c].fillna(0).astype(int)
    df["scope"] = pd.Categorical(df.scope, SCOPES, ordered=True)
    df = df[cols].sort_values(["group", "scope"]).reset_index(drop=True)
    df["note"] = gsa_notes(df)
    return df


def gsa_share(df):
    """for each row: the share of the group's competed awards (all sectors) that are GSA's"""
    key = "group" if "group" in df else df.columns[0]          # 'group', or the dimension name once titles are added
    n_all = df[df.scope == ALL].set_index(key).n_eval
    n_gsa = df[df.scope == "GSA"].set_index(key).n_eval
    frac = (n_gsa.reindex(n_all.index).fillna(0) / n_all).to_dict()
    return np.array([frac.get(g, 0.0) for g in df[key]])


def gsa_notes(df):
    """'all sectors' rows where GSA holds more than half of the competed awards get a note"""
    f = gsa_share(df)
    return [f"GSA holds {100 * x:.0f}% of these awards, mostly small supply orders whose offer coding changed over time: "
            "see the sector rows" if sc == ALL and x > 0.5 else "" for sc, x in zip(df.scope, f)]


# ---- titles and registered companies ---------------------------------------------------------------------------------------------
def read_ref(name, folder=REFERENCE):
    p = os.path.join(folder, name)
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def naics_titles(folder=REFERENCE):
    """{code: (title, vintage year)}; the vintage is the year in the file's vintage column ('2022 NAICS' -> '2022')"""
    return {r["naics4"].strip(): (r["title"].strip(), (r.get("vintage") or "2022").split()[0])
            for r in read_ref("naics4_titles.csv", folder) if r.get("naics4")}


def naics_title(code, titles):
    """the official title; '<title> (<vintage> code)' for a code of an earlier list; 'industry <code> (older code)' for a code in none"""
    if code in titles:
        t, v = titles[code] if isinstance(titles[code], tuple) else (titles[code], "2022")
        return t if v == "2022" else f"{t} ({v} code)"
    return f"industry {code} (older code)" if titles else f"industry {code}"


def older_code(code, titles):
    """True for an industry code that is not in the 2022 list (only when the titles file is present)"""
    if not titles:
        return False
    return code not in titles or (isinstance(titles[code], tuple) and titles[code][1] != "2022")


def psc_title(code):
    if code[:1].isdigit():
        if code[1:2].isdigit():
            return "Products: " + PSC_PRODUCT.get(code, "other products") + (" (codes retired in FY2021)" if code == "70" else "")
        return "Products: IT and telecom (codes from FY2021)" if code[:1] == "7" else "Products: other"
    cat = PSC_SERVICE.get(code[:1], "other services")
    return f"Services: {cat} (codes starting {code})"


def registered(folder=REFERENCE):
    """{naics4: (registered_primary, registered_any_largest, registered_any_listings, n_codes)} from market_depth_naics.csv, and the
    extract date"""
    out, ext = {}, ""
    for r in read_ref("market_depth_naics.csv", folder):
        c = (r.get("naics") or "").strip()
        if not re.fullmatch(r"\d{6}", c):
            continue
        ext = r.get("extract_date") or ext
        p, a = int(float(r.get("n_primary") or 0)), int(float(r.get("n_any") or 0))
        o = out.setdefault(c[:4], [0, 0, 0, 0])
        o[0] += p
        o[1] = max(o[1], a)
        o[2] += a
        o[3] += 1
    return out, ext


def add_titles(df, dim, titles=None, reg=None):
    df = df.copy()
    df.insert(1, "title", [naics_title(g, titles or {}) if dim == "naics4" else psc_title(g) for g in df.group])
    if dim == "naics4":
        df.insert(2, "older_code", [older_code(g, titles or {}) for g in df.group])
        reg = reg or {}
        for i, c in enumerate(["registered_primary", "registered_any_largest", "registered_any_listings", "registered_codes"]):
            df[c] = [reg[g][i] if g in reg else np.nan for g in df.group]
            df[c] = df[c].astype("Int64")
    return df.rename(columns={"group": dim})


# ---- summary -------------------------------------------------------------------------------------------------------------------------
def all_markets(counts, min_n=MIN_CELL):
    """every eval-set award of the recent window per scope (blank codes included), from the naics4 counts"""
    off = with_scopes(counts["recent"]["offers"][counts["recent"]["offers"].dim == "naics4"], ["group", "sector", "offers"])
    out = {}
    for sc, t in off.groupby("scope", sort=False):
        t = t.groupby("offers", as_index=False).n.sum()
        n, k = int(t.n.sum()), int(t.n[t.offers == 1].sum())
        s, lo, hi = cell_share(k, n, min_n)
        out[sc] = {"n_eval": n, "k_single": k, "share": s, "ci_lo": lo, "ci_hi": hi,
                   "offers_p25": hist_quantile(t.offers, t.n, 0.25), "offers_median": hist_quantile(t.offers, t.n, 0.5),
                   "offers_p75": hist_quantile(t.offers, t.n, 0.75), "share_one_or_two": int(t.n[t.offers <= 2].sum()) / n if n else None}
    return {sc: out[sc] for sc in SCOPES if sc in out}


def extremes(df, key="naics4", k=5, large=LARGE_MARKET):
    """the k thinnest (highest single-offer share) and k deepest (lowest) groups with at least `large` competed awards, all sectors,
    leaving out groups where GSA holds more than half of the awards (its offer coding changed over time) and groups with an older
    industry code (they hold only the awards recorded under the old code: fragments of a market)"""
    big = (df.scope == ALL) & (df.n_eval >= large) & df.share.notna()
    gsa = gsa_share(df) > 0.5
    old = df.older_code.astype(bool).to_numpy() if "older_code" in df else np.zeros(len(df), bool)
    a = df[big & ~gsa & ~old]
    pick = ["share", "ci_lo", "ci_hi", "n_eval", "offers_median"]

    def recs(t):
        return [{key: r[key], "title": r["title"], **{c: (float(r[c]) if c != "n_eval" else int(r[c])) for c in pick},
                 **({"registered_primary": (int(r["registered_primary"]) if pd.notna(r["registered_primary"]) else None)}
                    if "registered_primary" in t else {})} for _, r in t.iterrows()]
    return {"min_awards": large, "n_large": int(big.sum()), "n_ranked": int(len(a)), "left_out_gsa_majority": int((big & gsa).sum()),
            "left_out_older_code": int((big & ~gsa & old).sum()),
            "thinnest": recs(a.sort_values(["share", key], ascending=[False, True]).head(k)),
            "deepest": recs(a.sort_values(["share", key], ascending=[True, True]).head(k))}


def totals_check(totals, path):
    """compare the eval-set totals per year and sector with results/history_by_fy.csv (award_kind all).  None when the file is missing."""
    if not os.path.exists(path):
        return None
    h = pd.read_csv(path)
    h = h[h.award_kind == "all"]
    ref = {(int(r.fy), r.sector): int(r.n_eval) for r in h.itertuples()}
    diffs = [{"fy": fy, "sector": s, "ours": n, "history_by_fy": ref.get((fy, s))}
             for fy, by in sorted(totals.items()) for s, n in by.items() if ref.get((fy, s)) != n]
    return {"years": sorted(totals), "match": not diffs, "differences": diffs}


def clean(x):
    if isinstance(x, dict):
        return {k: clean(v) for k, v in x.items()}
    if isinstance(x, list):
        return [clean(v) for v in x]
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (float, np.floating)):
        return None if math.isnan(float(x)) else float(x)
    return x


def summary(window, tables, counts, totals, extract, check, built=None):
    nt = tables["naics4"]
    s = {
        "built_utc": built or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window": {"recent": window["recent"], "earlier": window["earlier"], "recent_present": window["recent_present"],
                   "earlier_present": window["earlier_present"], "missing_years": window["missing"],
                   "left_out_incomplete": window["left_out_incomplete"], "change_available": window["change_ok"],
                   "rule": (f"the last {WINDOW} complete fiscal years; a year is complete when it has ended and its Defense data are not cut "
                            "off by the 90-day publication delay (19_history_atlas.py defense_incomplete); one window for every scope")},
        "min_cell": MIN_CELL,
        "scopes": SCOPES,
        "groups": {dim: {"rows": int(len(t)), "groups": int(t[dim].nunique()),
                         "groups_published_all_sectors": int(((t.scope == ALL) & (t.n_eval >= MIN_CELL)).sum())}
                   for dim, t in tables.items()},
        "all_markets": all_markets(counts),
        "markets_naics4": extremes(nt, "naics4"),
        "markets_psc2": extremes(tables["psc2"], "psc2"),
        "eval_by_year": {str(fy): by for fy, by in sorted(totals.items())},
        "totals_check": check,
        "registered_extract_date": extract,
        "caveats": [
            "A single offer is a risk indicator about a market, not proof of a problem: a narrow requirement, a short deadline or a "
            "specialized need can all lead to one offer.",
            "Categories are broad: a 4-digit industry group or a 2-character product group mixes very different purchases.",
            "Offer counts are as recorded by agencies; coding differs between agencies and over time (see the quality monitor).",
            "Only new awards (base actions) are counted; orders under existing contract vehicles are not in the yearly tables.",
            "The earlier window holds the 2018 rise of the micro-purchase limit ($3,500 to $10,000) and of the simplified-acquisition "
            "limit ($150,000 to $250,000), on different dates by agency, so the mix of small purchases differs between the windows.",
            "Registered companies are SAM.gov registrations on the extract date; a registration is not a capable bidder.",
        ],
    }
    return clean(s)


# ---- main -----------------------------------------------------------------------------------------------------------------------------
def find_incomplete(files, today):
    """years that are not complete, checked from the newest down until a complete one is found"""
    bad = []
    for fy in sorted(files, reverse=True):
        if not year_ended(fy, today) or defense_incomplete(files[fy]):
            bad.append(fy)
            continue
        break
    return bad


def write_csv(df, path):
    tmp = path + ".part"
    df.to_csv(tmp, index=False, float_format="%.10g")
    os.replace(tmp, path)


def run(files, out_dir=RESULTS, ref_dir=REFERENCE, today=None, history_csv=None, built=None):
    """files: {fy: path}.  Writes the three outputs to out_dir and returns the summary."""
    today = today or date.today()
    incomplete = find_incomplete(files, today)
    window = choose_window(sorted(files), incomplete)
    if window is None:
        sys.exit("market-lookup: no complete fiscal year in the history tables")
    print(f"recent window FY{window['recent'][0]}-FY{window['recent'][-1]}, earlier FY{window['earlier'][0]}-FY{window['earlier'][-1]}"
          + (f"; left out as incomplete: FY{', FY'.join(map(str, window['left_out_incomplete']))}" if window["left_out_incomplete"] else "")
          + (f"; missing: FY{', FY'.join(map(str, window['missing']))}" if window["missing"] else ""))
    counts, totals = collect(files, {"recent": window["recent_present"], "earlier": window["earlier_present"]})
    titles = naics_titles(ref_dir)
    reg, extract = registered(ref_dir)
    tables = {dim: add_titles(market_table(dim, counts, window), dim, titles, reg) for dim in DIMS}
    check = totals_check(totals, history_csv or os.path.join(out_dir, "history_by_fy.csv"))
    s = summary(window, tables, counts, totals, extract, check, built)
    os.makedirs(out_dir, exist_ok=True)
    for dim, t in tables.items():
        write_csv(t, os.path.join(out_dir, f"market_lookup_{dim}.csv"))
    with open(os.path.join(out_dir, "market_lookup_summary.json.part"), "w", encoding="utf-8") as f:
        json.dump(s, f, indent=1, allow_nan=False)
    os.replace(os.path.join(out_dir, "market_lookup_summary.json.part"), os.path.join(out_dir, "market_lookup_summary.json"))
    return s


def main():
    paths = sorted(glob.glob(os.path.join(DATA, "history", "processed", "base_actions_FY*.parquet")))
    if not paths:
        sys.exit("no history tables: run `python run.py get-history` (or the history workflow) first")
    s = run({year_of(p): p for p in paths})
    a = s["all_markets"].get(ALL, {})
    print(f"all markets, all sectors: {a.get('n_eval', 0):,} competed awards, single-offer share {100 * (a.get('share') or 0):.1f}%, "
          f"median offers {a.get('offers_median')}")
    for dim, g in s["groups"].items():
        print(f"{dim}: {g['groups']} groups, {g['groups_published_all_sectors']} with at least {MIN_CELL} competed awards (all sectors)")
    m = s["markets_naics4"]
    print("thinnest large industry groups: " + "; ".join(f"{r['naics4']} {r['title']} {100 * r['share']:.0f}%" for r in m["thinnest"]))
    print("deepest large industry groups: " + "; ".join(f"{r['naics4']} {r['title']} {100 * r['share']:.0f}%" for r in m["deepest"]))
    c = s["totals_check"]
    if c is None:
        print("totals check: results/history_by_fy.csv not found, skipped")
    else:
        print("totals check against results/history_by_fy.csv: " + ("every year and sector matches" if c["match"] else
                                                                     f"{len(c['differences'])} differences: {c['differences'][:5]}"))
    print(f"peak memory: {_drivers.peak_mb():.0f} MB")


if __name__ == "__main__":
    main()
