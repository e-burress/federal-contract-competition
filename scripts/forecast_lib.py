"""
forecast_lib.py - the shared core of forecast model version 2: the chance that a solicitation's competed award gets ONE offer.

Nothing runs when this file is imported.  Training (scripts/30_model_v2.py) and daily scoring use the same functions, so a
feature means the same thing in both.  Scoring needs only numpy, pandas and the saved model file.

THE DAILY JOB (score this morning's new solicitations)
    import forecast_lib as fl
    model = fl.load_model(path)                               # <DATA>/models/model_v2.json (private: it holds office history)
    feats = fl.build_features(sols, model["history"], as_of="2026-11-10")
    p     = fl.predict(model, feats)                          # calibrated P(single offer), numpy array, one per row of sols
                                                              # (the correction follows each row's sector, feats["sector"])
    base  = fl.baseline_predictions(model, feats)             # DataFrame: p_sector_rate, p_agency_rate, p_office_history
  `sols` is a DataFrame with one row per solicitation.  Columns (all optional except the first two; missing ones count as unknown):
    sol_norm or sol_number   the solicitation number (it is normalised here: upper case, letters and digits only)
    first_posted             date and time the solicitation was FIRST posted
    window_first_days        days from first posting to the FIRST response deadline (or give first_deadline instead)
    type_first               "Solicitation" or "Combined Synopsis/Solicitation"
    base_type_first          type of the first notice in the chain (a chain that began as a Presolicitation counts as "has presol")
    set_aside_code, naics, psc, department, sub_tier, aac_code     as written on the first version of the notice (SAM.gov names)
    kw_brand, kw_sole        0/1 keyword flags from common.kw_flags(title, description start)
    presol_first_posted, ss_first_posted    date of the earliest Presolicitation / Sources Sought notice with the same number
                             (used only if it is BEFORE first_posted); or give has_presol / has_ss as 0/1 flags known at posting
  Fresher award history (optional).  The model file carries the history tables as they were on the training day.  To refresh:
    new  = fl.history_from_awards(fl.prepare_awards(base_actions))     # base_actions: rows of base_actions_FY<year>.parquet
    hist = fl.merge_history(model["history"], new)                      # then pass `hist` to build_features
  Rows are public records about markets.  A high probability is a risk indicator, not a statement about anyone's conduct.

NO LEAKAGE (what a feature may use)
  Only facts on the first version of the notice, plus award history that was already published on the posting day.
  History = competed awards with a usable offer count, counted per calendar quarter (calendar quarters are fiscal quarters).
  A solicitation posted in quarter Q uses the WINDOW_QUARTERS quarters that end at quarter Q - LAG_QUARTERS (the "mo_" features
  compare the last RECENT_QUARTERS of them with all of them).  With LAG_QUARTERS = 2
  the newest award used is 3 to 6 months old on the posting day.  That covers the 90-day delay of Defense data.
  Never used: number of amendments, the final deadline, award notices, the award's value or winner.

OUTCOME (the same rule as scripts/09_resolve_forecast.py)
  Normalise the solicitation number.  Among base actions (purchase orders, definitive contracts, contract vehicles) with the same
  normalised solicitation_identifier and an action date on or after the first posting day (at most MAX_WAIT_DAYS later), take the
  earliest (ties: the larger base-and-all-options value).  Resolved only if it was competed (extent A, D, E, F, CDO) with 1 to 998
  offers.  y = 1 if exactly one offer.

MAIN FUNCTIONS
  norm, norm_series                        solicitation number -> upper-case letters and digits
  prepare_awards(df)                       base-action rows -> the compact columns used here
  solicitation_index(sols), earliest_awards(sols, awards, previous)   outcome rule, one award file at a time
  label_outcomes(sols, best)               matched / resolved / y for each solicitation
  history_from_awards(awards), merge_history(a, b), trim_history(h, first_q)       quarterly history tables
  build_features(sols, history, as_of)     the feature table (training and scoring)
  design_matrix / fit_spec                 used by training only (scipy sparse)
  interaction_column, feature_column       sector x another category (INTERACTIONS), built from the feature table when needed
  predict_raw, predict, baseline_predictions, save_model, load_model
  apply_calibration(p, cal, sector)        the probability correction expit(a + b * logit(p)); with a correction by sector
                                           group in the model file, each row uses its group's a and b (CAL_GROUPS), and a
                                           row of another sector ("unknown") uses the pooled a and b
"""
import json
import os

import numpy as np
import pandas as pd

MODEL_VERSION = "v2"
LAG_QUARTERS = 2           # newest history quarter used = posting quarter - 2
WINDOW_QUARTERS = 12       # three years of history
RECENT_QUARTERS = 4        # "momentum" features compare the last year of that window with all of it
M_SHRINK = 20.0            # pseudo-observations at the parent rate (office -> agency, product code -> product group ...)
PRIOR_RATE = 0.20          # used only when there is no history at all
MAX_WAIT_DAYS = 730        # an award later than this after first posting is not matched
MIN_SOL_LEN = 8            # shorter solicitation numbers ("RFQ1", "0001") collide across agencies and are not matched
COMPETED = ("A", "D", "E", "F", "CDO")
DOD = "Department of Defense"
GSA_NAME = "General Services Administration"
SECTORS = ["civilian except GSA", "GSA", DOD]
UNKNOWN = "unknown"
EPS = 1e-4
_QBASE = 100_000           # quarter indices are below this; key code * _QBASE + quarter is one sortable number

AWARD_READ_COLS = ["solicitation_identifier", "action_date", "awarding_agency_name", "awarding_sub_agency_name",
                   "awarding_office_code", "award_kind", "extent_competed_code", "offers", "product_or_service_code",
                   "naics_code", "base_and_all_options_value"]
HISTORY_LEVELS = ["all", "sector", "agency", "subagency", "office", "prefix", "pscg", "psc4", "naics3", "naics6"]
INFO_LEVELS = ("office", "prefix")       # these also remember which agency the key belongs to
PRIVATE_LEVELS = ("office", "prefix")    # office-level: never written to a public file

# ---- the feature lists ------------------------------------------------------------------------------------------------
CAT_FEATURES = ["sector", "agency", "subagency", "setaside", "notice_type", "sol_letter", "pscg", "naics3", "month"]
CONT_FEATURES = ["lo_all", "lo_agency", "lo_subagency", "mo_subagency", "lo_office", "mo_office", "log_office_n",
                 "lo_prefix", "mo_prefix", "log_prefix_n", "lo_pscg", "lo_psc4", "lo_naics", "log_window", "log_depth"]
BIN_FEATURES = ["office_known", "prefix_known", "short7", "short14", "window_missing", "late_sept", "kw_brand", "kw_sole",
                "has_presol", "has_ss", "depth_missing", "naics_missing"]
