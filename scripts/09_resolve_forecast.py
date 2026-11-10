"""
09_resolve_forecast.py - SCORE the pre-registered single-offer forecast (PIO-F1) once USAspending has posted the awards.

Run it with no AI help:   python run.py score-f1 [path to the private forecast file]

Safety first
  The forecast file is private (it names buying offices), but its SHA-256 fingerprint is public in forecasts/SHA256SUMS.txt.
  Before anything is scored, this script fingerprints the file and REFUSES to score it if the fingerprint does not match.

Resolution rule (written into the forecast file before any outcome existed):
  For each forecast solicitation number S:
    1. Normalise S (upper case, letters and digits only).
    2. Look at USAspending BASE ACTIONS (modification_number 0) of purchase orders, definitive contracts and IDV awards whose
       normalised solicitation_identifier equals S and whose base action date is on or after the forecast's made_at date.
    3. If several match, use the one with the EARLIEST action date (ties: larger base-and-all-options value).
    4. The solicitation is RESOLVED only if that award was competed (extent competed A, D, E, F or CDO) and
       number_of_offers_received is a number from 1 to 998.  outcome = 1 if offers == 1 else 0.
    5. Everything else is UNRESOLVED, with its reason (no award yet / not competed / offers blank or 999), and is not scored.
  Scores use resolved solicitations only: Brier score, log loss, ROC-AUC, a calibration table by forecast band, and the same
  numbers for the baseline stored in the forecast file.

What is written where
  results/resolution_<forecast id>_<date>.json   AGGREGATE numbers only (public). No solicitation numbers, no offices.
                       Written ONLY when at least MIN_PUBLIC (20) forecasts have resolved, and then with small cells hidden.
                       With fewer, the aggregate goes to <data folder>/private/ instead.
  <data folder>/private/resolution_<forecast id>_<date>.csv   one row per solicitation (PRIVATE: never commit or publish it).

Usage
    python -I scripts/09_resolve_forecast.py [forecast file] [--date YYYY-MM-DD] [--from-processed]
      no file given : looks for forecasts/<id>.json (id from config.toml [forecasts.F1]), then <data folder>/private/<id>.json
      --date        : the "as of" date (default: today, UTC); also the end of the USAspending download window
      --from-processed : dry run. Uses data/processed/base_awards.parquet instead of downloading, ignores the date rule, and writes
                         only to the private folder. It tests the code on old data; the numbers mean nothing.

Exit codes
    0 done    2 forecast file not found / bad command line    3 refused (fingerprint mismatch or unusable file)
    4 the USAspending download failed (nothing was written)
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
import zipfile
from datetime import datetime, timezone

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import CONFIG, DATA, FORECASTS, RESULTS  # noqa: E402

API = "https://api.usaspending.gov/api/v2"
SUMS_NAME = "SHA256SUMS.txt"
COMPETED = {"A", "D", "E", "F", "CDO"}
COLS = ["contract_award_unique_key", "award_id_piid", "modification_number", "action_date", "award_type_code", "award_or_idv_flag",
        "base_and_all_options_value", "awarding_agency_name", "awarding_office_code", "solicitation_identifier",
        "extent_competed_code", "number_of_offers_received"]
ITEM_COLS = ["solicitation_number", "agency", "p_single_offer", "p_baseline_agency"]     # p_office_history is optional
BANDS = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.7, 1.0]                                          # the 7 calibration bands
CLIP = 0.001                                                                               # log loss never sees exactly 0 or 1
BOOT_RESAMPLES, BOOT_SEED, BOOT_MIN_N = 2000, 20261024, 10
EXIT_USAGE, EXIT_REFUSED, EXIT_DATA = 2, 3, 4
# Publication minimum. With very few resolved forecasts an aggregate gives individual forecasts away (with 1 resolved forecast the
# Brier score is the square of its probability, and a calibration band holds just that one). So below MIN_PUBLIC resolved
# forecasts NOTHING goes to results/ (the aggregate goes to the private folder instead). At MIN_PUBLIC or more, the public file
# drops every calibration band, and merges every department row, with fewer than MIN_CELL resolved forecasts.
MIN_PUBLIC, MIN_CELL = 20, 5

# the reasons a forecast can stay unresolved (counts for every one of them are always reported, even when zero)
REASONS = {
    "no_matching_award_yet": "no matching award posted yet",
    "award_not_competed": "the matching award was not competed",
    "offers_blank_or_999": "the matching award has a blank, 0 or 999 offers count",
    "no_solicitation_number": "the forecast has no usable solicitation number",
}


class ScoreError(Exception):
    """A clear, expected problem. `code` becomes the exit code."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------------------------------------------------------------
