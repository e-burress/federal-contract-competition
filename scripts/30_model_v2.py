"""
30_model_v2.py - forecast model version 2: the chance that a solicitation's competed award receives exactly ONE offer.

Version 1 (08_model_forecast.py) was trained on 1,802 solicitations from five departments and its inputs were not kept.
Version 2 is trained on every solicitation in SAM.gov's archive files (22_sam_archive.py) that can be matched to a competed
award in the history tables (18_history_build.py), for every agency, and every step can be rerun.

Three steps (run them in this order; --all runs all three)
  --build-training   Match solicitations to awards with the outcome rule of 09_resolve_forecast.py, build the quarterly history
                     tables, and build the features of every resolved solicitation.  Reads one award year and one block of
                     solicitations at a time.  Writes (PRIVATE, office level) to <DATA>/models/:
                       training_v2.parquet, history_v2.parquet, history_v2_meta.json, build_summary_v2.json
  --evaluate         Rolling origin.  For every test year t that has enough data: train on solicitations posted before fiscal
                     year t whose award was also dated before fiscal year t, test on solicitations posted in fiscal year t.
                     Models: sector rate, agency rate, office history only, version 1's feature set (refit), the version 2
                     logistic candidates of rule 1 below, a gradient-boosting benchmark.
                     "Calibrated" rows use a correction fitted only on held-out years BEFORE the test year.
                     Each evaluation fit uses a seeded random sample of at most 200,000 training rows (memory and time).
                     Also: what each block of features adds (the model refitted without the block, last two test years).
                     Writes results/model_v2_evaluation.csv, results/model_v2_calibration.csv (public, sector level) and
                     <DATA>/models/oot_predictions_v2.parquet (private).
  --train            Final model on ALL resolved solicitations (Newton's method, 50,000 rows at a time).  The calibration is
                     fitted on the held-out predictions of the last three test years.  Writes <DATA>/models/model_v2.json (PRIVATE: everything needed to score, with
                     office-level history) and the public results/model_v2_card.json, results/model_v2_coefficients.csv
                     (no office or component rows) and figures/model_v2.png.

Which model is shipped (rules fixed in this file, applied to the out-of-time results)
  Rule 1, the logistic candidates (written down on 10 November 2026, before any candidate below was fitted).
    Why: out of time, the one correction for all sectors left Defense forecasts too cautious (calibration slope about 1.45 in
    test years 2025 and 2026) and civilian forecasts too confident (about 0.75).  The raw slope also rose year after year.
    Candidates.  Each is a raw model and a probability correction.  Every raw model is the same L2 logistic regression (same
    features, C and level threshold), fitted on the same capped sample for each test year.
      Raw models
        R0  all earlier years (the model shipped until now)
        R1  the last 6 years only (as before)
        R2  all earlier years, time-decay weights with a half-life of 3 years
        R3  all earlier years, time-decay weights with a half-life of 5 years
            The weight of a row posted in fiscal year f, for test year t, is 0.5 ** ((t - 1 - f) / half-life); the newest year
            has weight 1.  Weights are rescaled to a mean of 1, so the penalty keeps the same strength against the data.
        R4  R0 plus sector x set-aside
        R5  R0 plus sector x set-aside, sector x notice type (notice type and number letter) and sector x product group
            An interaction is one more category feature, "sector | level", with the same rule as every category (a level
            needs 100 training rows to get a coefficient) and the same L2 penalty.  Sector is the sector known on the
            posting day (Defense, civilian except GSA, GSA, unknown).  All coefficients stay at sector level or coarser.
      Corrections (each fitted only on held-out predictions of the 3 test years before, as before)
        P   one correction for all sectors: expit(a + b * logit(raw)) (as before)
        S   a correction by sector group: Defense, and civilian (civilian except GSA together with GSA, which has only about
            150 resolved solicitations a year).  Rows whose sector is unknown on the posting day get the one correction P
            fitted on all rows.  A group with fewer than 1,000 rows, or with one outcome only, also gets P.
      The 12 candidates are every raw model with each correction.  R0 + P is the current model.
    Score: log loss of the corrected forecasts, pooled over every resolved solicitation of the LAST FIVE test years.
    Changes: the number of changes a candidate makes to R0 + P.  A shorter window or time decay counts 1, the correction by
    sector counts 1, and each interaction block counts 1 (so R4 = 1, R5 = 3, R5 + S = 4).
    Choice: let L be the lowest score.  Among the candidates whose score is within 0.5% of L (score x 0.995 <= L), ship the
    one with the fewest changes; at equal changes, the lower score.  So a simpler candidate gives way only to one that
    lowers the score by at least 0.5%.
    This rule replaces the earlier window rule ("last 6 years" only if 0.5% better), because R1 is one of the candidates.
    Reported for the choice, next to R0 + P and gradient boosting: AUC, Brier score, log loss and calibration slope by
    sector for each of the last five test years.
    Known limits: the rule is applied to the same five years that are reported, so the chosen model's numbers there are a
    little optimistic; outcomes exist only for resolved solicitations; the newest test year is incomplete (Defense awards
    are published 90 days late, and slow awards are still missing); the corrections are fitted on three years.
  Rule 2, gradient boosting (unchanged).  The logistic regression chosen by rule 1 is shipped unless gradient boosting is CLEARLY
  better out of time: pooled log loss over all test years at least 2% lower, lower in at least 80% of the test years, and a
  calibration slope no further from 1.
  Result of rule 1 on 10 November 2026 (award years 2008 to 2026): R2 + P, the time-decay model with a half-life of 3 years
  and one correction for all sectors (log loss 0.4861 against 0.4920 for the model shipped until then).  R2 + S, the same
  model with the correction by sector, scored 0.4837, which is 0.49% lower: just short of the 0.5% margin.

  AMENDMENT, MADE AFTER SEEING THE RESULTS (November 10, 2026).  Rule 1 selected R2 + P.  R2 + S is shipped instead.  This
  choice was made after the results were known, so it overrides the rule written down before fitting.  Reason: the revision
  was made because the forecasts were miscalibrated by sector.  Rule 1 measured only pooled log loss, where the correction
  by sector falls 0.01 points short of the 0.5% margin (0.49%).  But it largely removes the problem the revision was for.
  Calibration slopes in test years 2024 to 2026 (1.00 is ideal): civilian except GSA 0.74 to 0.86 with R2 + P, 0.96 to 1.09
  with R2 + S; Defense 1.26 to 1.38 with R2 + P, 1.11 to 1.23 with R2 + S.  The public scores are reported by sector, so a
  known miscalibration by sector should not be shipped.  Rule 2 is then applied to R2 + S.  The amendment (AMENDMENT below)
  applies only while rule 1 still selects R2 + P; if a rerun on new data selects another candidate, the rule's choice is
  shipped and the run says that the amendment no longer applies.

Features, leakage rules and the scoring interface are documented at the top of forecast_lib.py.
Privacy: public files are at agency level or coarser.  No office codes, solicitation numbers or company codes leave <DATA>.
These are risk indicators about markets, not statements about anyone's conduct.

Time and memory on a laptop, 19 award years (930,000 resolved solicitations): evaluate 11 minutes (12 logistic candidates),
train under a minute; peak memory about 1.2 GB (evaluate).  Fixed seeds: the same inputs give the same model.

Usage (from the repository root; rerun all three steps whenever new award years arrive)
  python -I scripts/30_model_v2.py --build-training
  python -I scripts/30_model_v2.py --evaluate
  python -I scripts/30_model_v2.py --train
  python -I scripts/30_model_v2.py --all
"""
import argparse
import glob
import json
import os
import re
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA, PROJECT, RESULTS  # noqa: E402
import forecast_lib as fl  # noqa: E402

SEED = 20261110
MODELS = os.path.join(DATA, "models")
SOL_PATH = os.path.join(DATA, "sam_archive", "solicitations_all.parquet")
NOTICE_GLOB = os.path.join(DATA, "sam_archive", "notices_FY*.parquet")
AWARD_GLOB = os.path.join(DATA, "history", "processed", "base_actions_FY*.parquet")
DEPTH_PATH = os.path.join(PROJECT, "reference", "market_depth_naics.csv")
TRAIN_PATH = os.path.join(MODELS, "training_v2.parquet")
HIST_PATH = os.path.join(MODELS, "history_v2.parquet")
META_PATH = os.path.join(MODELS, "history_v2_meta.json")
SUMMARY_PATH = os.path.join(MODELS, "build_summary_v2.json")
OOT_PATH = os.path.join(MODELS, "oot_predictions_v2.parquet")
MODEL_PATH = os.path.join(MODELS, "model_v2.json")
EVAL_CSV = os.path.join(RESULTS, "model_v2_evaluation.csv")
CAL_CSV = os.path.join(RESULTS, "model_v2_calibration.csv")
COEF_CSV = os.path.join(RESULTS, "model_v2_coefficients.csv")
CARD_JSON = os.path.join(RESULTS, "model_v2_card.json")
FIGURE = os.path.join(PROJECT, "figures", "model_v2.png")

SOL_COLS = ["sol_norm", "first_posted", "window_first_days", "type_first", "base_type_first", "set_aside_code", "naics", "psc",
            "department", "sub_tier", "aac_code", "kw_brand", "kw_sole"]
PRESOL_TYPES = ("Presolicitation", "Sources Sought")
C_LOGIT = 0.03             # L2 strength of the logistic regression (sklearn's C, version 1's value); out-of-time log loss
                           # moves by less than 0.001 between 0.01 and 1
MIN_LEVEL = 100            # training rows a category level needs to get its own coefficient
MIN_TRAIN, MIN_TEST = 3000, 500
RECENT_YEARS = 6
CAL_YEARS = 3              # held-out years behind a calibration fit
MIN_CAL_ROWS = 1000
ABLATION_YEARS, ABLATION_ROWS = 2, 200_000
KEEP_QUARTERS = 20         # quarters of history kept in the model file
AWARD_BATCH = 200_000      # award rows read at a time
SOL_BLOCK = 50_000         # solicitations whose features are built at a time
MAX_FIT_ROWS = 200_000     # an EVALUATION fit never uses more rows than this (a seeded random sample of the eligible rows;
                           # keeps memory and time low).  The FINAL model uses every row (fit_logit_all).
GBM_ROWS = 200_000         # the same cap for gradient boosting, also when it is the final model
GBM_MARGIN, GBM_YEAR_SHARE = 0.02, 0.8
BANDS = [0.0, 0.05, 0.1, 0.15, 0.2, 0.3, 0.4, 0.5, 0.7, 1.0]
CLIP = 1e-4

