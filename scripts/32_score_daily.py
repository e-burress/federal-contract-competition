"""
32_score_daily.py - SCORE the sealed daily single-offer forecasts (31_daily_forecast.py) once USAspending has posted the awards.

What it does
  1. Collects the private batch files PIO-D-<YYYYMMDD>.json from <DATA>/private/forecast_batches/ (with --download: first fetches
     them from the DRAFT releases forecast-batches-<YYYY-MM> with gh).  Each file is fingerprinted and used ONLY if its SHA-256
     matches the line in the public forecasts/daily/SHA256SUMS.txt (same check as 09_resolve_forecast.py).  A file that does not
     match is refused, left out, and makes the run end with exit code 3.  A file that is not listed is ignored (it was never sealed).
  2. Finds outcomes with the rule written in every batch (31_daily_forecast.RESOLUTION_RULE): the earliest base action
     (modification 0) with the same normalized solicitation number and an action date on or after the record's deadline_date
     (the date offers were due, as posted when the forecast was made; ties: larger base-and-all-options value); resolved only
     if competed (A, D, E, F, CDO) with 1 to 998 offers; outcome = 1 if exactly one offer.  The date floor is applied here;
     then 09_resolve_forecast.resolve is used as it is.  This differs from PIO-F1 (script 09), which counts awards from its
     forecast date on: offers were due at least a day after each daily forecast, so an earlier award under the same number
     cannot come from them.  Award transactions come from:
       civilian  <DATA>/usaspending_live/*.zip, filled every morning by 15_civilian_fytd.py (this fiscal year so far; the files
                 carry solicitation_identifier and modification_number).  Used only when 15's record of the download
                 (download_check.json) says every file matched USAspending's own count; otherwise nothing is scored (exit 4).
       Defense   only with --defense: Department of Defense transactions downloaded for the months since the oldest Defense
                 forecast that still has no matching award (17_history_download.py's window splitting, 9 columns only).
                 Defense data are published 90 days late, so Defense forecasts resolve months after civilian ones.
     Matches found earlier are kept in the private outcomes file and combined with new data (the earliest award still wins), so
     an award that leaves the live window (a new fiscal year) is not lost.  When the outcomes file was available, the Defense
     download starts at most 120 days before the end of the previous Defense download (enough for the 90-day delay).
  3. Writes
       PRIVATE  <DATA>/private/daily_forecast_outcomes.csv     one row per forecast: number, sector, forecasts, matched award,
                                                                status, outcome (never commit or publish it)
                <DATA>/private/daily_forecast_scores_full.json  the aggregate without small-cell suppression
       PUBLIC   results/daily_forecast_scores.json and results/daily_forecast_scores.csv  (aggregate only, structure below)
     With --upload, the private outcomes file is also uploaded (replaced) to the DRAFT release forecast-outcomes, and --download
     fetches it first, so the robot keeps earlier matches between runs.

Publication rule (the rules of 09_resolve_forecast.py)
  Scores are published only when at least MIN_PUBLIC (20) forecasts have resolved.  Below that, the public file has counts only.
  Calibration bands and sector or month rows with fewer than MIN_CELL (5) resolved forecasts lose their numbers.  Then the next
  smallest rows are hidden too, until at least two rows are hidden and they hold at least MIN_CELL resolved forecasts together,
  so nothing smaller than MIN_CELL can be recovered by subtracting the shown rows from the total.
  Fresh and backlog forecasts (31_daily_forecast.FRESH_RULE) get their own rows, under the same rules.  Everyone can tell
  the sector, month and freshness of each forecast solicitation (seen.txt, SAM.gov) and whether it resolved (USAspending), so
  every published row is a known sum.  An audit (isolates_small_set) checks that no combination of the published sector,
  month and freshness rows and the total singles out fewer than MIN_CELL resolved forecasts; if one does, the freshness table,
  then the month table, then the sector table is hidden whole until none does.
  The file is rebuilt every day and its history is public, so the scores are kept as they were until the number of resolved
  forecasts has changed by at least MIN_CELL (scores.as_of says when they were computed).  Otherwise the difference between two
  days' files would give away the forecast of a single solicitation that had just resolved.

results/daily_forecast_scores.json (read by the explorer and the README)
  {
   "built_utc", "as_of",                         when it was built; the as-of date (UTC)
   "what_is_forecast", "outcome_rule", "publication_rule",      plain-English text
   "award_data": {"civilian": {...}, "defense": {...}},          where the awards came from, and when Defense was last downloaded
   "batches": {"sealed", "scored", "missing", "refused", "first_forecast_date", "last_forecast_date"},
   "n_forecasts", "n_resolved", "resolved_share",
   "unresolved": {"no_matching_award_yet", "award_not_competed", "offers_blank_or_999"},
   "counts_by_sector": [{"sector", "n_forecasts", "n_resolved"}],
   "counts_by_freshness": [{"group", "n_forecasts", "n_resolved"}],
   "scores_published": true | false,
   "scores_note": "...",
   "scores": null | {
      "as_of":       the date these scores were computed (they are kept until MIN_CELL more forecasts have resolved)
      "overall":     {"n_resolved", "n_single_offer", "observed_share", "mean_forecast",
                      "model", "sector_rate", "agency_rate", "office_history",       each {"brier", "log_loss", "auc"}
                      "brier_skill_vs_agency_rate"},
      "calibration": [{"band", "n", "mean_forecast", "observed_share", "suppressed"}],
      "by_sector":   [{"group", "n_forecasts", "n_resolved", "n_single_offer", "observed_share", "mean_forecast",
                       "model", "sector_rate", "agency_rate", "office_history", "suppressed"}],
      "by_month":    [same fields; "group" is the month of the forecast, YYYY-MM]
      "by_freshness": [same fields; "group" is "fresh" or "backlog"]
      "tables_hidden_by_audit": [names of tables hidden whole by the audit]
   }
  }
  Lower Brier and log loss are better; AUC 0.5 is a coin flip.  Log loss clips probabilities to 0.001..0.999 (as in 09).
results/daily_forecast_scores.csv: the same rows flat (dimension, group, counts, Brier, log loss and AUC per forecaster).

Usage (from the repository root)
  python -I scripts/32_score_daily.py                        # local batches, civilian live data
  python -I scripts/32_score_daily.py --download --defense   # fetch batches from the draft releases; add Defense awards
Exit codes: 0 done   3 a batch did not match its fingerprint (the rest was scored)   4 a download failed (nothing written)
"""
import argparse
import glob
import hashlib
import importlib.util
import itertools
import json
import os
import shutil
import sys
import zipfile
from datetime import datetime, timezone

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from common import DATA, RESULTS  # noqa: E402
import forecast_lib as fl  # noqa: E402


