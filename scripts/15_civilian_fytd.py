"""
15_civilian_fytd.py - the LIVE single-offer table: every civilian agency, this fiscal year so far (refreshed daily).

What it does
  1. Downloads this fiscal year's contract transactions for every civilian agency, with the same request as
     01c_download_civilian.py (36 columns; purchase orders, definitive contracts and contract vehicles), one request per
     quarter (split into months if a quarter is too big).  Every run downloads the whole year so far again, because
     agencies keep adding and correcting reports for weeks.
  2. Builds one row per award with the same rules as 02_build_awards.py and keeps the awards whose base action
     (modification 0) falls in the fiscal year.
  3. Writes the single-offer share by agency, with 95% Wilson intervals, and the totals.

Civilian award reports are due within 3 business days and USAspending loads them on business days, so the newest days
are always incomplete.  Department of Defense data are published 90 days late, so DoD stays out, as everywhere else.

Usage:  python -I scripts/15_civilian_fytd.py [FY]          (default: the current fiscal year)
Output: results/live_fy<FY>_by_agency.csv, results/live_fy<FY>_totals.json
"""
import importlib.util
import json
import os
import shutil
import sys
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from atlas_lib import share_table  # noqa: E402
from common import DATA, RESULTS, UA_USASPENDING, http_json  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(DATA, "usaspending_live")


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


def main():
    today = datetime.now(timezone.utc).date()
    fy = int(sys.argv[1]) if len(sys.argv) > 1 else today.year + (today.month >= 10)
    last = min(date(fy, 9, 30), today - timedelta(days=1))
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)                            # always a fresh download (late reports change earlier weeks too)
    os.makedirs(OUT)
    agencies = civ.civilian_agencies(OUT)
    print(len(agencies), "civilian top-tier agencies", flush=True)
    failed = []
    for label, start, end in quarters(fy, last):
        failed += civ.fetch_window(label, start, end, agencies, out=OUT, force=True)
    if failed:
        sys.exit(f"download failed for {failed}; nothing written")

    base = b02.build(os.path.join(OUT, "*.zip"), b02.CIVILIAN_COLS, window_end=last.isoformat())
    base = base[base.fy == fy].copy()
    base["agency"] = base["agency"].astype(str).map(lambda a: FULL.get(a, a))
    abbr = {a["name"]: a.get("abbreviation") or "" for a in json.load(open(os.path.join(OUT, "agencies.json"), encoding="utf-8"))}
    ev = base[base.eval_set]
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
                             "ci": [float(ex.ci_lo), float(ex.ci_hi)]}}
    json.dump(out, open(os.path.join(RESULTS, f"live_fy{fy}_totals.json"), "w", encoding="utf-8"), indent=1)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