SELECT_YEARS = 5           # rule 1 scores the candidates on the last five test years
SELECT_MARGIN = 0.005      # a simpler candidate gives way only to a 0.5% lower log loss
HALF_LIVES = (3, 5)        # time-decay candidates (years)
# the amendment of the docstring, made after seeing the results: applies only while rule 1 still selects `rule_pick`
AMENDMENT = {
    "date": "2026-11-10", "rule_pick": "logistic v2, half-life 3 years",
    "shipped": "logistic v2, half-life 3 years, correction by sector",
    "statement": ("Amendment, made after seeing the results (November 10, 2026). Rule 1 selected R2 + P (time decay with a "
                  "half-life of 3 years, one correction for all sectors). R2 + S (the same model with the correction by sector) is "
                  "shipped instead. This choice was made after the results were known, so it overrides the rule written down "
                  "before fitting. Reason: the revision was made because the forecasts were miscalibrated by sector. Rule 1 "
                  "measured only pooled log loss, where the correction by sector falls 0.01 points short of the 0.5% margin "
                  "(0.49%), but it largely removes the problem the revision was for. Calibration slopes in test years 2024 to "
                  "2026 (1.00 is ideal): civilian except GSA 0.74 to 0.86 with R2 + P, 0.96 to 1.09 with R2 + S; Defense 1.26 to "
                  "1.38 with R2 + P, 1.11 to 1.23 with R2 + S. The public scores are reported by sector, so a known "
                  "miscalibration by sector should not be shipped.")}

M_SECTOR, M_AGENCY, M_OFFICE = "sector rate (baseline)", "agency rate (baseline)", "office history only (baseline)"
M_V1 = "version 1 feature set (refit)"
M_LOGIT, M_RECENT, M_GBM = "logistic v2", f"logistic v2, last {RECENT_YEARS} years", "gradient boosting"
RAW = " (raw)"
BY_SECTOR = ", correction by sector"
INTER_ONE = ["sector_x_setaside"]
INTER_ALL = ["sector_x_setaside", "sector_x_notice_type", "sector_x_sol_letter", "sector_x_pscg"]
# the raw logistic models of rule 1 (docstring).  changes = changes against the model shipped until 10 November 2026.
RAW_MODELS = [
    {"key": "R0", "col": "p_logit", "name": M_LOGIT, "changes": 0, "window": None, "half_life": None, "inter": [],
     "text": "all earlier years"},
    {"key": "R1", "col": "p_recent", "name": M_RECENT, "changes": 1, "window": RECENT_YEARS, "half_life": None, "inter": [],
     "text": f"the last {RECENT_YEARS} years only"},
    *[{"key": f"R{2 + i}", "col": f"p_decay{h}", "name": f"logistic v2, half-life {h} years", "changes": 1, "window": None,
       "half_life": h, "inter": [], "text": f"all earlier years, time-decay weights with a half-life of {h} years"}
      for i, h in enumerate(HALF_LIVES)],
    {"key": "R4", "col": "p_int1", "name": "logistic v2 + sector x set-aside", "changes": 1, "window": None, "half_life": None,
     "inter": INTER_ONE, "text": "all earlier years, plus sector x set-aside"},
    {"key": "R5", "col": "p_int3", "name": "logistic v2 + sector x (set-aside, notice type, product group)", "changes": 3,
     "window": None, "half_life": None, "inter": INTER_ALL,
     "text": "all earlier years, plus sector x set-aside, sector x notice type and number letter, sector x product group"},
]
# the 12 candidates: each raw model with the one correction (P, column <raw>_cal) and the correction by sector (S, <raw>_sec)
CANDIDATES = [{**r, "raw_col": r["col"], "correction": corr, "col": r["col"] + suffix, "name": r["name"] + label,
               "changes": r["changes"] + extra}
              for r in RAW_MODELS for corr, suffix, label, extra in (("one for all sectors", "_cal", "", 0),
                                                                     ("by sector", "_sec", BY_SECTOR, 1))]
PRED_COLS = {M_SECTOR: "p_sector", M_AGENCY: "p_agency", M_OFFICE: "p_office", M_V1: "p_v1",
             **{k: v for r in RAW_MODELS for k, v in ((r["name"] + RAW, r["col"]), (r["name"], r["col"] + "_cal"),
                                                     (r["name"] + BY_SECTOR, r["col"] + "_sec"))},
             M_GBM + RAW: "p_gbm", M_GBM: "p_gbm_cal"}


def say(*a):
    print(" ".join(str(x) for x in a), flush=True)


def peak_mb():
    """peak memory of this process in MB (Windows and Unix)."""
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
            return round(c.PeakWorkingSetSize / 2**20)
        import resource
        return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)
    except Exception:
        return None


def release():
    """give memory that is no longer used back to the system (pyarrow keeps freed blocks otherwise)."""
    import gc
    gc.collect()
    try:
        import pyarrow as pa
        pa.default_memory_pool().release_unused()
    except Exception:
        pass


def award_files():
    out = {}
    for p in glob.glob(AWARD_GLOB):
        m = re.search(r"base_actions_FY(\d{4})\.parquet$", p)
        if m:
            out[int(m.group(1))] = p
    return dict(sorted(out.items()))


def fy_start(fy):
    return pd.Timestamp(year=int(fy) - 1, month=10, day=1)


def write_json(obj, path):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=1, default=float)
    os.replace(tmp, path)


def read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# ----------------------------------------------------------------------------------------------------------------------
# history tables on disk
# ----------------------------------------------------------------------------------------------------------------------
def save_history(hist):
    parts = [t.assign(level=name) for name, t in hist["levels"].items()]
    df = pd.concat(parts, ignore_index=True)
    for c in ("key", "agency", "subagency", "level"):
        df[c] = df[c].astype("string")
    df.to_parquet(HIST_PATH + ".tmp", index=False)
    os.replace(HIST_PATH + ".tmp", HIST_PATH)
    write_json({k: v for k, v in hist.items() if k not in ("levels", "_compiled")}, META_PATH)


def load_history():
    df = pd.read_parquet(HIST_PATH)
    hist = read_json(META_PATH)
    hist["levels"] = {}
    for name, t in df.groupby("level", sort=False):
        t = t.drop(columns="level").reset_index(drop=True)
        if name not in fl.INFO_LEVELS:
            t = t.drop(columns=["agency", "subagency"])
        for c in ("key", "agency", "subagency"):
            if c in t.columns:
                t[c] = t[c].astype(object)
        hist["levels"][str(name)] = t
    return hist


def load_market_depth():
    if not os.path.exists(DEPTH_PATH):
        return {}
    d = pd.read_csv(DEPTH_PATH, dtype={"naics": str}, usecols=["naics", "n_any"])
    return {k: int(v) for k, v in zip(d.naics, d.n_any)}


# ----------------------------------------------------------------------------------------------------------------------
# step 1: the labelled training table
# ----------------------------------------------------------------------------------------------------------------------
def earlier_notices(idx):
    """For each solicitation in the index: the date of its earliest Presolicitation and Sources Sought notice (or NaT).
    The feature builder only counts a notice dated BEFORE the solicitation's first posting."""
    import pyarrow.parquet as pq
    parts = []
    for path in sorted(glob.glob(NOTICE_GLOB)):
        t = pq.read_table(path, columns=["sol_norm", "type", "posted_date"]).to_pandas()
        t = t[(t["type"].astype("string").isin(PRESOL_TYPES) & t["sol_norm"].notna()).to_numpy(bool)]
        if not len(t):
            continue
        g = pd.DataFrame({"h": fl.sol_hash(t["sol_norm"]), "ss": (t["type"].astype("string") == "Sources Sought").to_numpy(bool),
                          "d": t["posted_date"].to_numpy("datetime64[ns]")})
        parts.append(g.groupby(["h", "ss"], as_index=False).d.min())
        del t, g
        release()
    out = {"presol_first_posted": np.full(len(idx["hash"]), np.datetime64("NaT", "ns"), "datetime64[ns]")}
    out["ss_first_posted"] = out["presol_first_posted"].copy()
    if parts:
        allp = pd.concat(parts, ignore_index=True).groupby(["h", "ss"], as_index=False).d.min()
        for name, flag in (("presol_first_posted", False), ("ss_first_posted", True)):
            g = allp[allp.ss == flag]
            h = g.h.to_numpy()
            pos = np.clip(np.searchsorted(idx["hash"], h), 0, len(idx["hash"]) - 1)
            hit = idx["hash"][pos] == h
            out[name][pos[hit]] = g.d.to_numpy("datetime64[ns]")[hit]
    return out


