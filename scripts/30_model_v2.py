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
                     logistic regression (all earlier years, and the last 6 years only), a gradient-boosting benchmark.
                     "Calibrated" rows use a correction fitted only on held-out years BEFORE the test year.
                     Each evaluation fit uses a seeded random sample of at most 200,000 training rows (memory and time).
                     Also: what each block of features adds (the model refitted without the block, last two test years).
                     Writes results/model_v2_evaluation.csv, results/model_v2_calibration.csv (public, sector level) and
                     <DATA>/models/oot_predictions_v2.parquet (private).
  --train            Final model on ALL resolved solicitations (Newton's method, 50,000 rows at a time).  The calibration is
                     fitted on the held-out predictions of the last three test years.  Writes <DATA>/models/model_v2.json (PRIVATE: everything needed to score, with
                     office-level history) and the public results/model_v2_card.json, results/model_v2_coefficients.csv
                     (no office-level rows) and figures/model_v2.png.

Which model is shipped (rules fixed in this file, applied to the out-of-time results)
  The logistic regression is shipped unless gradient boosting is CLEARLY better out of time: pooled log loss at least 2% lower,
  lower in at least 80% of the test years, and a calibration slope no further from 1.  "Last 6 years only" replaces "all earlier
  years" only if its pooled log loss is at least 0.5% lower on the years where the two differ.

Features, leakage rules and the scoring interface are documented at the top of forecast_lib.py.
Privacy: public files are at agency level or coarser.  No office codes, solicitation numbers or company codes leave <DATA>.
These are risk indicators about markets, not statements about anyone's conduct.

Time and memory on a laptop, 14 award years (657,000 resolved solicitations): build 2 minutes, evaluate 6 minutes, train
half a minute; peak memory about 650 MB.  Fixed seeds: the same inputs give the same model.

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
GBM_MARGIN, GBM_YEAR_SHARE, RECENT_MARGIN = 0.02, 0.8, 0.005
BANDS = [0.0, 0.05, 0.1, 0.15, 0.2, 0.3, 0.4, 0.5, 0.7, 1.0]
CLIP = 1e-4

M_SECTOR, M_AGENCY, M_OFFICE = "sector rate (baseline)", "agency rate (baseline)", "office history only (baseline)"
M_V1 = "version 1 feature set (refit)"
M_LOGIT, M_RECENT, M_GBM = "logistic v2", f"logistic v2, last {RECENT_YEARS} years", "gradient boosting"
RAW = " (raw)"
PRED_COLS = {M_SECTOR: "p_sector", M_AGENCY: "p_agency", M_OFFICE: "p_office", M_V1: "p_v1", M_LOGIT + RAW: "p_logit",
             M_LOGIT: "p_logit_cal", M_RECENT + RAW: "p_recent", M_RECENT: "p_recent_cal", M_GBM + RAW: "p_gbm", M_GBM: "p_gbm_cal"}


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
def fit_logit(F, y, cat=fl.CAT_FEATURES, cont=fl.CONT_FEATURES, binary=fl.BIN_FEATURES, C=C_LOGIT):
    """L2 logistic regression on a sparse design.  Returns the readable coefficient dictionary of forecast_lib."""
    from sklearn.linear_model import LogisticRegression
    spec = fl.fit_spec(F, cat, cont, binary, MIN_LEVEL)
    lr = LogisticRegression(C=C, max_iter=2000, tol=1e-5)
    lr.fit(fl.design_matrix(F, spec), np.asarray(y))
    return fl.logit_to_dict(spec, lr.intercept_[0], lr.coef_[0])


def fit_logit_all(F, y, cat=fl.CAT_FEATURES, cont=fl.CONT_FEATURES, binary=fl.BIN_FEATURES, C=C_LOGIT, block=50_000, max_iter=60, tol=1e-7):
    """The same model as fit_logit (same penalty: the sum of squared coefficients divided by 2C, none on the intercept), fitted
    by Newton's method with the design matrix built 50,000 rows at a time.  Memory stays small however many rows there are."""
    y = np.asarray(y, float)
    spec = fl.fit_spec(F, cat, cont, binary, MIN_LEVEL)
    p = len(fl.design_names(spec))
    beta = np.zeros(p + 1)                                   # intercept first
    beta[0] = np.log(y.mean() / (1 - y.mean()))
    pen = np.r_[0.0, np.full(p, 1.0 / C)]
    for _ in range(max_iter):
        g, H = np.zeros(p + 1), np.zeros((p + 1, p + 1))
        for a in range(0, len(F), block):
            X = fl.design_matrix(F.iloc[a:a + block], spec)
            mu = fl._expit(beta[0] + X @ beta[1:])
            w, r = mu * (1 - mu), y[a:a + block] - mu
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


def nested_calibration(oot, col, years):
    """calibrated copy of column `col`: for test year t the correction is fitted on held-out predictions of the (at most
    CAL_YEARS) earlier test years, using only solicitations whose award was dated before fiscal year t."""
    out = oot[col].to_numpy(float).copy()
    for t in years:
        prev = [u for u in years if u < t][-CAL_YEARS:]
        fit = oot[oot.test_fy.isin(prev) & (oot.award_date < fy_start(t)) & oot[col].notna()]
        now = (oot.test_fy == t).to_numpy() & oot[col].notna().to_numpy()
        if len(fit) >= MIN_CAL_ROWS and fit.y.nunique() == 2:
            a, b = fl.fit_platt(fit[col].to_numpy(float), fit.y.to_numpy(float))
            out[now] = fl.apply_calibration(out[now], {"a": a, "b": b})
    return out


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
                          "award_date": Fte.award_date.to_numpy(), "y": Fte.y.to_numpy(float)})
        o["p_sector"] = fl.sector_rate_predictor(rates, Fte)
        o["p_agency"] = fl.agency_rate_predictor(rates, Fte)
        o["p_office"] = fl.office_history_predictor(Fte)
        o["p_v1"] = fl._expit(fl.linear_predictor(fit_logit(Ftr, ytr, fl.V1_CAT, fl.V1_CONT, fl.V1_BIN), Fte))
        o["p_logit"] = fl._expit(fl.linear_predictor(fit_logit(Ftr, ytr), Fte))
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
    for raw, cal in (("p_logit", "p_logit_cal"), ("p_recent", "p_recent_cal"), ("p_gbm", "p_gbm_cal")):
        oot[cal] = nested_calibration(oot, raw, years)
    oot.to_parquet(OOT_PATH + ".tmp", index=False)
    os.replace(OOT_PATH + ".tmp", OOT_PATH)

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

    # ---- reliability table (pooled over the test years)
    cal = []
    for model in (M_LOGIT, M_LOGIT + RAW, M_GBM):
        col = PRED_COLS[model]
        for sector in ["all"] + fl.SECTORS:
            g = oot if sector == "all" else oot[oot.sector == sector]
            band = pd.cut(g[col].clip(0, 1), BANDS, include_lowest=True)
            r = g.groupby(band, observed=False).agg(n=("y", "size"), mean_forecast=(col, "mean"), observed_share=("y", "mean")).reset_index()
            r.insert(0, "sector", sector)
            r.insert(0, "model", model)
            r["band"] = [f"{iv.left:.2f}-{iv.right:.2f}".replace("-0.00-", "0.00-") for iv in r.pop(col)]
            cal.append(r)
    cal = pd.concat(cal, ignore_index=True)
    cal["test_years"] = f"FY{years[0]}-FY{years[-1]}" if len(years) > 1 else f"FY{years[0]}"
    cal[["mean_forecast", "observed_share"]] = cal[["mean_forecast", "observed_share"]].round(4)
    cal[["model", "sector", "band", "n", "mean_forecast", "observed_share", "test_years"]].to_csv(CAL_CSV, index=False)

    show = ev[(ev.sector == "all") & ~ev.model.str.contains("without")]
    say("\nOut of time, all sectors:")
    say(show[["test_fy", "model", "n", "single_share", "auc", "brier", "logloss", "cal_slope"]].round(4).to_string(index=False))
    say("\nPooled by sector:")
    say(ev[(ev.test_fy == "pooled") & (ev.sector != "all")][["sector", "model", "n", "single_share", "auc", "brier", "logloss", "cal_slope"]]
        .round(4).to_string(index=False))
    if len(a):
        say("\nLog loss change when a block of features is dropped (positive = the block helps), pooled:")
        say(ev[(ev.test_fy == "pooled") & ev.model.str.contains("without")][["model", "delta_logloss_when_dropped", "delta_auc_when_dropped"]]
            .sort_values("delta_logloss_when_dropped", ascending=False).to_string(index=False))
    say(f"\nwrote {EVAL_CSV}, {CAL_CSV}  [{time.time() - t0:.0f}s, peak {peak_mb()} MB]")
    return ev


