"""
31_daily_forecast.py - every morning: forecast each NEW open solicitation's chance of getting only ONE offer, and SEAL the batch.

What it does
  1. Reads the newest daily snapshot of SAM.gov notices, written by 14_sam_daily_snapshot.py: since November 2026
     <DATA>/sam_snapshots/sam_view_<date>.parquet, the notices pulled day by day from SAM.gov's Get Opportunities API (active
     when pulled); before that sam_active_<date>.parquet, SAM.gov's nightly list of active notices.  The snapshot must be at
     most 2 days old.  While the API store behind a view covers fewer than 30 days (MIN_STORE_DAYS), nothing is forecast:
     the script says from which day the first batch can be sealed and exits 0 (a dry run still forecasts).  Before then
     an amendment of a solicitation posted earlier looks new, with the wrong first posting date.
  2. Picks the solicitations to forecast (SELECTION_RULE below):
       - group notices by normalized solicitation number (upper case, letters and digits only); numbers shorter than 8
         characters are skipped, because the outcome rule cannot match them to awards;
       - the NEWEST notice with that number (over all notice types) must be a "Solicitation" or a
         "Combined Synopsis/Solicitation", and no "Award Notice" with that number may be active;
       - the newest notice's response deadline must be at least one full day (24 hours) after the forecast time
         (a notice with no deadline is skipped);
       - the number must not be in forecasts/daily/seen.txt (forecast before).  One forecast per solicitation, made the
         first time it is seen.
  3. Builds the model inputs from the snapshot (mapping below), loads the private model file, and computes the forecast and the
     three baselines with scripts/forecast_lib.py.
  4. Writes the PRIVATE batch file <DATA>/private/forecast_batches/PIO-D-<YYYYMMDD>.json (once; it is never rewritten) and, in
     the same run, appends to the PUBLIC folder forecasts/daily/:
       SHA256SUMS.txt   <sha256>  PIO-D-<YYYYMMDD>.json  made_at=<UTC time>       (the seal)
       ledger.csv       one row per batch: counts by sector, the count of FRESH forecasts (first posted at most 3 days
                        before the forecast), mean forecast (blank under 5 forecasts), model version and the model file's
                        SHA-256, the batch's SHA-256
       seen.txt         the first 16 hex characters of SHA-256(normalized solicitation number) of every forecast solicitation
                        (NOT anonymous: anyone can hash a number from SAM.gov and look it up; it shows only THAT a
                        solicitation was forecast, never the forecast)
     The robot's daily commit timestamps these lines in public.  The forecasts themselves stay private: they name
     solicitations and buying offices.  They are risk indicators about markets, not statements about anyone's conduct.

Snapshot column -> forecast_lib column (one row per solicitation)
  sol_number          -> sol_norm            normalized here (forecast_lib.norm_series)
  posted_date         -> first_posted        PostedDate of the EARLIEST active solicitation-type notice with the number
  response_deadline   -> window_first_days   (deadline of that earliest notice - first_posted) in days, as in training
                                             (22_sam_archive.py); a missing deadline counts as missing
  type, base_type     -> type_first, base_type_first      from that earliest notice
  set_aside_code, naics, psc, department, sub_tier, aac_code, kw_brand, kw_sole  -> same names, from that earliest notice
  posted_date of active Presolicitation / Sources Sought notices with the number
                      -> presol_first_posted / ss_first_posted (earliest; forecast_lib uses them only if dated before
                         first_posted)
  The NEWEST notice's response deadline (the one checked by the selection rule) is kept as deadline_utc, and its calendar
  date as deadline_date: the outcome rule counts only awards dated on or after that date.  The snapshot stores deadlines in
  UTC (14_sam_daily_snapshot.py drops the notice's own offset), so the date is the UTC date.
  Not available in a daily snapshot: versions of a notice that were already archived.  So "first" means the earliest version
  still active, and an earlier presolicitation that is no longer active is missed.  Missing values are passed as empty or
  missing, which forecast_lib treats as unknown (exactly as for missing values in training).

The private batch file (schema_version 1)
  A header (rules, model and code fingerprints, as_of, selection funnel), then "fields" and one compact list per forecast in
  that order (FIELDS below): the model inputs exactly as passed to forecast_lib (MODEL_INPUTS), what forecast_lib derived
  from them (agency, subagency, sector, office_key, office_source), the newest notice type, the deadline as posted (UTC) and
  its date, days from first posting to the forecast and the fresh flag, the forecast and the three baselines.
  recompute(batch, model) rebuilds every forecast from the file and the model file alone (tests/test_daily_forecast.py checks
  that it matches).

Safety rules
  - The batch file is created with exclusive mode: if it exists, it is never overwritten.  Rerunning on the same day does not
    make a second batch; it only completes any public line that a crashed run left out.
  - If the public SHA256SUMS.txt or ledger.csv already lists today's batch with a different fingerprint than the private file,
    the script stops (exit 3) and changes nothing.
  - The public lines are appended seen.txt first, then ledger.csv, then SHA256SUMS.txt (the seal) last.  If only some of them
    name today's batch and the batch file is not on this computer (and cannot be fetched with --upload), the script stops
    (exit 3) instead of making a second batch for the same day.
  - With --upload, the batch is uploaded BEFORE any public line is written, so nothing is sealed that is not kept.  On GitHub
    Actions (GITHUB_ACTIONS=true) a real run without --upload is refused (exit 3): the runner's disk is deleted afterwards.
  - The robot's log is public.  A real run prints only what the ledger publishes: counts, and the mean forecast when there are
    at least 5.  The spread of the forecasts is printed only with --dry-run (and also only from 5 forecasts up).
  - Stale model: if the model's newest award history is more than 9 months before today, the script refuses to forecast
    (exit 3) unless --allow-stale is given.  The batch then records "stale_model_override": true.

Options
  --dry-run       compute everything and print the numbers; write NOTHING public.  The batch goes to
                  <DATA>/private/forecast_batches/dryrun/PIO-D-<date>-DRYRUN.json (overwritten each time, never sealed).
  --upload        upload the new batch to the DRAFT (non-public) GitHub release forecast-batches-<YYYY-MM> with gh, never
                  overwriting an asset.  Before forecasting, it also fetches today's batch from that release if a crashed
                  run left one there, so a rerun reseals that file instead of making a new one.  Needs gh and GH_TOKEN.
  --fetch-model   if the model file is missing, download model_v2.json from the DRAFT release tagged model-v2 with gh.
  --model PATH    model file (default <DATA>/models/model_v2.json; private: it holds office-level history).
  --allow-stale   forecast even with a stale model (for tests; the batch records it).

Usage (from the repository root)
  python -I scripts/31_daily_forecast.py                         # forecast and seal today's new solicitations
  python -I scripts/31_daily_forecast.py --allow-stale --dry-run # try it without writing anything public

Exit codes: 0 done (also: nothing new today, or already sealed today)   2 missing input   3 refused   4 upload or download failed
"""
import argparse
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA, FORECASTS  # noqa: E402
import forecast_lib as fl  # noqa: E402