# finding and checking the forecast file
# ---------------------------------------------------------------------------------------------------------------------
def norm(s):
    """Upper case, letters and digits only. Anything that is not text becomes empty."""
    if not isinstance(s, str):
        return ""
    return "".join(ch for ch in s.upper() if ch.isalnum())


def f1_id():
    return CONFIG.get("forecasts", {}).get("F1", {}).get("id", "")


def locate_forecast(arg, forecasts_dir=FORECASTS, private_dir=None, forecast_id=None):
    """Return the path of the forecast file, or None. An explicit path is used as given."""
    if arg:
        return arg if os.path.isfile(arg) else None
    forecast_id = forecast_id or f1_id()
    private_dir = private_dir or os.path.join(DATA, "private")
    for folder in (forecasts_dir, private_dir):
        cand = os.path.join(folder, forecast_id + ".json")
        if forecast_id and os.path.isfile(cand):
            return cand
    return None


def read_expected_hash(sums_path, filename):
    """Look up `filename` in a SHA256SUMS file. Lines look like: <hash>  <file name>  [extra notes]. Returns None if not listed."""
    if not os.path.isfile(sums_path):
        raise ScoreError(EXIT_REFUSED, f"Cannot check the fingerprint: {sums_path} is missing. Get it from the public repository.")
    with open(sums_path, encoding="utf-8") as fh:
        for line in fh:
            parts = line.split()
            if len(parts) >= 2 and parts[1].lstrip("*") == filename and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
                return parts[0].lower()
    return None


def load_checked_forecast(path, sums_path):
    """Fingerprint the file; refuse unless it matches the public fingerprint. Returns (forecast dict, sha256).
    The JSON is parsed from the very bytes that were fingerprinted."""
    with open(path, "rb") as fh:
        data = fh.read()
    actual = hashlib.sha256(data).hexdigest()
    name = os.path.basename(path)
    expected = read_expected_hash(sums_path, name)
    if expected is None:
        raise ScoreError(EXIT_REFUSED, f"REFUSED: '{name}' is not listed in {os.path.basename(sums_path)}, so it cannot be checked. "
                                       "Use the file under its original name.")
    if actual != expected:
        raise ScoreError(EXIT_REFUSED, "REFUSED: this file does not match the public fingerprint, so it is not the sealed forecast.\n"
                                       f"  public fingerprint : {expected}\n  this file's        : {actual}\n"
                                       "Nothing was scored. Get the original, unedited file.")
    try:
        fc = json.loads(data.decode("utf-8"))
        items = pd.DataFrame(fc["forecasts"])
        fc["id"], fc["made_at_utc"]
    except Exception as e:
        raise ScoreError(EXIT_REFUSED, f"REFUSED: the file matches its fingerprint but cannot be read as a forecast ({e!r}).")
    missing = [c for c in ITEM_COLS if c not in items.columns]
    if missing:
        raise ScoreError(EXIT_REFUSED, f"REFUSED: the forecast rows lack the columns {missing}.")
    if name != fc["id"] + ".json":
        raise ScoreError(EXIT_REFUSED, f"REFUSED: the file name '{name}' does not match the forecast id inside it.")
    return fc, actual


