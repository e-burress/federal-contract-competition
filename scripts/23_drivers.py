"""
23_drivers.py - why do some competed awards get only one offer?  Which measurable circumstances go with single offers, and how much
do they explain?

What it does
  1. Shares.  For every factor below, the single-offer share of competed awards with a usable offer count (the eval set, as in
     19_history_atlas.py), with a 95% Wilson interval, pooled and for each sector (civilian except GSA, GSA, Department of Defense).
     Factors: award kind; size band of the initial value; set-aside group; product or service and the broad product/service category;
     industry sector (NAICS 2 digits); extent-competed code; solicitation procedure; pricing type; commercial-item procedures; whether the
     award carries a solicitation number; fiscal quarter, September, the last 7 days of September; buying-office workload (base awards
     per office per year, in bands); incumbency (the winning company won an award from the same office for the same 4-character
     product code in one of the three previous fiscal years; only for years whose three previous years are present); market depth
     (registered companies in the award's 6-digit NAICS, only if reference/market_depth_naics.csv exists).
  2. Model.  A logistic regression on a stratified random sample (equal quota per fiscal year x sector, fixed seed, at most 400,000)
     of competed purchase orders and definitive contracts.  Controls: agency (Defense: its component) and sector x fiscal year.
     Results are average marginal effects (percentage points, each level against the most common level of its factor) with 95%
     intervals from standard errors clustered by buying office.  Extent competed and solicitation procedure enter the model as one
     combined "competition route" factor, because the two codes almost always move together (F = SP1).
  3. Blocks.  The model is fitted on earlier years and scored on the latest complete year held out; the change in log loss and AUC
     when each block of factors is dropped (and when it is used alone) shows how much each block explains.  With one year only, the
     year is split by date: even days of the month train, odd days test.

Method notes
  - Reads one fiscal year at a time with explicit columns, so memory stays low with all 19 years.
  - The logit is fitted by Newton-Raphson on a sparse design (a tiny ridge, 1.0, keeps rare levels finite); tests check it
    against statsmodels.  Factor levels with fewer than 300 sampled awards are pooled into "other".
  - Defense data are published 90 days late: a Defense year whose September is missing or thin is left out and reported.
  - Office codes and company ids are used inside the computation only (workload, incumbency, clustering) and never written out.
  - These are associations, not causes.  Coding differences between agencies (results/quality_summary.json) can look like drivers.

Input : data/history/processed/base_actions_FY*.parquet (18_history_build.py), optional reference/market_depth_naics.csv
Output: results/drivers_shares.csv, results/drivers_model.csv, results/drivers_blocks.csv, results/drivers_summary.json,
        figures/drivers.png, and a printed summary
Usage : python -I scripts/23_drivers.py
"""
import glob
import json
import os
import re
import sys
import time

import numpy as np
import pandas as pd
from scipy import sparse
from scipy.special import expit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA, PROJECT, RESULTS  # noqa: E402

SEED = 20261109
MAX_SAMPLE = 400_000
MIN_LEVEL = 300            # sampled awards a factor level needs to get its own coefficient
RIDGE = 1.0
N_BOOT = 100
GSA = "General Services Administration"
SECTORS = ["civilian except GSA", "GSA", "Department of Defense"]

READ_COLS = ["awarding_agency_name", "awarding_sub_agency_name", "awarding_office_code", "award_kind", "action_date", "fy",
             "solicitation_identifier", "product_or_service_code", "naics_code", "extent_competed_code",
             "solicitation_procedures_code", "type_of_set_aside_code", "base_and_all_options_value",
             "type_of_contract_pricing_code", "commercial_item_acquisition_procedures_code", "vendor_id", "is_dod", "eval_set", "single"]

# ---- factor definitions and labels ---------------------------------------------------------------------------------------------------
SA_MAP = {"NONE": "no set-aside", "SBA": "small business", "SBP": "small business", "ESB": "small business", "RSB": "small business",
          "8A": "8(a)", "8AN": "8(a)", "HZC": "HUBZone", "HZS": "HUBZone", "SDVOSBC": "SDVOSB", "SDVOSBS": "SDVOSB",
          "WOSB": "WOSB/EDWOSB", "WOSBSS": "WOSB/EDWOSB", "EDWOSB": "WOSB/EDWOSB", "EDWOSBSS": "WOSB/EDWOSB",
          "VSA": "VOSB", "VSS": "VOSB", "IEE": "other", "ISBEE": "other", "BICiv": "other"}      # same groups as 03b_atlas_civilian.py
SIZE_BINS = [0, 25_000, 100_000, 250_000, 1_000_000, 10_000_000, np.inf]
SIZE_LABELS = ["<25K", "25-100K", "100-250K", "250K-1M", "1-10M", ">10M"]
WORK_BINS = [0, 100, 500, 2_000, 10_000, np.inf]
WORK_LABELS = ["1-99", "100-499", "500-1,999", "2,000-9,999", "10,000+"]
DEPTH_BINS = [-0.5, 0.5, 10, 100, 1_000, 10_000, np.inf]
DEPTH_LABELS = ["0", "1-9", "10-99", "100-999", "1,000-9,999", "10,000+"]
TIMING_LEVELS = ["Q1 Oct-Dec", "Q2 Jan-Mar", "Q3 Apr-Jun", "Q4 Jul-Aug", "September 1-23", "September 24-30"]

EXTENT = {"A": "full and open", "D": "full and open after exclusion of sources", "E": "follow-on to a competed action",
          "F": "competed under simplified acquisition", "CDO": "competitive delivery order"}
PROCEDURE = {"NP": "negotiated proposal or quote", "SP1": "simplified acquisition", "SB": "sealed bid", "TS": "two-step",
             "AE": "architect-engineer selection", "BR": "basic research", "AS": "alternative sources",
             "MAFO": "multiple-award fair opportunity", "SSS": "only one source"}
PRICING = {"J": "firm fixed price", "K": "fixed price with economic price adjustment", "A": "other fixed price", "B": "other fixed price",
           "L": "other fixed price", "M": "other fixed price", "R": "cost reimbursement", "S": "cost reimbursement",
           "T": "cost reimbursement", "U": "cost reimbursement", "V": "cost reimbursement", "Y": "time and materials or labor hours",
           "Z": "time and materials or labor hours", "1": "order dependent, combination or other", "2": "order dependent, combination or other",
           "3": "order dependent, combination or other"}
COMMERCIAL = {"A": "commercial products or services", "B": "supplies or services under FAR 12.102(f)",
              "C": "services under FAR 12.102(g)", "D": "commercial procedures not used"}