def build_training():
    import pyarrow.parquet as pq
    t0 = time.time()
    os.makedirs(MODELS, exist_ok=True)
    files = award_files()
    if not files:
        raise SystemExit("no base_actions_FY<year>.parquet files found; run `python run.py history` first")
    say(f"award years present: {list(files)}")
    pf = pq.ParquetFile(SOL_PATH)
    keys = pf.read(columns=["sol_norm", "first_posted"]).to_pandas()
    n_sols = len(keys)
    idx = fl.solicitation_index(keys)
    del keys
    release()
    say(f"solicitations: {n_sols:,}; with a usable number and posting date: {len(idx['hash']):,}  [{time.time() - t0:.0f}s]")
    earlier = earlier_notices(idx)
    say(f"earlier notices found: presolicitation {int((~np.isnat(earlier['presol_first_posted'])).sum()):,}, "
        f"sources sought {int((~np.isnat(earlier['ss_first_posted'])).sum()):,}  [{time.time() - t0:.0f}s]")

    # ---- one award year at a time: history counts and the earliest matching award
    cands, parts, n_awards = [], [], {}
    for fy, path in files.items():
        n_awards[fy] = 0
        for batch in pq.ParquetFile(path).iter_batches(batch_size=AWARD_BATCH, columns=fl.AWARD_READ_COLS):
            aw = fl.prepare_awards(batch.to_pandas())
            del batch
            n_awards[fy] += len(aw)
            parts.append(fl.history_from_awards(aw))
            cands.append(fl.earliest_awards(idx, aw))
            del aw
        parts = [fl.merge_history(*parts)]
        release()
        say(f"  FY{fy}: {n_awards[fy]:,} base actions; candidate matches so far {sum(len(c) for c in cands):,}  [{time.time() - t0:.0f}s, peak {peak_mb()} MB]")
    best = fl.combine_earliest(cands)
    del cands
    release()
    say(f"matched solicitations: {len(best):,}  [{time.time() - t0:.0f}s, peak {peak_mb()} MB]")

    hist = parts[0]
    idx = {"hash": idx["hash"]}                      # the text and the dates are no longer needed
    # ---- name crosswalk SAM.gov -> USAspending, from matched pairs
    pairs = []
    b_names = best[["sol_norm", "agency", "subagency"]]
    for rg in range(pf.num_row_groups):
        ch = pf.read_row_group(rg, columns=["sol_norm", "department", "sub_tier"]).to_pandas()
        ch = ch[ch["department"].notna().to_numpy(bool)]
        ch = pd.DataFrame({"sol_norm": fl.norm_series(ch["sol_norm"]), "d": ch["department"].astype("string").to_numpy(),
                           "s": ch["sub_tier"].astype("string").fillna("").to_numpy()}).merge(b_names, on="sol_norm")
        pairs.append(ch.groupby(["d", "s", "agency", "subagency"]).size().rename("c").reset_index())
    pairs = pd.concat(pairs, ignore_index=True)
    hist["crosswalk"] = fl.build_crosswalk(pairs.d, pairs.s, pairs.agency, pairs.subagency, count=pairs.c)
    hist["market_depth"] = load_market_depth()
    hist["award_years"] = list(files)
    save_history(hist)
    say(f"history saved: {sum(len(t) for t in hist['levels'].values()):,} rows; crosswalk {len(hist['crosswalk']['department'])} departments, "
        f"{len(hist['crosswalk']['subtier'])} sub-tiers; market depth {len(hist['market_depth'])} industries  [{time.time() - t0:.0f}s]")

    # ---- features, one block of solicitations at a time (the same builder scores new solicitations)
    import pyarrow as pa
    writer, n_rows, n_single, funnel, office = None, 0, 0.0, [], []
    for rg, a in [(rg, a) for rg in range(pf.num_row_groups) for a in range(0, pf.metadata.row_group(rg).num_rows, SOL_BLOCK)]:
        ch = pf.read_row_group(rg, columns=SOL_COLS).slice(a, SOL_BLOCK).to_pandas()
        ch["sol_norm"] = fl.norm_series(ch["sol_norm"]).to_numpy()
        h = fl.sol_hash(ch["sol_norm"])
        pos = np.clip(np.searchsorted(idx["hash"], h), 0, len(idx["hash"]) - 1)
        hit = idx["hash"][pos] == h
        for name, arr in earlier.items():
            ch[name] = np.where(hit, arr[pos], np.datetime64("NaT", "ns"))
        lab = fl.label_outcomes(ch[["sol_norm"]], best)
        f = fl.build_features(ch, hist)
        f["matched"], f["resolved"], f["y"], f["award_date"] = lab.matched.to_numpy(), lab.resolved.to_numpy(), lab.y.to_numpy(), lab.award_date.to_numpy()
        f["award_sector"] = np.where(lab.matched, fl.sector_of(lab.award_agency.astype(object)), "")
        funnel.append(f.groupby(["posted_fy", "sector"]).agg(solicitations=("matched", "size"), matched=("matched", "sum"),
                                                             resolved=("resolved", "sum"), single=("y", "sum")).reset_index())
        m = f.matched.to_numpy()
        aw_off = lab.award_office.astype("string").fillna("").to_numpy(object)[m]
        aac = ch["aac_code"].astype("string").fillna("").str.upper().to_numpy(object)[m]
        sol = f.sol_norm.to_numpy(object)[m]
        chk = pd.DataFrame({"posted_fy": f.posted_fy.to_numpy()[m], "sector": f.award_sector.to_numpy(object)[m], "matched": 1,
                            "sam_code_present": aac != "", "sam_code_equal": (aac != "") & (aac == aw_off),
                            "number_start_equal": np.array([s[:6] for s in sol], dtype=object) == aw_off,
                            "office_key_found": f.office_key.to_numpy(object)[m] != "",
                            "office_key_equal": (f.office_key.to_numpy(object)[m] != "") & (f.office_key.to_numpy(object)[m] == aw_off)})
        office.append(chk.groupby(["posted_fy", "sector"]).sum().reset_index())
        keep = f[f.resolved.to_numpy()].drop(columns=["matched", "resolved"]).reset_index(drop=True)
        for c in keep.columns:                       # compact types; the table is written block by block
            if keep[c].dtype == "float64" and c != "y":
                keep[c] = keep[c].astype("float32")
            elif keep[c].dtype == object:
                keep[c] = keep[c].astype("string")
        keep["y"] = keep["y"].astype("int8")
        keep["posted_fy"] = keep["posted_fy"].astype("int16")
        keep["award_date"] = keep["award_date"].astype("datetime64[ms]")
        tab = pa.Table.from_pandas(keep, preserve_index=False, schema=None if writer is None else writer.schema)
        if writer is None:
            writer = pq.ParquetWriter(TRAIN_PATH + ".tmp", tab.schema)
        writer.write_table(tab)
        n_rows, n_single = n_rows + len(keep), n_single + float(keep.y.sum())
        del ch, f, lab, keep, tab
        release()
    writer.close()
    os.replace(TRAIN_PATH + ".tmp", TRAIN_PATH)

    funnel = pd.concat(funnel).groupby(["posted_fy", "sector"], as_index=False).sum()
    office = pd.concat(office).groupby(["posted_fy", "sector"], as_index=False).sum()
    by_year = funnel.groupby("posted_fy", as_index=False)[["solicitations", "matched", "resolved", "single"]].sum()
    by_year["award_file_present"] = by_year.posted_fy.isin(list(files))
    summary = {
        "built_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "award_years": list(files), "base_actions_read": n_awards,
        "solicitations": int(n_sols), "solicitations_with_usable_number": int(len(idx["hash"])),
        "min_number_length": fl.MIN_SOL_LEN, "max_wait_days": fl.MAX_WAIT_DAYS,
        "resolved": int(n_rows), "single_offer_share": round(n_single / n_rows, 4) if n_rows else None,
        "last_history_quarter": fl.last_quarter(hist),
        "funnel_by_year": by_year.to_dict(orient="records"),
        "funnel_by_year_sector": funnel.to_dict(orient="records"),
        "office_code_check": office.to_dict(orient="records"),
        "seconds": round(time.time() - t0), "peak_mb": peak_mb()}
    write_json(summary, SUMMARY_PATH)
    print_funnel(summary)
    say(f"training table: {n_rows:,} resolved solicitations -> {TRAIN_PATH}  [{time.time() - t0:.0f}s, peak {peak_mb()} MB]")
    return summary


def funnel_tables(summary, present_only=True):
    """the funnel by posting year and by sector (sector as known on the posting day), with shares."""
    f = pd.DataFrame(summary["funnel_by_year_sector"])
    if present_only:
        f = f[f.posted_fy.isin(summary["award_years"])]

    def shares(g):
        g = g.copy()
        g["matched_share"] = (g.matched / g.solicitations).round(4)
        g["resolved_share"] = (g.resolved / g.solicitations).round(4)
        g["single_offer_share"] = (g.single / g.resolved.where(g.resolved > 0)).round(4)
        return g

    cols = ["solicitations", "matched", "resolved", "single"]
    by_year = shares(f.groupby("posted_fy", as_index=False)[cols].sum())
    by_sector = shares(f.groupby("sector", as_index=False)[cols].sum())
    total = shares(pd.DataFrame([f[cols].sum()]))
    return by_year, by_sector, total


def office_check_table(summary):
    o = pd.DataFrame(summary["office_code_check"])
    o = o[o.posted_fy.isin(summary["award_years"])]
    o["group"] = np.where(o.sector == fl.DOD, "Defense", "civilian")
    g = o.groupby(["posted_fy", "group"], as_index=False).sum(numeric_only=True)
    out = g[["posted_fy", "group", "matched"]].copy()
    out["sam_code_present_share"] = (g.sam_code_present / g.matched).round(3)
    out["sam_code_equals_award_office"] = (g.sam_code_equal / g.sam_code_present.where(g.sam_code_present > 0)).round(3)
    out["number_start_equals_award_office"] = (g.number_start_equal / g.matched).round(3)
    out["office_key_found_share"] = (g.office_key_found / g.matched).round(3)
    out["office_key_equals_award_office"] = (g.office_key_equal / g.office_key_found.where(g.office_key_found > 0)).round(3)
    return out


def print_funnel(summary):
    by_year, by_sector, total = funnel_tables(summary)
    say("\nFunnel, posting years whose award file is present (sector as known on the posting day):")
    say(by_year.to_string(index=False))
    say(by_sector.to_string(index=False))
    say(total.to_string(index=False))
    say("\nOffice codes among matched solicitations (award office = the office code on the matched award):")
    say(office_check_table(summary).to_string(index=False))


# ----------------------------------------------------------------------------------------------------------------------
# step 2: rolling-origin evaluation
# ----------------------------------------------------------------------------------------------------------------------
def decay_weights(posted_fy, newest_fy, half_life):
    """time-decay weights: 0.5 ** ((newest_fy - posted_fy) / half_life), rescaled to a mean of 1 (so the L2 penalty keeps the
    same strength against the data as an unweighted fit on the same rows)."""
    w = 0.5 ** ((float(newest_fy) - np.asarray(posted_fy, float)) / float(half_life))
    return w / w.mean()


def coef_vector(logit, spec):
    """the coefficients of a readable dictionary in the order of design_names(spec) (levels it does not have: 0)."""
    out = []
    for c, levels in spec["cat"].items():
        out += [logit["cat"].get(c, {}).get(lv, 0.0) for lv in levels]
    out += [logit["cont"][c][0] if c in logit["cont"] else 0.0 for c in spec["cont"]]
    out += [logit["bin"].get(c, 0.0) for c in spec["bin"]]
    return np.asarray(out, float)


def fit_logit(F, y, cat=fl.CAT_FEATURES, cont=fl.CONT_FEATURES, binary=fl.BIN_FEATURES, C=C_LOGIT, weights=None, init=None):
    """L2 logistic regression on a sparse design.  Returns the readable coefficient dictionary of forecast_lib.
    weights: optional row weights.  init: optional fitted dictionary to start from (same optimum, fewer iterations)."""
    from sklearn.linear_model import LogisticRegression
    spec = fl.fit_spec(F, cat, cont, binary, MIN_LEVEL)
    lr = LogisticRegression(C=C, max_iter=2000, tol=1e-5, warm_start=init is not None)
    if init is not None:
        lr.coef_ = coef_vector(init, spec)[None, :]
        lr.intercept_ = np.array([float(init["intercept"])])
    lr.fit(fl.design_matrix(F, spec), np.asarray(y), sample_weight=None if weights is None else np.asarray(weights, float))
    return fl.logit_to_dict(spec, lr.intercept_[0], lr.coef_[0])


def fit_logit_all(F, y, cat=fl.CAT_FEATURES, cont=fl.CONT_FEATURES, binary=fl.BIN_FEATURES, C=C_LOGIT, block=50_000, max_iter=60, tol=1e-7,
                  weights=None):
    """The same model as fit_logit (same penalty: the sum of squared coefficients divided by 2C, none on the intercept; the
    same optional row weights), fitted by Newton's method with the design matrix built 50,000 rows at a time.  Memory stays
    small however many rows there are."""
    y = np.asarray(y, float)
    sw = np.ones(len(y)) if weights is None else np.asarray(weights, float)
    spec = fl.fit_spec(F, cat, cont, binary, MIN_LEVEL)
    p = len(fl.design_names(spec))
    beta = np.zeros(p + 1)                                   # intercept first
    ybar = np.average(y, weights=sw)
    beta[0] = np.log(ybar / (1 - ybar))
    pen = np.r_[0.0, np.full(p, 1.0 / C)]
    for _ in range(max_iter):
        g, H = np.zeros(p + 1), np.zeros((p + 1, p + 1))
        for a in range(0, len(F), block):
            X = fl.design_matrix(F.iloc[a:a + block], spec)
            mu = fl._expit(beta[0] + X @ beta[1:])
            s = sw[a:a + block]
            w, r = s * mu * (1 - mu), s * (y[a:a + block] - mu)
            xw = X.T @ w
            g[0] += r.sum()
            g[1:] += X.T @ r
            H[0, 0] += w.sum()
            H[0, 1:] += xw
            H[1:, 0] += xw
            H[1:, 1:] += (X.T @ X.multiply(w[:, None]).tocsr()).toarray()
        step = np.linalg.solve(H + np.diag(pen) + 1e-10 * np.eye(p + 1), g - pen * beta)
        big = np.abs(step).max()
        beta += step * (5 / big if big > 5 else 1.0)
        if big < tol:
            break
    return fl.logit_to_dict(spec, beta[0], beta[1:])