SNAP_DIR = os.path.join(DATA, "sam_snapshots")
MODEL_PATH = os.path.join(DATA, "models", "model_v2.json")
BATCH_DIR = os.path.join(DATA, "private", "forecast_batches")
DAILY_DIR = os.path.join(FORECASTS, "daily")
SUMS_NAME, LEDGER_NAME, SEEN_NAME = "SHA256SUMS.txt", "ledger.csv", "seen.txt"
MODEL_TAG, MODEL_ASSET = "model-v2", "model_v2.json"
BATCH_PREFIX = "PIO-D-"
SOLICITATION_TYPES = ("Solicitation", "Combined Synopsis/Solicitation")
PRESOL, SOURCES_SOUGHT, AWARD_NOTICE = "Presolicitation", "Sources Sought", "Award Notice"
MIN_LEAD = pd.Timedelta(days=1)          # the deadline must be at least this far after the forecast time
STALE_MONTHS = 9                         # refuse when the model's newest award history is older than this
MAX_SNAPSHOT_AGE_DAYS = 2
MIN_STORE_DAYS = 30                      # an API view must cover at least this many days (see the header)
MIN_CELL = 5                            # the ledger's mean forecast is left blank for a batch with fewer forecasts
HASH_CHARS = 16
FRESH_DAYS = 3                           # "fresh": first posted at most this many days before the forecast
SCHEMA_VERSION = 1
MODEL_INPUTS = ["sol_norm", "first_posted", "window_first_days", "type_first", "base_type_first", "set_aside_code", "naics", "psc",
                "department", "sub_tier", "aac_code", "kw_brand", "kw_sole", "presol_first_posted", "ss_first_posted"]
DATE_INPUTS = ["first_posted", "presol_first_posted", "ss_first_posted"]
DERIVED = ["agency", "subagency", "sector", "office_key", "office_source"]
P_COLS = ["p_single_offer", "p_sector_rate", "p_agency_rate", "p_office_history"]
FIELDS = (MODEL_INPUTS + DERIVED + ["type_newest", "deadline_utc", "deadline_date", "days_first_posted_to_forecast", "fresh"]
          + P_COLS)
EXIT_USAGE, EXIT_REFUSED, EXIT_GH = 2, 3, 4
SNAP_COLS = ["notice_id", "sol_number", "type", "base_type", "posted_date", "response_deadline", "department", "sub_tier",
             "aac_code", "set_aside_code", "naics", "psc", "kw_brand", "kw_sole"]
FIRST_COLS = ["set_aside_code", "naics", "psc", "department", "sub_tier", "aac_code", "kw_brand", "kw_sole"]
SECTOR_COLS = {"civilian except GSA": "n_civilian_except_gsa", "GSA": "n_gsa", fl.DOD: "n_defense", fl.UNKNOWN: "n_unknown"}
LEDGER_COLS = (["batch_id", "forecast_date", "made_at_utc", "snapshot_date", "model_version", "model_sha256", "n_forecasts"]
               + list(SECTOR_COLS.values()) + ["n_fresh", "mean_forecast", "batch_sha256"])

SELECTION_RULE = (
    "From the newest daily snapshot of SAM.gov notices (since November 2026 the notices pulled day by day from SAM.gov's Get "
    "Opportunities API, active when pulled), group notices by normalized solicitation number (upper case, letters "
    "and digits only; numbers shorter than 8 characters are skipped). Keep a number only if its newest notice is a Solicitation or a "
    "Combined Synopsis/Solicitation, no Award Notice with the number is active, the newest notice's response deadline is at least "
    "24 hours after the forecast time, and the number has not been forecast before. One forecast per solicitation, made the first "
    "time it is seen. Model inputs come from the earliest active solicitation notice with the number.")