NAICS2 = {"11": "agriculture, forestry, fishing", "21": "mining, oil and gas", "22": "utilities", "23": "construction",
          "31-33": "manufacturing", "42": "wholesale trade", "44-45": "retail trade", "48-49": "transportation and warehousing",
          "51": "information", "52": "finance and insurance", "53": "real estate and rental", "54": "professional, scientific, technical services",
          "55": "management of companies", "56": "administrative support and waste services", "61": "educational services",
          "62": "health care and social assistance", "71": "arts and recreation", "72": "accommodation and food services",
          "81": "other services", "92": "public administration"}
PSC_SERVICE = {"A": "research and development", "B": "special studies and analyses", "C": "architect and engineering services",
               "D": "IT and telecom services", "E": "purchase of structures and facilities", "F": "natural resources management",
               "G": "social services", "H": "quality control, testing, inspection", "J": "maintenance and repair of equipment",
               "K": "modification of equipment", "L": "technical representative services", "M": "operation of facilities",
               "N": "installation of equipment", "P": "salvage services", "Q": "medical services",
               "R": "professional, administrative, management support", "S": "utilities and housekeeping",
               "T": "photo, mapping, printing, publication", "U": "education and training", "V": "transportation, travel, relocation",
               "W": "lease or rental of equipment", "X": "lease or rental of facilities", "Y": "construction of structures and facilities",
               "Z": "maintenance and repair of real property"}
PSC_PRODUCT = {"10": "weapons", "13": "ammunition and explosives", "15": "aircraft and airframe parts", "16": "aircraft components",
               "19": "ships and small craft", "20": "ship and marine equipment", "23": "motor vehicles, trailers, cycles",
               "25": "vehicle equipment components", "28": "engines and turbines", "29": "engine accessories",
               "30": "mechanical power transmission", "31": "bearings", "34": "metalworking machinery", "35": "service and trade equipment",
               "36": "special industry machinery", "37": "agricultural machinery", "38": "construction and mining equipment",
               "39": "materials handling equipment", "40": "rope, cable, chain", "41": "refrigeration and air conditioning",
               "42": "fire fighting, rescue, safety", "43": "pumps and compressors", "44": "furnaces, steam plants, drying",
               "45": "plumbing, heating, waste disposal", "46": "water purification", "47": "pipe, tubing, hose, fittings", "48": "valves",
               "49": "repair shop equipment", "51": "hand tools", "52": "measuring tools", "53": "hardware and abrasives",
               "54": "prefabricated structures", "55": "lumber and millwork", "56": "construction materials",
               "58": "communication and detection equipment", "59": "electrical and electronic components", "60": "fiber optics",
               "61": "electric wire and power distribution", "62": "lighting fixtures", "63": "alarm, signal, security detection",
               "65": "medical, dental, veterinary supplies", "66": "instruments and laboratory equipment", "67": "photographic equipment",
               "68": "chemicals", "69": "training aids and devices", "70": "IT equipment and software (pre-2020 codes)", "71": "furniture",
               "72": "household and commercial furnishings", "73": "food preparation equipment", "74": "office machines",
               "75": "office supplies", "76": "books, maps, publications", "77": "musical instruments", "78": "recreational and athletic equipment",
               "79": "cleaning equipment and supplies", "80": "brushes, paints, sealers", "81": "containers and packaging",
               "83": "textiles, leather, tents", "84": "clothing and individual equipment", "85": "toiletries", "87": "agricultural supplies",
               "88": "live animals", "89": "food (subsistence)", "91": "fuels and lubricants", "93": "nonmetal fabricated materials",
               "94": "nonmetal crude materials", "95": "metal bars, sheets, shapes", "96": "ores and minerals", "99": "miscellaneous"}

# factor name -> plain-English name (order = order of the shares table)
FACTORS = {"award_kind": "award kind", "size_band": "initial value", "setaside_grp": "set-aside", "prod_serv": "product or service",
           "psc_cat": "product/service category", "naics2": "industry (NAICS sector)", "extent_competed": "extent competed",
           "sol_procedure": "solicitation procedure", "route": "competition route", "pricing": "pricing type",
           "commercial": "commercial-item procedures", "has_sol_id": "solicitation number", "fquarter": "fiscal quarter",
           "september": "September", "last_week": "last 7 days of September", "timing": "timing in the fiscal year",
           "workload_band": "buying-office workload (awards per year)", "incumbent": "incumbent winner",
           "depth_band": "registered companies in the NAICS code", "fy": "fiscal year"}
BLOCKS = {"award type and size": ["award_kind", "size_band"],
          "set-aside": ["setaside_grp"],
          "competition route and solicitation number": ["route", "has_sol_id"],
          "what is bought": ["psc_cat", "naics2"],
          "contract terms": ["pricing", "commercial"],
          "timing (September rush)": ["timing"],
          "buying-office workload": ["workload_band"],
          "incumbency": ["incumbent"],
          "market depth": ["depth_match", "log_firms"],
          "agency or Defense component": ["org"]}
CONTROLS = ["org", "time_ctrl"]
NUMERIC = ["log_firms"]
GENERIC_LEVELS = {"other", "unknown", "blank"}


# ======================================================================================================================================
# per-year feature building (pure functions; the tests call them on synthetic tables)
# ======================================================================================================================================
def _cat(x):
    """x as a categorical Series (most input columns already are; categories are the distinct values)."""
    return x if isinstance(x.dtype, pd.CategoricalDtype) else x.astype("category")


def _from_codes(codes, labels, index):
    """categorical Series from integer codes into `labels` (labels may repeat; they are merged)."""
    uniq, inv = np.unique(np.asarray(labels, dtype=object).astype(str), return_inverse=True)
    return pd.Series(pd.Categorical.from_codes(inv[codes], uniq), index=index)


def _cmap(x, fn, missing=""):
    """apply fn (a function of a Series of stripped strings) to the DISTINCT values of x only, so millions of rows cost almost nothing.
    Missing values are passed to fn as `missing`.  Returns a categorical Series."""
    x = _cat(x)
    cats = pd.Series(list(x.cat.categories.astype(str)) + [missing], dtype=object).str.strip()
    lab = pd.Series(fn(cats), index=cats.index)
    codes = x.cat.codes.to_numpy()
    return _from_codes(np.where(codes < 0, len(cats) - 1, codes), lab.to_numpy(), x.index)


def _combine(a, b, sep=" / "):
    """categorical Series of 'a<sep>b' built from the two columns' codes."""
    a, b = _cat(a), _cat(b)
    la, lb = list(a.cat.categories.astype(str)) + ["blank"], list(b.cat.categories.astype(str)) + ["blank"]
    ca, cb = a.cat.codes.to_numpy().astype(np.int64), b.cat.codes.to_numpy().astype(np.int64)
    ca, cb = np.where(ca < 0, len(la) - 1, ca), np.where(cb < 0, len(lb) - 1, cb)
    labels = [f"{x}{sep}{y}" for x in la for y in lb]
    return _from_codes(ca * len(lb) + cb, labels, a.index)