# ----------------------------------------------------------------------------------------------------------------------
# step 3: the final model, the public card, the figure
# ----------------------------------------------------------------------------------------------------------------------
def choose_model(ev, oot):
    """apply the fixed rules of the docstring; returns (kind, variant, reasons)."""
    pooled = ev[(ev.test_fy == "pooled") & (ev.sector == "all")].set_index("model")
    by_year = ev[(ev.test_fy != "pooled") & (ev.sector == "all")]
    reasons = []
    variant = "all earlier years"
    differs = oot[oot.p_recent.notna() & (np.abs(oot.p_recent - oot.p_logit) > 1e-12)]
    if len(differs) >= MIN_TEST and differs.y.nunique() == 2:
        la, lr = metrics(differs.y, differs.p_logit_cal)["logloss"], metrics(differs.y, differs.p_recent_cal)["logloss"]
        if lr < la * (1 - RECENT_MARGIN):
            variant = f"last {RECENT_YEARS} years"
        reasons.append(f"Training window: log loss {la:.4f} with all earlier years, {lr:.4f} with the last {RECENT_YEARS} years "
                       f"(test years where the two differ). Kept: {variant}.")
    else:
        reasons.append(f"Training window: too few test years with more than {RECENT_YEARS} earlier years to compare. Kept: all earlier years.")
    base = M_LOGIT if variant == "all earlier years" else M_RECENT
    kind = "logit"
    if M_GBM in pooled.index and base in pooled.index:
        ll, lg = pooled.logloss[base], pooled.logloss[M_GBM]
        yl = by_year[by_year.model == base].set_index("test_fy").logloss
        yg = by_year[by_year.model == M_GBM].set_index("test_fy").logloss
        share = float((yg.reindex(yl.index) < yl).mean())
        sl, sg = abs(pooled.cal_slope[base] - 1), abs(pooled.cal_slope[M_GBM] - 1)
        clearly = (lg < ll * (1 - GBM_MARGIN)) and (share >= GBM_YEAR_SHARE) and (sg <= sl + 0.05)
        kind = "gbm" if clearly else "logit"
        reasons.append(f"Gradient boosting against the logistic regression, pooled out of time: log loss {lg:.4f} against {ll:.4f} "
                       f"({100 * (1 - lg / ll):+.1f}%), better in {100 * share:.0f}% of the test years, calibration slope "
                       f"{pooled.cal_slope[M_GBM]:.2f} against {pooled.cal_slope[base]:.2f}. "
                       + ("That is clearly better by the fixed rule (2% lower log loss, 80% of the years, slope no worse), so gradient boosting is shipped. "
                          "The logistic coefficients are still published for reading." if clearly else
                          "That is not clearly better by the fixed rule (2% lower log loss, 80% of the years, slope no worse), "
                          "so the readable logistic regression is shipped."))
    return kind, variant, reasons


