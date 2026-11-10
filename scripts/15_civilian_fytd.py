"""
15_civilian_fytd.py - the LIVE single-offer table: every civilian agency, this fiscal year so far (refreshed daily).

What it does
  1. Downloads this fiscal year's contract transactions for every civilian agency, with the same request as
     01c_download_civilian.py (36 columns; purchase orders, definitive contracts and contract vehicles), one request per
     quarter (split into months if a quarter is too big).  Every run downloads the whole year so far again, because
     agencies keep adding and correcting reports for weeks.  Every file goes through the shared verified download
     (01_download_usaspending.fetch_verified): its rows must match USAspending's own count for the same filters and dates; a
     cut-off file is replaced by smaller windows, and if that still fails the script stops and writes nothing.  If only the
     count service cannot be reached, the table is still written but marked "verified": false in the totals file (a live table
     one day old is useful; a wrong one is not, so only the missing check is tolerated, never a detected cut-off).
  2. Builds one row per award with the same rules as 02_build_awards.py and keeps the awards whose base action
     (modification 0) falls in the fiscal year.
  3. Writes the single-offer share by agency, with 95% Wilson intervals, and the totals.

Civilian award reports are due within 3 business days and USAspending loads them on business days, so the newest days
are always incomplete.  Department of Defense data are published 90 days late, so DoD stays out, as everywhere else.

In the first days of a fiscal year (October 1, or before USAspending has loaded any award of the year) there is nothing to
compute yet: the script says so, leaves the previous live files alone and ends without an error.

Usage:  python -I scripts/15_civilian_fytd.py [FY]          (default: the current fiscal year)
Output: results/live_fy<FY>_by_agency.csv, results/live_fy<FY>_totals.json
"""
import glob
import importlib.util
import json
import os
import shutil
import sys
import zipfile
from datetime import date, datetime, timedelta, timezone

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from atlas_lib import share_table  # noqa: E402
from common import DATA, RESULTS, UA_USASPENDING, http_json  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(DATA, "usaspending_live")
# written into OUT only after every window was downloaded: which files belong to this download and whether they were verified.
# 32_score_daily.py reads the files in OUT and refuses to score when this record is missing (a run that stopped half-way leaves
# some files and no record) or says verified: false.
CHECK_NAME = "download_check.json"


def load(name, filename):
    """import a sibling script whose file name starts with a digit"""
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


civ = load("dl01c", "01c_download_civilian.py")
b02 = load("build02", "02_build_awards.py")
FULL = {v: k for k, v in b02.SHORT.items()}           # "VA" -> "Department of Veterans Affairs"


def quarters(fy, last):
    """(label, start, end) for each quarter of fiscal year `fy` that has started by `last`, the end cut at `last`"""
    q = [(date(fy - 1, 10, 1), date(fy - 1, 12, 31)), (date(fy, 1, 1), date(fy, 3, 31)),
         (date(fy, 4, 1), date(fy, 6, 30)), (date(fy, 7, 1), date(fy, 9, 30))]
    return [(f"FY{fy}Q{k}", s.isoformat(), min(e, last).isoformat()) for k, (s, e) in enumerate(q, 1) if s <= last]


def has_transactions(pattern):
    """True when at least one ZIP matching `pattern` holds at least one transaction row (reads one row per file)."""
    for path in glob.glob(pattern):
        z = zipfile.ZipFile(path)
        for name in z.namelist():
            if "Transactions" in name and name.endswith(".csv") and len(pd.read_csv(z.open(name), dtype=str, nrows=1)):
                return True
    return False


def nothing_yet(fy, why):
    print(f"FY{fy}: {why}. The previous live files are left as they are.", flush=True)