def _known(x):
    """boolean array: value present and not blank."""
    x = _cat(x)
    good = (x.cat.categories.astype(str).str.strip() != "")
    good = np.append(np.asarray(good, dtype=bool), False)
    codes = x.cat.codes.to_numpy()
    return (codes >= 0) & good[codes]


def timing_flags(dates):
    """fiscal quarter, September, last 7 days of September and the combined timing level for a Series of dates."""
    m, d = dates.dt.month.to_numpy(), dates.dt.day.to_numpy()
    fq = np.select([m >= 10, m <= 3, m <= 6], [0, 1, 2], 3)
    sept = m == 9
    last = sept & (d >= 24)
    tim = np.select([m >= 10, m <= 3, m <= 6, m <= 8, ~last], [0, 1, 2, 3, 4], 5)
    cat = pd.Categorical.from_codes
    return pd.DataFrame({"fquarter": cat(fq, ["Q1", "Q2", "Q3", "Q4"]),
                         "september": cat(sept.astype(int), ["October-August", "September"]),
                         "last_week": cat(last.astype(int), ["rest of year", "September 24-30"]),
                         "timing": cat(tim, TIMING_LEVELS)}, index=dates.index)


def incumbency_keys(office, psc, vendor):
    """64-bit keys of (office, 4-character product code, company) and a mask of rows where all three are known."""
    o, p4, v = _cat(office), _cmap(psc, lambda c: c.str[:4]), _cat(vendor)
    ok = _known(o) & _known(p4) & _known(v)
    keys = np.zeros(len(o), dtype=np.uint64)
    if ok.any():
        # hashing a categorical hashes its values (categories by code), so keys agree across years with different categories
        parts = pd.DataFrame({"o": o[ok].reset_index(drop=True), "p": p4[ok].reset_index(drop=True), "v": v[ok].reset_index(drop=True)})
        keys[ok] = pd.util.hash_pandas_object(parts, index=False).to_numpy()
    return keys, ok


def incumbent_flags(keys, ok, prior_sets):
    """'yes' if the key appears in any earlier year's key set, 'no' if not; missing where the key is unknown or no earlier years given.
    prior_sets must hold only EARLIER fiscal years (the caller passes the three previous years, or None if any is absent)."""
    codes = np.full(len(keys), -1, dtype=np.int8)
    if prior_sets is not None:
        prior = np.unique(np.concatenate(prior_sets)) if len(prior_sets) else np.array([], dtype=np.uint64)
        codes[ok] = np.isin(keys[ok], prior).astype(np.int8)
    return pd.Categorical.from_codes(codes, ["no", "yes"])


def naics_sector(c):
    """2-digit NAICS sector (31-33, 44-45 and 48-49 merged) of a Series of NAICS code strings."""
    n2 = c.str[:2].replace({"31": "31-33", "32": "31-33", "33": "31-33", "44": "44-45", "45": "44-45", "48": "48-49", "49": "48-49"})
    return n2.where(n2.isin(list(NAICS2)), "unknown")


def psc_category(c):
    """products: 'P' + the 2-character group; services: 'S' + the letter."""
    prod = c.str[:1].str.match(r"^[0-9]$").fillna(False).to_numpy(bool)
    return np.where(c == "", "unknown", np.where(prod, "P" + c.str[:2], "S" + c.str[:1]))


def read_year(path):
    """one fiscal year's base actions with the columns we need; text columns come in as categoricals (compact)."""
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq
    text = ["awarding_office_code", "product_or_service_code", "naics_code", "commercial_item_acquisition_procedures_code", "vendor_id"]
    cols = {}
    for col in READ_COLS:            # one column at a time: only one column is ever decoded in full
        t = pq.read_table(path, columns=[col], read_dictionary=[col] if col in text else None)
        if col == "solicitation_identifier":
            sol = t.column(col)
            cols["has_sol"] = pc.fill_null(pc.not_equal(pc.utf8_trim_whitespace(sol), ""), False).to_numpy(zero_copy_only=False)
            del sol
        else:
            x = t.column(col).to_pandas()
            if x.dtype == object or pd.api.types.is_string_dtype(x.dtype):
                x = x.astype("category")
            cols[col] = x.to_numpy() if not isinstance(x.dtype, pd.CategoricalDtype) else x.array
        del t
        pa.default_memory_pool().release_unused()
    return pd.DataFrame(cols)


def add_factors(b, prior_sets=None, depth=None):
    """derive every factor column for one fiscal year's base actions (all rows; workload counts every base award of the office).
    Returns the factor table (categoricals) and the year's sorted incumbency keys."""
    idx = b.index
    is_dod = b.is_dod.fillna(False).to_numpy(bool)
    agency = _cat(b.awarding_agency_name)
    f = pd.DataFrame(index=idx)
    is_gsa = _cmap(agency, lambda c: np.where(c == GSA, "y", "n")).to_numpy() == "y"
    f["sector"] = pd.Categorical.from_codes(np.where(is_dod, 2, np.where(is_gsa, 1, 0)), SECTORS)
    sub = _cat(b.awarding_sub_agency_name)
    la = list(agency.cat.categories.astype(str)) + ["unknown"]
    ls = ["DoD: " + x for x in sub.cat.categories.astype(str)] + ["DoD: unknown"]
    ca, cs = agency.cat.codes.to_numpy().astype(np.int64), sub.cat.codes.to_numpy().astype(np.int64)
    ca, cs = np.where(ca < 0, len(la) - 1, ca), np.where(cs < 0, len(ls) - 1, cs)
    f["org"] = _from_codes(np.where(is_dod, len(la) + cs, ca), la + ls, idx)
    f["award_kind"] = _cmap(b.award_kind, lambda c: c.replace("", "unknown"))
    v = pd.to_numeric(b.base_and_all_options_value, errors="coerce").clip(lower=0)
    f["size_band"] = pd.cut(v, bins=SIZE_BINS, labels=SIZE_LABELS, right=False).cat.add_categories("unknown").fillna("unknown")
    f["setaside_grp"] = _cmap(b.type_of_set_aside_code, lambda c: c.map(SA_MAP).fillna("other/blank"))
    f["prod_serv"] = _cmap(b.product_or_service_code, lambda c: np.where(c == "", "unknown", np.where(
        c.str[:1].str.match(r"^[0-9]$").fillna(False).to_numpy(bool), "product", "service")))
    f["psc_cat"] = _cmap(b.product_or_service_code, psc_category)
    f["naics2"] = _cmap(b.naics_code, naics_sector)
    f["extent_competed"] = _cmap(b.extent_competed_code, lambda c: c.replace("", "blank"))
    f["sol_procedure"] = _cmap(b.solicitation_procedures_code, lambda c: c.replace("", "blank"))
    f["route"] = _combine(f.extent_competed, f.sol_procedure)
    f["pricing"] = _cmap(b.type_of_contract_pricing_code, lambda c: c.map(PRICING).fillna("blank"))
    f["commercial"] = _cmap(b.commercial_item_acquisition_procedures_code, lambda c: c.replace("", "blank"))
    has_sol = b.has_sol.to_numpy(bool) if "has_sol" in b else _known(b.solicitation_identifier)
    f["has_sol_id"] = pd.Categorical.from_codes(has_sol.astype(int), ["no solicitation number", "has a solicitation number"])
    dates = pd.to_datetime(b.action_date)
    f = f.join(timing_flags(dates))
    f["day"] = dates.dt.day.to_numpy().astype(np.int8)
    office = _cat(b.awarding_office_code)
    oc = office.cat.codes.to_numpy()
    okoff = _known(office)
    per_office = np.bincount(oc[okoff], minlength=len(office.cat.categories))
    load = np.where(okoff, per_office[np.where(oc < 0, 0, oc)], np.nan)
    f["workload_band"] = pd.cut(load, bins=WORK_BINS, labels=WORK_LABELS, right=False).add_categories("unknown").fillna("unknown")
    keys, ok = incumbency_keys(office, b.product_or_service_code, b.vendor_id)
    f["incumbent"] = incumbent_flags(keys, ok, prior_sets)
    f["office"] = office
    if depth is not None:
        firms = _cmap(b.naics_code, lambda c: c.str[:6].map(depth).fillna(-1).astype(str)).astype(str).astype(float).to_numpy()
        firms = np.where(firms < 0, np.nan, firms)
        f["depth_match"] = pd.Categorical.from_codes(np.isnan(firms).astype(int), ["matched", "no match"])
        f["depth_band"] = pd.cut(firms, bins=DEPTH_BINS, labels=DEPTH_LABELS).add_categories("no match").fillna("no match")
        f["log_firms"] = np.log1p(firms).astype(np.float32)
    f["fy"] = pd.Categorical(b.fy.astype(int).astype(str).to_numpy())
    f["single"] = b.single.fillna(False).to_numpy(bool)
    f["eval_set"] = b.eval_set.fillna(False).to_numpy(bool)
    return f, np.unique(keys[ok])