# ---------------------------------------------------------------------------------------------------------------------
# getting the award data
# ---------------------------------------------------------------------------------------------------------------------
def _fetch_window(ag, start, end, out_dir, depth=0):
    """One agency, one date window, through the shared verified download (01_download_usaspending.fetch_verified): the rows in the
    file must match USAspending's own count, and a window that is too big or cut off is split. Returns a list of DataFrames, or
    None if it failed or could not be verified (also when the count service does not answer: never score from unchecked data)."""
    import importlib.util
    import shutil
    spec = importlib.util.spec_from_file_location("dl01", os.path.join(os.path.dirname(os.path.abspath(__file__)), "01_download_usaspending.py"))
    DL = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(DL)
    d = os.path.join(out_dir, "f1_" + re.sub(r"\W+", "_", ag))
    shutil.rmtree(d, ignore_errors=True)                       # always fresh
    os.makedirs(d)
    failed = DL.fetch_verified("W", start, end, [ag], d, prefix="TX", columns=COLS, force=True)
    check, leaves = DL.verification(d, ["W"])
    if failed or not check["verified"]:
        print("  not verified:", "; ".join(check["problems"]) or failed, flush=True)
        return None
    frames = []
    for lf in leaves:
        with zipfile.ZipFile(lf["zip"]) as z:
            for n in z.namelist():
                if "Transactions" in n and n.endswith(".csv"):
                    frames.append(pd.read_csv(z.open(n), dtype=str, keep_default_na=False))
    if any(not set(COLS) <= set(f.columns) for f in frames):
        return None
    return frames


def fetch_recent_base_actions(agencies, start, end):
    """Download transaction rows for the given departments from USAspending (a few columns only).
    Returns (DataFrame, list of departments whose download failed)."""
    out_dir = os.path.join(DATA, "resolve_tmp")
    os.makedirs(out_dir, exist_ok=True)
    frames, failed = [], []
    for ag in agencies:
        print(f"  downloading {ag} ({start} to {end}) ...", flush=True)
        got = _fetch_window(ag, start, end, out_dir)
        if got is None:
            failed.append(ag)
            print(f"  FAILED: {ag}", flush=True)
        else:
            frames += got
        time.sleep(3)
    df = pd.concat(frames, ignore_index=True).drop_duplicates() if frames else pd.DataFrame(columns=COLS)
    return df, failed


def base_actions_from_tx(tx):
    """Keep BASE actions (modification number 0) that carry a solicitation number; add sol_norm, value, offers."""
    tx = tx.copy()
    tx["action_date"] = pd.to_datetime(tx["action_date"], errors="coerce")
    tx["value"] = pd.to_numeric(tx["base_and_all_options_value"], errors="coerce")
    tx["offers"] = pd.to_numeric(tx["number_of_offers_received"], errors="coerce")
    b = tx[tx.modification_number.isin(["0", "00", "000", ""])].copy()
    b["sol_norm"] = b.solicitation_identifier.map(norm)
    return b[b.sol_norm != ""]


def base_actions_from_processed():
    """Dry run input: the processed base-award table (old data)."""
    path = os.path.join(DATA, "processed", "base_awards.parquet")
    if not os.path.isfile(path):
        raise ScoreError(EXIT_USAGE, f"--from-processed needs {path}. Run `python run.py quick` first.")
    b = pd.read_parquet(path, columns=["award_id_piid", "action_date", "solicitation_identifier", "extent_competed_code", "offers", "value0"])
    b = b.assign(sol_norm=b.solicitation_identifier.map(norm), value=b.value0, extent_competed_code=b.extent_competed_code.astype(str))
    return b[b.sol_norm != ""]