def fit_gbm(F, y):
    from sklearn.ensemble import HistGradientBoostingClassifier
    if len(F) > GBM_ROWS:
        pick = np.sort(np.random.default_rng(SEED).choice(len(F), GBM_ROWS, replace=False))
        F, y = F.iloc[pick], np.asarray(y)[pick]
    g = HistGradientBoostingClassifier(learning_rate=0.06, max_iter=400, max_leaf_nodes=31, min_samples_leaf=200, l2_regularization=1.0,
                                       early_stopping=True, validation_fraction=0.1, n_iter_no_change=25, random_state=SEED)
    return g.fit(fl.gbm_matrix(F), np.asarray(y))


def export_gbm(g):
    """the fitted trees as plain lists, so that scoring needs numpy only (checked against sklearn before it is shipped)."""
    trees = []
    for stage in g._predictors:
        nd = stage[0].nodes
        trees.append({"feature": nd["feature_idx"].astype(int).tolist(),
                      "threshold": np.nan_to_num(nd["num_threshold"].astype(float), posinf=1e300, neginf=-1e300).tolist(),
                      "left": nd["left"].astype(int).tolist(), "right": nd["right"].astype(int).tolist(),
                      "value": nd["value"].astype(float).tolist(), "leaf": nd["is_leaf"].astype(int).tolist(),
                      "missing_left": nd["missing_go_to_left"].astype(int).tolist()})
    return {"features": list(fl.GBM_FEATURES), "baseline": float(np.ravel(g._baseline_prediction)[0]), "trees": trees}


def metrics(y, p):
    from sklearn.metrics import roc_auc_score
    y = np.asarray(y, float)
    p = np.clip(np.asarray(p, float), CLIP, 1 - CLIP)
    two = len(y) > 1 and y.min() != y.max()
    a, b = fl.fit_platt(p, y) if two else (np.nan, np.nan)
    return {"n": int(len(y)), "single_share": float(y.mean()) if len(y) else np.nan, "mean_forecast": float(p.mean()) if len(y) else np.nan,
            "auc": float(roc_auc_score(y, p)) if two else np.nan, "brier": float(np.mean((p - y) ** 2)) if len(y) else np.nan,
            "logloss": float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))) if len(y) else np.nan,
            "cal_intercept": a, "cal_slope": b}


def capped(rows, seed):
    """row numbers for a fit: all of them, or a seeded random sample of MAX_FIT_ROWS (sorted, so the result is repeatable)."""
    rows = np.asarray(rows)
    if len(rows) <= MAX_FIT_ROWS:
        return rows
    return np.sort(np.random.default_rng(seed).choice(rows, MAX_FIT_ROWS, replace=False))


MODEL_COLS = (["posted_fy", "award_date", "award_sector", "y", "p_office_history"] + fl.CAT_FEATURES + fl.CONT_FEATURES
              + fl.BIN_FEATURES + ["ord_" + c for c in fl.ORDINALS])


def load_training():
    """the columns the models need (no solicitation numbers, no office codes); text columns as categories: small in memory."""
    import pyarrow.parquet as pq
    T = pq.read_table(TRAIN_PATH, columns=MODEL_COLS, read_dictionary=fl.CAT_FEATURES + ["award_sector"]).to_pandas(
        split_blocks=True, self_destruct=True)
    for c in fl.BIN_FEATURES + ["ord_" + c for c in fl.ORDINALS]:        # 0/1 flags and small codes: one byte each
        T[c] = T[c].astype("int8")
    release()
    return T


def test_years(T, files):
    out = []
    for t in sorted(int(v) for v in T.posted_fy.unique()):
        if t not in files:
            continue
        tr = (T.posted_fy < t) & (T.award_date < fy_start(t))
        te = T.posted_fy == t
        if tr.sum() >= MIN_TRAIN and te.sum() >= MIN_TEST and T.y[tr].nunique() == 2 and T.y[te].nunique() == 2:
            out.append(t)
    return out


def fit_calibration(p, y, sector=None):
    """the probability correction expit(a + b * logit(p)).  Without `sector`: one (a, b) for every row.  With `sector` (the
    sector known on the posting day, one per row): also one (a, b) per group of forecast_lib.CAL_GROUPS; a group with fewer
    than MIN_CAL_ROWS rows or one outcome only gets none, and its rows (like rows of an unknown sector) use the pooled a and b."""
    p, y = np.asarray(p, float), np.asarray(y, float)
    a, b = fl.fit_platt(p, y)
    cal = {"a": a, "b": b, "n": int(len(y))}
    if sector is not None:
        g = pd.Series(np.asarray(sector, dtype=object)).map(fl.CAL_GROUPS).to_numpy(object)
        by = {}
        for name in sorted(set(fl.CAL_GROUPS.values())):
            m = g == name
            if m.sum() >= MIN_CAL_ROWS and len(np.unique(y[m])) == 2:
                ga, gb = fl.fit_platt(p[m], y[m])
                by[name] = {"a": ga, "b": gb, "n": int(m.sum())}
        cal["groups"], cal["by_group"] = dict(fl.CAL_GROUPS), by
    return cal


def nested_calibration(oot, col, years, by_sector=False, fits=None):
    """calibrated copy of column `col`: for test year t the correction is fitted on held-out predictions of the (at most
    CAL_YEARS) earlier test years, using only solicitations whose award was dated before fiscal year t.
    by_sector: the correction by sector group (fit_calibration), keyed on the sector known on the posting day.
    fits: an optional dictionary that receives {t: the correction used for test year t}."""
    out = oot[col].to_numpy(float).copy()
    for t in years:
        prev = [u for u in years if u < t][-CAL_YEARS:]
        fit = oot[oot.test_fy.isin(prev) & (oot.award_date < fy_start(t)) & oot[col].notna()]
        now = (oot.test_fy == t).to_numpy() & oot[col].notna().to_numpy()
        if len(fit) >= MIN_CAL_ROWS and fit.y.nunique() == 2:
            cal = fit_calibration(fit[col].to_numpy(float), fit.y.to_numpy(float), fit.sector_known.to_numpy(object) if by_sector else None)
            out[now] = fl.apply_calibration(out[now], cal, oot.sector_known.to_numpy(object)[now] if by_sector else None)
            if fits is not None:
                fits[t] = cal
    return out


def defense_limit(oot, col, raw_col, years, cal, by_sector):
    """The limits of the shipped Defense correction, from the data (sentences for the card's caveats): how the out-of-time
    Defense forecasts above 0.7 did on the held-out years behind the correction, and whether the shipped Defense slope is
    steeper than every Defense slope used in the out-of-time evaluation."""
    out = []
    held = oot[oot.test_fy.isin(years[-CAL_YEARS:]) & (oot.sector_known == fl.DOD) & oot[col].notna()]
    top = held[held[col] > 0.7]
    span = f"FY{years[-CAL_YEARS:][0]} to FY{years[-1]}"
    if len(top):
        out.append(f"On the held-out years behind the correction ({span}), Defense forecasts above 0.7 averaged {top[col].mean():.2f} "
                   f"and came true {top.y.mean():.2f} of the time ({len(top):,} solicitations)."
                   + (" So the highest Defense forecasts run high." if top[col].mean() > top.y.mean() + 0.01 else
                      " So the highest Defense forecasts run low." if top[col].mean() < top.y.mean() - 0.01 else ""))
    if not cal:
        return out
    g = fl.CAL_GROUPS.get(fl.DOD)
    shipped_b = (cal.get("by_group") or {}).get(g, cal)["b"]
    fits = {}
    nested_calibration(oot, raw_col, years, by_sector=by_sector, fits=fits)
    used = [(c.get("by_group") or {}).get(g, c)["b"] for c in fits.values()]
    if used and shipped_b > max(used):
        out.append(f"The shipped Defense correction (slope {shipped_b:.2f}) is steeper than any used in the out-of-time "
                   f"evaluation (at most {max(used):.2f}). So the published scores do not fully cover it.")
    elif used:
        out.append(f"The shipped Defense correction (slope {shipped_b:.2f}) is within the range used in the out-of-time "
                   f"evaluation ({min(used):.2f} to {max(used):.2f}).")
    return out


def select_logistic(oot, years):
    """rule 1 of the docstring.  Returns (table of the candidates, the chosen candidate)."""
    last = years[-SELECT_YEARS:]
    part = oot[oot.test_fy.isin(last)]
    cur = next(c for c in CANDIDATES if c["name"] == M_LOGIT)
    rows = []
    for c in CANDIDATES:
        if c["col"] not in part.columns or part[c["col"]].isna().any():
            continue                                         # a candidate must cover every row of the five years
        m = metrics(part.y.to_numpy(float), part[c["col"]].to_numpy(float))
        per_year = {t: metrics(part.y[part.test_fy == t], part.loc[part.test_fy == t, c["col"]])["logloss"] for t in last}
        cur_year = {t: metrics(part.y[part.test_fy == t], part.loc[part.test_fy == t, cur["col"]])["logloss"] for t in last}
        rows.append({"candidate": c["name"], "raw_model": f"{c['key']}: {c['text']}", "correction": c["correction"],
                     "changes": c["changes"], "logloss": m["logloss"], "auc": m["auc"], "brier": m["brier"], "cal_slope": m["cal_slope"],
                     "years_better_than_current": int(sum(per_year[t] < cur_year[t] - 1e-12 for t in last))})
    tab = pd.DataFrame(rows)
    best = tab.logloss.min()
    cur_ll = float(tab.loc[tab.candidate == M_LOGIT, "logloss"].iloc[0])
    tab["change_vs_current_pct"] = 100 * (tab.logloss / cur_ll - 1)
    tab["within_margin_of_best"] = tab.logloss * (1 - SELECT_MARGIN) <= best
    pick = tab[tab.within_margin_of_best].sort_values(["changes", "logloss"], kind="mergesort").iloc[0]
    tab["chosen_by_rule"] = tab.candidate == pick.candidate
    tab["score_years"] = f"FY{last[0]}-FY{last[-1]}"
    return tab, next(c for c in CANDIDATES if c["name"] == pick.candidate)


def amended(tab, rule_cand):
    """the candidate to ship after the AMENDMENT (docstring), and a note.  The amendment applies only while rule 1 selects
    AMENDMENT["rule_pick"]; otherwise rule 1's choice is shipped."""
    tab = tab.copy()
    if rule_cand["name"] == AMENDMENT["rule_pick"] and AMENDMENT["shipped"] in set(tab.candidate):
        ship = next(c for c in CANDIDATES if c["name"] == AMENDMENT["shipped"])
        note = AMENDMENT["statement"]
    else:
        ship = rule_cand
        note = (f"The amendment of {AMENDMENT['date']} (ship {AMENDMENT['shipped']} instead of {AMENDMENT['rule_pick']}) no longer "
                f"applies, because rule 1 now selects {rule_cand['name']}. Rule 1's choice is shipped.")
    tab["shipped"] = tab.candidate == ship["name"]
    return tab, ship, note