# version 1's feature set, rebuilt with time-aware office history (version 1 used out-of-fold encoding)
V1_CAT = ["agency", "pscg", "naics3", "setaside", "sol_letter", "notice_type"]
V1_CONT = ["lo_office", "log_office_n", "log_window"]
V1_BIN = ["short14", "has_presol", "has_ss", "kw_brand", "kw_sole"]
# feature blocks (for the "what does each block add" table) and one line of justification each
BLOCKS = {
    "agency": (["sector", "agency", "subagency", "lo_agency", "lo_subagency", "mo_subagency"],
               "Who buys. Single-offer shares differ a lot between agencies and components. Includes the Defense flag (sector)."),
    "office history": (["lo_office", "mo_office", "log_office_n", "office_known", "lo_prefix", "mo_prefix", "log_prefix_n", "prefix_known"],
                       "The buying office's own past single-offer rate (shrunk toward its agency), its last year against the "
                       "three-year rate, and its volume. Two keys: the award office code, and the first six characters of the "
                       "solicitation number."),
    "product and industry": (["pscg", "naics3", "lo_pscg", "lo_psc4", "lo_naics", "naics_missing"],
                             "What is bought: product or service code and industry, with their past single-offer rates."),
    "set-aside": (["setaside"], "Small-business set-asides change who may bid."),
    "notice type": (["notice_type", "sol_letter"], "Solicitation or combined synopsis, and the letter in the number "
                                                  "(Q quote, R proposal, B sealed bid, T automated quote)."),
    "response window": (["log_window", "short7", "short14", "window_missing"], "Days given to respond on the first version."),
    "calendar": (["month", "late_sept"], "Month of posting and the last two weeks of the fiscal year."),
    "keywords": (["kw_brand", "kw_sole"], "Brand-name and sole-source wording in the title or first lines."),
    "earlier notice": (["has_presol", "has_ss"], "A presolicitation or sources-sought notice came first (market research)."),
    "market depth": (["log_depth", "depth_missing"], "Registered companies in the industry (one 2026 snapshot, see caveats)."),
    "trend": (["lo_all"], "The government-wide single-offer rate in the history window (lets the base rate drift)."),
}
SETASIDE_MAP = {"": "none", "NONE": "none", "NAN": "none", "<NA>": "none", "SBA": "small business", "SBP": "small business",
                "ESB": "small business", "RSB": "small business", "8A": "8(a)", "8AN": "8(a)", "HZC": "HUBZone", "HZS": "HUBZone",
                "SDVOSBC": "SDVOSB", "SDVOSBS": "SDVOSB", "WOSB": "WOSB", "WOSBSS": "WOSB", "EDWOSB": "WOSB", "EDWOSBSS": "WOSB",
                "VSA": "VOSB", "VSS": "VOSB", "VSB": "VOSB"}
# sector interactions: one more category feature per pair, with levels "sector | level" (built from the two columns when needed,
# so the feature table itself does not change).  Sector is the sector known on the posting day.
INTERACTIONS = {"sector_x_setaside": ("sector", "setaside"), "sector_x_notice_type": ("sector", "notice_type"),
                "sector_x_sol_letter": ("sector", "sol_letter"), "sector_x_pscg": ("sector", "pscg")}
# the probability correction by sector group (a sector not listed here, such as "unknown", gets the one pooled correction)
CAL_GROUPS = {DOD: "Defense", "civilian except GSA": "civilian", "GSA": "civilian"}
# fixed orders, used to turn small categories into numbers for the gradient-boosting benchmark
ORDINALS = {"sector": SECTORS + [UNKNOWN],
            "setaside": ["none", "small business", "8(a)", "HUBZone", "SDVOSB", "WOSB", "VOSB", "other"],
            "notice_type": ["Solicitation", "Combined Synopsis/Solicitation", "other"],
            "sol_letter": ["Q", "R", "B", "T", "other", UNKNOWN],
            "month": [str(m) for m in range(1, 13)]}
GBM_FEATURES = CONT_FEATURES + BIN_FEATURES + ["ord_" + c for c in ORDINALS]


# ----------------------------------------------------------------------------------------------------------------------
# small helpers
# ----------------------------------------------------------------------------------------------------------------------
def norm(s):
    """Upper case, letters and digits only.  Anything that is not text becomes empty."""
    if not isinstance(s, str):
        return ""
    return "".join(ch for ch in s.upper() if ch.isalnum() and ch.isascii())


def _string(x):
    """a column as pandas text (missing -> empty string), without turning every value into a Python object."""
    return pd.Series(x).reset_index(drop=True).astype("string").fillna("")


def norm_series(x):
    """norm() for a whole column (missing -> empty string)."""
    return _string(x).str.upper().str.replace(r"[^A-Z0-9]+", "", regex=True)


def _category(x, upper=False):
    """a column as a category of clean text; missing values become the empty-string level."""
    x = pd.Series(x).reset_index(drop=True)
    if not isinstance(x.dtype, pd.CategoricalDtype):
        x = x.astype("string").astype("category")
    cats = pd.Index(x.cat.categories.astype(str)).str.strip()
    if upper:
        cats = cats.str.upper()
    uniq, inv = np.unique(np.append(cats.to_numpy(object), "").astype(str), return_inverse=True)
    codes = x.cat.codes.to_numpy()
    return pd.Categorical.from_codes(np.where(codes < 0, inv[-1], inv[np.clip(codes, 0, None)]), categories=uniq)


def _text(df, name, upper=True):
    """column `name` as clean text (missing column or value -> empty string)."""
    if name not in df.columns:
        return pd.Series("", index=df.index, dtype=object)
    s = df[name].astype("string").fillna("").str.strip()
    return (s.str.upper() if upper else s).astype(object)


def _num(df, name, default=0.0):
    if name not in df.columns:
        return np.full(len(df), default, dtype=float)
    return pd.to_numeric(df[name], errors="coerce").fillna(default).to_numpy(float)


def _dates(x):
    """naive timestamps (a time zone, if any, is converted to UTC and dropped)."""
    d = pd.to_datetime(pd.Series(x).reset_index(drop=True), errors="coerce")
    if getattr(d.dt, "tz", None) is not None:
        d = d.dt.tz_convert("UTC").dt.tz_localize(None)
    return d


def quarter_index(dates):
    """year * 4 + quarter (0..3) of each date, as int64; missing dates give -1."""
    d = _dates(dates)
    q = d.dt.year * 4 + (d.dt.month - 1) // 3
    return q.fillna(-1).to_numpy("int64")


def fiscal_year(dates):
    """federal fiscal year (October to September); missing dates give -1."""
    d = _dates(dates)
    return (d.dt.year + (d.dt.month >= 10)).fillna(-1).to_numpy("int64")