# ---------------------------------------------------------------------------------------------------------------------
# the matching rule
# ---------------------------------------------------------------------------------------------------------------------
def resolve(items, base, made_at, ignore_date=False):
    """Apply the resolution rule. `items`: forecast rows. `base`: base actions with columns sol_norm, award_id_piid, action_date,
    value, offers, extent_competed_code. Returns one row per forecast with `status` and `outcome`."""
    items = items.copy()
    items["sol_norm"] = items["solicitation_number"].map(norm)
    b = base[base.sol_norm.isin(set(items.sol_norm) - {""})]
    if not ignore_date:
        b = b[b.action_date >= made_at]
    b = b.sort_values(["sol_norm", "action_date", "value"], ascending=[True, True, False]).drop_duplicates("sol_norm", keep="first")
    b = b[["sol_norm", "award_id_piid", "action_date", "extent_competed_code", "offers"]].assign(matched=True)
    m = items.merge(b, on="sol_norm", how="left")
    m["matched"] = m["matched"].notna()
    m["competed"] = m.extent_competed_code.fillna("").astype(str).str.strip().str.upper().isin(COMPETED)
    m["offers_ok"] = pd.to_numeric(m.offers, errors="coerce").between(1, 998)
    m["status"] = np.select(
        [m.sol_norm == "", ~m.matched, ~m.competed, ~m.offers_ok],
        ["no_solicitation_number", "no_matching_award_yet", "award_not_competed", "offers_blank_or_999"], default="resolved")
    m["outcome"] = np.where(m.status == "resolved", (pd.to_numeric(m.offers, errors="coerce") == 1).astype(float), np.nan)
    return m