def load(name, filename):
    """import a sibling script whose file name starts with a digit"""
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


R09 = load("resolve09", "09_resolve_forecast.py")
D31 = load("daily31", "31_daily_forecast.py")

MIN_PUBLIC, MIN_CELL, BANDS = R09.MIN_PUBLIC, R09.MIN_CELL, R09.BANDS
LIVE_GLOB = os.path.join(DATA, "usaspending_live", "*.zip")
LIVE_CHECK = "download_check.json"       # left next to the live files by 15_civilian_fytd.py once its download is complete
DEFENSE_DIR = os.path.join(DATA, "score_daily_tmp", "defense")
PRIVATE_DIR = os.path.join(DATA, "private")
OUTCOMES_NAME = "daily_forecast_outcomes.csv"
FULL_NAME = "daily_forecast_scores_full.json"
PUBLIC_JSON, PUBLIC_CSV = "daily_forecast_scores.json", "daily_forecast_scores.csv"
OUTCOMES_TAG = "forecast-outcomes"
TX_COLS = ["contract_award_unique_key", "award_id_piid", "modification_number", "action_date", "awarding_agency_name",
           "solicitation_identifier", "extent_competed_code", "number_of_offers_received", "base_and_all_options_value"]
DEFENSE_OVERLAP_DAYS = 120
UNRESOLVED = ("no_matching_award_yet", "award_not_competed", "offers_blank_or_999")
FORECASTERS = {"model": "p_single_offer", "sector_rate": "p_sector_rate", "agency_rate": "p_agency_rate",
               "office_history": "p_office_history"}
ITEM_COLS = (["batch_id", "forecast_date", "sol_norm", "department", "agency", "sector", "deadline_date", "fresh"]
             + list(FORECASTERS.values()))
FRESHNESS = ("fresh", "backlog")
AUDIT_ORDER = [("by_freshness", "freshness"), ("by_month", "month"), ("by_sector", "sector")]   # hidden whole in this order
AUDIT_LIMIT = 200_000            # more candidate sets than this: treat as exposed (hide a table) rather than check them all
AWARD_COLS = ["award_id_piid", "action_date", "value", "extent_competed_code", "offers", "source"]
EXIT_REFUSED, EXIT_DATA = 3, 4
OUTCOME_RULE = D31.RESOLUTION_RULE
PUBLICATION_RULE = (f"Scores are published only when at least {MIN_PUBLIC} forecasts have resolved. Calibration bands and sector, "
                    f"month or freshness rows with fewer than {MIN_CELL} resolved forecasts are hidden, and then the next smallest "
                    f"rows too, until at least two rows with at least {MIN_CELL} resolved forecasts together are hidden. A whole "
                    f"table is hidden if combining the shown sector, month and freshness rows would single out fewer than "
                    f"{MIN_CELL} resolved forecasts. The scores are "
                    f"recomputed only when the number of resolved forecasts has changed by at least {MIN_CELL}. So no single "
                    "forecast can be read from this file or from the difference between two days' files.")


def say(*a):
    print(*a, flush=True)