def dod_incomplete(dates, is_dod):
    """True if the Defense rows of this year look cut off: September (normally the busiest month) below half the average month."""
    d = pd.to_datetime(dates[is_dod])
    if len(d) == 0:
        return False
    per_month = d.dt.month.value_counts()
    others = per_month.drop(9, errors="ignore")
    return bool(per_month.get(9, 0) < 0.5 * others.mean()) if len(others) else False


# ======================================================================================================================================
# shares
# ======================================================================================================================================
def share_from_counts(g):
    """single-offer share with a 95% Wilson interval from summed counts (the same calculation as atlas_lib.share_table)."""
    from statsmodels.stats.proportion import proportion_confint
    g = g.copy()
    lo, hi = proportion_confint(g.k_single, g.n_eval, alpha=0.05, method="wilson")
    g["share"] = g.k_single / g.n_eval
    g["ci_lo"], g["ci_hi"] = lo, hi
    return g


def count_year(ev, factors):
    """per sector x factor level: single-offer awards and eval-set awards."""
    out = []
    for fac in factors:
        if fac not in ev:
            continue
        g = ev.groupby(["sector", fac], observed=True)["single"].agg(["sum", "count"]).reset_index()
        g.columns = ["scope", "level", "k_single", "n_eval"]
        g.insert(0, "factor", fac)
        out.append(g)
    return pd.concat(out, ignore_index=True)


def level_label(fac, level):
    level = str(level)
    if fac == "psc_cat":
        if level.startswith("P"):
            return f"product group {level[1:]}: {PSC_PRODUCT.get(level[1:], 'IT and telecom (2020 codes)' if level[1:2] == '7' else 'other products')}"
        if level.startswith("S"):
            return f"service {level[1:]}: {PSC_SERVICE.get(level[1:], 'other services')}"
        return level
    if fac == "naics2":
        return f"{level}: {NAICS2[level]}" if level in NAICS2 else level
    if fac == "extent_competed":
        return f"{level}: {EXTENT[level]}" if level in EXTENT else level
    if fac == "sol_procedure":
        return f"{level}: {PROCEDURE[level]}" if level in PROCEDURE else level
    if fac == "route" and " / " in level:
        e, p = level.split(" / ", 1)
        return f"{EXTENT.get(e, e)}; {PROCEDURE.get(p, p)}"
    if fac == "commercial":
        return f"{level}: {COMMERCIAL[level]}" if level in COMMERCIAL else level
    if fac == "award_kind":
        return {"PO": "purchase order", "DEF": "definitive contract", "IDV": "contract vehicle"}.get(level, level)
    if fac == "incumbent":
        return {"yes": "won from this office for this product code in the previous 3 years", "no": "not an incumbent"}.get(level, level)
    return level


# ======================================================================================================================================
# sparse logistic regression with office-clustered standard errors and average marginal effects
# ======================================================================================================================================
def _labels(x):
    """string labels of a column as a numpy object array ('unknown' for missing values)."""
    x = _cat(x)
    cats = np.append(np.asarray(x.cat.categories.astype(str), dtype=object), "unknown")
    codes = x.cat.codes.to_numpy()
    return cats[np.where(codes < 0, len(cats) - 1, codes)]


def level_spec(df, factors, min_n=MIN_LEVEL):
    """for each factor: reference level (most common) and the other levels that keep their own column; rare levels pool into 'other'."""
    spec = {}
    for fac in factors:
        vc = pd.Series(_labels(df[fac])).value_counts()
        big = list(vc[vc >= min_n].index)
        if not big:
            continue
        has_other = (vc[vc < min_n].sum() > 0) or ("other" in big)
        levels = [x for x in big[1:] if x != "other"] + (["other"] if has_other and big[0] != "other" else [])
        if levels:
            spec[fac] = (big[0], levels)
    return spec