def write_calibration_table(oot, years, shipped):
    """the public reliability table: forecast bands by sector, pooled over the last five test years (one scope), for the
    shipped model, the model shipped until November 2026 (corrected and raw) and gradient boosting."""
    last = years[-SELECT_YEARS:]
    recent = oot[oot.test_fy.isin(last)]
    cal = []
    for model in dict.fromkeys([shipped, M_LOGIT, M_LOGIT + RAW, M_GBM]):
        col = PRED_COLS[model]
        for sector in ["all"] + fl.SECTORS:
            g = recent if sector == "all" else recent[recent.sector == sector]
            band = pd.cut(g[col].clip(0, 1), BANDS, include_lowest=True)
            r = g.groupby(band, observed=False).agg(n=("y", "size"), mean_forecast=(col, "mean"), observed_share=("y", "mean")).reset_index()
            r.insert(0, "sector", sector)
            r.insert(0, "model", model)
            r["band"] = [f"{iv.left:.2f}-{iv.right:.2f}".replace("-0.00-", "0.00-") for iv in r.pop(col)]
            cal.append(r)
    cal = pd.concat(cal, ignore_index=True)
    cal["test_years"] = f"FY{last[0]}-FY{last[-1]}" if len(last) > 1 else f"FY{last[0]}"
    cal[["mean_forecast", "observed_share"]] = cal[["mean_forecast", "observed_share"]].round(4)
    cal[["model", "sector", "band", "n", "mean_forecast", "observed_share", "test_years"]].to_csv(CAL_CSV, index=False)
    return cal


def evaluate():
    t0 = time.time()
    files = award_files()
    T = load_training()
    years = test_years(T, files)
    if not years:
        raise SystemExit("no test year has enough data yet (need earlier award years with at least "
                         f"{MIN_TRAIN:,} resolved solicitations and a test year with {MIN_TEST:,})")
    say(f"training table: {len(T):,} resolved solicitations; test years: {years}")
    rng = np.random.default_rng(SEED)
    parts, info, abl = [], {}, []
    for t in years:
        tr = ((T.posted_fy < t) & (T.award_date < fy_start(t))).to_numpy()
        te = (T.posted_fy == t).to_numpy()
        rates = fl.baseline_rates(T.loc[tr, ["sector", "agency"]], T.y.to_numpy(float)[tr])
        pick = capped(np.flatnonzero(tr), SEED + t)
        Ftr, ytr, Fte = T.iloc[pick], T.y.to_numpy(float)[pick], T[te]
        o = pd.DataFrame({"row": np.flatnonzero(te), "test_fy": t, "sector": Fte.award_sector.to_numpy(object),
                          "sector_known": Fte.sector.to_numpy(object),          # the sector known on the posting day
                          "award_date": Fte.award_date.to_numpy(), "y": Fte.y.to_numpy(float)})
        o["p_sector"] = fl.sector_rate_predictor(rates, Fte)
        o["p_agency"] = fl.agency_rate_predictor(rates, Fte)
        o["p_office"] = fl.office_history_predictor(Fte)
        o["p_v1"] = fl._expit(fl.linear_predictor(fit_logit(Ftr, ytr, fl.V1_CAT, fl.V1_CONT, fl.V1_BIN), Fte))
        base = fit_logit(Ftr, ytr)
        o["p_logit"] = fl._expit(fl.linear_predictor(base, Fte))
        for r in RAW_MODELS:                                 # rule 1's other raw models, on the same sample (start: the base fit)
            if r["half_life"]:
                w = decay_weights(Ftr.posted_fy.to_numpy(float), t - 1, r["half_life"])
                o[r["col"]] = fl._expit(fl.linear_predictor(fit_logit(Ftr, ytr, weights=w, init=base), Fte))
            elif r["inter"]:
                o[r["col"]] = fl._expit(fl.linear_predictor(fit_logit(Ftr, ytr, cat=fl.CAT_FEATURES + r["inter"], init=base), Fte))
        release()
        recent = tr & (T.posted_fy >= t - RECENT_YEARS).to_numpy()
        if recent.sum() == tr.sum():
            o["p_recent"] = o["p_logit"]
        elif recent.sum() >= MIN_TRAIN and T.y[recent].nunique() == 2:
            pick_r = capped(np.flatnonzero(recent), SEED + t)
            o["p_recent"] = fl._expit(fl.linear_predictor(fit_logit(T.iloc[pick_r], T.y.to_numpy(float)[pick_r]), Fte))
        else:
            o["p_recent"] = np.nan
        release()
        o["p_gbm"] = fit_gbm(Ftr, ytr).predict_proba(fl.gbm_matrix(Fte))[:, 1]
        info[t] = {"n_train": int(tr.sum()), "train_years": f"FY{int(Ftr.posted_fy.min())}-FY{int(Ftr.posted_fy.max())}",
                   "recent_differs": bool(recent.sum() != tr.sum()), "outcome_window_complete": bool((t + 1) in files)}
        parts.append(o)
        release()
        say(f"  test FY{t}: train {tr.sum():,} ({info[t]['train_years']}), test {te.sum():,}, single-offer share {o.y.mean():.3f}  "
            f"[{time.time() - t0:.0f}s, peak {peak_mb()} MB]")
        # ---- what each block of features adds (last test years; a seeded sample of the training rows keeps it quick)
        if t in years[-ABLATION_YEARS:]:
            pick_a = pick if len(pick) <= ABLATION_ROWS else np.sort(rng.choice(pick, ABLATION_ROWS, replace=False))
            Fa, ya = T.iloc[pick_a], T.y.to_numpy(float)[pick_a]
            yte = Fte.y.to_numpy(float)
            full = metrics(yte, fl._expit(fl.linear_predictor(fit_logit(Fa, ya), Fte)))
            for block, (cols, _) in fl.BLOCKS.items():
                keep = [[c for c in group if c not in cols] for group in (fl.CAT_FEATURES, fl.CONT_FEATURES, fl.BIN_FEATURES)]
                m = metrics(yte, fl._expit(fl.linear_predictor(fit_logit(Fa, ya, *keep), Fte)))
                release()
                abl.append({"test_fy": t, "block": block, "n": m["n"], "logloss_full": full["logloss"], "logloss_without": m["logloss"],
                            "auc_full": full["auc"], "auc_without": m["auc"]})
            del Fa, ya
            say(f"    feature blocks done  [{time.time() - t0:.0f}s]")
        del Ftr, ytr, Fte
        release()
    del T
    release()
    oot = pd.concat(parts, ignore_index=True)
    for raw in [r["col"] for r in RAW_MODELS] + ["p_gbm"]:
        oot[raw + "_cal"] = nested_calibration(oot, raw, years)
        if raw != "p_gbm":
            oot[raw + "_sec"] = nested_calibration(oot, raw, years, by_sector=True)
    oot.to_parquet(OOT_PATH + ".tmp", index=False)
    os.replace(OOT_PATH + ".tmp", OOT_PATH)
    say(f"held-out predictions and corrections done  [{time.time() - t0:.0f}s, peak {peak_mb()} MB]")

    # ---- the public evaluation table
    rows = []
    for scope, part in [(t, oot[oot.test_fy == t]) for t in years] + [("pooled", oot)]:
        for sector in ["all"] + fl.SECTORS:
            g = part if sector == "all" else part[part.sector == sector]
            for model, col in PRED_COLS.items():
                ok = g[col].notna().to_numpy()
                if not ok.any():
                    continue
                m = metrics(g.y.to_numpy()[ok], g[col].to_numpy()[ok])
                extra = info.get(scope, {"n_train": None, "train_years": None, "outcome_window_complete": None})
                rows.append({"test_fy": scope, "sector": sector, "model": model, **m, "n_train": extra["n_train"],
                             "train_years": extra["train_years"], "outcome_window_complete": extra["outcome_window_complete"]})
    ev = pd.DataFrame(rows)
    a = pd.DataFrame(abl)
    if len(a):
        pooled = a.assign(w=a.n).groupby("block", as_index=False, sort=False).apply(
            lambda g: pd.Series({"test_fy": "pooled", "n": g.n.sum(), "logloss_full": np.average(g.logloss_full, weights=g.w),
                                 "logloss_without": np.average(g.logloss_without, weights=g.w),
                                 "auc_full": np.average(g.auc_full, weights=g.w), "auc_without": np.average(g.auc_without, weights=g.w)}),
            include_groups=False)
        a = pd.concat([a, pooled], ignore_index=True)
        a["model"] = "logistic v2 (raw) without: " + a.block
        a["sector"] = "all"
        a["logloss"], a["auc"] = a.logloss_without, a.auc_without
        a["delta_logloss_when_dropped"] = a.logloss_without - a.logloss_full
        a["delta_auc_when_dropped"] = a.auc_without - a.auc_full
        ev = pd.concat([ev, a[["test_fy", "sector", "model", "n", "auc", "logloss", "delta_logloss_when_dropped", "delta_auc_when_dropped"]]],
                       ignore_index=True)
    num = ev.select_dtypes("number").columns
    ev[num] = ev[num].round(5)
    ev.to_csv(EVAL_CSV, index=False)

    # ---- rule 1, and the reliability table by sector for the recent test years (one scope: the years rule 1 scores)
    tab, rule_pick = select_logistic(oot, years)
    tab, chosen, note = amended(tab, rule_pick)
    last = years[-SELECT_YEARS:]
    write_calibration_table(oot, years, chosen["name"])

    main = [M_LOGIT, chosen["name"], M_GBM]
    show = ev[(ev.sector == "all") & ev.model.isin(main + [M_LOGIT + RAW, M_SECTOR, M_OFFICE])]
    say("\nOut of time, all sectors:")
    say(show[["test_fy", "model", "n", "single_share", "auc", "brier", "logloss", "cal_slope"]].round(4).to_string(index=False))
    say("\nPooled over all test years, by sector:")
    say(ev[(ev.test_fy == "pooled") & (ev.sector != "all") & ev.model.isin(main)][["sector", "model", "n", "single_share", "auc", "brier",
                                                                                   "logloss", "cal_slope"]].round(4).to_string(index=False))
    say(f"\nRule 1: the candidates, log loss pooled over FY{last[0]}-FY{last[-1]} (lowest first):")
    say(tab.sort_values("logloss")[["candidate", "changes", "logloss", "change_vs_current_pct", "auc", "brier", "cal_slope",
                                    "years_better_than_current", "within_margin_of_best", "chosen_by_rule", "shipped"]].round(5).to_string(index=False))
    say(f"chosen by rule 1: {rule_pick['name']}; shipped: {chosen['name']}")
    if chosen["name"] != rule_pick["name"] or AMENDMENT["rule_pick"] != rule_pick["name"]:
        say(note)
    lastrows = ev[ev.test_fy.astype(str).isin([str(t) for t in last]) & ev.model.isin(main)]
    say("\nLast five test years by sector (the current model, the choice, gradient boosting):")
    say(lastrows[["test_fy", "sector", "model", "n", "auc", "brier", "logloss", "cal_slope"]].round(4).to_string(index=False))
    if len(a):
        say("\nLog loss change when a block of features is dropped (positive = the block helps), pooled:")
        say(ev[(ev.test_fy == "pooled") & ev.model.str.contains("without")][["model", "delta_logloss_when_dropped", "delta_auc_when_dropped"]]
            .sort_values("delta_logloss_when_dropped", ascending=False).to_string(index=False))
    say(f"\nwrote {EVAL_CSV}, {CAL_CSV}  [{time.time() - t0:.0f}s, peak {peak_mb()} MB]")
    return ev