def default_paths():
    return {"batch_dir": D31.BATCH_DIR, "daily_dir": D31.DAILY_DIR, "live_glob": LIVE_GLOB, "defense_dir": DEFENSE_DIR,
            "private_dir": PRIVATE_DIR, "results_dir": RESULTS}


# ----------------------------------------------------------------------------------------------------------------------
# batches
# ----------------------------------------------------------------------------------------------------------------------
def load_batches(batch_dir, daily_dir):
    """Sealed batches only.  Returns (list of batch dicts, names refused, names not listed, names listed but missing)."""
    sums = D31.read_sums(daily_dir)
    batches, refused, unlisted, present = [], [], [], set()
    for path in sorted(glob.glob(os.path.join(batch_dir, D31.BATCH_PREFIX + "*.json"))):
        name = os.path.basename(path)
        present.add(name)
        with open(path, "rb") as fh:
            data = fh.read()
        sha = hashlib.sha256(data).hexdigest()
        if name not in sums:
            unlisted.append(name)
            continue
        if sums[name] != sha:
            refused.append(name)
            say(f"REFUSED: {name} does not match its public fingerprint; it is left out.\n  public {sums[name]}\n  file   {sha}")
            continue
        try:
            b = json.loads(data.decode("utf-8"))
            ok = b["id"] + ".json" == name and isinstance(b["forecasts"], list)
        except Exception:
            ok = False
        if not ok:
            refused.append(name)
            say(f"REFUSED: {name} matches its fingerprint but cannot be read as a batch; it is left out.")
            continue
        batches.append(b)
    missing = sorted(n for n in sums if n not in present)
    return batches, refused, unlisted, missing


def items_from_batches(batches):
    rows = []
    for b in batches:
        t = D31.batch_records(b)
        if not len(t):
            continue
        t["batch_id"] = b["id"]
        t["forecast_date"] = b["forecast_date"]
        rows.append(t)
    if not rows:
        return pd.DataFrame({c: pd.Series(dtype=float if c.startswith("p_") else object) for c in ITEM_COLS})
    out = pd.concat(rows, ignore_index=True)
    for c in ITEM_COLS:
        if c not in out.columns:
            out[c] = np.nan if c.startswith("p_") else ""
    return out[ITEM_COLS]


# ----------------------------------------------------------------------------------------------------------------------
# award transactions
# ----------------------------------------------------------------------------------------------------------------------
def base_actions_from_zips(paths, wanted, source):
    """Base actions (modification 0) whose normalized solicitation number is in `wanted`, read in chunks from download ZIPs."""
    parts = []
    for p in paths:
        with zipfile.ZipFile(p) as z:
            for name in z.namelist():
                if "Transactions" not in name or not name.endswith(".csv"):
                    continue
                with z.open(name) as fh:
                    head = pd.read_csv(fh, nrows=0)
                missing = [c for c in TX_COLS if c not in head.columns]
                if missing:
                    raise ValueError(f"{os.path.basename(p)}/{name} lacks the columns {missing}")
                with z.open(name) as fh:
                    for ch in pd.read_csv(fh, usecols=TX_COLS, dtype=str, keep_default_na=False, chunksize=200_000):
                        ch = ch[ch["modification_number"].isin(["0", "00", "000", ""])]
                        sol = fl.norm_series(ch["solicitation_identifier"]).to_numpy(object)
                        hit = pd.Series(sol, dtype=object).isin(wanted).to_numpy(bool)
                        if hit.any():
                            c = ch[hit]
                            parts.append(pd.DataFrame({
                                "sol_norm": sol[hit], "award_id_piid": c["award_id_piid"].to_numpy(object),
                                "action_date": pd.to_datetime(c["action_date"], errors="coerce").to_numpy(),
                                "value": pd.to_numeric(c["base_and_all_options_value"], errors="coerce").to_numpy(float),
                                "extent_competed_code": c["extent_competed_code"].str.strip().str.upper().to_numpy(object),
                                "offers": pd.to_numeric(c["number_of_offers_received"], errors="coerce").to_numpy(float),
                                "source": source}))
    if not parts:
        return pd.DataFrame({c: pd.Series(dtype="datetime64[ns]" if c == "action_date" else float if c in ("value", "offers")
                                          else object) for c in ["sol_norm"] + AWARD_COLS})
    out = pd.concat(parts, ignore_index=True)
    return out[out["action_date"].notna()].drop_duplicates().reset_index(drop=True)