def coefficient_table(logit):
    block_of = {c: b for b, (cols, _) in fl.BLOCKS.items() for c in cols}
    rows = [{"feature": "(intercept)", "level": "", "type": "intercept", "block": "", "coefficient": logit["intercept"]}]
    for c, table in logit["cat"].items():
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


def train():
    t0 = time.time()
    if not (os.path.exists(EVAL_CSV) and os.path.exists(OOT_PATH)) or os.path.getmtime(OOT_PATH) < os.path.getmtime(TRAIN_PATH):
        say("the evaluation is missing or older than the training table: running --evaluate first")
        evaluate()
    ev = pd.read_csv(EVAL_CSV, dtype={"test_fy": str})
    oot = pd.read_parquet(OOT_PATH, columns=["test_fy", "y", "p_logit", "p_recent", "p_gbm", "p_logit_cal", "p_recent_cal"])
    summary = read_json(SUMMARY_PATH)
    kind, variant, reasons = choose_model(ev, oot)
    T = load_training()
    for r in reasons:
        say(r)
    last_fy = int(T.posted_fy.max())
    use = np.ones(len(T), bool) if variant == "all earlier years" else (T.posted_fy > last_fy - RECENT_YEARS).to_numpy()
    rates = fl.baseline_rates(T.loc[use, ["sector", "agency"]], T.y.to_numpy(float)[use])
    F, y = (T if use.all() else T[use]), T.y.to_numpy(float)[use]
    logit = fit_logit_all(F, y)                              # every row; the evaluation fits used a capped sample
    model = {"version": fl.MODEL_VERSION, "kind": kind, "logit": logit,
             "settings": {"lag_quarters": fl.LAG_QUARTERS, "window_quarters": fl.WINDOW_QUARTERS, "m_shrink": fl.M_SHRINK,
                          "prior_rate": fl.PRIOR_RATE, "C": C_LOGIT, "min_level": MIN_LEVEL, "seed": SEED},
             "baselines": rates}
    if kind == "gbm":
        g = fit_gbm(F, y)
        model["gbm"] = export_gbm(g)
        gap = np.abs(fl._expit(fl.gbm_raw(model["gbm"], fl.gbm_matrix(F.iloc[:20000]))) - g.predict_proba(fl.gbm_matrix(F.iloc[:20000]))[:, 1]).max()
        if gap > 1e-6:
            raise SystemExit(f"exported trees do not reproduce sklearn (largest gap {gap:.2e}); nothing written")
    years = sorted(int(v) for v in oot.test_fy.unique())
    raw_col = {"logit": "p_logit" if variant == "all earlier years" else "p_recent", "gbm": "p_gbm"}[kind]
    cal_rows = oot[oot.test_fy.isin(years[-CAL_YEARS:]) & oot[raw_col].notna()]
    if len(cal_rows) >= MIN_CAL_ROWS and cal_rows.y.nunique() == 2:
        a, b = fl.fit_platt(cal_rows[raw_col].to_numpy(float), cal_rows.y.to_numpy(float))
        model["calibration"] = {"a": a, "b": b, "fitted_on": f"held-out predictions for test years {years[-CAL_YEARS:]}", "n": int(len(cal_rows))}
    else:
        model["calibration"] = None
    n_rows, share, mean_fit = int(len(F)), float(y.mean()), float(fl.predict(model, F).mean())
    posting_years = [int(F.posted_fy.min()), int(F.posted_fy.max())]
    sample = F.iloc[:: max(1, len(F) // 5000)].copy()
    del T, F, y, oot, cal_rows                               # the big tables are done; free them before the history is loaded
    release()
    hist = load_history()
    last_q = fl.last_quarter(hist)
    model["history"] = fl.trim_history(hist, last_q - KEEP_QUARTERS + 1)
    del hist
    release()
    model["trained_on"] = {"award_years": summary["award_years"], "posting_years": posting_years,
                           "rows": n_rows, "single_offer_share": share, "training_window": variant,
                           "last_history_quarter": f"{last_q // 4}Q{last_q % 4 + 1} (calendar)"}
    fl.save_model(model, MODEL_PATH)
    back = fl.load_model(MODEL_PATH)
    if np.abs(fl.predict(back, sample) - fl.predict(model, sample)).max() > 1e-12:
        raise SystemExit("the saved model does not reproduce the in-memory predictions")
    del back
    say(f"final model: {kind}, {n_rows:,} rows, mean forecast {mean_fit:.3f}, single-offer share {share:.3f}; saved {MODEL_PATH} "
        f"({os.path.getsize(MODEL_PATH) / 1e6:.1f} MB)  [{time.time() - t0:.0f}s]")

    coef = coefficient_table(logit)
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
    shipped_name = {"logit": M_LOGIT if variant == "all earlier years" else M_RECENT, "gbm": M_GBM}[kind]
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
                    "training_window": variant, "why": reasons, "rows": n_rows, "C": C_LOGIT,
                    "calibration": model["calibration"], "probability": "expit(a + b * logit(raw probability))"},
        "features": {"categories": fl.CAT_FEATURES, "continuous": fl.CONT_FEATURES, "flags": fl.BIN_FEATURES,
                     "blocks": {b: {"features": cols, "why": why, "logloss_change_when_dropped": delta.get(b),
                                    "out_of_time": verdict(delta.get(b))} for b, (cols, why) in fl.BLOCKS.items()},
                     "version_1_set": fl.V1_CAT + fl.V1_CONT + fl.V1_BIN},
        "settings": model["settings"],
        "evaluation": {"design": "Rolling origin. Train: posted before fiscal year t and awarded before fiscal year t. Test: posted in fiscal year t. "
                                 "Calibrated rows use a correction fitted on earlier held-out years only. "
                                 f"Each evaluation fit uses a seeded random sample of at most {MAX_FIT_ROWS:,} training rows; "
                                 "the final model uses every row.",
                       "test_years": years, "pooled": pooled[keep_cols].to_dict(orient="records"),
                       "feature_blocks_logloss_change_when_dropped": blocks[["block", "delta_logloss_when_dropped", "delta_auc_when_dropped"]]
                       .to_dict(orient="records"), "files": ["results/model_v2_evaluation.csv", "results/model_v2_calibration.csv"]},
        "baselines": model["baselines"],
        "scoring": {"library": "scripts/forecast_lib.py (numpy and pandas only)",
                    "steps": ["model = forecast_lib.load_model(<DATA>/models/model_v2.json)",
                              "feats = forecast_lib.build_features(solicitations, model['history'], as_of=<today>)",
                              "p = forecast_lib.predict(model, feats)", "base = forecast_lib.baseline_predictions(model, feats)"],
                    "history_in_model_file": f"the last {KEEP_QUARTERS} quarters; refresh with history_from_awards and merge_history"},
        "crosswalk_sam_to_usaspending": model["history"].get("crosswalk"),
        "private_files": "The model file, the training table and the history tables hold office-level history and stay in the private data folder. "
                         "This card, the coefficient table and the evaluation tables are at agency level or coarser.",
        "caveats": [
            "Conditional forecast: it says nothing about whether a solicitation leads to an award, or to a competed one.",
            "Only solicitations whose number appears on a USAspending award can be scored. Civilian awards often lack the number.",
            "Before fiscal year 2020 about half of the archived notices carry no agency or office; the agency is then taken from earlier awards "
            "with the same number start.",
            "Market depth is one 2026 snapshot of SAM.gov registrations applied to every year. It is not an outcome, but it is not historical either.",
            "Defense data are published 90 days late; the two-quarter lag covers that.",
            "Award years present when this card was built: " + ", ".join(str(v) for v in summary["award_years"]) + ". Rerun all three steps when more arrive."],
        "rerun": ["python -I scripts/30_model_v2.py --build-training", "python -I scripts/30_model_v2.py --evaluate", "python -I scripts/30_model_v2.py --train"],
        "peak_mb": {"build": summary.get("peak_mb"), "train": peak_mb()}}
    write_json(card, CARD_JSON)
    plot(ev, pd.read_csv(CAL_CSV), blocks, shipped_name, years)
    say(f"wrote {CARD_JSON}, {COEF_CSV}, {FIGURE}  [{time.time() - t0:.0f}s, peak {peak_mb()} MB]")
    say("\nLargest coefficients (odds ratios; continuous features per standard deviation):")
    say(coef[coef.type != "intercept"].head(20)[["feature", "level", "coefficient", "odds_ratio"]].to_string(index=False))
    return model