def sector_of(agency):
    a = pd.Series(agency).astype(object)
    return np.where(a == DOD, DOD, np.where(a == GSA_NAME, "GSA", np.where((a == UNKNOWN) | a.isna() | (a == ""), UNKNOWN,
                                                                         SECTORS[0]))).astype(object)


def setaside_group(code):
    c = _text(pd.DataFrame({"c": pd.Series(code).reset_index(drop=True)}), "c")
    return c.map(lambda v: SETASIDE_MAP.get(v, "other")).to_numpy(object)


def psc_group(code):
    """2 digits for products (65 = medical supplies), 1 letter for services (R = professional support)."""
    c = pd.Series(code).astype("string").fillna("").str.strip().str.upper()
    c = c.to_numpy(object)
    return np.array([v[:2] if v[:1].isdigit() else v[:1] for v in c], dtype=object)


def sol_letter(sol_norm):
    """the instrument letter of a uniform contract number: 6 characters of office code, 2 digits of year, then the letter."""
    s = pd.Series(sol_norm).astype("string").fillna("")
    m = s.str.extract(r"^[A-Z0-9]{6}\d{2}([A-Z])", expand=False).astype(object)
    return np.array([UNKNOWN if not isinstance(v, str) else (v if v in "QRBT" else "other") for v in m], dtype=object)


def _logit(p):
    p = np.clip(np.asarray(p, float), EPS, 1 - EPS)
    return np.log(p / (1 - p))


def _expit(x):
    return 1.0 / (1.0 + np.exp(-np.clip(np.asarray(x, float), -35, 35)))


# ----------------------------------------------------------------------------------------------------------------------
# awards and the outcome rule
# ----------------------------------------------------------------------------------------------------------------------
def prepare_awards(df):
    """base-action rows (columns AWARD_READ_COLS) -> compact columns.  `ok` = competed with 1..998 offers; `single` = ok and 1 offer."""
    df = df.reset_index(drop=True)
    out = pd.DataFrame(index=range(len(df)))
    out["sol_norm"] = norm_series(df["solicitation_identifier"])
    out["action_date"] = _dates(df["action_date"]).dt.normalize()
    for new, old, upper in (("agency", "awarding_agency_name", False), ("subagency", "awarding_sub_agency_name", False),
                            ("office", "awarding_office_code", True), ("kind", "award_kind", True),
                            ("psc", "product_or_service_code", True), ("naics", "naics_code", True)):
        out[new] = _category(df[old], upper) if old in df.columns else pd.Categorical(np.full(len(df), ""))
    offers = _num(df, "offers", np.nan)
    extent = _category(df["extent_competed_code"], True)
    out["competed"] = np.isin(np.asarray(extent.categories, dtype=object), COMPETED)[extent.codes]
    out["offers_ok"] = (offers >= 1) & (offers <= 998)
    out["ok"] = out["competed"] & out["offers_ok"]
    out["single"] = out["ok"] & (offers == 1)
    out["value"] = _num(df, "base_and_all_options_value", 0.0)
    return out[out["action_date"].notna().to_numpy()].reset_index(drop=True)


_BEST_TEXT = ("sol_norm", "agency", "subagency", "office", "kind")
_BEST_COLS = ["sol_norm", "action_date", "value", "competed", "offers_ok", "single", "agency", "subagency", "office", "kind"]


def sol_hash(x):
    """a 64-bit number per solicitation number (matching 2 million numbers as integers needs far less memory than as text)."""
    s = _string(x)
    step = 250_000                                   # in slices: the text is turned into Python objects only briefly
    parts = [pd.util.hash_array(s.iloc[i:i + step].to_numpy(object), categorize=False) for i in range(0, len(s), step)]
    return np.concatenate(parts) if parts else np.zeros(0, "uint64")


def solicitation_index(sols):
    """What the outcome rule needs from the solicitations, sorted for fast matching: {"hash", "sol_norm", "posted_day"}.
    Rows without a posting date or with a number shorter than MIN_SOL_LEN are left out; a repeated number keeps its first row."""
    sol = _string(sols["sol_norm"])
    day = _dates(sols["first_posted"]).dt.normalize()
    keep = (day.notna() & (sol.str.len() >= MIN_SOL_LEN)).to_numpy(bool)
    sol, day = sol[keep], day[keep].to_numpy("datetime64[ns]")
    h = sol_hash(sol)
    order = np.argsort(h, kind="stable")
    h, sol, day = h[order], sol.iloc[order].reset_index(drop=True), day[order]
    first = np.concatenate([[True], h[1:] != h[:-1]]) if len(h) else np.zeros(0, bool)
    return {"hash": h[first], "sol_norm": sol[first].reset_index(drop=True), "posted_day": day[first]}


def earliest_awards(sols, awards, previous=None, max_wait_days=MAX_WAIT_DAYS):
    """The outcome rule, one award file at a time.
    sols: a table with sol_norm and first_posted, or solicitation_index(table).  awards: prepare_awards().
    previous: the result of an earlier call (or None).
    Returns one row per matched solicitation: its earliest award on or after the first posting day (ties: larger value)."""
    idx = sols if isinstance(sols, dict) else solicitation_index(sols)
    a = awards[(awards["sol_norm"].str.len() >= MIN_SOL_LEN).to_numpy(bool)][_BEST_COLS].reset_index(drop=True)
    if len(a) and len(idx["hash"]):
        h = sol_hash(a["sol_norm"])
        pos = np.clip(np.searchsorted(idx["hash"], h), 0, len(idx["hash"]) - 1)
        hit = idx["hash"][pos] == h
        a, pos = a[hit].reset_index(drop=True), pos[hit]
        same = (_string(a["sol_norm"]).to_numpy(object) == idx["sol_norm"].iloc[pos].to_numpy(object))     # guard against hash clashes
        wait = (a["action_date"].to_numpy("datetime64[ns]") - idx["posted_day"][pos]) / np.timedelta64(1, "D")
        m = a[same & (wait >= 0) & (wait <= max_wait_days)].copy()
    else:
        m = a.iloc[:0].copy()
    m["sol_norm"] = m["sol_norm"].astype("string")
    for c in _BEST_TEXT[1:]:                              # categories: a few bytes per row instead of the full text
        m[c] = _category(m[c]).remove_unused_categories()
    return combine_earliest([previous, m])