def live_problem(live_glob):
    """None when the civilian live files may be used, otherwise the reason why not. 15_civilian_fytd.py empties the folder, downloads,
    and only then writes LIVE_CHECK (the files of the download, and verified: true only when every file matched USAspending's own
    count). So a run that stopped half-way leaves files without a record. No live folder at all: nothing is read (None)."""
    folder = os.path.dirname(live_glob)
    if not os.path.isdir(folder):
        return None
    try:
        with open(os.path.join(folder, LIVE_CHECK), encoding="utf-8") as fh:
            rec = json.load(fh)
    except FileNotFoundError:
        return (f"the civilian live download has no {LIVE_CHECK}: 15_civilian_fytd.py stopped before it finished (or is still "
                "running), so its files may be incomplete")
    except (OSError, ValueError):
        return f"{LIVE_CHECK} of the civilian live download cannot be read"
    if not isinstance(rec, dict) or rec.get("verified") is not True:
        probs = (rec.get("problems") or [])[:3] if isinstance(rec, dict) else []
        return ("the civilian live download is NOT VERIFIED against USAspending's own count"
                + (f" ({'; '.join(probs)})" if probs else ""))
    if sorted(os.path.basename(p) for p in glob.glob(live_glob)) != sorted(rec.get("files") or []):
        return "the civilian live files are not the ones the verified download recorded"
    return None


def date_span(base):
    if not len(base):
        return None
    return [base["action_date"].min().strftime("%Y-%m-%d"), base["action_date"].max().strftime("%Y-%m-%d")]


def fetch_split(label, start, end, agencies, out, prefix):
    """the shared verified download (01_download_usaspending.fetch_verified) with the scorer's 9 columns: the file's rows must
    match USAspending's own count, a window that is too big or cut off is split. Strict: an unverified window counts as failed."""
    H17 = load("hist17", "17_history_download.py")
    return H17.civ.dl.fetch_verified(label, start, end, agencies, out, prefix=prefix, columns=TX_COLS)


def download_defense(start, end, out_dir):
    """Department of Defense transactions, one request per month (split further if too big). Returns the failed windows."""
    H17 = load("hist17", "17_history_download.py")
    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir)                       # always fresh: late reports change earlier weeks too
    os.makedirs(out_dir)
    failed = []
    for s, e in H17.civ.months(start, end):
        failed += fetch_split(f"DOD_{s[:7]}", s, e, [H17.DOD], out_dir, "DEFENSE")
    return failed


def defense_window(items, prev, prev_public, today, state_loaded):
    """(start, end) of the Defense download, or None when no Defense forecast still waits for an award."""
    dod = items[items["sector"] == fl.DOD]
    if state_loaded and len(prev):
        done = prev[prev["status"] != "no_matching_award_yet"]
        dod = dod[~(dod["batch_id"] + "|" + dod["sol_norm"]).isin(done["batch_id"] + "|" + done["sol_norm"])]
    if not len(dod):
        return None
    start = max(pd.Timestamp(dod["forecast_date"].min()), today - pd.Timedelta(days=fl.MAX_WAIT_DAYS))
    last = ((prev_public or {}).get("award_data", {}).get("defense", {}) or {}).get("last_download") or {}
    if state_loaded and last.get("to"):
        start = max(start, pd.Timestamp(last["to"]) - pd.Timedelta(days=DEFENSE_OVERLAP_DAYS))
    return start.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")


# ----------------------------------------------------------------------------------------------------------------------
# outcomes (09's rule)
# ----------------------------------------------------------------------------------------------------------------------
def read_previous(private_dir):
    path = os.path.join(private_dir, OUTCOMES_NAME)
    if not os.path.isfile(path):
        return pd.DataFrame(columns=["batch_id", "sol_norm", "status"] + AWARD_COLS), False
    prev = pd.read_csv(path, dtype={"batch_id": str, "sol_norm": str, "award_id_piid": str, "extent_competed_code": str,
                                    "status": str, "source": str}, keep_default_na=False, na_values=[""])
    prev["action_date"] = pd.to_datetime(prev.get("award_date"), errors="coerce")
    prev["value"] = pd.to_numeric(prev.get("award_value"), errors="coerce")
    prev["offers"] = pd.to_numeric(prev.get("offers"), errors="coerce")
    return prev, True


def resolve_all(items, base, prev):
    """09_resolve_forecast.resolve, batch by batch (each batch has its own forecast date).  `prev` adds earlier matches."""
    old = prev[prev["action_date"].notna()] if len(prev) else prev
    out = []
    for (bid, fdate), it in items.groupby(["batch_id", "forecast_date"], sort=True):
        cand = base[base["sol_norm"].isin(set(it["sol_norm"]))]
        if len(old):
            o = old[old["batch_id"] == bid][["sol_norm"] + AWARD_COLS]
            cand = pd.concat([cand, o], ignore_index=True) if len(cand) else o
        # the date floor of the daily rule: on or after the deadline date recorded in the batch (never before the forecast date)
        f0 = pd.Timestamp(fdate)
        floor = pd.to_datetime(it["deadline_date"], errors="coerce").fillna(f0)
        lim = dict(zip(it["sol_norm"], floor.where(floor >= f0, f0)))
        cand = cand[(cand["action_date"] >= cand["sol_norm"].map(lim)).to_numpy(bool)]
        cand = cand.reset_index(drop=True)
        m = R09.resolve(it.rename(columns={"sol_norm": "solicitation_number"}), cand, pd.Timestamp(fdate))
        # 09 uses the value only to break ties; carry it (and where the award was found) into the private file
        v = cand.sort_values(["sol_norm", "action_date", "value"], ascending=[True, True, False])
        v = v.drop_duplicates(["sol_norm", "award_id_piid", "action_date"])[["sol_norm", "award_id_piid", "action_date", "value", "source"]]
        out.append(m.merge(v, on=["sol_norm", "award_id_piid", "action_date"], how="left"))
    if not out:
        return pd.DataFrame(columns=ITEM_COLS + ["status", "outcome"] + AWARD_COLS)
    m = pd.concat(out, ignore_index=True)
    m = m.drop(columns=["solicitation_number"]).rename(columns={"action_date": "award_date", "value": "award_value"})
    return m