# ---------------------------------------------------------------------------------------------------------------------
# scores
# ---------------------------------------------------------------------------------------------------------------------
def auc(y, p):
    """ROC-AUC by ranks (ties count half). None unless both outcomes occur."""
    y, p = np.asarray(y, float), np.asarray(p, float)
    n1, n0 = int((y == 1).sum()), int((y == 0).sum())
    if n1 == 0 or n0 == 0:
        return None
    ranks = pd.Series(p).rank(method="average").values
    return float((ranks[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def scores(y, p):
    """Brier score, log loss (probabilities clipped to 0.001..0.999) and AUC."""
    y, p = np.asarray(y, float), np.asarray(p, float)
    pc = np.clip(p, CLIP, 1 - CLIP)
    return {"brier": float(np.mean((p - y) ** 2)),
            "log_loss": float(-np.mean(y * np.log(pc) + (1 - y) * np.log(1 - pc))),
            "auc": auc(y, p)}


def calibration_table(y, p, bands=BANDS):
    """One row per forecast band: how many, the mean forecast, the observed single-offer share. Bands are (low, high]."""
    y, p = np.asarray(y, float), np.asarray(p, float)
    idx = np.clip(np.digitize(p, bands[1:-1], right=True), 0, len(bands) - 2)
    rows = []
    for i in range(len(bands) - 1):
        sel = idx == i
        n = int(sel.sum())
        rows.append({"band": f"{bands[i]:.1f} to {bands[i + 1]:.1f}", "n": n,
                     "mean_forecast": float(p[sel].mean()) if n else None, "observed_share": float(y[sel].mean()) if n else None})
    return rows


def bootstrap_intervals(y, p_model, p_base, resamples=BOOT_RESAMPLES, seed=BOOT_SEED):
    """95% bootstrap intervals for the two Brier scores and for the Brier skill versus the baseline."""
    y, pm, pb = (np.asarray(a, float) for a in (y, p_model, p_base))
    n = len(y)
    idx = np.random.default_rng(seed).integers(0, n, size=(resamples, n))
    em, eb = ((pm - y) ** 2)[idx].mean(axis=1), ((pb - y) ** 2)[idx].mean(axis=1)
    skill = 1 - em / np.where(eb > 0, eb, np.nan)

    def ci(a):
        a = a[~np.isnan(a)]
        return [float(np.percentile(a, 2.5)), float(np.percentile(a, 97.5))] if len(a) else None
    return {"resamples": resamples, "seed": seed, "model_brier_95": ci(em), "baseline_brier_95": ci(eb), "brier_skill_95": ci(skill)}


def stage_for(snap, fc):
    """provisional = before the first check date; final = on or after the forecast's resolution date; otherwise interim."""
    checks = sorted(CONFIG.get("forecasts", {}).get("F1", {}).get("checks", []))
    final = fc.get("resolution_date") or (checks[-1] if checks else None)
    if checks and snap < checks[0]:
        return "provisional"
    if final and snap >= final:
        return "final"
    return "interim"


def build_summary(fc, m, sha, snap, stage, dry_run=False):
    """The AGGREGATE result (public). Nothing in it identifies a solicitation or an office."""
    r = m[m.status == "resolved"]
    n, nr = int(len(m)), int(len(r))
    reasons = {k: int((m.status == k).sum()) for k in REASONS}
    out = {"forecast_id": fc["id"], "forecast_file_sha256": sha, "fingerprint_checked_against": f"forecasts/{SUMS_NAME}",
           "forecast_made_at_utc": fc["made_at_utc"], "snapshot_date": snap, "stage": stage, "provisional": stage == "provisional",
           "dry_run": dry_run,
           "data_source": "USAspending.gov base actions (processed table, old data)" if dry_run else "USAspending.gov base actions (live download)",
           "n_forecasts": n, "n_resolved": nr, "resolved_share": nr / n if n else None,
           "unresolved_reasons": reasons, "n_unresolved": n - nr}
    if dry_run:
        out["note"] = "Dry run on old data with the date rule switched off. It tests the code only; the numbers mean nothing."
    if nr:
        y = r.outcome.values
        out["n_single_offer_among_resolved"] = int(y.sum())
        out["observed_single_offer_share"] = float(y.mean())
        out["model"] = scores(y, r.p_single_offer.values)
        out["baseline_agency_rate"] = scores(y, r.p_baseline_agency.values)
        if "p_office_history" in r.columns and r.p_office_history.notna().all():
            out["baseline_office_history"] = scores(y, r.p_office_history.values)
        bm, bb = out["model"]["brier"], out["baseline_agency_rate"]["brier"]
        out["brier_skill_vs_baseline"] = 1 - bm / bb if bb > 0 else None
        out["calibration"] = calibration_table(y, r.p_single_offer.values)
        if nr >= BOOT_MIN_N:
            out["bootstrap"] = bootstrap_intervals(y, r.p_single_offer.values, r.p_baseline_agency.values)
        if nr < 30:
            out["scores_note"] = f"Only {nr} resolved forecast{'' if nr == 1 else 's'}: the scores are very noisy and prove nothing yet."
    out["by_department"] = {
        ag: {"n_forecasts": int(len(g)), "n_resolved": int((g.status == "resolved").sum()),
             "n_single_offer": int(g.outcome.fillna(0).sum()),
             "unresolved": {k: int((g.status == k).sum()) for k in REASONS}}
        for ag, g in m.groupby("agency", sort=True)}
    return out


# ---------------------------------------------------------------------------------------------------------------------
# output
# ---------------------------------------------------------------------------------------------------------------------
def _clean(o):
    """Make the summary strictly valid JSON (NaN becomes null, numpy numbers become plain numbers)."""
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return None if np.isnan(o) or np.isinf(o) else float(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    return o


def is_public(summary):
    """True only for a real (not dry-run) result with at least MIN_PUBLIC resolved forecasts."""
    return not summary["dry_run"] and summary["n_resolved"] >= MIN_PUBLIC


def public_view(summary):
    """The version of the aggregate that may be published (call only when is_public). Small cells are hidden:
    calibration bands with fewer than MIN_CELL forecasts lose their numbers (marked suppressed), and departments with fewer than
    MIN_CELL resolved forecasts are merged into one "(other departments)" row (its single-offer count is dropped if still small)."""
    out = dict(summary)
    if "calibration" in out:
        out["calibration"] = [r if r["n"] >= MIN_CELL else {"band": r["band"], "n": None, "mean_forecast": None, "observed_share": None,
                                                             "suppressed": r["n"] > 0} for r in out["calibration"]]
    big, other = {}, None
    for ag, d in out["by_department"].items():
        if d["n_resolved"] >= MIN_CELL:
            big[ag] = d
        elif other is None:
            other = {"n_forecasts": d["n_forecasts"], "n_resolved": d["n_resolved"], "n_single_offer": d["n_single_offer"],
                     "unresolved": dict(d["unresolved"])}
        else:
            for k in ("n_forecasts", "n_resolved", "n_single_offer"):
                other[k] += d[k]
            for k, v in d["unresolved"].items():
                other["unresolved"][k] += v
    if other is not None:
        if other["n_resolved"] < MIN_CELL:
            other["n_single_offer"] = None
        big["(other departments)"] = other
    out["by_department"] = big
    out["small_cell_rule"] = (f"Calibration bands and department rows with fewer than {MIN_CELL} resolved forecasts are hidden or merged "
                              "so that no single forecast can be read from this file.")
    return out


def write_outputs(summary, m, results_dir, private_dir):
    """Per-solicitation CSV -> private_dir. Aggregate JSON -> results_dir (public) only if is_public(summary) (then with small cells
    hidden); otherwise (dry run, or fewer than MIN_PUBLIC resolved) the full aggregate JSON goes to private_dir.
    Returns (json path, csv path)."""
    os.makedirs(private_dir, exist_ok=True)
    tag = f"{summary['forecast_id']}_" + ("DRYRUN" if summary["dry_run"] else summary["snapshot_date"])
    public = is_public(summary)
    jdir = results_dir if public else private_dir
    os.makedirs(jdir, exist_ok=True)
    jpath = os.path.join(jdir, f"resolution_{tag}.json")
    with open(jpath, "w", encoding="utf-8") as fh:
        json.dump(_clean(public_view(summary) if public else summary), fh, indent=1, allow_nan=False)
    cols = [c for c in ["solicitation_number", "agency", "p_single_offer", "p_baseline_agency", "p_office_history", "status",
                        "award_id_piid", "action_date", "extent_competed_code", "offers", "outcome"] if c in m.columns]
    cpath = os.path.join(private_dir, f"resolution_{tag}.csv")
    m[cols].to_csv(cpath, index=False)
    return jpath, cpath


def _f(x, nd=4):
    return "n/a" if x is None else f"{x:.{nd}f}"


def summary_text(s):
    """A short plain-English summary for the screen."""
    L = [""]
    if s["dry_run"]:
        L += ["DRY RUN on old data (tests the code only; the numbers mean nothing)."]
    L += [f"Forecast {s['forecast_id']}, as of {s['snapshot_date']} ({s['stage']} check)",
          "The file's fingerprint matches the public one.",
          f"{s['n_resolved']} of {s['n_forecasts']} forecasts have resolved so far.", "Why the rest have not:"]
    L += [f"  {s['unresolved_reasons'][k]:4d}  {REASONS[k]}" for k in REASONS if s["unresolved_reasons"][k]] or ["  none"]
    if s["n_resolved"]:
        L += [f"Of the resolved ones, {s['n_single_offer_among_resolved']} got a single offer ({s['observed_single_offer_share']:.1%}).",
              "Scores (lower Brier and log loss are better; AUC 0.5 is a coin flip):",
              f"  model     Brier {_f(s['model']['brier'])}   log loss {_f(s['model']['log_loss'])}   AUC {_f(s['model']['auc'], 3)}",
              f"  baseline  Brier {_f(s['baseline_agency_rate']['brier'])}   log loss {_f(s['baseline_agency_rate']['log_loss'])}   "
              f"AUC {_f(s['baseline_agency_rate']['auc'], 3)}   (department-wide rate)"]
        if "brier_skill_vs_baseline" in s:
            L += [f"  Brier skill versus the baseline: {_f(s['brier_skill_vs_baseline'], 3)} (above 0 means the model beat the baseline)"]
        if "bootstrap" in s and s["bootstrap"]["brier_skill_95"]:
            lo, hi = s["bootstrap"]["brier_skill_95"]
            L += [f"  95% interval for that skill: {lo:.3f} to {hi:.3f}"]
        if "scores_note" in s:
            L += [s["scores_note"]]
    else:
        L += ["No forecast has resolved yet, so there is nothing to score."]
    L += ["By department (resolved / forecasts):"]
    L += [f"  {ag}: {d['n_resolved']} / {d['n_forecasts']}" for ag, d in s["by_department"].items()]
    return L


# ---------------------------------------------------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------------------------------------------------
def parse_args(argv):
    ap = argparse.ArgumentParser(prog="python run.py score-f1", description="Score the sealed single-offer forecast PIO-F1.")
    ap.add_argument("forecast", nargs="?", help="path to the private forecast file (default: look in forecasts/ for the id in config.toml)")
    ap.add_argument("snapshot_date", nargs="?", help="same as --date")
    ap.add_argument("--date", help="as-of date YYYY-MM-DD (default: today, UTC)")
    ap.add_argument("--from-processed", action="store_true", help="dry run on data/processed/base_awards.parquet (no download)")
    a = ap.parse_args(argv)
    if a.forecast and re.fullmatch(r"\d{4}-\d{2}-\d{2}", a.forecast):          # old habit: date given without a file
        a.forecast, a.snapshot_date = None, a.forecast
    a.date = a.date or a.snapshot_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        datetime.strptime(a.date, "%Y-%m-%d")
    except ValueError:
        ap.error(f"'{a.date}' is not a date in the form YYYY-MM-DD")
    return a


def run(argv):
    a = parse_args(argv)
    path = locate_forecast(a.forecast)
    if path is None:
        if a.forecast:
            raise ScoreError(EXIT_USAGE, f"The forecast file '{a.forecast}' does not exist.")
        raise ScoreError(EXIT_USAGE, f"Could not find the private forecast file.\n  Put '{f1_id()}.json' in:\n    {FORECASTS}\n"
                                     f"  (or in {os.path.join(DATA, 'private')}), or give its path:  python run.py score-f1 <path to the file>")
    print(f"Forecast file: {os.path.basename(path)}")
    fc, sha = load_checked_forecast(path, os.path.join(FORECASTS, SUMS_NAME))
    print("Fingerprint check passed: the file is the sealed forecast.")
    items = pd.DataFrame(fc["forecasts"])
    made_at = pd.Timestamp(fc["made_at_utc"][:10])
    snap = a.date
    if a.from_processed:
        print("Dry run: using the processed table, no download.")
        base, ignore_date = base_actions_from_processed(), True
    else:
        agencies = sorted(items["agency"].dropna().unique())
        print(f"Downloading base actions from USAspending for {len(agencies)} departments. This can take several minutes.")
        tx, failed = fetch_recent_base_actions(agencies, (made_at - pd.Timedelta(days=7)).strftime("%Y-%m-%d"), snap)
        if failed:
            raise ScoreError(EXIT_DATA, "The USAspending download failed for: " + "; ".join(failed) +
                             ".\nNothing was scored or written, because missing data would look like 'no award yet'. Run the same command again.")
        base, ignore_date = base_actions_from_tx(tx), False
    m = resolve(items, base, made_at, ignore_date)
    summary = build_summary(fc, m, sha, snap, stage_for(snap, fc), dry_run=a.from_processed)
    jpath, cpath = write_outputs(summary, m, RESULTS, os.path.join(DATA, "private"))
    for line in summary_text(summary):
        print(line)
    print("")
    if is_public(summary):
        print(f"Aggregate results: {os.path.normpath(jpath)}  (public: commit this one; small cells are hidden)")
    elif a.from_processed:
        print(f"Aggregate results (dry run, private): {os.path.normpath(jpath)}")
    else:
        need = MIN_PUBLIC - summary["n_resolved"]
        print(f"NOT PUBLISHED: only {summary['n_resolved']} forecasts have resolved, and at least {MIN_PUBLIC} are needed before results "
              f"can be made public (fewer would reveal individual forecasts). {need} more must resolve. Nothing was written to results/.")
        print(f"Aggregate results (PRIVATE, do not commit): {os.path.normpath(jpath)}")
    print(f"Per-solicitation file (PRIVATE, never publish or commit): {os.path.normpath(cpath)}")
    return 0


def main(argv=None):
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(errors="replace")
    try:
        return run(sys.argv[1:] if argv is None else argv)
    except ScoreError as e:
        print(f"\n{e}", file=sys.stderr)
        return e.code


if __name__ == "__main__":
    sys.exit(main())