def combine_earliest(frames):
    """Several earliest_awards() results -> one: per solicitation number the earliest award (ties: the larger value)."""
    frames = [f[_BEST_COLS] for f in frames if f is not None and len(f)]
    if not frames:
        return pd.DataFrame({c: pd.Series(dtype="string" if c == "sol_norm" else "category" if c in _BEST_TEXT else
                                          "datetime64[ns]" if c == "action_date" else float if c == "value" else bool) for c in _BEST_COLS})
    if len(frames) == 1:
        m = frames[0]
    else:
        m = pd.DataFrame({c: (pd.api.types.union_categoricals([_category(f[c]) for f in frames]) if c in _BEST_TEXT[1:] else
                              pd.concat([f[c] for f in frames], ignore_index=True)) for c in _BEST_COLS})
    # sort on numbers (a 64-bit code of the solicitation number, the date, the value): much lighter than sorting text
    key = sol_hash(m["sol_norm"])
    order = np.lexsort((-m["value"].to_numpy(float), m["action_date"].to_numpy("datetime64[ns]").astype("int64"), key))
    key = key[order]
    first = np.concatenate([[True], key[1:] != key[:-1]])
    return m.iloc[order[first]].reset_index(drop=True)


def label_outcomes(sols, best):
    """Add the outcome columns to `sols`: matched, resolved, y (missing unless resolved), reason, award_date and the award's
    agency, sub-agency, office and kind (for reporting only; never used as features)."""
    out = sols.reset_index(drop=True).copy()
    b = best[_BEST_COLS] if best["sol_norm"].is_unique else best.drop_duplicates("sol_norm")[_BEST_COLS]
    key = _string(out["sol_norm"])
    b = b[_string(b["sol_norm"]).isin(key).to_numpy(bool)]
    b = b.assign(sol_norm=_string(b["sol_norm"]).to_numpy())
    r = pd.DataFrame({"sol_norm": key}).merge(b, on="sol_norm", how="left")
    hit = r["action_date"].notna().to_numpy()
    competed = hit & r["competed"].fillna(False).to_numpy(bool)
    ok = competed & r["offers_ok"].fillna(False).to_numpy(bool)
    out["matched"] = hit
    out["resolved"] = ok
    out["y"] = np.where(ok, r["single"].fillna(False).to_numpy(bool).astype(float), np.nan)
    out["reason"] = np.where(~hit, "no_matching_award", np.where(~competed, "award_not_competed",
                                                                  np.where(~ok, "offers_blank_or_999", "resolved")))
    out["award_date"] = r["action_date"].to_numpy()
    for c in ("agency", "subagency", "office", "kind"):
        out["award_" + c] = r[c].astype(object).to_numpy()
    return out


# ----------------------------------------------------------------------------------------------------------------------
# history tables: counts per key and calendar quarter
# ----------------------------------------------------------------------------------------------------------------------
def history_from_awards(aw):
    """prepare_awards() rows -> {"levels": {level: DataFrame(key, q, n, k, n_all[, agency, subagency])}}.
    n = competed awards with a usable offer count, k = those with one offer, n_all = all base actions, per key and quarter."""
    sol = _string(aw["sol_norm"])
    t = pd.DataFrame({"q": quarter_index(aw["action_date"]), "n": aw["ok"].to_numpy("int64"), "k": aw["single"].to_numpy("int64")})
    t["n_all"] = 1
    for c in ("agency", "subagency", "office", "psc", "naics"):
        t[c] = _category(aw[c])
    t["prefix"] = _category(sol.str[:6].where(sol.str.len() >= MIN_SOL_LEN, ""))

    def agg(cols):
        g = t.groupby(cols + ["q"], observed=True, sort=True)[["n", "k", "n_all"]].sum().reset_index()
        for c in cols:
            g[c] = g[c].astype(str).astype(object)
        return g

    def roll(g, key):
        g = g.assign(key=np.asarray(key, dtype=object))
        g = g[g.key != ""]
        return g.groupby(["key", "q"], sort=True)[["n", "k", "n_all"]].sum().reset_index()

    levels = {}
    g = agg(["agency", "subagency"])
    levels["all"] = roll(g, np.full(len(g), "all", object))
    levels["sector"] = roll(g, sector_of(g.agency))
    levels["agency"] = roll(g, g.agency)
    levels["subagency"] = roll(g, g.agency + " | " + g.subagency)
    for name in INFO_LEVELS:                 # these also keep the agency that most of the key's awards in the quarter belong to
        g = agg([name, "agency", "subagency"])
        g = g[g[name] != ""]
        who = g.sort_values([name, "q", "n_all", "agency", "subagency"], ascending=[True, True, False, True, True],
                            kind="mergesort").drop_duplicates([name, "q"]).rename(columns={name: "key"})
        levels[name] = roll(g, g[name]).merge(who[["key", "q", "agency", "subagency"]], on=["key", "q"], how="left")
    g = agg(["psc"])
    levels["pscg"] = roll(g, psc_group(g.psc))
    levels["psc4"] = roll(g, np.where(g.psc.str.len() == 4, g.psc, ""))
    g = agg(["naics"])
    levels["naics3"] = roll(g, np.where(g.naics.str.len() >= 3, g.naics.str[:3], ""))
    levels["naics6"] = roll(g, np.where(g.naics.str.len() == 6, g.naics, ""))
    return {"levels": levels}


def merge_history(*histories):
    """Add history objects (counts are summed; everything else, such as the name crosswalk, is kept from the first that has it)."""
    out = {}
    for h in reversed(histories):
        out.update({k: v for k, v in h.items() if k not in ("levels", "_compiled")})
    out["levels"] = {}
    for name in sorted(set().union(*[set(h.get("levels", {})) for h in histories])):
        parts = [h["levels"][name] for h in histories if name in h.get("levels", {}) and len(h["levels"][name])]
        if not parts:
            out["levels"][name] = pd.DataFrame({"key": [], "q": [], "n": [], "k": [], "n_all": []})
            continue
        t = pd.concat(parts, ignore_index=True)
        g = t.groupby(["key", "q"], sort=True)[["n", "k", "n_all"]].sum().reset_index()
        if "agency" in t.columns:
            who = t.sort_values(["key", "q", "n_all"], ascending=[True, True, False], kind="mergesort").drop_duplicates(["key", "q"])
            g = g.merge(who[["key", "q", "agency", "subagency"]], on=["key", "q"], how="left")
        out["levels"][name] = g
    return out


def trim_history(history, first_q, drop_levels=()):
    """A copy with only quarters >= first_q (the model file keeps what scoring needs, not all 19 years)."""
    out = {k: v for k, v in history.items() if k not in ("levels", "_compiled")}
    out["levels"] = {name: t[t.q >= first_q].reset_index(drop=True) for name, t in history["levels"].items()
                     if name not in drop_levels}
    return out


def last_quarter(history):
    qs = [int(t.q.max()) for t in history["levels"].values() if len(t)]
    return max(qs) if qs else -1