def private_table(m):
    cols = ITEM_COLS + ["status", "outcome", "award_id_piid", "award_date", "award_value", "extent_competed_code", "offers", "source"]
    t = m.copy()
    for c in cols:
        if c not in t.columns:
            t[c] = np.nan
    t["award_date"] = pd.to_datetime(t["award_date"], errors="coerce").dt.strftime("%Y-%m-%d")
    return t[cols].sort_values(["batch_id", "sol_norm"]).reset_index(drop=True)


# ----------------------------------------------------------------------------------------------------------------------
# aggregate scores
# ----------------------------------------------------------------------------------------------------------------------
def score_block(r):
    """scores of every forecaster on resolved rows `r`."""
    y = r["outcome"].to_numpy(float)
    out = {"n_resolved": int(len(r)), "n_single_offer": int(y.sum()), "observed_share": float(y.mean()) if len(r) else None,
           "mean_forecast": float(r["p_single_offer"].mean()) if len(r) else None}
    for name, col in FORECASTERS.items():
        p = r[col].to_numpy(float)
        out[name] = R09.scores(y, p) if len(r) and np.isfinite(p).all() else None
    return out


def group_rows(m, col):
    rows = []
    for g, t in m.groupby(col, sort=True):
        r = t[t["status"] == "resolved"]
        row = {"group": str(g), "n_forecasts": int(len(t))}
        row.update(score_block(r))
        rows.append(row)
    return rows


HIDE_GROUP = ["n_single_offer", "observed_share", "mean_forecast"] + list(FORECASTERS)
HIDE_BAND = ["n", "mean_forecast", "observed_share"]


def suppress(rows, count_key, hide):
    """hide rows with 1..MIN_CELL-1 resolved.  Then hide the next smallest rows until at least two rows are hidden AND they
    hold at least MIN_CELL resolved forecasts together: the total minus the shown rows gives the hidden rows' sum, and that
    sum must not be a small cell either (two hidden rows of 1 each would give away both forecasts)."""
    small = [r for r in rows if 0 < (r[count_key] or 0) < MIN_CELL]
    rest = sorted((r for r in rows if (r[count_key] or 0) >= MIN_CELL), key=lambda r: r[count_key])
    while small and rest and (len(small) == 1 or sum(r[count_key] for r in small) < MIN_CELL):
        small.append(rest.pop(0))
    for r in rows:
        r["suppressed"] = False
    for r in small:
        for k in hide:
            r[k] = None
        r["suppressed"] = True
    return rows


def freshness_of(m):
    return m["fresh"].map({True: "fresh", False: "backlog"}).fillna("unknown").astype(str)


def isolates_small_set(labels, shown):
    """True if some combination of the published rows singles out fewer than MIN_CELL resolved forecasts.
    labels: one row per resolved forecast, one column per table (its group in that table); shown: {column: groups published}.
    Each published row (and the total) is a sum over a known set of forecasts.  Forecasts with the same group in every table (an
    "atom") always appear together.  A set S of small atoms (fewer than MIN_CELL forecasts in all) is exposed if a combination
    of published rows is zero outside S, i.e. if the columns of S in the projection off the published rows are dependent."""
    cols = list(shown)
    if not cols or not len(labels):
        return False
    atoms = labels[cols].astype(str).value_counts(sort=False).sort_index()
    keys = [k if isinstance(k, tuple) else (k,) for k in atoms.index]
    w = atoms.to_numpy()
    rows = [np.ones(len(w))]
    for j, c in enumerate(cols):
        for g in shown[c]:
            rows.append(np.array([1.0 if k[j] == g else 0.0 for k in keys]))
    R = np.array(rows)
    N = np.eye(len(w)) - np.linalg.pinv(R) @ R
    checked = 0
    for size in range(1, MIN_CELL):
        pool = [i for i in range(len(w)) if w[i] <= MIN_CELL - size]
        for S in itertools.combinations(pool, size):
            if w[list(S)].sum() >= MIN_CELL:
                continue
            checked += 1
            if checked > AUDIT_LIMIT:
                return True
            if np.linalg.matrix_rank(N[:, list(S)], tol=1e-8) < size:
                return True
    return False