# ----------------------------------------------------------------------------------------------------------------------
# step 3: the final model, the public card, the figure
# ----------------------------------------------------------------------------------------------------------------------
def short_items(text, limit=380):
    """a long statement as a list of items of whole sentences, each at most `limit` characters where possible (pages that
    show the card cut longer items)."""
    out, cur = [], ""
    for sent in [x for x in re.split(r"(?<=\.) ", text) if x.strip()]:
        if cur and len(cur) + 1 + len(sent) > limit:
            out.append(cur)
            cur = sent
        else:
            cur = (cur + " " + sent).strip()
    return out + ([cur] if cur else [])


def choose_model(ev, oot, years):
    """apply the fixed rules of the docstring; returns (kind, chosen logistic candidate, reasons, table of the candidates)."""
    pooled = ev[(ev.test_fy == "pooled") & (ev.sector == "all")].set_index("model")
    by_year = ev[(ev.test_fy != "pooled") & (ev.sector == "all")]
    tab, cand = select_logistic(oot, years)
    tab, ship, note = amended(tab, cand)
    last = years[-SELECT_YEARS:]
    cur = tab.set_index("candidate").loc[M_LOGIT]
    ch = tab.set_index("candidate").loc[cand["name"]]
    best = tab.sort_values("logloss").iloc[0]
    reasons = [f"Rule 1 (fixed before the candidates were fitted): 12 logistic candidates (6 raw models, each with one correction "
               f"for all sectors or a correction by sector), scored by log loss pooled over the last five test years, "
               f"FY{last[0]} to FY{last[-1]}. A simpler candidate gives way only to one that lowers that log loss by at least "
               f"{100 * SELECT_MARGIN:.1f}%."]
    reasons.append(f"The lowest log loss is {best.logloss:.4f} ({best.candidate}). The model shipped until now (one correction for "
                   f"all sectors) has {cur.logloss:.4f}. "
                   + (f"Rule 1 selects {cand['name']}, with {ch.logloss:.4f} ({ch.change_vs_current_pct:+.1f}% against the model shipped "
                      f"until now; lower in {int(ch.years_better_than_current)} of the 5 years). "
                      + ("It is the candidate with the fewest changes within 0.5% of the lowest log loss."
                         if cand["name"] != best.candidate else "No candidate with fewer changes comes within 0.5% of it.")
                      if cand["name"] != M_LOGIT else "No candidate lowers it by 0.5%, so rule 1 keeps the model shipped until now."))
    if ship["name"] != cand["name"]:
        sh = tab.set_index("candidate").loc[ship["name"]]
        reasons += short_items(note + f" Log loss of the shipped model, pooled over FY{last[0]} to FY{last[-1]}: {sh.logloss:.4f} "
                               f"({sh.change_vs_current_pct:+.1f}% against the model shipped until now).")
    elif cand["name"] != AMENDMENT["rule_pick"]:
        reasons += short_items(note)
    cand = ship
    if cand["correction"] == "by sector":
        reasons.append("The correction is fitted by sector group: Defense, and civilian (civilian agencies except GSA together with "
                       "GSA, which has about 150 resolved solicitations a year). A solicitation whose sector is unknown on the posting "
                       "day gets the one correction fitted on all sectors.")
    base = cand["name"]
    kind = "logit"
    if M_GBM in pooled.index and base in pooled.index:
        ll, lg = pooled.logloss[base], pooled.logloss[M_GBM]
        yl = by_year[by_year.model == base].set_index("test_fy").logloss
        yg = by_year[by_year.model == M_GBM].set_index("test_fy").logloss
        share = float((yg.reindex(yl.index) < yl).mean())
        sl, sg = abs(pooled.cal_slope[base] - 1), abs(pooled.cal_slope[M_GBM] - 1)
        clearly = (lg < ll * (1 - GBM_MARGIN)) and (share >= GBM_YEAR_SHARE) and (sg <= sl + 0.05)
        kind = "gbm" if clearly else "logit"
        reasons += short_items(f"Gradient boosting against the shipped logistic regression, pooled over all test years: log loss {lg:.4f} against {ll:.4f} "
                       f"({100 * (1 - lg / ll):+.1f}%), better in {100 * share:.0f}% of the test years, calibration slope "
                       f"{pooled.cal_slope[M_GBM]:.2f} against {pooled.cal_slope[base]:.2f}. "
                       + ("That is clearly better by the fixed rule (2% lower log loss, 80% of the years, slope no worse), so gradient boosting is shipped. "
                          "The logistic coefficients are still published for reading." if clearly else
                          "That is not clearly better by the fixed rule (2% lower log loss, 80% of the years, slope no worse), "
                          "so the readable logistic regression is shipped."))
    return kind, cand, reasons, tab


COMPONENT_NOTE = "Component (sub-agency) effects are part of the model but are not published."


def public_logit(logit):
    """the coefficients without the sub-agency (component) table.  No public file carries a row for a component, named or
    unnamed: unnamed labels did not protect the names (see the review of 10 November 2026), because coefficients published
    with names in earlier versions can be matched to them.  The private model file keeps every component; the calculator gets
    one typical component effect per agency instead (calculator_card)."""
    return {**logit, "cat": {c: dict(t) for c, t in logit["cat"].items() if c != "subagency"}}


def coefficient_table(logit):
    """the public coefficient table.  It never has a row for a component (sub-agency), even when given the private model."""
    block_of = {c: b for b, (cols, _) in fl.BLOCKS.items() for c in cols}
    rows = [{"feature": "(intercept)", "level": "", "type": "intercept", "block": "", "coefficient": logit["intercept"]}]
    for c, table in public_logit(logit)["cat"].items():
        rows += [{"feature": c, "level": lv, "type": "category level (against the pooled other levels)", "block": block_of.get(c, ""),
                  "coefficient": b} for lv, b in table.items()]
    for c, (b, mean, sd) in logit["cont"].items():
        rows.append({"feature": c, "level": f"per standard deviation ({sd:.3f}; mean {mean:.3f})", "type": "continuous",
                     "block": block_of.get(c, ""), "coefficient": b})
    for c, b in logit["bin"].items():
        rows.append({"feature": c, "level": "yes", "type": "flag", "block": block_of.get(c, ""), "coefficient": b})
    out = pd.DataFrame(rows)
    out["odds_ratio"] = np.exp(out.coefficient)
    out = out.reindex(out.coefficient.abs().sort_values(ascending=False, kind="mergesort").index)
    out[["coefficient", "odds_ratio"]] = out[["coefficient", "odds_ratio"]].round(4)
    return out


CALC_YEARS = 3             # the "typical" values of the explorer's what-if calculator come from the last 3 posting years
CALC_MIN_ROWS = 20         # a group with fewer recent rows uses all its rows; with fewer than this in all years, its parent
OFFICE_FEATURES = fl.BLOCKS["office history"][0]