def design(df, spec, numeric=()):
    """sparse design matrix: intercept, one column per non-reference level, numeric columns.  Returns X and column names.
    Values not seen when the spec was made go to 'other' if the factor has it, otherwise to the reference.
    Each factor adds at most one entry per row, so the CSR arrays are built directly (low memory)."""
    n = len(df)
    num = [c for c in numeric if c in df]
    slots = np.full((n, 1 + len(spec) + len(num)), -1, dtype=np.int32)    # column index of each row's entries, -1 = none
    slots[:, 0] = 0
    names = [("(intercept)", "")]
    for k, (fac, (ref, levels)) in enumerate(spec.items(), start=1):
        x = _cat(df[fac])
        cats = list(x.cat.categories.astype(str)) + ["unknown"]
        codes = x.cat.codes.to_numpy()
        codes = np.where(codes < 0, len(cats) - 1, codes)
        index = {lv: len(names) + i for i, lv in enumerate(levels)}
        names += [(fac, lv) for lv in levels]
        fallback = index.get("other", -1)
        cat_col = np.array([index.get(c, -1 if c == ref else fallback) for c in cats], dtype=np.int32)
        slots[:, k] = cat_col[codes]
    values = np.ones(slots.shape, dtype=np.float64) if num else None
    for k, c in enumerate(num, start=1 + len(spec)):
        v = df[c].to_numpy(float)
        slots[:, k] = len(names)
        values[:, k] = np.where(np.isfinite(v), v, 0.0)
        names.append((c, "per doubling"))
    keep = slots >= 0                                  # row-major order keeps each row's column indices increasing
    indptr = np.concatenate([[0], np.cumsum(keep.sum(axis=1))]).astype(np.int64)
    data = values[keep] if num else np.ones(int(indptr[-1]))
    X = sparse.csr_matrix((data, slots[keep], indptr), shape=(n, len(names)))
    return X, names


def _scaled(X, w):
    """X with row i multiplied by w[i] (shares X's index arrays)."""
    return sparse.csr_matrix((X.data * np.repeat(w, np.diff(X.indptr)), X.indices, X.indptr), shape=X.shape)


def _xtwx(X, w, chunk=100_000):
    """X' diag(w) X as a dense p x p array, built in row chunks to keep memory low."""
    p = X.shape[1]
    H = np.zeros((p, p))
    for i in range(0, X.shape[0], chunk):
        Xc = X[i:i + chunk]
        H += (Xc.T @ _scaled(Xc, w[i:i + chunk])).toarray()
    return H


def fit_logit(X, y, ridge=RIDGE, max_iter=60, tol=1e-8):
    """Newton-Raphson logit; a small ridge (not on the intercept) keeps levels with no variation finite."""
    n, p = X.shape
    y = np.asarray(y, float)
    beta = np.zeros(p)
    beta[0] = np.log(y.mean() / (1 - y.mean()))
    pen = np.full(p, float(ridge))
    pen[0] = 0.0
    for _ in range(max_iter):
        mu = expit(X @ beta)
        g = X.T @ (y - mu) - pen * beta
        H = _xtwx(X, mu * (1 - mu)) + np.diag(pen)
        step = np.linalg.solve(H + 1e-10 * np.eye(p), g)
        big = np.abs(step).max()
        if big > 5:
            step *= 5 / big
        beta += step
        if big < tol:
            break
    mu = expit(X @ beta)
    return beta, _xtwx(X, mu * (1 - mu)) + np.diag(pen)


def cluster_cov(X, y, beta, H, clusters):
    """sandwich covariance clustered by `clusters`, with the usual small-sample correction G/(G-1) x (n-1)/(n-p)."""
    n, p = X.shape
    r = np.asarray(y, float) - expit(X @ beta)
    codes, uniq = pd.factorize(pd.Series(_labels(pd.Series(clusters))))
    G = len(uniq)
    C = sparse.csr_matrix((np.ones(n), (codes, np.arange(n))), shape=(G, n))
    U = (C @ _scaled(X, r)).toarray()
    Hinv = np.linalg.inv(H)
    c = G / (G - 1) * (n - 1) / (n - p)
    return c * Hinv @ (U.T @ U) @ Hinv, G


def marginal_effects(X, names, beta, V, skip=()):
    """average marginal effect of each level against its reference (and of a doubling for numeric columns), with delta-method SEs."""
    n = X.shape[0]
    eta = X @ beta
    nnz = np.bincount(X.indices, minlength=X.shape[1])
    out = []
    facs = {}
    for j, (fac, lv) in enumerate(names):
        if j == 0 or fac in skip:
            continue
        facs.setdefault(fac, []).append(j)
    for fac, idx in facs.items():
        if fac in NUMERIC:
            j = idx[0]
            delta = np.log(2)
            l1, l0 = expit(eta + beta[j] * delta), expit(eta)
            d1, d0 = l1 * (1 - l1), l0 * (1 - l0)
            grad = (X.T @ (d1 - d0)) / n
            grad[j] += d1.mean() * delta
            out.append((fac, names[j][1], (l1 - l0).mean(), float(np.sqrt(grad @ V @ grad)), int(nnz[j])))
            continue
        base = eta - X[:, idx] @ beta[idx]
        l0 = expit(base)
        d0 = l0 * (1 - l0)
        for j in idx:
            l1 = expit(base + beta[j])
            d1 = l1 * (1 - l1)
            grad = (X.T @ (d1 - d0)) / n
            grad[idx] = 0.0
            grad[j] += d1.mean()
            out.append((fac, names[j][1], (l1 - l0).mean(), float(np.sqrt(grad @ V @ grad)), int(nnz[j])))
    t = pd.DataFrame(out, columns=["factor", "level", "ame", "se", "n_level"])
    t["ci_lo"], t["ci_hi"] = t.ame - 1.96 * t.se, t.ame + 1.96 * t.se
    return t


def year_span(years):
    """'FY2009-FY2011, FY2018' style list of fiscal years."""
    ys, out = sorted(int(y) for y in set(years)), []
    for y in ys:
        if out and y == out[-1][1] + 1:
            out[-1][1] = y
        else:
            out.append([y, y])
    return ", ".join(f"FY{a}" if a == b else f"FY{a}-FY{b}" for a, b in out)


def log_loss(y, p):
    p = np.clip(p, 1e-12, 1 - 1e-12)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def auc(y, p):
    """area under the ROC curve via ranks (ties get average ranks)."""
    y = np.asarray(y, bool)
    r = pd.Series(p).rank().to_numpy()
    n1, n0 = y.sum(), (~y).sum()
    return float((r[y].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)) if n1 and n0 else float("nan")


def fit_scope(s, factors, controls, numeric):
    """fit one model on sample `s`; return the marginal-effects table, the in-sample fit and the spec."""
    spec = level_spec(s, factors + controls)
    X, names = design(s, spec, numeric)
    y = s.single.to_numpy(float)
    beta, H = fit_logit(X, y)
    V, G = cluster_cov(X, y, beta, H, s.office.to_numpy())
    me = marginal_effects(X, names, beta, V, skip=set(controls))
    p = expit(X @ beta)
    ll0 = log_loss(y, np.full(len(y), y.mean()))
    fit = {"n": int(len(y)), "single_share": float(y.mean()), "n_clusters_offices": int(G), "n_parameters": int(X.shape[1]),
           "log_loss": log_loss(y, p), "mcfadden_r2": 1 - log_loss(y, p) / ll0, "auc_in_sample": auc(y, p)}
    me["reference"] = [spec[f][0] if f in spec else "" for f in me.factor]
    raw = {}
    for fac in me.factor.unique():
        if fac in s and fac not in NUMERIC:
            ref, levels = spec[fac]
            lab = _labels(s[fac])
            lab = np.where(np.isin(lab, levels + [ref]), lab, "other") if "other" in levels else lab
            raw[fac] = pd.Series(y).groupby(lab).mean()
    me["single_share_level"] = [float(raw[f].get(lv, np.nan)) if f in raw else np.nan for f, lv in zip(me.factor, me.level)]
    return me, fit