def _compiled(history, level):
    """sorted arrays for fast look-ups (built once per history object and level)."""
    cache = history.setdefault("_compiled", {})
    if level not in cache:
        t = history.get("levels", {}).get(level)
        if t is None or not len(t):
            cache[level] = None
        else:
            t = t.sort_values(["key", "q"], kind="mergesort").reset_index(drop=True)
            keys = pd.unique(t.key)
            code = {k: i for i, k in enumerate(keys)}
            comp = t.key.map(code).to_numpy("int64") * _QBASE + t.q.to_numpy("int64")
            c = {"code": code, "comp": comp}
            for col in ("n", "k", "n_all"):
                c[col] = np.concatenate([[0.0], np.cumsum(t[col].to_numpy(float))])
            if "agency" in t.columns:
                c["agency"], c["subagency"] = t.agency.to_numpy(object), t.subagency.to_numpy(object)
            cache[level] = c
    return cache[level]


def _codes(c, keys):
    return pd.Series(keys).astype(object).map(c["code"]).fillna(-1).to_numpy("int64")


def window_counts(history, level, keys, q_hi, window=WINDOW_QUARTERS):
    """(n, k, n_all) per row: the key's counts in quarters q_hi - window + 1 ... q_hi.  Unknown keys give zeros."""
    q_hi = np.asarray(q_hi, "int64")
    c = _compiled(history, level)
    zero = np.zeros(len(q_hi))
    if c is None:
        return zero, zero.copy(), zero.copy()
    code = _codes(c, keys)
    ok = (code >= 0) & (q_hi >= 0)
    hi = np.searchsorted(c["comp"], code * _QBASE + q_hi, side="right")
    lo = np.searchsorted(c["comp"], code * _QBASE + np.maximum(q_hi - window, 0), side="right")
    return tuple(np.where(ok, c[col][hi] - c[col][lo], 0.0) for col in ("n", "k", "n_all"))