def calculator_card(F, logit, last_fy):
    """Public aggregates for the what-if calculator of the explorer (docs/explorer.html).  The calculator rebuilds the linear
    predictor of forecast_lib.linear_predictor from these numbers.  Inputs that the model takes from history are replaced by
    medians of the training rows (the last CALC_YEARS posting years), and never below agency level:
      office history (8 features)                -> median by sector (a typical buying office of the sector)
      lo_agency                                  -> median by agency
      subagency, lo_subagency, mo_subagency      -> ONE number per agency, the typical component effect: the median over the
                                                    agency's recent training rows of (sub-agency coefficient + the two rate
                                                    terms), and the number of component levels the model has for the agency.
                                                    No component is published, named or unnamed (public_logit)
      lo_pscg, lo_psc4                           -> median by product or service group
      lo_naics, log_depth, depth_missing, naics_missing -> median by 3-digit industry
      lo_all                                     -> median of all recent rows
    Also the full-precision coefficients (the public coefficient table rounds them to 4 places; no component table) and the
    most common level of each input (the calculator's starting point).  Agency level or coarser: no office codes, numbers,
    companies or component names."""
    pub = public_logit(logit)
    recent = (F.posted_fy >= last_fy - CALC_YEARS + 1).to_numpy()

    def med(rows, cols):
        return {c: round(float(np.median(rows[c].to_numpy(float))), 4) for c in cols}

    def by_group(key, cols, levels=None, parent=None):
        out = {}
        col_all = F[key].astype(str)
        for lv in (levels if levels is not None else sorted(col_all.unique())):
            m = (col_all == lv).to_numpy()
            rows = F[m & recent] if (m & recent).sum() >= CALC_MIN_ROWS else F[m]
            if len(rows) >= CALC_MIN_ROWS:
                out[lv] = {**med(rows, cols), "n_recent": int((m & recent).sum())}
            elif parent is not None:
                out[lv] = parent(lv)
        return out

    R = F[recent]
    overall = med(R, ["lo_all"] + OFFICE_FEATURES)
    sectors = by_group("sector", OFFICE_FEATURES, levels=list(fl.SECTORS), parent=lambda lv: {c: overall[c] for c in OFFICE_FEATURES})
    # only levels with their own coefficient (the calculator offers no others); the unknown agency is left out
    agencies = by_group("agency", ["lo_agency"], levels=[a for a in logit["cat"].get("agency", {}) if a != fl.UNKNOWN])
    sub_coef = logit["cat"].get("subagency", {})
    (b_lo, m_lo, s_lo), (b_mo, m_mo, s_mo) = logit["cont"]["lo_subagency"], logit["cont"]["mo_subagency"]
    ag_col = F["agency"].astype(str).to_numpy(object)
    components = {}
    for ag in agencies:                                  # the typical component of each agency (see the docstring)
        m = ag_col == ag
        rows = F[m & recent] if (m & recent).sum() >= CALC_MIN_ROWS else F[m]
        if not len(rows):
            continue
        sub = rows["subagency"].astype(str).to_numpy(object)
        lo, mo = rows["lo_subagency"].to_numpy(float), rows["mo_subagency"].to_numpy(float)
        effect = np.array([sub_coef.get(v, 0.0) for v in sub]) + b_lo * (lo - m_lo) / s_lo + b_mo * (mo - m_mo) / s_mo
        i = int(np.argsort(effect, kind="mergesort")[(len(effect) - 1) // 2])
        components[ag] = {"effect": float(effect[i]), "component_levels": sum(str(v).startswith(ag + " | ") for v in sub_coef)}
    pscg = by_group("pscg", ["lo_pscg", "lo_psc4"], levels=list(logit["cat"].get("pscg", {})))
    naics = by_group("naics3", ["lo_naics", "log_depth", "depth_missing", "naics_missing"], levels=list(logit["cat"].get("naics3", {})))

    def mode(rows, c):
        vc = rows[c].astype(str).value_counts()
        return str(vc.index[0]) if len(vc) else None
    known = R[R.agency.astype(str) != fl.UNKNOWN]
    w = R.loc[R.window_missing.to_numpy() == 0, "log_window"].to_numpy(float)
    modes = {c: mode(known, c) for c in ("agency", "setaside", "notice_type", "sol_letter", "pscg", "naics3", "month")}
    modes["window_days_median"] = round(float(np.expm1(np.median(w))), 1) if len(w) else None
    for c in ("late_sept", "kw_brand", "kw_sole", "has_presol", "has_ss"):
        modes[c] = int(np.median(R[c].to_numpy(float)) >= 0.5)
    return {"what": "Aggregates for the explorer's what-if calculator: the model's full-precision coefficients, and typical values "
                    "(medians of the training rows) for the inputs that come from history. Office history is replaced by the "
                    "median of the sector, so the calculator shows a typical buying office of the sector, not any real one.",
            "years": [int(last_fy - CALC_YEARS + 1), int(last_fy)], "rows_recent": int(recent.sum()),
            "component_note": COMPONENT_NOTE + " Each agency gets one typical component effect (the median over its "
                                               "recent solicitations) and the number of component levels the model has for it.",
            "logit": pub, "typical": {"overall": overall, "office_by_sector": sectors, "agency": agencies, "component_by_agency": components,
                                        "pscg": pscg, "naics3": naics},
            "modes": modes}


def train():
    t0 = time.time()
    if not (os.path.exists(EVAL_CSV) and os.path.exists(OOT_PATH)) or os.path.getmtime(OOT_PATH) < os.path.getmtime(TRAIN_PATH):
        say("the evaluation is missing or older than the training table: running --evaluate first")
        evaluate()
    ev = pd.read_csv(EVAL_CSV, dtype={"test_fy": str})
    oot = pd.read_parquet(OOT_PATH, columns=["test_fy", "y", "sector", "sector_known", "award_date", "p_gbm", "p_gbm_cal"]
                          + sorted({c for r in RAW_MODELS for c in (r["col"], r["col"] + "_cal", r["col"] + "_sec")}))
    summary = read_json(SUMMARY_PATH)
    years = sorted(int(v) for v in oot.test_fy.unique())
    kind, cand, reasons, cand_table = choose_model(ev, oot, years)
    rule_pick_name = cand_table.loc[cand_table.chosen_by_rule, "candidate"].iloc[0]
    write_calibration_table(oot, years, {"logit": cand["name"], "gbm": M_GBM}[kind])     # the reliability table of the shipped model
    T = load_training()
    for r in reasons:
        say(r)
    last_fy = int(T.posted_fy.max())
    use = np.ones(len(T), bool) if not cand["window"] else (T.posted_fy > last_fy - cand["window"]).to_numpy()
    rates = fl.baseline_rates(T.loc[use, ["sector", "agency"]], T.y.to_numpy(float)[use])
    F, y = (T if use.all() else T[use]), T.y.to_numpy(float)[use]
    weights = decay_weights(F.posted_fy.to_numpy(float), last_fy, cand["half_life"]) if cand["half_life"] else None
    logit = fit_logit_all(F, y, cat=fl.CAT_FEATURES + cand["inter"], weights=weights)   # every row; the evaluation used a sample
    model = {"version": fl.MODEL_VERSION, "kind": kind, "logit": logit,
             "settings": {"lag_quarters": fl.LAG_QUARTERS, "window_quarters": fl.WINDOW_QUARTERS, "m_shrink": fl.M_SHRINK,
                          "prior_rate": fl.PRIOR_RATE, "C": C_LOGIT, "min_level": MIN_LEVEL, "seed": SEED,
                          "candidate": cand["name"], "training_window": cand["text"], "half_life_years": cand["half_life"],
                          "window_years": cand["window"], "interactions": cand["inter"], "correction": cand["correction"]},
             "baselines": rates}
    if kind == "gbm":
        g = fit_gbm(F, y)
        model["gbm"] = export_gbm(g)
        gap = np.abs(fl._expit(fl.gbm_raw(model["gbm"], fl.gbm_matrix(F.iloc[:20000]))) - g.predict_proba(fl.gbm_matrix(F.iloc[:20000]))[:, 1]).max()
        if gap > 1e-6:
            raise SystemExit(f"exported trees do not reproduce sklearn (largest gap {gap:.2e}); nothing written")
    raw_col = {"logit": cand["raw_col"], "gbm": "p_gbm"}[kind]
    by_sector = kind == "logit" and cand["correction"] == "by sector"
    cal_rows = oot[oot.test_fy.isin(years[-CAL_YEARS:]) & oot[raw_col].notna()]
    if len(cal_rows) >= MIN_CAL_ROWS and cal_rows.y.nunique() == 2:
        model["calibration"] = {**fit_calibration(cal_rows[raw_col].to_numpy(float), cal_rows.y.to_numpy(float),
                                                  cal_rows.sector_known.to_numpy(object) if by_sector else None),
                                "fitted_on": f"held-out predictions for test years {years[-CAL_YEARS:]}"}
    else:
        model["calibration"] = None
    limits = defense_limit(oot, {"logit": cand["col"], "gbm": "p_gbm_cal"}[kind], raw_col, years, model["calibration"], by_sector)
    n_rows, share, mean_fit = int(len(F)), float(y.mean()), float(fl.predict(model, F).mean())
    posting_years = [int(F.posted_fy.min()), int(F.posted_fy.max())]
    calculator = calculator_card(F, logit, last_fy) if kind == "logit" else None     # public aggregates (see calculator_card)
    sample = F.iloc[:: max(1, len(F) // 5000)].copy()
    del T, F, y, oot, cal_rows, weights                               # the big tables are done; free them before the history is loaded
    release()
    hist = load_history()
    last_q = fl.last_quarter(hist)
    model["history"] = fl.trim_history(hist, last_q - KEEP_QUARTERS + 1)
    del hist
    release()
    model["trained_on"] = {"award_years": summary["award_years"], "posting_years": posting_years,
                           "rows": n_rows, "single_offer_share": share, "training_window": cand["text"],
                           "last_history_quarter": f"{last_q // 4}Q{last_q % 4 + 1} (calendar)"}
    fl.save_model(model, MODEL_PATH)
    back = fl.load_model(MODEL_PATH)
    if np.abs(fl.predict(back, sample) - fl.predict(model, sample)).max() > 1e-12:
        raise SystemExit("the saved model does not reproduce the in-memory predictions")
    del back
    say(f"final model: {kind}, {n_rows:,} rows, mean forecast {mean_fit:.3f}, single-offer share {share:.3f}; saved {MODEL_PATH} "
        f"({os.path.getsize(MODEL_PATH) / 1e6:.1f} MB)  [{time.time() - t0:.0f}s]")

    coef = coefficient_table(logit)                                   # no component rows (public_logit)
    coef.to_csv(COEF_CSV, index=False)
    by_year, by_sector, total = funnel_tables(summary)
    pooled = ev[(ev.test_fy == "pooled") & ~ev.model.str.contains("without")]
    blocks = ev[(ev.test_fy == "pooled") & ev.model.str.contains("without")].copy()
    blocks["block"] = blocks.model.str.replace("logistic v2 (raw) without: ", "", regex=False)
    blocks = blocks.sort_values("delta_logloss_when_dropped", ascending=False)
    keep_cols = ["sector", "model", "n", "single_share", "mean_forecast", "auc", "brier", "logloss", "cal_intercept", "cal_slope"]
    delta = dict(zip(blocks.block, blocks.delta_logloss_when_dropped))

    def verdict(d):
        if d is None or not np.isfinite(d):
            return "not measured"
        if d > 0.0005:
            return "earns its place out of time"
        return "small gain out of time" if d > 0 else "no measurable gain out of time; kept so that its coefficients can be read"
    shipped_name = {"logit": cand["name"], "gbm": M_GBM}[kind]
    last = years[-SELECT_YEARS:]
    main = list(dict.fromkeys([shipped_name, cand["name"], M_LOGIT, M_GBM]))
    lf = ev[ev.test_fy.isin([str(t) for t in last]) & ev.model.isin(main)].copy()
    lf["test_fy"] = lf.test_fy.astype(int)
    last_five = lf[["test_fy", "sector", "model", "n", "single_share", "mean_forecast", "auc", "brier", "logloss", "cal_slope"]]
    cal_card = None
    if model["calibration"]:
        c = model["calibration"]
        cal_card = {"probability": "expit(a + b * logit(raw probability))", "a_pooled": c["a"], "b_pooled": c["b"], "n_pooled": c["n"],
                    "fitted_on": c["fitted_on"]}
        if c.get("by_group"):
            cal_card["by_sector_group"] = [{"group": g, "sectors": sorted(s for s, gg in c["groups"].items() if gg == g),
                                            "a": v["a"], "b": v["b"], "n": v["n"]} for g, v in c["by_group"].items()]
            cal_card["other_sectors"] = "a solicitation whose sector is unknown on the posting day uses a_pooled and b_pooled"
    card = {
        "model": "single-offer forecast, version 2", "built_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "what_it_predicts": "P(exactly one offer), given that the solicitation ends in a competed award with a usable offer count. "
                            "A risk indicator about the market for a requirement, not a statement about anyone's conduct.",
        "outcome_rule": f"Earliest base action (purchase order, definitive contract or contract vehicle) with the same normalised solicitation number, "
                        f"dated on or after first posting and at most {fl.MAX_WAIT_DAYS} days later. Resolved only if competed (A, D, E, F, CDO) "
                        f"with 1 to 998 offers. Numbers shorter than {fl.MIN_SOL_LEN} characters are not matched.",
        "data": {"award_years_present": summary["award_years"], "solicitations_in_archive": summary["solicitations"],
                 "resolved_solicitations": summary["resolved"], "single_offer_share": summary["single_offer_share"],
                 "funnel_total_years_present": total.to_dict(orient="records")[0],
                 "funnel_by_posting_year": by_year.to_dict(orient="records"), "funnel_by_sector": by_sector.to_dict(orient="records"),
                 "office_code_check": office_check_table(summary).to_dict(orient="records"),
                 "funnel_note": "Sector here is the sector known on the posting day; 'unknown' means SAM.gov gave no agency and the "
                                "number matched no earlier award."},
        "no_leakage": f"Only the first version of the notice and awards from quarters ending at least {fl.LAG_QUARTERS - 1} full quarter(s) "
                      f"before the posting quarter (a {fl.WINDOW_QUARTERS}-quarter window). Not used: amendments, final deadline, award notices.",
        "shipped": {"kind": "L2 logistic regression" if kind == "logit" else "gradient boosting (exported trees)", "name_in_evaluation": shipped_name,
                    "training_window": cand["text"], "interactions": cand["inter"], "why": reasons, "rows": n_rows, "C": C_LOGIT,
                    "calibration": cal_card, "probability": "expit(a + b * logit(raw probability)); a and b by sector group when "
                                                            "calibration.by_sector_group is given"},
        "selection": {"rule_pick": rule_pick_name, "shipped": cand["name"],
                      "amendment": AMENDMENT["statement"] if cand["name"] != rule_pick_name else None,
                      "rule": "Rule 1 in the docstring of scripts/30_model_v2.py, written down before the candidates were fitted. "
                              "Score: log loss pooled over the last five test years. Changes: a shorter window or time decay 1, the "
                              "correction by sector 1, each interaction block 1. Among the candidates within 0.5% of the lowest score, "
                              "the one with the fewest changes is shipped (equal changes: the lower score).",
                      "score_years": last,
                      "candidates": cand_table.sort_values("logloss").round(5).to_dict(orient="records"),
                      "limits": ["The rule is applied to the same five years that are reported, so the chosen model's numbers there are a "
                                 "little optimistic.",
                                 "Outcomes exist only for solicitations that ended in a competed award with a usable offer count.",
                                 f"The newest test year (FY{last[-1]}) is incomplete: Defense awards are published 90 days late, and slow "
                                 "awards are still missing.",
                                 "The corrections are fitted on the held-out predictions of three test years."]},
        "features": {"categories": fl.CAT_FEATURES, "continuous": fl.CONT_FEATURES, "flags": fl.BIN_FEATURES,
                     "interactions_shipped": {c: " x ".join(fl.INTERACTIONS[c]) for c in cand["inter"]},
                     "blocks": {b: {"features": cols, "why": why, "logloss_change_when_dropped": delta.get(b),
                                    "out_of_time": verdict(delta.get(b))} for b, (cols, why) in fl.BLOCKS.items()},
                     "version_1_set": fl.V1_CAT + fl.V1_CONT + fl.V1_BIN},
        "settings": model["settings"],
        "evaluation": {"design": "Rolling origin. Train: posted before fiscal year t and awarded before fiscal year t. Test: posted in fiscal year t. "
                                 "Calibrated rows use a correction fitted on earlier held-out years only (one for all sectors, or "
                                 "by sector group for the rows named 'correction by sector'). "
                                 f"Each evaluation fit uses a seeded random sample of at most {MAX_FIT_ROWS:,} training rows; "
                                 "the final model uses every row.",
                       "test_years": years, "pooled": pooled[keep_cols].to_dict(orient="records"),
                       "last_five_years_by_sector": last_five.round(5).to_dict(orient="records"),
                       "last_five_note": "Sector here is the sector of the award's agency, as in the evaluation table. The correction "
                                         "uses the sector known on the posting day; the two differ for very few recent solicitations.",
                       "feature_blocks_logloss_change_when_dropped": blocks[["block", "delta_logloss_when_dropped", "delta_auc_when_dropped"]]
                       .to_dict(orient="records"), "files": ["results/model_v2_evaluation.csv", "results/model_v2_calibration.csv"]},
        "baselines": model["baselines"],
        "scoring": {"library": "scripts/forecast_lib.py (numpy and pandas only)",
                    "steps": ["model = forecast_lib.load_model(<DATA>/models/model_v2.json)",
                              "feats = forecast_lib.build_features(solicitations, model['history'], as_of=<today>)",
                              "p = forecast_lib.predict(model, feats)", "base = forecast_lib.baseline_predictions(model, feats)"],
                    "history_in_model_file": f"the last {KEEP_QUARTERS} quarters; refresh with history_from_awards and merge_history"},
        "calculator": calculator,
        "crosswalk_sam_to_usaspending": {"note": "SAM.gov department and sub-tier names mapped to USAspending agencies. Kept in the private "
                                                 "model file only, because it names components below agency level.",
                                         "subtier_pairs": len((model["history"].get("crosswalk") or {}).get("subtier") or {}),
                                         "departments": len((model["history"].get("crosswalk") or {}).get("department") or {})},
        "components": COMPONENT_NOTE,
        "private_files": "The model file, the training table and the history tables hold office-level history and stay in the private data folder. "
                         "This card, the coefficient table and the evaluation tables are at agency level or coarser.",
        "caveats": [
            "Conditional forecast: it says nothing about whether a solicitation leads to an award, or to a competed one.",
            "Only solicitations whose number appears on a USAspending award can be scored. Civilian awards often lack the number.",
            "Before fiscal year 2020 about half of the archived notices carry no agency or office; the agency is then taken from earlier awards "
            "with the same number start.",
            "Market depth is one 2026 snapshot of SAM.gov registrations applied to every year. It is not an outcome, but it is not historical either.",
            "Defense data are published 90 days late; the two-quarter lag covers that.",
            "Single-offer outcomes became more predictable in recent years, mostly in Defense. A model fitted on many years and a "
            "correction fitted on three years lag behind such a drift; the out-of-time tables show how much.",
            *limits,
            "Award years present when this card was built: " + ", ".join(str(v) for v in summary["award_years"]) + ". Rerun all three steps when more arrive."],
        "rerun": ["python -I scripts/30_model_v2.py --build-training", "python -I scripts/30_model_v2.py --evaluate", "python -I scripts/30_model_v2.py --train"],
        "peak_mb": {"build": summary.get("peak_mb"), "train": peak_mb()}}
    write_json(card, CARD_JSON)
    plot(ev, pd.read_csv(CAL_CSV), blocks, shipped_name, years)
    say("\nRule 1 candidates (log loss pooled over the last five test years):")
    say(cand_table.sort_values("logloss")[["candidate", "changes", "logloss", "change_vs_current_pct", "chosen_by_rule", "shipped"]].round(5).to_string(index=False))
    say(f"wrote {CARD_JSON}, {COEF_CSV}, {FIGURE}  [{time.time() - t0:.0f}s, peak {peak_mb()} MB]")
    say("\nLargest coefficients (odds ratios; continuous features per standard deviation):")
    say(coef[coef.type != "intercept"].head(20)[["feature", "level", "coefficient", "odds_ratio"]].to_string(index=False))
    return model


def plot(ev, cal, blocks, shipped, years):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    ink, muted, grid = "#333333", "#666666", "#e6e6e6"
    series = [(shipped, "#2a78d6", "version 2" if shipped == M_LOGIT else "version 2 (shipped)")]
    if shipped != M_LOGIT:
        series.append((M_LOGIT, "#7a5195", "version 2 as shipped before Nov 2026"))
    series += [(M_OFFICE, "#eb6834", "office history only"), (M_AGENCY, "#1baf7a", "agency rate"), (M_SECTOR, "#eda100", "sector rate")]
    fig, axes = plt.subplots(1, 3, figsize=(14, 4.6), layout="constrained", gridspec_kw={"width_ratios": [1.15, 1, 1.1]})
    ax = axes[0]
    yr = ev[(ev.test_fy != "pooled") & (ev.sector == "all")]
    xs = np.arange(len(years))
    for name, color, label in series:
        v = yr[yr.model == name].set_index("test_fy").logloss.reindex([str(t) for t in years]).to_numpy(float)
        ax.plot(xs, v, "-o", color=color, lw=2, ms=6, mec="white", mew=1.2, label=label, zorder=3 if name == shipped else 2)
    ax.set_xticks(xs, [f"FY{t}" for t in years], fontsize=8, color=ink, rotation=0 if len(years) <= 8 else 60)
    ax.set_xlim(-0.4, len(years) - 0.6)
    ax.set_ylabel("log loss on the held-out year (lower is better)", fontsize=8.5, color=ink)
    ax.set_title("Out of time: each year is forecast\nfrom earlier years only", fontsize=9.5, loc="left", color=ink)
    ax.legend(fontsize=8, frameon=False, loc="upper center", bbox_to_anchor=(0.5, -0.2), ncol=2)
    ax = axes[1]
    ax.plot([0, 1], [0, 1], color="#8c8c8c", lw=1, ls="--")
    top, n_all = 0.3, 0
    for sector, color, label in ((fl.DOD, "#2a78d6", "Defense"), (fl.SECTORS[0], "#eb6834", "civilian except GSA")):
        c = cal[(cal.model == shipped) & (cal.sector == sector) & (cal.n > 0)]
        if not len(c):
            continue
        ax.plot(c.mean_forecast, c.observed_share, "-o", color=color, lw=1.2, ms=6, mec="white", mew=1.2, label=label, zorder=3)
        top, n_all = max(top, float(c.mean_forecast.max()), float(c.observed_share.max())), n_all + int(c.n.sum())
    top *= 1.08
    years_txt = str(cal.test_years.iloc[0]).replace("-", " to ") if len(cal) else ""
    ax.legend(fontsize=8, frameon=False, loc="upper left")
    ax.set_xlim(0, top)
    ax.set_ylim(0, top)
    ax.set_aspect("equal", adjustable="box")
    ax.set_xlabel("forecast chance of a single offer (band mean)", fontsize=8.5, color=ink)
    ax.set_ylabel("share that got a single offer", fontsize=8.5, color=ink)
    ax.set_title(f"Calibration by sector, {years_txt}\n(shipped model; n = {n_all:,})", fontsize=9.5, loc="left", color=ink)
    ax = axes[2]
    b = blocks.sort_values("delta_logloss_when_dropped")
    ax.barh(np.arange(len(b)), b.delta_logloss_when_dropped, height=0.6, color="#2a78d6")
    ax.axvline(0, color="#8c8c8c", lw=1)
    ax.set_yticks(np.arange(len(b)), b.block, fontsize=8, color=ink)
    ax.set_xlabel("log loss added when the block is left out", fontsize=8.5, color=ink)
    ax.set_title("What each block of features adds\n(model refitted without it; last test years)", fontsize=9.5, loc="left", color=ink)
    for ax in axes:
        ax.spines[["top", "right"]].set_visible(False)
        ax.tick_params(colors=muted, labelsize=8)
        ax.grid(color=grid, lw=0.8)
        ax.set_axisbelow(True)
    axes[2].grid(axis="y", visible=False)
    axes[2].tick_params(axis="y", length=0)
    fig.suptitle("Single-offer forecast, version 2: tested on years it had not seen", fontsize=12, x=0.01, ha="left", color=ink)
    os.makedirs(os.path.dirname(FIGURE), exist_ok=True)
    fig.savefig(FIGURE, dpi=150, bbox_inches="tight", pad_inches=0.15)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description="forecast model version 2: build the training table, evaluate out of time, train")
    ap.add_argument("--build-training", action="store_true")
    ap.add_argument("--evaluate", action="store_true")
    ap.add_argument("--train", action="store_true")
    ap.add_argument("--all", action="store_true", help="the three steps in order")
    a = ap.parse_args()
    if not (a.build_training or a.evaluate or a.train or a.all):
        ap.print_help()
        return 2
    if a.build_training or a.all:
        build_training()
        release()
    if a.evaluate or a.all:
        evaluate()
        release()
    if a.train or a.all:
        train()
    return 0


if __name__ == "__main__":
    sys.exit(main())