def audit_tables(res, tables):
    """hide whole tables (AUDIT_ORDER) until no combination of what is shown singles out a small set.  Returns the hidden names."""
    labels = pd.DataFrame({"sector": res["sector"].astype(str).to_numpy(), "month": res["forecast_date"].str[:7].to_numpy(),
                           "freshness": freshness_of(res).to_numpy()})
    hidden = []
    while True:
        shown = {col: [r["group"] for r in tables[name] if not r["suppressed"] and r["n_resolved"]]
                 for name, col in AUDIT_ORDER if name not in hidden}
        if not isolates_small_set(labels, shown):
            return hidden
        name = next(n for n, _ in AUDIT_ORDER if n not in hidden)
        for r in tables[name]:
            for k in HIDE_GROUP:
                r[k] = None
            r["suppressed"] = True
        hidden.append(name)


def build_summary(m, batches_info, award_data, as_of, public, prev_public=None):
    """The aggregate.  public=True applies the publication rule (no scores under MIN_PUBLIC, small cells hidden, and the
    scores of `prev_public` kept unless the number of resolved forecasts has moved by at least MIN_CELL since them)."""
    res = m[m["status"] == "resolved"]
    n, nr = int(len(m)), int(len(res))
    out = {"built_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "as_of": as_of,
           "what_is_forecast": D31.WHAT, "outcome_rule": OUTCOME_RULE, "publication_rule": PUBLICATION_RULE,
           "award_data": award_data, "batches": batches_info,
           "n_forecasts": n, "n_resolved": nr, "resolved_share": nr / n if n else None,
           "unresolved": {k: int((m["status"] == k).sum()) for k in UNRESOLVED},
           "counts_by_sector": [{"sector": s, "n_forecasts": int((m["sector"] == s).sum()),
                                 "n_resolved": int(((m["sector"] == s) & (m["status"] == "resolved")).sum())}
                                for s in D31.SECTOR_COLS],
           "counts_by_freshness": [{"group": g, "n_forecasts": int((freshness_of(m) == g).sum()),
                                    "n_resolved": int(((freshness_of(m) == g) & (m["status"] == "resolved")).sum())}
                                   for g in FRESHNESS]}
    show = nr >= MIN_PUBLIC or not public
    out["scores_published"] = bool(nr >= MIN_PUBLIC) if public else None
    if public and not show:
        out["scores_note"] = (f"{nr} of {n} forecasts have resolved. Scores are published once at least {MIN_PUBLIC} have resolved "
                              f"({MIN_PUBLIC - nr} more).")
        out["scores"] = None
        return out
    if not nr:
        out["scores_note"], out["scores"] = "No forecast has resolved yet.", None
        return out
    overall = score_block(res)
    b, a = overall["model"]["brier"], overall["agency_rate"]["brier"] if overall["agency_rate"] else None
    overall["brier_skill_vs_agency_rate"] = 1 - b / a if a else None
    cal = R09.calibration_table(res["outcome"].to_numpy(float), res["p_single_offer"].to_numpy(float), BANDS)
    by_sector = group_rows(m, "sector")
    by_month = group_rows(m.assign(month=m["forecast_date"].str[:7]), "month")
    by_fresh = group_rows(m.assign(freshness=freshness_of(m)), "freshness")
    hidden = []
    if public:
        cal = suppress(cal, "n", HIDE_BAND)
        by_sector = suppress(by_sector, "n_resolved", HIDE_GROUP)
        by_month = suppress(by_month, "n_resolved", HIDE_GROUP)
        by_fresh = suppress(by_fresh, "n_resolved", HIDE_GROUP)
        hidden = audit_tables(res, {"by_sector": by_sector, "by_month": by_month, "by_freshness": by_fresh})
    scores = {"as_of": as_of, "overall": overall, "calibration": cal, "by_sector": by_sector, "by_month": by_month,
              "by_freshness": by_fresh, "tables_hidden_by_audit": hidden}
    old = (prev_public or {}).get("scores") if public else None
    if old and (old.get("overall") or {}).get("n_resolved") is not None and abs(nr - old["overall"]["n_resolved"]) < MIN_CELL:
        scores = old              # no new scores yet: one more resolved forecast could be read from the day-to-day difference
    n0 = scores["overall"]["n_resolved"]
    out["scores_note"] = ("Lower Brier and log loss are better; AUC 0.5 is a coin flip." +
                          (f" Only {n0} resolved forecasts: the scores are noisy." if n0 < 100 else "") +
                          (f" The scores were computed on {scores.get('as_of') or 'an earlier day'} from {n0} resolved forecasts. "
                           f"They are recomputed only when the number of resolved forecasts has changed by at least {MIN_CELL}, so "
                           "that no single forecast can be worked out from the difference between two days' files."
                           if public else ""))
    out["scores"] = scores
    return out