RESOLUTION_RULE = (
    "For each forecast solicitation number S: look at USAspending base actions (modification 0) of purchase orders, definitive "
    "contracts and contract vehicles whose normalized solicitation_identifier equals S and whose action date is on or after the "
    "record's deadline_date: the calendar date (UTC) of the response deadline posted when the forecast was made. Offers were "
    "due at least a day after the forecast, so an award dated before that day cannot come from those offers (it is an earlier "
    "award under the same number). If several match, use the earliest (ties: the larger base-and-all-options value). The "
    "forecast is RESOLVED only if that award was competed (extent competed A, D, E, F or CDO) and its number of offers is 1 to "
    "998; outcome = 1 if exactly one offer, else 0. Everything else stays unresolved (no award yet, not competed, offers blank "
    "or 999) and is not scored.")
FRESH_RULE = (f"fresh = first posted (the earliest active solicitation notice in the snapshot) at most {FRESH_DAYS} days before "
              "made_at_utc; otherwise backlog (a missing posting date counts as backlog).")
RECOMPUTE = ("forecast_lib.build_features(the MODEL_INPUTS columns of the records, model['history'], as_of=as_of), then "
             "forecast_lib.predict(model, features); the model file is the one whose SHA-256 is in model.sha256.")
WHAT = ("p_single_offer: the model's probability that the solicitation's competed award receives exactly one offer, given that it "
        "ends in a competed award with a usable offer count. A risk indicator about the market for a requirement, not a statement "
        "about anyone's conduct. Baselines: p_sector_rate, p_agency_rate (training-period rates) and p_office_history (the "
        "office's shrunk past rate).")


def say(*a):
    print(*a, flush=True)


def default_paths():
    return {"snap_dir": SNAP_DIR, "model_path": MODEL_PATH, "batch_dir": BATCH_DIR, "daily_dir": DAILY_DIR,
            "dryrun_dir": os.path.join(BATCH_DIR, "dryrun")}


# ----------------------------------------------------------------------------------------------------------------------
# small helpers
# ----------------------------------------------------------------------------------------------------------------------
def short_hash(sol_norm):
    """first 16 hex characters of SHA-256 of a normalized solicitation number (not anonymous: anyone can recompute it)."""
    return hashlib.sha256(sol_norm.encode("utf-8")).hexdigest()[:HASH_CHARS]


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def batch_id_for(day):
    return f"{BATCH_PREFIX}{pd.Timestamp(day):%Y%m%d}"


def newest_snapshot(snap_dir):
    """(path, date) of the newest sam_view_<YYYY-MM-DD>.parquet (API) or sam_active_<YYYY-MM-DD>.parquet (nightly list,
    before November 2026); on the same date the view wins.  (None, None) if there is none."""
    best, best_rank = (None, None), None
    for p in glob.glob(os.path.join(snap_dir, "sam_*.parquet")):
        m = re.search(r"sam_(view|active)_(\d{4}-\d{2}-\d{2})\.parquet$", p)
        if m:
            rank = (m.group(2), m.group(1) == "view")
            if best_rank is None or rank > best_rank:
                best, best_rank = (p, m.group(2)), rank
    return best


def store_first_day(path):
    """first day of the API store behind a sam_view file (None for a nightly-list snapshot or an empty view)"""
    import pyarrow.parquet as pq
    if "store_first_day" not in pq.read_schema(path).names:
        return None
    s = pd.read_parquet(path, columns=["store_first_day"])["store_first_day"]
    return None if not len(s) or pd.isna(s.min()) else pd.Timestamp(s.min())


def read_sums(daily_dir):
    """{file name: sha256} from forecasts/daily/SHA256SUMS.txt (empty if the file does not exist yet)."""
    out, path = {}, os.path.join(daily_dir, SUMS_NAME)
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) >= 2 and re.fullmatch(r"[0-9a-f]{64}", parts[0]):
                    out[parts[1]] = parts[0]
    return out


def read_seen(daily_dir):
    """(set of short hashes, set of batch ids that have a block in seen.txt)."""
    hashes, batches, path = set(), set(), os.path.join(daily_dir, SEEN_NAME)
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                m = re.match(r"#\s*batch\s+(\S+)", line)
                if m:
                    batches.add(m.group(1))
                elif re.fullmatch(r"[0-9a-f]{%d}" % HASH_CHARS, line):
                    hashes.add(line)
    return hashes, batches


def ledger_shas(daily_dir):
    """{batch id: batch_sha256} from forecasts/daily/ledger.csv (empty if the file does not exist yet)."""
    path = os.path.join(daily_dir, LEDGER_NAME)
    if not os.path.isfile(path):
        return {}
    t = pd.read_csv(path, dtype=str, usecols=["batch_id", "batch_sha256"], keep_default_na=False)
    return dict(zip(t["batch_id"], t["batch_sha256"]))


def ledger_ids(daily_dir):
    return set(ledger_shas(daily_dir))


def public_records_of(daily_dir, bid):
    """which public files already have a line for batch `bid`: {file name: True/False}."""
    return {SUMS_NAME: bid + ".json" in read_sums(daily_dir), LEDGER_NAME: bid in ledger_shas(daily_dir),
            SEEN_NAME: bid in read_seen(daily_dir)[1]}