# ======================================================================================================================================
# block importance on a held-out period
# ======================================================================================================================================
def block_importance(s, blocks, numeric, test_year=None, seed=SEED, n_boot=N_BOOT):
    """fit on the training part, score the test part with each block dropped and alone; bootstrap intervals on the test rows."""
    if test_year is None:
        tr, te = s[s.day % 2 == 0], s[s.day % 2 == 1]
        split = "one fiscal year: even days of the month train, odd days test"
    else:
        tr, te = s[s.fy.astype(int) < test_year], s[s.fy.astype(int) == test_year]
        split = f"train {year_span(tr.fy.astype(int).unique())}, test FY{test_year}"
    ytr, yte = tr.single.to_numpy(float), te.single.to_numpy(float)
    rate = ytr.mean()

    def predict(facs):
        fac_cat = [f for f in facs if f not in NUMERIC]
        num = [f for f in facs if f in NUMERIC]
        spec = level_spec(tr, fac_cat)
        Xtr, _ = design(tr, spec, num)
        beta, _ = fit_logit(Xtr, ytr)
        Xte, _ = design(te, spec, num)
        return expit(Xte @ beta)

    all_f = [f for fs in blocks.values() for f in fs]
    p_full = predict(all_f)
    p_base = np.full(len(yte), rate)
    rng = np.random.default_rng(seed)
    boots = [rng.integers(0, len(yte), len(yte)) for _ in range(n_boot)]
    ll_full, ll_base, auc_full = log_loss(yte, p_full), log_loss(yte, p_base), auc(yte, p_full)
    rows = []
    for name, fs in blocks.items():
        p_drop = predict([f for f in all_f if f not in fs])
        p_alone = predict(fs)
        dl = [log_loss(yte[b], p_drop[b]) - log_loss(yte[b], p_full[b]) for b in boots]
        da = [auc(yte[b], p_full[b]) - auc(yte[b], p_drop[b]) for b in boots]
        ll_drop, ll_alone = log_loss(yte, p_drop), log_loss(yte, p_alone)
        rows.append({"block": name, "factors": " + ".join(fs),
                     "log_loss_full": ll_full, "log_loss_without": ll_drop, "log_loss_increase_without": ll_drop - ll_full,
                     "log_loss_increase_ci_lo": float(np.percentile(dl, 2.5)), "log_loss_increase_ci_hi": float(np.percentile(dl, 97.5)),
                     "share_of_gain_lost_without": (ll_drop - ll_full) / (ll_base - ll_full) if ll_base > ll_full else np.nan,
                     "auc_full": auc_full, "auc_without": auc(yte, p_drop), "auc_drop_without": auc_full - auc(yte, p_drop),
                     "auc_drop_ci_lo": float(np.percentile(da, 2.5)), "auc_drop_ci_hi": float(np.percentile(da, 97.5)),
                     "log_loss_alone": ll_alone, "share_of_gain_alone": (ll_base - ll_alone) / (ll_base - ll_full) if ll_base > ll_full else np.nan,
                     "auc_alone": auc(yte, p_alone)})
    t = pd.DataFrame(rows).sort_values("log_loss_increase_without", ascending=False)
    t.insert(1, "split", split)
    t.insert(2, "n_train", len(tr))
    t.insert(3, "n_test", len(te))
    t["log_loss_base_rate"] = ll_base
    return t


# ======================================================================================================================================
# main
# ======================================================================================================================================
def stack(parts):
    """concatenate yearly samples column by column, merging categories (keeps the sample compact)."""
    from pandas.api.types import union_categoricals
    out = {}
    for col in parts[0].columns:
        if isinstance(parts[0][col].dtype, pd.CategoricalDtype):
            out[col] = union_categoricals([p[col] for p in parts])
        else:
            out[col] = np.concatenate([p[col].to_numpy() for p in parts])
    return pd.DataFrame(out)


def load_market_depth():
    """registered companies per 6-digit NAICS from reference/market_depth_naics.csv, or (None, reason)."""
    path = os.path.join(PROJECT, "reference", "market_depth_naics.csv")
    if not os.path.exists(path):
        return None, "reference/market_depth_naics.csv not found, so market depth was skipped"
    t = pd.read_csv(path, dtype=str)
    ncol = next((c for c in t.columns if "naics" in c.lower()), None)
    num = [c for c in t.columns if c != ncol and pd.to_numeric(t[c], errors="coerce").notna().mean() > 0.9]
    ccol = "n_any" if "n_any" in num else next((c for c in num if re.search(r"firm|regist|entit|compan|vendor|count|^n_", c.lower())), None)
    if ncol is None or ccol is None:
        return None, f"reference/market_depth_naics.csv has unrecognized columns {list(t.columns)}, so market depth was skipped"
    t["naics6"] = t[ncol].str.strip().str[:6]
    s = pd.to_numeric(t[ccol], errors="coerce").groupby(t.naics6).sum()
    return s, (f"reference/market_depth_naics.csv: column '{ccol}' (companies registered in SAM.gov listing the code) by '{ncol}', "
               f"{len(s)} NAICS codes; codes not in the table (often retired codes in older years) form their own 'no match' level")


def peak_mb():
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            class PMC(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                            ("WorkingSetSize", ctypes.c_size_t), ("a", ctypes.c_size_t), ("b", ctypes.c_size_t),
                            ("c", ctypes.c_size_t), ("d", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                            ("PeakPagefileUsage", ctypes.c_size_t)]
            k32 = ctypes.windll.kernel32
            k32.GetCurrentProcess.restype = wintypes.HANDLE
            k32.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
            c = PMC()
            c.cb = ctypes.sizeof(PMC)
            k32.K32GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(c), c.cb)
            return c.PeakWorkingSetSize / 2**20
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    except Exception:
        return float("nan")