def flat_rows(summary):
    """the public tables as flat CSV rows."""
    rows = []
    s = summary.get("scores")
    if not s:
        for c in summary["counts_by_sector"]:
            rows.append({"dimension": "sector", "group": c["sector"], "n_forecasts": c["n_forecasts"], "n_resolved": c["n_resolved"]})
        return rows

    def add(dim, r):
        row = {"dimension": dim, "group": r.get("group", r.get("band", "all")), "n_forecasts": r.get("n_forecasts"),
               "n_resolved": r.get("n_resolved", r.get("n")), "n_single_offer": r.get("n_single_offer"),
               "observed_share": r.get("observed_share"), "mean_forecast": r.get("mean_forecast"),
               "suppressed": r.get("suppressed", False)}
        for name in FORECASTERS:
            sc = r.get(name) or {}
            for k in ("brier", "log_loss", "auc"):
                row[f"{k}_{name}"] = sc.get(k)
        rows.append(row)
    add("overall", dict(s["overall"], group="all", n_forecasts=summary["n_forecasts"]))
    for r in s["by_sector"]:
        add("sector", r)
    for r in s["by_month"]:
        add("month", r)
    for r in s.get("by_freshness", []):
        add("freshness", r)
    for r in s["calibration"]:
        add("calibration band", r)
    return rows


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(R09._clean(obj), fh, indent=1, allow_nan=False)
        fh.write("\n")
    os.replace(tmp, path)


# ----------------------------------------------------------------------------------------------------------------------
# gh (draft releases)
# ----------------------------------------------------------------------------------------------------------------------
def download_from_releases(daily_dir, batch_dir, private_dir):
    """batches from forecast-batches-<YYYY-MM> (the months listed in SHA256SUMS.txt) and the private outcomes file."""
    D31.need_gh("--download")
    months = sorted({n[len(D31.BATCH_PREFIX):len(D31.BATCH_PREFIX) + 6] for n in D31.read_sums(daily_dir)})
    os.makedirs(batch_dir, exist_ok=True)
    for ym in months:
        tag = f"forecast-batches-{ym[:4]}-{ym[4:]}"
        d = D31._gh(["release", "download", tag, "--pattern", D31.BATCH_PREFIX + "*.json", "--dir", batch_dir, "--skip-existing"])
        if d.returncode != 0:
            raise RuntimeError(f"gh release download {tag} failed: " + d.stderr.strip())
        say(f"  batches of {tag}: downloaded")
    os.makedirs(private_dir, exist_ok=True)
    if D31.draft_exists(OUTCOMES_TAG) and OUTCOMES_NAME in D31.release_assets(OUTCOMES_TAG):
        d = D31._gh(["release", "download", OUTCOMES_TAG, "--pattern", OUTCOMES_NAME, "--dir", private_dir, "--clobber"])
        if d.returncode != 0:
            raise RuntimeError("gh release download of the outcomes file failed: " + d.stderr.strip())
        say(f"  earlier outcomes: downloaded from {OUTCOMES_TAG}")


def upload_outcomes(path):
    D31.need_gh("--upload")
    D31.ensure_draft(OUTCOMES_TAG, "Daily forecast outcomes (working file)",
                     "Per-solicitation outcomes of the daily forecasts, kept between runs. Private. Draft: not public.")
    u = D31._gh(["release", "upload", OUTCOMES_TAG, path, "--clobber"])     # a working file, not a sealed one
    if u.returncode != 0:
        raise RuntimeError("gh release upload failed: " + u.stderr.strip())
    say(f"  uploaded {os.path.basename(path)} to the draft release {OUTCOMES_TAG}")


# ----------------------------------------------------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------------------------------------------------
def parse_args(argv):
    ap = argparse.ArgumentParser(prog="python -I scripts/32_score_daily.py", description="Score the sealed daily forecasts (aggregate only).")
    ap.add_argument("--download", action="store_true", help="first fetch the batches and earlier outcomes from the draft releases (gh)")
    ap.add_argument("--upload", action="store_true", help="afterwards upload the private outcomes file to a draft release (gh)")
    ap.add_argument("--defense", action="store_true", help="also download Department of Defense transactions (slow)")
    ap.add_argument("--date", help="as-of date YYYY-MM-DD (default today, UTC)")
    return ap.parse_args(argv)