def main(today=None, argv=None):
    today = today or datetime.now(timezone.utc).date()
    argv = sys.argv[1:] if argv is None else argv
    fy = int(argv[0]) if argv else today.year + (today.month >= 10)
    last = min(date(fy, 9, 30), today - timedelta(days=1))
    windows = quarters(fy, last)
    if not windows:                                   # October 1: the year has no finished day yet
        return nothing_yet(fy, f"no day of the fiscal year has ended yet (it starts {fy - 1}-10-01), so there is nothing to download")
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)                            # always a fresh download (late reports change earlier weeks too)
    os.makedirs(OUT)
    agencies = civ.civilian_agencies(OUT)
    print(len(agencies), "civilian top-tier agencies", flush=True)
    failed = []
    for label, start, end in windows:
        failed += civ.fetch_window(label, start, end, agencies, out=OUT, force=True, allow_unverified=True)
    if failed:
        sys.exit(f"download failed or did not match USAspending's own count for {failed}; nothing written")
    check, leaves = civ.dl.verification(OUT, [w[0] for w in windows])
    if not check["verified"]:
        print("WARNING: the download is NOT VERIFIED against USAspending's count (" + "; ".join(check["problems"]) + "). "
              "The live table is written but marked verified: false.", flush=True)
    with open(os.path.join(OUT, CHECK_NAME), "w", encoding="utf-8") as f:
        json.dump({"fiscal_year": fy, "data_to": last.isoformat(), "windows": [w[0] for w in windows], "verified": check["verified"],
                   "files": sorted(os.path.basename(lf["zip"]) for lf in leaves), "problems": check["problems"]}, f, indent=1)
    if not has_transactions(os.path.join(OUT, "*.zip")):
        return nothing_yet(fy, "USAspending has no award records for this fiscal year yet")

    base = b02.build(os.path.join(OUT, "*.zip"), b02.CIVILIAN_COLS, window_end=last.isoformat())
    base = base[base.fy == fy].copy()
    base["agency"] = base["agency"].astype(str).map(lambda a: FULL.get(a, a))
    abbr = {a["name"]: a.get("abbreviation") or "" for a in json.load(open(os.path.join(OUT, "agencies.json"), encoding="utf-8"))}
    ev = base[base.eval_set]
    if ev.empty or (ev.agency == "General Services Administration").all():
        return nothing_yet(fy, f"{len(base):,} awards loaded so far, too few to compute shares (none competed with a usable offer count"
                               " outside GSA)")
    cov = base.groupby("agency").agg(n_base=("award_kind", "size"), n_competed=("competed", "sum"))
    t = share_table(ev, "agency").merge(cov, left_on="agency", right_index=True)
    t.insert(1, "abbreviation", t.agency.map(abbr).fillna(""))
    t = t.sort_values("n_eval", ascending=False)
    t.to_csv(os.path.join(RESULTS, f"live_fy{fy}_by_agency.csv"), index=False)

    tot = share_table(ev.assign(scope="all"), "scope").iloc[0]
    # GSA's purchase orders are mostly very small supply orders that outnumber everything else,
    # so the totals are also given without GSA
    ex = share_table(ev[ev.agency != "General Services Administration"].assign(scope="x"), "scope").iloc[0]
    loaded = (http_json(f"{civ.dl.API}/awards/last_updated/", ua=UA_USASPENDING) or {}).get("last_updated")
    out = {"fiscal_year": fy, "as_of": today.isoformat(), "data_from": f"{fy - 1}-10-01", "data_to": last.isoformat(),
           "usaspending_last_updated": datetime.strptime(loaded, "%m/%d/%Y").date().isoformat() if loaded else None,
           "n_agencies": int(len(t)), "n_base_awards": int(len(base)), "k_single": int(tot.k_single), "n_eval": int(tot.n_eval),
           "share": float(tot.share), "ci": [float(tot.ci_lo), float(tot.ci_hi)],
           "excluding_gsa": {"k_single": int(ex.k_single), "n_eval": int(ex.n_eval), "share": float(ex.share),
                             "ci": [float(ex.ci_lo), float(ex.ci_hi)]},
           "verified": check["verified"],
           "download_verification": {k: check[k] for k in ("rule", "expected_rows", "received_rows", "problems")}}
    json.dump(out, open(os.path.join(RESULTS, f"live_fy{fy}_totals.json"), "w", encoding="utf-8"), indent=1)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