def plot(ev, cal, blocks, shipped, years):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    ink, muted, grid = "#333333", "#666666", "#e6e6e6"
    series = [(shipped, "#2a78d6", "version 2"), (M_OFFICE, "#eb6834", "office history only"),
              (M_AGENCY, "#1baf7a", "agency rate"), (M_SECTOR, "#eda100", "sector rate")]
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
    ax.legend(fontsize=8, frameon=False, loc="best")
    ax = axes[1]
    c = cal[(cal.model == shipped) & (cal.sector == "all") & (cal.n > 0)]
    ax.plot([0, 1], [0, 1], color="#8c8c8c", lw=1, ls="--")
    ax.scatter(c.mean_forecast, c.observed_share, s=np.clip(12 + 60 * np.sqrt(c.n / c.n.max()), 12, 72), color="#2a78d6",
               edgecolor="white", linewidth=1.2, zorder=3)
    top = float(max(c.mean_forecast.max(), c.observed_share.max(), 0.3)) * 1.08
    ax.set_xlim(0, top)
    ax.set_ylim(0, top)
    ax.set_aspect("equal", adjustable="box")
    ax.set_xlabel("forecast chance of a single offer (band mean)", fontsize=8.5, color=ink)
    ax.set_ylabel("share that got a single offer", fontsize=8.5, color=ink)
    ax.set_title(f"Calibration, held-out years pooled\n(dot size: solicitations in the band; n = {int(c.n.sum()):,})", fontsize=9.5, loc="left", color=ink)
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