def run(argv=None, paths=None):
    a = parse_args(sys.argv[1:] if argv is None else argv)
    P = dict(default_paths(), **(paths or {}))
    today = pd.Timestamp(a.date) if a.date else pd.Timestamp(datetime.now(timezone.utc).date())
    if a.download:
        try:
            download_from_releases(P["daily_dir"], P["batch_dir"], P["private_dir"])
        except RuntimeError as e:
            say(f"ERROR: {e}\nNothing was scored or written.")
            return EXIT_DATA
    batches, refused, unlisted, missing = load_batches(P["batch_dir"], P["daily_dir"])
    if unlisted:
        say(f"Ignored {len(unlisted)} batch file(s) not listed in {D31.SUMS_NAME} (never sealed).")
    if missing:
        say(f"{len(missing)} sealed batch(es) are not in {P['batch_dir']} (use --download): they are counted as missing.")
    items = items_from_batches(batches)
    dates = sorted(b["forecast_date"] for b in batches)
    batches_info = {"sealed": len(D31.read_sums(P["daily_dir"])), "scored": len(batches), "missing": len(missing),
                    "refused": len(refused), "first_forecast_date": dates[0] if dates else None,
                    "last_forecast_date": dates[-1] if dates else None}
    say(f"{len(batches)} sealed batch(es), {len(items):,} forecasts.")

    prev, state_loaded = read_previous(P["private_dir"])
    pub_path = os.path.join(P["results_dir"], PUBLIC_JSON)
    prev_public = None
    if os.path.isfile(pub_path):
        with open(pub_path, encoding="utf-8") as fh:
            prev_public = json.load(fh)
    wanted = set(items["sol_norm"])
    problem = live_problem(P["live_glob"])
    if problem:
        say(f"ERROR: {problem}.\nNothing was scored or written (missing awards would look like 'no award yet'). Run "
            "'python run.py live' again, then score.")
        return EXIT_DATA
    live = sorted(glob.glob(P["live_glob"]))
    civ = base_actions_from_zips(live, wanted, "civilian_live")
    award_data = {"civilian": {"source": "USAspending live download of this fiscal year so far (15_civilian_fytd.py)",
                               "files": len(live), "matching_base_actions": int(len(civ)), "action_dates": date_span(civ)}}
    say(f"Civilian live files: {len(live)}; base actions with a forecast number: {len(civ):,}.")
    old_def = ((prev_public or {}).get("award_data", {}) or {}).get("defense") or {}
    award_data["defense"] = {"source": "USAspending download of Department of Defense transactions (published 90 days late)",
                             "last_download": old_def.get("last_download")}
    base = civ
    if a.defense:
        win = defense_window(items, prev, prev_public, today, state_loaded)
        if win is None:
            say("Defense: no Defense forecast is waiting for an award; nothing to download.")
        else:
            say(f"Defense: downloading transactions {win[0]} to {win[1]} ...")
            failed = download_defense(win[0], win[1], P["defense_dir"])
            if failed:
                say(f"ERROR: the Defense download failed for {failed}. Nothing was scored or written (missing data would look "
                    "like 'no award yet'). Run the same command again.")
                return EXIT_DATA
            dod = base_actions_from_zips(sorted(glob.glob(os.path.join(P["defense_dir"], "*.zip"))), wanted, "defense_download")
            award_data["defense"]["last_download"] = {"from": win[0], "to": win[1], "matching_base_actions": int(len(dod))}
            say(f"Defense: base actions with a forecast number: {len(dod):,}.")
            base = pd.concat([civ, dod], ignore_index=True) if len(dod) else civ

    m = resolve_all(items, base, prev)
    tab = private_table(m) if len(m) else private_table(pd.DataFrame(columns=ITEM_COLS))
    os.makedirs(P["private_dir"], exist_ok=True)
    opath = os.path.join(P["private_dir"], OUTCOMES_NAME)
    tab.to_csv(opath + ".tmp", index=False, lineterminator="\n")
    os.replace(opath + ".tmp", opath)
    as_of = today.strftime("%Y-%m-%d")
    write_json(os.path.join(P["private_dir"], FULL_NAME), build_summary(tab, batches_info, award_data, as_of, public=False))
    pub = build_summary(tab, batches_info, award_data, as_of, public=True, prev_public=prev_public)
    write_json(pub_path, pub)
    flat = pd.DataFrame(flat_rows(R09._clean(pub)))
    for c in ("n_forecasts", "n_resolved", "n_single_offer"):
        if c in flat.columns:
            flat[c] = flat[c].astype("Int64")
    flat.to_csv(os.path.join(P["results_dir"], PUBLIC_CSV), index=False, lineterminator="\n")

    say(f"Resolved {pub['n_resolved']:,} of {pub['n_forecasts']:,} forecasts. Unresolved: "
        + ", ".join(f"{k.replace('_', ' ')} {v:,}" for k, v in pub["unresolved"].items()) + ".")
    if pub["scores"]:
        o = pub["scores"]["overall"]
        say("Scores (public): " + "; ".join(f"{k} Brier {o[k]['brier']:.4f}" for k in FORECASTERS if o.get(k)))
    else:
        say(pub["scores_note"])
    say(f"Public aggregate: {os.path.normpath(pub_path)}\nPRIVATE per-solicitation outcomes (never commit): {os.path.normpath(opath)}")
    if a.upload:
        try:
            upload_outcomes(opath)
        except RuntimeError as e:
            say(f"ERROR: {e}")
            return EXIT_DATA
    return EXIT_REFUSED if refused else 0


def main(argv=None):
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(errors="replace")
    return run(argv)


if __name__ == "__main__":
    sys.exit(main())