def main():
    t0 = time.time()
    files = sorted(glob.glob(os.path.join(DATA, "history", "processed", "base_actions_FY*.parquet")))
    if not files:
        sys.exit("no history tables: run `python run.py get-history` (or the history workflow) first")
    years = {int(re.search(r"FY(\d{4})", os.path.basename(p)).group(1)): p for p in files}
    present = sorted(years)
    inc_years = [y for y in present if all(y - k in years for k in (1, 2, 3))]
    model_years = inc_years or present
    use_inc = bool(inc_years)
    quota = MAX_SAMPLE // (len(SECTORS) * len(model_years))
    depth, depth_note = load_market_depth()
    print(f"fiscal years present: {', '.join(map(str, present))}")
    print(f"incumbency computed for: {', '.join(map(str, inc_years)) or 'none (needs the three previous years)'}")
    print(f"model years: {', '.join(map(str, model_years))}; sample quota {quota:,} per fiscal year x sector")
    print(depth_note)

    factors_share = [f for f in FACTORS if f != "depth_band" or depth is not None]
    counts, samples, prior, dod_cut, inc_rate = [], [], {}, [], {}
    for y in present:
        b = read_year(years[y])
        sets = [prior[y - k] for k in (1, 2, 3)] if y in inc_years else None
        cut = dod_incomplete(b.action_date, b.is_dod.fillna(False).to_numpy(bool))
        f, keyset = add_factors(b, sets, depth)
        del b
        prior[y] = keyset
        prior.pop(y - 3, None)
        if cut:
            dod_cut.append(y)
            f = f[f.sector != SECTORS[2]]
        ev = f[f.eval_set]
        del f
        counts.append(count_year(ev, factors_share))
        if y in inc_years:
            inc_rate[y] = float((ev.incumbent == "yes").sum() / ev.incumbent.notna().sum())
        if y in model_years:
            m = ev[ev.award_kind.isin(["PO", "DEF"])]
            if use_inc:
                m = m[m.incumbent.notna()]
            for si, sec in enumerate(SECTORS):
                idx = np.flatnonzero((m.sector == sec).to_numpy())
                if len(idx) > quota:
                    idx = np.sort(np.random.default_rng([SEED, y, si]).choice(idx, quota, replace=False))
                samples.append(m.iloc[idx].drop(columns=["eval_set"]).reset_index(drop=True))
        print(f"  FY{y}: {len(ev):,} competed awards with usable offers" + ("  (Defense rows left out: September missing or thin)" if cut else "")
              + f"  [{time.time() - t0:.0f}s, peak {peak_mb():.0f} MB]", flush=True)
        del ev

    # ---- 1. shares ------------------------------------------------------------------------------------------------------------------
    c = pd.concat(counts, ignore_index=True)
    c = c.groupby(["factor", "scope", "level"], as_index=False)[["k_single", "n_eval"]].sum()
    pooled = c.groupby(["factor", "level"], as_index=False)[["k_single", "n_eval"]].sum().assign(scope="all sectors")
    sh = share_from_counts(pd.concat([pooled, c], ignore_index=True))
    sh["factor_name"] = sh.factor.map(FACTORS)
    sh["label"] = [level_label(f, lv) for f, lv in zip(sh.factor, sh.level)]
    order = {f: i for i, f in enumerate(FACTORS)}
    scope_order = {s: i for i, s in enumerate(["all sectors"] + SECTORS)}
    sh = sh.sort_values(["factor", "scope", "n_eval"], key=lambda s: s.map(order) if s.name == "factor" else
                        (s.map(scope_order) if s.name == "scope" else -s)).reset_index(drop=True)
    sh = sh[["factor", "factor_name", "scope", "level", "label", "k_single", "n_eval", "share", "ci_lo", "ci_hi"]]
    sh.to_csv(os.path.join(RESULTS, "drivers_shares.csv"), index=False)

    # ---- 2. model -------------------------------------------------------------------------------------------------------------------
    s = stack(samples)
    del samples
    s["time_ctrl"] = _combine(s.sector, s.fy, " FY")
    model_factors = ["award_kind", "size_band", "setaside_grp", "route", "has_sol_id", "psc_cat", "naics2", "pricing", "commercial",
                     "timing", "workload_band"] + (["incumbent"] if use_inc else []) + (["depth_match"] if depth is not None else [])
    numeric = ["log_firms"] if depth is not None else []
    print(f"\nmodel sample: {len(s):,} purchase orders and definitive contracts "
          + ", ".join(f"{k} {v:,}" for k, v in s.sector.value_counts().items()))
    tables, fits = [], {}
    for scope in ["all sectors"] + SECTORS:
        d = s if scope == "all sectors" else s[s.sector == scope]
        if len(d) < 2_000 or d.single.nunique() < 2:
            continue
        ctrl = ["org", "time_ctrl"]
        me, fit = fit_scope(d, model_factors, ctrl, numeric)
        tables.append(me.assign(scope=scope))
        fits[scope] = fit
        print(f"  fitted {scope}: n = {fit['n']:,}, AUC {fit['auc_in_sample']:.3f}, McFadden R2 {fit['mcfadden_r2']:.3f}"
              f"  [{time.time() - t0:.0f}s, peak {peak_mb():.0f} MB]", flush=True)
    me = pd.concat(tables, ignore_index=True)
    me["factor_name"] = me.factor.map(lambda f: FACTORS.get(f, "registered companies in the NAICS code (log)" if f == "log_firms" else
                                                             "NAICS code found in the market-depth table" if f == "depth_match" else f))
    me["block"] = me.factor.map({f: b for b, fs in BLOCKS.items() for f in fs})
    me["label"] = [level_label(f, lv) for f, lv in zip(me.factor, me.level)]
    me["reference_label"] = [level_label(f, r) for f, r in zip(me.factor, me.reference)]
    for col in ["ame", "se", "ci_lo", "ci_hi"]:
        me[col + "_points"] = 100 * me[col]
    me = me[["scope", "block", "factor", "factor_name", "level", "label", "reference", "reference_label", "n_level", "single_share_level",
             "ame_points", "ci_lo_points", "ci_hi_points", "se_points"]]
    me.to_csv(os.path.join(RESULTS, "drivers_model.csv"), index=False)

    # ---- 3. blocks ------------------------------------------------------------------------------------------------------------------
    blocks = {k: [f for f in v if f in model_factors + numeric + ["org"]] for k, v in BLOCKS.items()}
    blocks = {k: v for k, v in blocks.items() if v}
    complete = [y for y in model_years if y not in dod_cut]
    test_year = max(complete) if len(model_years) > 1 and complete and min(model_years) < max(complete) else None
    bt = block_importance(s, blocks, numeric, test_year)
    bt.to_csv(os.path.join(RESULTS, "drivers_blocks.csv"), index=False)
    print(f"  blocks scored ({bt.split.iloc[0]})  [{time.time() - t0:.0f}s, peak {peak_mb():.0f} MB]", flush=True)

    # ---- summary, figure, printout --------------------------------------------------------------------------------------------------
    pooled_me = me[(me.scope == "all sectors") & ~me.level.isin(GENERIC_LEVELS) & ~me.level.str.contains("other|blank|unknown")
                   & (me.n_level >= 1_000)].copy()
    # ranked by the end of the 95% interval nearest zero: large AND precisely estimated effects come first
    pooled_me["abs"] = np.where(pooled_me.ci_lo_points > 0, pooled_me.ci_lo_points,
                                np.where(pooled_me.ci_hi_points < 0, -pooled_me.ci_hi_points, 0.0))
    top_levels = pooled_me[pooled_me["abs"] > 0].sort_values("abs", ascending=False)
    top5 = top_levels.drop_duplicates("factor").head(5)
    # the chart: the largest effects, at most two levels per factor (otherwise the many product categories crowd out the rest)
    chart = top_levels.groupby("factor", sort=False).head(2).head(14)
    tot = sh[(sh.factor == "fy")].groupby("scope")[["k_single", "n_eval"]].sum()
    summary = {
        "question": "Which measurable circumstances go with single offers on competed federal awards, and how much do they explain?",
        "years_present": present, "years_in_shares": present, "years_in_model": model_years,
        "defense_years_left_out_incomplete": dod_cut, "incumbency_years": inc_years,
        "incumbency_rate_by_year": {str(k): round(v, 4) for k, v in inc_rate.items()},
        "market_depth": depth_note,
        "single_offer_share": {k: {"share": round(v.k_single / v.n_eval, 4), "n_eval": int(v.n_eval)} for k, v in tot.iterrows()},
        "sample": {"design": "equal random quota per fiscal year x sector (each sector-year counts equally), competed purchase orders and "
                             "definitive contracts with usable offers" + (", with a known incumbency status" if use_inc else ""),
                   "quota_per_stratum": quota, "seed": SEED},
        "model": {"controls": "agency (Defense: its component) and sector x fiscal year; their effects are not reported",
                  "standard_errors": "clustered by buying office", "fit": fits},
        "top_five_factors_ranking": "one level per factor; ranked by the end of the 95% interval nearest zero (large and precise)",
        "top_five_factors": [{"factor": r.factor_name, "level": r.label, "versus": r.reference_label,
                              "marginal_effect_points": round(r.ame_points, 2),
                              "ci_95_points": [round(r.ci_lo_points, 2), round(r.ci_hi_points, 2)], "n_sampled": int(r.n_level)}
                             for r in top5.itertuples()],
        "blocks": {"split": bt.split.iloc[0], "n_train": int(bt.n_train.iloc[0]), "n_test": int(bt.n_test.iloc[0]),
                   "auc_full_model": round(float(bt.auc_full.iloc[0]), 4),
                   "ranked_by_log_loss_increase_when_dropped": [
                       {"block": r.block, "log_loss_increase": round(r.log_loss_increase_without, 5),
                        "share_of_gain_lost": round(r.share_of_gain_lost_without, 3), "auc_drop": round(r.auc_drop_without, 4),
                        "share_of_gain_alone": round(r.share_of_gain_alone, 3)} for r in bt.itertuples()]},
        "caveats": ["Associations, not causes: the factors travel together and with things the data do not record.",
                    "Coding differences between agencies and over time (results/quality_summary.json) can look like drivers; agency and "
                    "sector x year controls absorb level differences but not coding that differs within an agency.",
                    "Company ids changed when identifiers moved from DUNS to UEI in 2022, so incumbency is undercounted around that switch.",
                    "Defense data arrive 90 days late; an incomplete Defense year is left out.",
                    "Market depth is a count of companies registered in SAM.gov on one recent date, applied to every year; older years "
                    "and retired NAICS codes fit it less well.",
                    "Out-of-time log loss also moves with shifts in the overall single-offer rate between years; AUC (ranking only) is "
                    "the steadier measure of how much a block explains.",
                    "The sample gives each sector-year the same weight, so pooled effects are not dominated by the very many small "
                    "Defense and GSA purchase orders; they describe that balanced mix, not every award equally."],
        "runtime_seconds": round(time.time() - t0, 1), "peak_memory_mb": round(peak_mb()),
    }
    with open(os.path.join(RESULTS, "drivers_summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=1)
    plot(chart, model_years)

    print("\nSINGLE-OFFER SHARE BY FACTOR, all sectors (competed awards with usable offers)")
    show = sh[(sh.scope == "all sectors") & (sh.factor != "psc_cat") & (sh.n_eval >= 500)]
    print(show[["factor", "label", "n_eval", "share", "ci_lo", "ci_hi"]].round(3).to_string(index=False))
    print("\nLARGEST AVERAGE MARGINAL EFFECTS, all sectors (percentage points, versus the most common level; at most 2 per factor)")
    print(chart[["factor_name", "label", "reference_label", "ame_points", "ci_lo_points", "ci_hi_points"]]
          .round(2).to_string(index=False))
    print(f"\nHOW MUCH EACH BLOCK EXPLAINS ({bt.split.iloc[0]}; full model AUC {bt.auc_full.iloc[0]:.3f})")
    print(bt[["block", "log_loss_increase_without", "share_of_gain_lost_without", "auc_drop_without", "share_of_gain_alone", "auc_alone"]]
          .round(4).to_string(index=False))
    print(f"\ndone in {time.time() - t0:.0f}s, peak memory {peak_mb():.0f} MB")


def plot(top, model_years):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    top = top.iloc[::-1]
    ink, muted, blue = "#333333", "#666666", "#2a78d6"
    fig, ax = plt.subplots(figsize=(11, 0.45 * len(top) + 1.8), layout="constrained")
    ypos = np.arange(len(top))
    ax.axvline(0, color="#8c8c8c", lw=1)
    ax.hlines(ypos, top.ci_lo_points, top.ci_hi_points, color=blue, lw=2)
    ax.plot(top.ame_points, ypos, "o", color=blue, ms=7, mec="white", mew=1.5)
    labels = [f"{r.factor_name}: {r.label}\n(vs {r.reference_label})" for r in top.itertuples()]
    ax.set_yticks(ypos, labels, fontsize=7.5, color=ink)
    ax.set_xlabel("change in the chance of a single offer, percentage points (dot) and 95% interval (line)", color=ink, fontsize=9)
    fig.suptitle("Circumstances that go with single offers on competed awards", fontsize=11, x=0.01, ha="left", color=ink)
    ax.set_title(f"Largest average marginal effects, all sectors, {year_span(model_years)}; at most two levels per factor.\n"
                 "Each level is compared with the most common level of its factor. Associations, not causes.",
                 fontsize=8, loc="left", color=muted)
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.tick_params(axis="y", length=0)
    ax.grid(axis="x", color="#e6e6e6", lw=0.8)
    ax.set_axisbelow(True)
    os.makedirs(os.path.join(PROJECT, "figures"), exist_ok=True)
    fig.savefig(os.path.join(PROJECT, "figures", "drivers.png"), dpi=150, bbox_inches="tight", pad_inches=0.15)
    plt.close(fig)


if __name__ == "__main__":
    main()