def model_newest_award(model):
    """last day of the newest calendar quarter in the model's award history (NaT if it has none)."""
    q = fl.last_quarter(model["history"])
    if q < 0:
        return pd.NaT
    return pd.Timestamp(year=q // 4, month=3 * (q % 4) + 1, day=1) + pd.offsets.QuarterEnd(0)


# ----------------------------------------------------------------------------------------------------------------------
# selection and model inputs
# ----------------------------------------------------------------------------------------------------------------------
def select_solicitations(snap, now, seen):
    """snapshot rows -> (one row per solicitation to forecast, with the forecast_lib input columns; funnel counts).
    now: tz-aware UTC timestamp of the forecast.  seen: set of short hashes already forecast."""
    t = pd.DataFrame({c: snap[c].astype("string") if c not in ("posted_date", "response_deadline", "kw_brand", "kw_sole")
                      else snap[c] for c in SNAP_COLS if c in snap.columns})
    t["sol_norm"] = fl.norm_series(t["sol_number"]).to_numpy(object)
    dl = pd.to_datetime(t["response_deadline"], errors="coerce", utc=True)
    t["response_deadline"] = dl
    t["posted_date"] = pd.to_datetime(t["posted_date"], errors="coerce")
    t["type"] = t["type"].fillna("")
    funnel = {"notices_in_snapshot": int(len(t))}
    t = t[(t["sol_norm"] != "").to_numpy(bool)]
    t = t.sort_values(["sol_norm", "posted_date", "notice_id"], na_position="first", kind="mergesort").reset_index(drop=True)
    is_sol = t["type"].isin(SOLICITATION_TYPES)
    funnel["solicitation_numbers_with_a_solicitation_notice"] = int(t.loc[is_sol, "sol_norm"].nunique())
    newest = t.groupby("sol_norm", sort=False).tail(1)
    cand = newest[newest["type"].isin(SOLICITATION_TYPES)]
    funnel["newest_notice_is_a_solicitation"] = int(len(cand))
    awarded = set(t.loc[t["type"] == AWARD_NOTICE, "sol_norm"])
    cand = cand[~cand["sol_norm"].isin(awarded)]
    funnel["no_active_award_notice"] = int(len(cand))
    cand = cand[(cand["response_deadline"] >= now + MIN_LEAD).fillna(False).to_numpy(bool)]
    funnel["deadline_at_least_one_day_ahead"] = int(len(cand))
    cand = cand[(cand["sol_norm"].str.len() >= fl.MIN_SOL_LEN).to_numpy(bool)]
    funnel["number_long_enough_to_match"] = int(len(cand))
    h = cand["sol_norm"].map(short_hash)
    cand = cand[~h.isin(seen).to_numpy(bool)]
    funnel["not_forecast_before"] = int(len(cand))
    keep = set(cand["sol_norm"])

    first = t[is_sol & t["sol_norm"].isin(keep)].groupby("sol_norm", sort=True).head(1).set_index("sol_norm")
    out = pd.DataFrame(index=pd.Index(sorted(keep), name="sol_norm"))
    out["first_posted"] = first["posted_date"]
    posted_utc = out["first_posted"].dt.tz_localize("UTC")
    out["window_first_days"] = ((first["response_deadline"] - posted_utc) / pd.Timedelta(days=1)).astype(float)
    out["type_first"] = first["type"]
    out["base_type_first"] = first["base_type"]
    for c in FIRST_COLS:
        out[c] = first[c] if c in first.columns else pd.NA
    for col, typ in (("presol_first_posted", PRESOL), ("ss_first_posted", SOURCES_SOUGHT)):
        e = t[(t["type"] == typ) & t["sol_norm"].isin(keep)].groupby("sol_norm")["posted_date"].min()
        out[col] = e.reindex(out.index)
    newest_kept = cand.set_index("sol_norm")
    out["type_newest"] = newest_kept["type"]
    out["deadline_utc"] = newest_kept["response_deadline"]            # the deadline checked above, as posted (UTC)
    out = out.reset_index()
    for c in ("kw_brand", "kw_sole"):
        out[c] = pd.to_numeric(out[c], errors="coerce").fillna(0).astype(int)
    return out, funnel


def _text_or_none(x):
    return [None if pd.isna(v) else str(v) for v in pd.Series(x).astype("string")]


def _date_text(x, fmt="%Y-%m-%dT%H:%M:%S"):
    return [None if pd.isna(v) else v.strftime(fmt) for v in pd.Series(x)]


def forecast_rows(model, sols, as_of, made_at=None):
    """forecast_lib scoring: one record per solicitation (sorted by number), with every field of FIELDS in JSON-ready form:
    the model inputs exactly as passed to forecast_lib, what it derived from them, the deadline, freshness and the forecasts."""
    sols = sols.sort_values("sol_norm").reset_index(drop=True)
    made_at = pd.Timestamp(made_at if made_at is not None else as_of)
    made_at = made_at.tz_convert("UTC").tz_localize(None) if made_at.tzinfo is not None else made_at
    feats = fl.build_features(sols, model["history"], as_of=as_of)
    p = fl.predict(model, feats)
    base = fl.baseline_predictions(model, feats)
    rec = pd.DataFrame(index=sols.index)
    for c in MODEL_INPUTS + ["type_newest"]:
        col = sols[c] if c in sols.columns else pd.Series(pd.NA, index=sols.index)
        if c in DATE_INPUTS:
            rec[c] = _date_text(pd.to_datetime(col, errors="coerce"))
        elif c in ("kw_brand", "kw_sole"):
            rec[c] = pd.to_numeric(col, errors="coerce").fillna(0).astype(int).tolist()
        elif c == "window_first_days":
            rec[c] = [None if not np.isfinite(v) else float(v) for v in pd.to_numeric(col, errors="coerce").to_numpy(float)]
        else:
            rec[c] = _text_or_none(col)
    for c in DERIVED:
        rec[c] = feats[c].astype(str).to_numpy(object)
    dl = pd.to_datetime(sols["deadline_utc"] if "deadline_utc" in sols.columns else pd.Series(pd.NaT, index=sols.index),
                        errors="coerce", utc=True)
    rec["deadline_utc"] = _date_text(dl, "%Y-%m-%dT%H:%M:%S+00:00")
    rec["deadline_date"] = _date_text(dl, "%Y-%m-%d")
    days = (made_at - pd.to_datetime(sols["first_posted"], errors="coerce")) / pd.Timedelta(days=1)
    rec["days_first_posted_to_forecast"] = [None if pd.isna(v) else round(float(v), 3) for v in days]
    rec["fresh"] = [bool(v <= FRESH_DAYS) if not pd.isna(v) else False for v in days]
    rec["p_single_offer"] = np.round(p, 6)
    for c in P_COLS[1:]:
        rec[c] = np.round(base[c].to_numpy(float), 6)
    return rec[FIELDS].reset_index(drop=True)


def batch_records(batch):
    """the forecasts of a batch as a DataFrame (one row per forecast, the columns of batch["fields"])."""
    return pd.DataFrame(batch["forecasts"], columns=batch["fields"])


def recompute(batch, model):
    """rebuild every forecast of a batch from its own records and the model file (see RECOMPUTE)."""
    t = batch_records(batch)[MODEL_INPUTS].copy()
    for c in DATE_INPUTS:
        t[c] = pd.to_datetime(t[c], format="ISO8601", errors="coerce")
    return fl.predict(model, fl.build_features(t, model["history"], as_of=batch["as_of"]))


def sector_counts(rec):
    vc = rec["sector"].value_counts()
    return {s: int(vc.get(s, 0)) for s in SECTOR_COLS}


def sha256_of_code(name):
    return sha256_file(os.path.join(os.path.dirname(os.path.abspath(__file__)), name))


def build_batch(bid, made_at, snap_date, snap_file, model, model_sha, model_file, newest_award, stale_override, rec, funnel):
    return {
        "schema_version": SCHEMA_VERSION,
        "id": bid,
        "kind": "daily single-offer forecasts (sealed: fingerprint public, contents private until scored)",
        "made_at_utc": made_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "forecast_date": made_at.strftime("%Y-%m-%d"),
        "as_of": made_at.strftime("%Y-%m-%d"),
        "snapshot": {"file": snap_file, "date": snap_date},
        "model": {"version": model.get("version"), "kind": model.get("kind"), "file": model_file, "sha256": model_sha,
                  "newest_award_history": None if pd.isna(newest_award) else newest_award.strftime("%Y-%m-%d"),
                  "stale_model_override": bool(stale_override)},
        "code": {"forecast_lib.py_sha256": sha256_of_code("forecast_lib.py"),
                 "31_daily_forecast.py_sha256": sha256_of_code(os.path.basename(__file__))},
        "what_is_forecast": WHAT,
        "selection_rule": SELECTION_RULE,
        "resolution_rule": RESOLUTION_RULE,
        "fresh_rule": FRESH_RULE,
        "recompute": RECOMPUTE,
        "selection_funnel": funnel,
        "n_forecasts": int(len(rec)),
        "n_fresh": int(rec["fresh"].sum()) if len(rec) else 0,
        "counts_by_sector": sector_counts(rec),
        "fields": list(FIELDS),
        "forecasts": rec[FIELDS].to_numpy(object).tolist(),
    }


def _plain(v):
    """numpy scalars -> plain Python values for json; a missing value (NaN, NaT, NA) -> null"""
    v = v.item() if isinstance(v, np.generic) else v
    return None if v is not None and not isinstance(v, (str, bool, list, dict)) and pd.isna(v) else v


def batch_bytes(batch):
    """the header with one key per line, then one compact line per forecast (deterministic, about 300 bytes a forecast)."""
    head = json.dumps({k: v for k, v in batch.items() if k != "forecasts"}, indent=1, ensure_ascii=True, allow_nan=False)
    rows = ",\n".join("  " + json.dumps([_plain(v) for v in r], ensure_ascii=True, allow_nan=False, separators=(",", ":"))
                      for r in batch["forecasts"])
    return (head[:-2] + ',\n "forecasts": [\n' + rows + ("\n" if rows else "") + " ]\n}\n").encode("utf-8")


def write_once(path, data):
    """create the file; never overwrite (exclusive mode)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "xb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())


# ----------------------------------------------------------------------------------------------------------------------
# public records (append only)
# ----------------------------------------------------------------------------------------------------------------------
def ledger_row(batch, sha):
    rec = batch_records(batch)
    n = len(rec)
    row = {"batch_id": batch["id"], "forecast_date": batch["forecast_date"], "made_at_utc": batch["made_at_utc"],
           "snapshot_date": batch["snapshot"]["date"], "model_version": batch["model"]["version"],
           "model_sha256": batch["model"]["sha256"], "n_forecasts": n}
    counts = batch.get("counts_by_sector") or sector_counts(rec)
    for s, col in SECTOR_COLS.items():
        row[col] = int(counts.get(s, 0))
    row["n_fresh"] = int(rec["fresh"].astype(bool).sum()) if "fresh" in rec.columns else ""
    row["mean_forecast"] = f"{rec['p_single_offer'].mean():.3f}" if n >= MIN_CELL else ""
    row["batch_sha256"] = sha
    return row


def _append(path, text, header=None):
    new = not os.path.isfile(path)
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        if new and header:
            fh.write(header)
        fh.write(text)


def complete_public_records(daily_dir, batch, sha):
    """append whatever public line of this batch is missing (seen block, ledger row, fingerprint). Returns what was added.
    All lines are prepared first and then appended one right after the other, the fingerprint (the seal) last, so a line
    in SHA256SUMS.txt means the other two files have theirs."""
    os.makedirs(daily_dir, exist_ok=True)
    bid, have = batch["id"], public_records_of(daily_dir, batch["id"])
    hashes = sorted({short_hash(x) for x in batch_records(batch)["sol_norm"]})
    seen_header = ("# Solicitations already forecast by the daily job: first 16 hex characters of SHA-256 of the normalized\n"
                   "# solicitation number (upper case, letters and digits only). NOT anonymous: anyone can hash a number from\n"
                   "# SAM.gov and look it up here. It shows only that a solicitation was forecast, never the forecast.\n")
    row = ledger_row(batch, sha)
    todo = [(SEEN_NAME, f"# batch {bid} ({len(hashes)})\n" + "".join(h + "\n" for h in hashes), seen_header),
            (LEDGER_NAME, ",".join(str(row[c]) for c in LEDGER_COLS) + "\n", ",".join(LEDGER_COLS) + "\n"),
            (SUMS_NAME, f"{sha}  {bid}.json  made_at={batch['made_at_utc']}\n", None)]
    added = []
    for name, text, header in todo:
        if not have[name]:
            _append(os.path.join(daily_dir, name), text, header=header)
            added.append(name)
    return added


# ----------------------------------------------------------------------------------------------------------------------
# GitHub draft releases (gh)
# ----------------------------------------------------------------------------------------------------------------------
def _gh(args):
    return subprocess.run(["gh", *args], capture_output=True, text=True)


def need_gh(what):
    if shutil.which("gh") is None:
        raise SystemExit(f"{what} needs the GitHub CLI (gh) on PATH and GH_TOKEN")


def draft_exists(tag):
    """True if a DRAFT release with this tag exists (listed explicitly: a draft has no git tag yet, see script 14)."""
    lst = _gh(["release", "list", "--limit", "200", "--json", "tagName,isDraft"])
    if lst.returncode != 0:
        raise RuntimeError("gh release list failed: " + lst.stderr.strip())
    return any(r["tagName"] == tag and r["isDraft"] for r in json.loads(lst.stdout or "[]"))


def ensure_draft(tag, title, notes):
    if not draft_exists(tag):
        say(f"creating DRAFT release {tag}")
        c = _gh(["release", "create", tag, "--draft", "--title", title, "--notes", notes])
        if c.returncode != 0:
            raise RuntimeError("gh release create failed: " + c.stderr.strip())


def release_assets(tag):
    v = _gh(["release", "view", tag, "--json", "assets"])
    if v.returncode != 0:
        raise RuntimeError("gh release view failed: " + v.stderr.strip())
    return {a["name"] for a in json.loads(v.stdout or "{}").get("assets", [])}


def batch_tag(day):
    return f"forecast-batches-{pd.Timestamp(day):%Y-%m}"


def upload_batch(path, day):
    """upload to the DRAFT release forecast-batches-<YYYY-MM>; never overwrite an asset that is already there."""
    need_gh("--upload")
    tag = batch_tag(day)
    ensure_draft(tag, f"Daily forecast batches {pd.Timestamp(day):%Y-%m}",
                 "Sealed daily single-offer forecast batches. Private (they name solicitations and buying offices); their "
                 "SHA-256 fingerprints are public in forecasts/daily/SHA256SUMS.txt. Draft: not public.")
    name = os.path.basename(path)
    if name in release_assets(tag):
        say(f"  {name} is already in {tag}; not uploaded again (assets are never overwritten)")
        return
    u = _gh(["release", "upload", tag, path])                 # no --clobber: a sealed batch is never replaced
    if u.returncode != 0:
        raise RuntimeError("gh release upload failed: " + u.stderr.strip())
    say(f"  uploaded {name} to the draft release {tag}")


def fetch_existing_batch(bid, day, batch_dir):
    """if a crashed run already uploaded today's batch, download it (so it is resealed, not replaced). True if fetched."""
    need_gh("--upload")
    tag = batch_tag(day)
    if not draft_exists(tag) or bid + ".json" not in release_assets(tag):
        return False
    os.makedirs(batch_dir, exist_ok=True)
    d = _gh(["release", "download", tag, "--pattern", bid + ".json", "--dir", batch_dir])
    if d.returncode != 0:
        raise RuntimeError("gh release download failed: " + d.stderr.strip())
    say(f"  found today's batch {bid}.json in {tag} (left by an earlier run); it is used as it is")
    return True


def fetch_model(path):
    """download model_v2.json from the DRAFT release model-v2 into the folder of `path`."""
    need_gh("--fetch-model")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    d = _gh(["release", "download", MODEL_TAG, "--pattern", MODEL_ASSET, "--dir", os.path.dirname(os.path.abspath(path))])
    if d.returncode != 0:
        raise RuntimeError(f"could not download {MODEL_ASSET} from the draft release {MODEL_TAG}: " + d.stderr.strip())
    got = os.path.join(os.path.dirname(os.path.abspath(path)), MODEL_ASSET)
    if os.path.abspath(got) != os.path.abspath(path):
        os.replace(got, path)
    say(f"downloaded the model file from the draft release {MODEL_TAG}")


# ----------------------------------------------------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------------------------------------------------
def parse_args(argv):
    ap = argparse.ArgumentParser(prog="python -I scripts/31_daily_forecast.py", description="Forecast and seal today's new open solicitations.")
    ap.add_argument("--dry-run", action="store_true", help="write nothing public (the batch goes to a private dryrun folder)")
    ap.add_argument("--upload", action="store_true", help="upload the batch to a DRAFT GitHub release (needs gh)")
    ap.add_argument("--fetch-model", action="store_true", help="download the model file from the draft release model-v2 if missing")
    ap.add_argument("--model", help="model file (default <DATA>/models/model_v2.json)")
    ap.add_argument("--allow-stale", action="store_true", help="forecast even if the model's award history is stale")
    return ap.parse_args(argv)


def summary_lines(rec, funnel, detail=False):
    """what is printed.  The robot's log is public, so a sealed run prints only what the ledger publishes (counts, and the
    mean when there are at least MIN_CELL forecasts).  detail=True (dry run) adds the spread, also only from MIN_CELL up."""
    L = ["Selection funnel:"] + [f"  {v:7,d}  {k.replace('_', ' ')}" for k, v in funnel.items()]
    if len(rec):
        L += ["Forecasts by sector:"] + [f"  {v:7,d}  {s}" for s, v in sector_counts(rec).items()]
        L += [f"Fresh (first posted at most {FRESH_DAYS} days ago): {int(rec['fresh'].astype(bool).sum()):,}; backlog: "
              f"{int((~rec['fresh'].astype(bool)).sum()):,}"]
    if len(rec) < MIN_CELL:
        if len(rec):
            L += [f"Fewer than {MIN_CELL} forecasts: no summary of them is printed (it would show single forecasts)."]
        return L
    if not detail:
        L += [f"Mean forecast: {rec['p_single_offer'].mean():.3f}"]
    else:
        q = np.percentile(rec["p_single_offer"], [10, 25, 50, 75, 90])
        L += [f"Forecast distribution: mean {rec['p_single_offer'].mean():.3f}; 10/25/50/75/90th percentiles "
              + " / ".join(f"{x:.3f}" for x in q)]
        bands = pd.cut(rec["p_single_offer"], [0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.7, 1.0], include_lowest=True).value_counts(sort=False)
        L += ["  by band: " + ", ".join(f"{str(k)}: {v:,}" for k, v in bands.items())]
        for c in ("p_sector_rate", "p_agency_rate", "p_office_history"):
            L += [f"  baseline {c}: mean {rec[c].mean():.3f}"]
    return L


def run(argv=None, paths=None, now=None):
    a = parse_args(sys.argv[1:] if argv is None else argv)
    P = dict(default_paths(), **(paths or {}))
    if a.model:
        P["model_path"] = a.model
    now = pd.Timestamp(now or datetime.now(timezone.utc))
    now = now.tz_localize("UTC") if now.tzinfo is None else now.tz_convert("UTC")
    now = now.floor("s")
    today = now.tz_localize(None).normalize()
    bid = batch_id_for(today)
    path = os.path.join(P["batch_dir"], bid + ".json")

    # ---- already sealed today?  (idempotent rerun; never a second batch, never a changed one)
    if not a.dry_run:
        if os.environ.get("GITHUB_ACTIONS") == "true" and not a.upload:
            say("REFUSED: on GitHub Actions a batch must be kept with --upload. The runner's disk is deleted after the run, and a "
                "sealed batch that is lost can never be scored. Nothing was forecast.")
            return EXIT_REFUSED
        have = public_records_of(P["daily_dir"], bid)
        if not os.path.isfile(path) and a.upload and not all(have.values()):
            try:
                fetch_existing_batch(bid, today, P["batch_dir"])
            except RuntimeError as e:
                say(f"ERROR: {e}")
                return EXIT_GH
        if os.path.isfile(path):
            sha = sha256_file(path)
            public = {read_sums(P["daily_dir"]).get(bid + ".json"), ledger_shas(P["daily_dir"]).get(bid)} - {None}
            if public and public != {sha}:
                say(f"REFUSED: {bid}.json does not match its public fingerprint in forecasts/daily/. Nothing was changed.\n"
                    f"  public: {' / '.join(sorted(public))}\n  file:   {sha}")
                return EXIT_REFUSED
            with open(path, "rb") as fh:
                batch = json.loads(fh.read().decode("utf-8"))
            if a.upload:                                       # first keep the file safe, then seal it in public
                try:
                    upload_batch(path, today)
                except RuntimeError as e:
                    say(f"ERROR: {e}\nThe public records were NOT completed; rerun the same command today to finish.")
                    return EXIT_GH
            added = complete_public_records(P["daily_dir"], batch, sha)
            say(f"Today's batch {bid} already exists ({batch['n_forecasts']} forecasts); it was not changed. "
                + (f"Completed the public records: {', '.join(added)}." if added else "Its public records are complete."))
            return 0
        if all(have.values()):
            say(f"Today's batch {bid} is already sealed in forecasts/daily/ (made by an earlier run). Nothing to do.")
            return 0
        if any(have.values()):
            say(f"REFUSED: today's batch {bid} is only partly recorded in forecasts/daily/ (it is in "
                f"{', '.join(k for k, v in have.items() if v)}; not in {', '.join(k for k, v in have.items() if not v)}), and the "
                f"batch file is not here: {os.path.normpath(path)}\n  A new batch for the same day would break the seal. Put the "
                "file back (or run with --upload to fetch it from the draft release) and run again. Nothing was changed.")
            return EXIT_REFUSED

    # ---- inputs
    snap_path, snap_date = newest_snapshot(P["snap_dir"])
    if snap_path is None:
        say(f"No snapshot found in {P['snap_dir']}. Run 14_sam_daily_snapshot.py first (python run.py sam-snapshot).")
        return EXIT_USAGE
    age = (today - pd.Timestamp(snap_date)).days
    if age > MAX_SNAPSHOT_AGE_DAYS:
        say(f"The newest snapshot is from {snap_date}, {age} days old (at most {MAX_SNAPSHOT_AGE_DAYS} allowed). "
            "Run 14_sam_daily_snapshot.py first. Nothing was forecast.")
        return EXIT_USAGE
    first_day = store_first_day(snap_path)
    if first_day is not None and not a.dry_run:
        span = (pd.Timestamp(snap_date) - first_day).days
        if span < MIN_STORE_DAYS:
            ready = (first_day + pd.Timedelta(days=MIN_STORE_DAYS)).date()
            say(f"WAITING: the SAM.gov notices pulled from the API begin on {first_day.date()}, {span} day{'' if span == 1 else 's'} "
                f"before {snap_date}; "
                f"{MIN_STORE_DAYS} days are needed, so the first batch can be sealed from {ready}. With fewer, an amendment of a "
                "solicitation posted before the first day looks like a new solicitation, with the wrong first posting date. "
                "Nothing was forecast today (dry runs are allowed).")
            return 0
    if not os.path.isfile(P["model_path"]):
        if a.fetch_model:
            try:
                fetch_model(P["model_path"])
            except RuntimeError as e:
                say(f"ERROR: {e}")
                return EXIT_GH
        else:
            say(f"The model file is missing: {P['model_path']}\n  Train it (python run.py model) or download it with --fetch-model.")
            return EXIT_USAGE
    model = fl.load_model(P["model_path"])
    model_sha = sha256_file(P["model_path"])
    newest = model_newest_award(model)
    limit = today - pd.DateOffset(months=STALE_MONTHS)
    stale = pd.isna(newest) or newest < limit
    if stale and not a.allow_stale:
        say(f"REFUSED: the model is stale. Its newest award history ends {newest.date() if not pd.isna(newest) else 'nowhere'}, "
            f"more than {STALE_MONTHS} months before today ({today.date()}). Retrain it (python run.py model) and run again, "
            "or pass --allow-stale for a test. Nothing was forecast.")
        return EXIT_REFUSED
    if stale:
        say(f"WARNING: stale model (award history ends {newest.date() if not pd.isna(newest) else 'nowhere'}); "
            "forecasting anyway because of --allow-stale.")

    seen, _ = read_seen(P["daily_dir"])
    snap = pd.read_parquet(snap_path, columns=SNAP_COLS)
    sols, funnel = select_solicitations(snap, now, seen)
    del snap
    say(f"Snapshot {snap_date}: {funnel['not_forecast_before']:,} new solicitations to forecast "
        f"({len(seen):,} forecast before).")
    rec = forecast_rows(model, sols, today.strftime("%Y-%m-%d"), now) if len(sols) else pd.DataFrame(columns=FIELDS)
    for line in summary_lines(rec, funnel, detail=a.dry_run):
        say(line)
    if not len(rec):
        say("Nothing new to forecast today. Nothing was written.")
        return 0

    batch = build_batch(bid, now, snap_date, os.path.basename(snap_path), model, model_sha, os.path.basename(P["model_path"]),
                        newest, stale, rec, funnel)
    data = batch_bytes(batch)
    if a.dry_run:
        os.makedirs(P["dryrun_dir"], exist_ok=True)
        dpath = os.path.join(P["dryrun_dir"], f"{bid}-DRYRUN.json")
        with open(dpath, "wb") as fh:
            fh.write(data)
        say(f"DRY RUN: nothing public was written. The would-be batch (private, never sealed): {os.path.normpath(dpath)}")
        return 0

    try:
        write_once(path, data)
    except FileExistsError:
        say("REFUSED: today's batch file appeared while this run was working (another run at the same time?). It was not "
            "changed. Run the same command again to finish its public records.")
        return EXIT_REFUSED
    sha = hashlib.sha256(data).hexdigest()
    if sha256_file(path) != sha:
        say("ERROR: the batch file on disk does not match what was written. Nothing public was written.")
        return EXIT_REFUSED
    say(f"Batch written (PRIVATE, never commit it): {os.path.normpath(path)}\n  SHA-256 {sha}")
    if a.upload:
        try:
            upload_batch(path, today)
        except RuntimeError as e:
            say(f"ERROR: {e}\nThe public records were NOT written, so nothing was sealed. Rerun the same command today: on this "
                "computer the batch is kept and is sealed as it is; on a fresh GitHub runner it is gone and a new one is made.")
            return EXIT_GH
    added = complete_public_records(P["daily_dir"], batch, sha)
    say(f"Public records appended in {os.path.normpath(P['daily_dir'])}: {', '.join(added)}. Commit them today.")
    return 0


def main(argv=None):
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(errors="replace")
    return run(argv)


if __name__ == "__main__":
    sys.exit(main())