def latest_info(history, level, keys, q_hi):
    """(agency, subagency, found) per row: from the key's latest quarter at or before q_hi."""
    q_hi = np.asarray(q_hi, "int64")
    c = _compiled(history, level)
    n = len(q_hi)
    if c is None or "agency" not in c:
        return np.full(n, "", object), np.full(n, "", object), np.zeros(n, bool)
    code = _codes(c, keys)
    pos = np.searchsorted(c["comp"], code * _QBASE + q_hi, side="right") - 1
    safe = np.clip(pos, 0, len(c["comp"]) - 1)
    found = (code >= 0) & (q_hi >= 0) & (pos >= 0) & (c["comp"][safe] // _QBASE == code)
    ag = np.where(found, c["agency"][safe], "")
    sub = np.where(found, c["subagency"][safe], "")
    return ag.astype(object), sub.astype(object), found


def shrunk_rate(k, n, prior, m=M_SHRINK):
    """(k + m * prior) / (n + m): the observed rate pulled toward `prior`; with no history it IS the prior."""
    return (np.asarray(k, float) + m * np.asarray(prior, float)) / (np.asarray(n, float) + m)


def build_crosswalk(department, sub_tier, agency, subagency, count=None, min_n=5, min_share=0.6):
    """SAM.gov names -> USAspending names, by majority vote over matched solicitation / award pairs (`count` = pairs per row).
    Returns {"subtier": {"DEPT|SUB TIER": [agency, sub-agency]}, "department": {"DEPT": agency}} (agency level, public)."""
    t = pd.DataFrame({"d": _string(department).str.strip().str.upper().to_numpy(object),
                      "s": _string(sub_tier).str.strip().str.upper().to_numpy(object),
                      "a": _string(agency).to_numpy(object), "b": _string(subagency).to_numpy(object)})
    t["c"] = 1 if count is None else np.asarray(count, float)
    t = t[(t.d != "") & (t.a != "")]
    out = {"subtier": {}, "department": {}}
    g = t.groupby(["d", "a"]).c.sum().reset_index().sort_values(["d", "c", "a"], ascending=[True, False, True])
    tot = g.groupby("d").c.transform("sum")
    for d, a, c, n in zip(g.d, g.a, g.c, tot):
        if d not in out["department"] and c >= min_n and c / n >= min_share:
            out["department"][d] = a
    g = t.groupby(["d", "s", "a", "b"]).c.sum().reset_index().sort_values(["d", "s", "c", "a", "b"],
                                                                         ascending=[True, True, False, True, True])
    tot = g.groupby(["d", "s"]).c.transform("sum")
    for d, s, a, b, c, n in zip(g.d, g.s, g.a, g.b, g.c, tot):
        key = d + "|" + s
        if key not in out["subtier"] and c >= min_n and c / n >= min_share:
            out["subtier"][key] = [a, b]
    return out


# ----------------------------------------------------------------------------------------------------------------------
# features
# ----------------------------------------------------------------------------------------------------------------------
def build_features(sols, history, as_of=None):
    """The feature table: one row per row of `sols`, in the same order.  Used the same way for training and for scoring.
    history: history tables (history_from_awards / merge_history, or model["history"]); may also hold "crosswalk" and
             "market_depth" ({6-digit NAICS: registered companies}).
    as_of:   optional date.  History newer than (as_of's quarter - LAG_QUARTERS) is never used, and a missing first_posted
             counts as as_of.  Each row uses its own first_posted, so awards dated after the posting day cannot matter."""
    s = sols.reset_index(drop=True)
    n = len(s)
    posted = _dates(s["first_posted"]) if "first_posted" in s.columns else pd.Series(pd.NaT, index=range(n))
    if as_of is not None:
        cap = pd.Timestamp(as_of)
        posted = posted.fillna(cap).where(posted.fillna(cap) <= cap, cap)
    q_hi = quarter_index(posted) - LAG_QUARTERS
    q_hi = np.where(q_hi < 0, -1, q_hi)
    sol = norm_series(s["sol_norm"] if "sol_norm" in s.columns else s["sol_number"])
    f = pd.DataFrame({"sol_norm": sol.to_numpy(object)})
    f["posted_fy"] = fiscal_year(posted)

    # ---- who buys: office key, then agency ------------------------------------------------------------------------
    prefix = sol.str[:6].where(sol.str.len() >= MIN_SOL_LEN, "").to_numpy(object)
    aac = _text(s, "aac_code").to_numpy(object)
    ag1, sub1, found1 = latest_info(history, "office", prefix, q_hi)          # the number's first six characters as office code
    ag2, sub2, found2 = latest_info(history, "office", aac, q_hi)             # SAM.gov's office code
    ag3, sub3, found3 = latest_info(history, "prefix", prefix, q_hi)          # past awards whose numbers start the same way
    office = np.where(found1, prefix, np.where(found2, aac, "")).astype(object)
    cw = history.get("crosswalk") or {"subtier": {}, "department": {}}
    dept, tier = _text(s, "department"), _text(s, "sub_tier")
    cw_sub = (dept + "|" + tier).map(cw.get("subtier", {}))
    ag4 = cw_sub.map(lambda v: v[0] if isinstance(v, (list, tuple)) else "").to_numpy(object)
    sub4 = cw_sub.map(lambda v: v[1] if isinstance(v, (list, tuple)) else "").to_numpy(object)
    ag5 = dept.map(cw.get("department", {})).fillna("").to_numpy(object)
    agency = np.where(found1, ag1, np.where(found2, ag2, np.where(found3, ag3, np.where(ag4 != "", ag4, ag5))))
    subag = np.where(found1, sub1, np.where(found2, sub2, np.where(found3, sub3, sub4)))
    agency = np.where(agency == "", UNKNOWN, agency).astype(object)
    f["agency"] = agency
    f["subagency"] = np.where((agency == UNKNOWN) | (subag == ""), UNKNOWN, agency + " | " + subag).astype(object)
    f["sector"] = sector_of(agency)
    f["office_key"] = office                       # private; not a model input
    f["office_source"] = np.where(found1, "number", np.where(found2, "sam_code", "none"))

    # ---- history rates (each shrunk toward its parent) ------------------------------------------------------------
    def rate(level, keys, prior):
        nn, kk, _ = window_counts(history, level, keys, q_hi)
        return shrunk_rate(kk, nn, prior), nn

    r_all, _ = rate("all", np.full(n, "all", object), PRIOR_RATE)
    r_sector, _ = rate("sector", f["sector"].to_numpy(object), r_all)
    r_agency, _ = rate("agency", agency, r_sector)
    r_sub, _ = rate("subagency", f["subagency"].to_numpy(object), r_agency)
    r_office, n_office = rate("office", office, r_agency)
    r_prefix, n_prefix = rate("prefix", prefix, r_agency)
    psc = _text(s, "psc")
    naics = _text(s, "naics")
    f["pscg"] = psc_group(psc)
    r_pscg, _ = rate("pscg", f["pscg"].to_numpy(object), r_all)
    r_psc4, _ = rate("psc4", psc.where(psc.str.len() == 4, "").to_numpy(object), r_pscg)
    n6 = naics.where(naics.str.fullmatch(r"\d{6}"), "")
    f["naics3"] = naics.str[:3].where(naics.str.len() >= 3, UNKNOWN).to_numpy(object)
    r_n3, _ = rate("naics3", naics.str[:3].where(naics.str.len() >= 3, "").to_numpy(object), r_all)
    r_n6, _ = rate("naics6", n6.to_numpy(object), r_n3)
    def momentum(level, keys, base):               # log-odds of the last year's rate (shrunk toward the 3-year rate) minus the 3-year rate
        nn, kk, _ = window_counts(history, level, keys, q_hi, RECENT_QUARTERS)
        return _logit(shrunk_rate(kk, nn, base)) - _logit(base)

    f["mo_subagency"] = momentum("subagency", f["subagency"].to_numpy(object), r_sub)
    f["mo_office"], f["mo_prefix"] = momentum("office", office, r_office), momentum("prefix", prefix, r_prefix)
    f["lo_all"], f["lo_agency"], f["lo_subagency"] = _logit(r_all), _logit(r_agency), _logit(r_sub)
    f["lo_office"], f["log_office_n"], f["office_known"] = _logit(r_office), np.log1p(n_office), (n_office > 0).astype(float)
    f["lo_prefix"], f["log_prefix_n"], f["prefix_known"] = _logit(r_prefix), np.log1p(n_prefix), (n_prefix > 0).astype(float)
    f["lo_pscg"], f["lo_psc4"], f["lo_naics"] = _logit(r_pscg), _logit(r_psc4), _logit(r_n6)
    f["naics_missing"] = (n6 == "").to_numpy(float)
    f["p_office_history"] = r_office              # baseline "office history only"

    # ---- the notice itself ------------------------------------------------------------------------------------------
    f["setaside"] = setaside_group(s["set_aside_code"] if "set_aside_code" in s.columns else pd.Series("", index=range(n)))
    typ = _text(s, "type_first", upper=False)
    f["notice_type"] = np.where(typ.isin(ORDINALS["notice_type"][:2]), typ, "other").astype(object)
    f["sol_letter"] = sol_letter(sol)
    if "window_first_days" in s.columns:
        w = pd.to_numeric(s["window_first_days"], errors="coerce").to_numpy(float)
    elif "first_deadline" in s.columns:
        w = ((_dates(s["first_deadline"]) - posted).dt.total_seconds() / 86400).to_numpy(float)
    else:
        w = np.full(n, np.nan)
    good = np.isfinite(w) & (w > 0) & (w <= 365)
    f["log_window"] = np.where(good, np.log1p(np.clip(np.where(good, w, 14.0), 0, 120)), np.log1p(14.0))
    f["short7"], f["short14"] = (good & (w <= 7)).astype(float), (good & (w <= 14)).astype(float)
    f["window_missing"] = (~good).astype(float)
    month, day = posted.dt.month.fillna(0).astype(int), posted.dt.day.fillna(0).astype(int)
    f["month"] = month.astype(str).to_numpy(object)
    f["late_sept"] = ((month == 9) & (day >= 17)).to_numpy(float)
    f["kw_brand"], f["kw_sole"] = (_num(s, "kw_brand") > 0).astype(float), (_num(s, "kw_sole") > 0).astype(float)
    base_type = _text(s, "base_type_first", upper=False)
    for flag, col, name in (("has_presol", "presol_first_posted", "Presolicitation"), ("has_ss", "ss_first_posted", "Sources Sought")):
        if col in s.columns:                       # time-aware: the earlier notice must be dated before the solicitation
            earlier = (_dates(s[col]) < posted).fillna(False).to_numpy(bool)
        else:
            earlier = _num(s, flag) > 0
        f[flag] = (earlier | (base_type == name).to_numpy(bool)).astype(float)
    depth = history.get("market_depth") or {}
    d = n6.map(depth).to_numpy(float) if depth else np.full(n, np.nan)
    f["log_depth"] = np.where(np.isfinite(d), np.log1p(np.where(np.isfinite(d), d, 0.0)), np.log1p(800.0))
    f["depth_missing"] = (~np.isfinite(d)).astype(float)
    for c, levels in ORDINALS.items():
        f["ord_" + c] = f[c].map({lv: i for i, lv in enumerate(levels)}).fillna(len(levels)).to_numpy(float)
    return f


# ----------------------------------------------------------------------------------------------------------------------
# the logistic model: specification, design matrix (training) and linear predictor (scoring)
# ----------------------------------------------------------------------------------------------------------------------
def _as_category(x):
    """a column as a pandas category of text (missing values become the empty-string level)."""
    x = pd.Series(x).reset_index(drop=True)
    if not isinstance(x.dtype, pd.CategoricalDtype):
        x = x.astype(object).where(x.notna(), "").astype(str).astype("category")
    return x


def interaction_column(feats, a, b):
    """the category "level of a | level of b" for each row (category codes are combined; no text is built per row)."""
    ca, cb = _as_category(feats[a]), _as_category(feats[b])
    la = list(ca.cat.categories.astype(str)) + [""]
    lb = list(cb.cat.categories.astype(str)) + [""]
    ia = np.where(ca.cat.codes.to_numpy() < 0, len(la) - 1, ca.cat.codes.to_numpy()).astype(np.int64)
    ib = np.where(cb.cat.codes.to_numpy() < 0, len(lb) - 1, cb.cat.codes.to_numpy()).astype(np.int64)
    code = ia * len(lb) + ib
    used, inv = np.unique(code, return_inverse=True)
    names = [f"{la[c // len(lb)]} | {lb[c % len(lb)]}" for c in used]
    return pd.Series(pd.Categorical.from_codes(inv.reshape(-1), categories=names), index=feats.index)


def feature_column(feats, name):
    """feats[name], or the interaction column when `name` is one of INTERACTIONS and the table does not have it."""
    if name in INTERACTIONS and name not in feats.columns:
        return interaction_column(feats, *INTERACTIONS[name])
    return feats[name]


def fit_spec(feats, cat=CAT_FEATURES, cont=CONT_FEATURES, binary=BIN_FEATURES, min_level=100):
    """What the design needs to be rebuilt later: the levels of each category that get their own coefficient (at least
    `min_level` rows; the rest share the baseline) and the mean and standard deviation of each continuous feature.
    A category may be one of INTERACTIONS (sector x another category)."""
    spec = {"cat": {}, "cont": {}, "bin": list(binary)}
    for c in cat:
        col = feature_column(feats, c)
        vc = col.value_counts() if isinstance(col.dtype, pd.CategoricalDtype) else col.astype(str).value_counts()
        spec["cat"][c] = sorted(str(k) for k, v in vc.items() if v >= min_level)
    for c in cont:
        x = feats[c].to_numpy(float)
        sd = float(x.std())
        spec["cont"][c] = [float(x.mean()), sd if sd > 1e-9 else 1.0]
    return spec


def design_names(spec):
    names = [(c, lv) for c, levels in spec["cat"].items() for lv in levels]
    return names + [(c, "per standard deviation") for c in spec["cont"]] + [(c, "yes") for c in spec["bin"]]


def _level_values(col, table, default):
    """table[level] for each row of a category column (levels missing from the table get `default`).
    For a pandas category the look-up is done once per level, not once per row."""
    if isinstance(col.dtype, pd.CategoricalDtype):
        per_level = np.array([table.get(str(c), default) for c in col.cat.categories] + [default])
        return per_level[col.cat.codes.to_numpy()]              # code -1 (missing) picks the default at the end
    return col.astype(str).map(table).fillna(default).to_numpy()


def design_matrix(feats, spec, block=50_000):
    """sparse design matrix for training (no intercept column).  Needs scipy; scoring uses linear_predictor instead.
    The CSR arrays are filled block by block, so the peak memory is close to the size of the finished matrix."""
    from scipy import sparse
    n = len(feats)
    codes, start = [], 0
    for c, levels in spec["cat"].items():                       # column number of each row's level, -1 = no own coefficient
        idx = _level_values(feature_column(feats, c), {lv: i + start for i, lv in enumerate(levels)}, -1).astype(np.int32)
        codes.append(idx)
        start += len(levels)
    cont = [(c, mean, sd) for c, (mean, sd) in spec["cont"].items()]
    flags = [feats[c].to_numpy(np.float32) for c in spec["bin"]]
    width = len(codes) + len(cont) + len(flags)
    per_row = np.full(n, len(cont), dtype=np.int64)
    for idx in codes:
        per_row += idx >= 0
    for x in flags:
        per_row += x != 0
    indptr = np.concatenate([[0], np.cumsum(per_row)])
    data, indices = np.empty(int(indptr[-1]), dtype=np.float64), np.empty(int(indptr[-1]), dtype=np.int32)
    for a in range(0, n, block):
        b = min(n, a + block)
        slots = np.full((b - a, width), -1, dtype=np.int32)
        values = np.ones((b - a, width), dtype=np.float64)
        k = 0
        for idx in codes:
            slots[:, k] = idx[a:b]
            k += 1
        for j, (c, mean, sd) in enumerate(cont):
            slots[:, k] = start + j
            values[:, k] = (feats[c].iloc[a:b].to_numpy(float) - mean) / sd
            k += 1
        for j, x in enumerate(flags):
            slots[:, k] = np.where(x[a:b] != 0, start + len(cont) + j, -1)
            values[:, k] = x[a:b]
            k += 1
        keep = slots >= 0                                       # row-major order keeps each row's column numbers increasing
        data[indptr[a]:indptr[b]] = values[keep]
        indices[indptr[a]:indptr[b]] = slots[keep]
    return sparse.csr_matrix((data, indices, indptr), shape=(n, start + len(cont) + len(flags)))


def logit_to_dict(spec, intercept, coef):
    """coefficients as a readable dictionary: {"intercept", "cat": {feature: {level: b}}, "cont": {feature: [b, mean, sd]}, "bin"}."""
    out = {"intercept": float(intercept), "cat": {}, "cont": {}, "bin": {}}
    i = 0
    for c, levels in spec["cat"].items():
        out["cat"][c] = {lv: float(coef[i + j]) for j, lv in enumerate(levels)}
        i += len(levels)
    for c, (mean, sd) in spec["cont"].items():
        out["cont"][c] = [float(coef[i]), float(mean), float(sd)]
        i += 1
    for c in spec["bin"]:
        out["bin"][c] = float(coef[i])
        i += 1
    return out


def linear_predictor(logit, feats):
    """intercept + the coefficient of each row's category levels + coefficient x standardised value.  numpy and pandas only."""
    lp = np.full(len(feats), logit["intercept"], dtype=float)
    for c, table in logit["cat"].items():
        lp += _level_values(feature_column(feats, c), table, 0.0).astype(float)
    for c, (b, mean, sd) in logit["cont"].items():
        lp += b * (feats[c].to_numpy(float) - mean) / sd
    for c, b in logit["bin"].items():
        lp += b * feats[c].to_numpy(float)
    return lp


# ---- gradient boosting, exported as plain arrays ----------------------------------------------------------------------
def gbm_matrix(feats, columns=GBM_FEATURES):
    return feats[list(columns)].to_numpy(float)


def gbm_raw(gbm, X):
    """log-odds from exported trees.  Each tree: arrays feature, threshold, left, right, value, leaf, missing_left."""
    out = np.full(X.shape[0], gbm["baseline"], dtype=float)
    for t in gbm["trees"]:
        feat, thr = np.asarray(t["feature"], "int64"), np.asarray(t["threshold"], float)
        left, right = np.asarray(t["left"], "int64"), np.asarray(t["right"], "int64")
        value, leaf = np.asarray(t["value"], float), np.asarray(t["leaf"], bool)
        miss_left = np.asarray(t["missing_left"], bool)
        node = np.zeros(X.shape[0], "int64")
        active = ~leaf[node]
        while active.any():
            i = np.flatnonzero(active)
            nd = node[i]
            x = X[i, feat[nd]]
            go_left = np.where(np.isnan(x), miss_left[nd], x <= thr[nd])
            node[i] = np.where(go_left, left[nd], right[nd])
            active = ~leaf[node]
        out += value[node]
    return out


# ----------------------------------------------------------------------------------------------------------------------
# calibration, prediction, baselines
# ----------------------------------------------------------------------------------------------------------------------
def fit_platt(p, y, max_iter=50):
    """logistic regression of y on logit(p): returns (intercept a, slope b).  A well calibrated forecast has a = 0, b = 1.
    The slope is also the "calibration slope" reported in the evaluation."""
    x, y = _logit(p), np.asarray(y, float)
    if len(y) < 2 or y.min() == y.max() or x.std() < 1e-9:
        return 0.0, 1.0
    beta = np.array([0.0, 1.0])
    X = np.c_[np.ones(len(x)), x]
    for _ in range(max_iter):
        mu = _expit(X @ beta)
        w = np.clip(mu * (1 - mu), 1e-9, None)
        step = np.linalg.solve(X.T @ (X * w[:, None]) + 1e-9 * np.eye(2), X.T @ (y - mu))
        beta = beta + step
        if np.abs(step).max() < 1e-10:
            break
    return float(beta[0]), float(beta[1])


def calibration_parameters(cal, n, sector=None):
    """(a, b) per row.  cal: {"a", "b"} (one correction for every row), optionally with
         "groups":   {sector: group name}, for example {"Department of Defense": "Defense", "GSA": "civilian", ...}
         "by_group": {group name: {"a", "b", ...}}
    A row whose sector is in no group, or whose group has no correction, gets the pooled a and b."""
    a, b = np.full(n, float(cal["a"])), np.full(n, float(cal["b"]))
    groups, by_group = cal.get("groups") or {}, cal.get("by_group") or {}
    if sector is not None and groups and by_group:
        s = pd.Series(np.asarray(sector, dtype=object)).map(groups).to_numpy(object)
        for g, c in by_group.items():
            m = s == g
            a[m], b[m] = float(c["a"]), float(c["b"])
    return a, b


def apply_calibration(p, cal, sector=None):
    """p -> expit(a + b * logit(p)); cal is {"a": .., "b": ..} or None (no change).  With `sector` (one value per row) and a
    correction by sector group in `cal`, each row gets its group's a and b (see calibration_parameters)."""
    if not cal:
        return np.clip(np.asarray(p, float), 0.0, 1.0)
    lp = _logit(p)
    a, b = calibration_parameters(cal, len(np.atleast_1d(lp)), sector)
    return _expit(a + b * lp)


def predict_raw(model, feats):
    """uncalibrated probability from the shipped model ("logit" or "gbm")."""
    if model.get("kind") == "gbm":
        return _expit(gbm_raw(model["gbm"], gbm_matrix(feats, model["gbm"]["features"])))
    return _expit(linear_predictor(model["logit"], feats))


def predict(model, feats):
    """calibrated P(exactly one offer | the award is competed with a usable offer count), one number per row, in [0, 1].
    The correction is chosen by each row's sector as known on the posting day (feats["sector"]), when the model has one by sector."""
    sector = feats["sector"].to_numpy(object) if "sector" in feats.columns else None
    return apply_calibration(predict_raw(model, feats), model.get("calibration"), sector)


def sector_rate_predictor(rates, feats):
    """baseline: the training-period single-offer rate of the row's sector (unknown sector -> the overall rate)."""
    return feats["sector"].astype(object).map(rates.get("sector", {})).astype(float).fillna(rates["global"]).to_numpy(float)


def agency_rate_predictor(rates, feats):
    """baseline: the training-period rate of the row's agency (agencies not seen in training -> the sector rate)."""
    p = feats["agency"].astype(object).map(rates.get("agency", {})).astype(float)
    return p.fillna(pd.Series(sector_rate_predictor(rates, feats), index=feats.index)).to_numpy(float)


def office_history_predictor(feats):
    """baseline: the office's shrunk past single-offer rate (an office with no history gets its agency's rate)."""
    return feats["p_office_history"].to_numpy(float)


def baseline_rates(feats, y, min_agency=50):
    """the rate tables behind the two rate baselines, from training rows."""
    y = pd.Series(np.asarray(y, float))
    sector, agency = feats["sector"].reset_index(drop=True), feats["agency"].reset_index(drop=True)
    ag = y.groupby(agency, observed=True).agg(["mean", "size"])
    return {"global": float(y.mean()) if len(y) else PRIOR_RATE,
            "sector": {str(k): float(v) for k, v in y.groupby(sector, observed=True).mean().items()},
            "agency": {str(k): float(v) for k, v in ag[ag["size"] >= min_agency]["mean"].items()}}


def baseline_predictions(model, feats):
    r = model["baselines"]
    return pd.DataFrame({"p_sector_rate": sector_rate_predictor(r, feats), "p_agency_rate": agency_rate_predictor(r, feats),
                         "p_office_history": office_history_predictor(feats)})


# ----------------------------------------------------------------------------------------------------------------------
# saving and loading
# ----------------------------------------------------------------------------------------------------------------------
def history_to_json(history):
    out = {k: v for k, v in history.items() if k not in ("levels", "_compiled")}
    out["levels"] = {name: {c: t[c].tolist() for c in t.columns} for name, t in history["levels"].items()}
    return out


def history_from_json(obj):
    out = {k: v for k, v in obj.items() if k != "levels"}
    out["levels"] = {name: pd.DataFrame(cols) for name, cols in obj.get("levels", {}).items()}
    return out


def save_model(model, path):
    """Write the model as JSON (temporary name first).  The file holds office-level history: keep it private."""
    obj = {k: v for k, v in model.items() if k != "history"}
    obj["history"] = history_to_json(model["history"])
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, separators=(",", ":"), sort_keys=True)
    os.replace(tmp, path)


def load_model(path):
    with open(path, encoding="utf-8") as fh:
        obj = json.load(fh)
    if obj.get("version") != MODEL_VERSION:
        raise ValueError(f"{path} is not a version-{MODEL_VERSION} model file")
    obj["history"] = history_from_json(obj["history"])
    return obj
