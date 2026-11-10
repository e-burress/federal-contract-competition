"""
28_reconcile.py - a data-completeness check: compare OUR yearly totals with USAspending's own totals for the same filters.

For every fiscal year that has a table data/history/processed/award_year_FY<fy>.parquet, we compare
  transactions : ours = sum of n_tx (and "transactions" in history_FY<fy>.json)      theirs = the API's transaction count
  dollars      : ours = sum of obligation_sum                                         theirs = the API's obligated amount
for the same filters as the download (17_history_download.py, 01c_download_civilian.py, 01_download_usaspending.py):
  award types B, D and the IDV types; action date inside the fiscal year (1 Oct to 30 Sep); every awarding top-tier agency
  (the civilian request lists all agencies except the Department of Defense, the Defense request the rest).
Endpoints (public, no key, no account):
  POST /api/v2/search/spending_by_transaction_count/   contracts + idvs = number of transactions
  POST /api/v2/search/spending_over_time/ (group fiscal_year)   obligated amount by the fiscal year of the action date
One combined total per year (civilian and Defense together): the yearly tables hold awards, not agencies, and an award that was made in an earlier year
has no agency in that year's tables, so a civilian / Defense split of our totals would not be reliable.

Polite: four requests per year (the same two with and without the agency list), a pause between them, retries with back-off.  If the service is down the script says so, keeps
what it has, and exits with code 0 (it runs in a weekly robot job and must not break it).  It never changes our numbers.

--strict: exit with code 1 when a SETTLED year's like-for-like total (sector download_agencies: transactions or dollars) differs from
USAspending's by more than STRICT_PCT (0.5 percent), when a year could not be checked, or when the script could not finish (USAspending
could not be reached: then nothing is committed that week).  A year is settled when its last day (30 September) is more than
SETTLED_MONTHS (15) months before the day of the check.  Before that, USAspending's total keeps growing after our download by itself:
Defense data are published 90 days late, and agencies report late and correct for months.  So a year that is not settled fails only
when our transaction count is ABOVE USAspending's by more than STRICT_PCT (we should never hold more than the source); a lower count
is reported as expected growth since the download.  Recent years are protected by the record made at download time instead
(python run.py get-history --require-verified).  The weekly robot (.github/workflows/history-analysis.yml) runs both first, so it
never commits results built from incomplete yearly tables.  Without --strict the script always exits with code 0, as before.

Usage:  python -I scripts/28_reconcile.py [FY ...] [--strict]       (default: every year present)
Output: results/reconciliation.csv, results/reconciliation_summary.json
"""
import calendar
import csv
import glob
import json
import os
import re
import sys
import time
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

API = "https://api.usaspending.gov/api/v2"
AWARD_TYPES = ["B", "D", "IDV_A", "IDV_B", "IDV_B_A", "IDV_B_B", "IDV_B_C", "IDV_C", "IDV_D", "IDV_E"]   # as in 01_download_usaspending.py
ENDPOINT = "search/spending_by_transaction_count + search/spending_over_time"
COLUMNS = ["fy", "sector", "our_transactions", "api_transactions", "diff_transactions_pct", "our_obligations", "api_obligations",
           "diff_obligations_pct", "checked_utc", "endpoint"]
FLAG_PCT = 1.0
STRICT_PCT = 0.5             # --strict fails above this difference (a cut-off download makes years 3 to 31 percent short)
SETTLED_MONTHS = 15          # a year is held to STRICT_PCT only when it ended more than this many months before the check: Defense
                             # data come 90 days late, and late reports and corrections keep arriving for about a year
PAUSE = 3.0


def filters(fy, names=None):
    f = {"award_type_codes": AWARD_TYPES,
         "time_period": [{"start_date": f"{fy - 1}-10-01", "end_date": f"{fy}-09-30", "date_type": "action_date"}]}
    if names:
        f["agencies"] = [{"type": "awarding", "tier": "toptier", "name": n} for n in names]
    return f


def agency_names():
    """the awarding agencies our downloads ask for: every top-tier agency on USAspending's reference list (civilian ones in
    01c_download_civilian.py, plus the Department of Defense).  None when the service does not answer."""
    d = common.http_json(f"{API}/references/toptier_agencies/", timeout=120, retries=3, pause=5)
    if not d or "results" not in d:
        return None
    return [a["agency_name"] for a in d["results"]]


def fetch_api(fy, names=None):
    """USAspending's totals for fiscal year fy (for the agencies `names`, or for every agency when None):
    {"transactions": int, "obligations": float}, or None when the service does not answer."""
    f = filters(fy, names)
    c = common.http_json(f"{API}/search/spending_by_transaction_count/", method="POST", body={"filters": f}, timeout=120, retries=3, pause=5)
    if not c or "results" not in c:
        return None
    time.sleep(PAUSE)
    o = common.http_json(f"{API}/search/spending_over_time/", method="POST", body={"group": "fiscal_year", "filters": f, "subawards": False},
                         timeout=120, retries=3, pause=5)
    if not o or "results" not in o:
        return None
    amount = None
    for r in o["results"]:
        if str((r.get("time_period") or {}).get("fiscal_year")) == str(fy):
            amount = float(r.get("aggregated_amount") or 0)
    if amount is None:
        return None
    r = c["results"]
    return {"transactions": int(sum(r.get(k) or 0 for k in ("contracts", "idvs", "other", "grants", "loans", "direct_payments"))),
            "obligations": amount}


def pct_diff(ours, theirs):
    """(ours - theirs) / theirs in percent; None when theirs is missing or zero"""
    if ours is None or theirs is None or theirs == 0:
        return None
    return round((ours - theirs) / theirs * 100, 4)


def years_present(processed):
    ys = []
    for p in glob.glob(os.path.join(processed, "award_year_FY*.parquet")):
        m = re.search(r"award_year_FY(\d{4})\.parquet$", p)
        if m:
            ys.append(int(m.group(1)))
    return sorted(ys)


def our_totals(processed, fy):
    """our numbers for one year: transactions and dollars from award_year (two columns only), the json count, the table's file date"""
    import pandas as pd
    t = pd.read_parquet(os.path.join(processed, f"award_year_FY{fy}.parquet"), columns=["n_tx", "obligation_sum"])
    out = {"transactions": int(t["n_tx"].sum()), "obligations": float(t["obligation_sum"].sum()), "json_transactions": None, "downloaded": None}
    jp = os.path.join(processed, f"history_FY{fy}.json")
    if os.path.exists(jp):
        try:
            out["json_transactions"] = int(json.load(open(jp, encoding="utf-8")).get("transactions"))
        except Exception:
            pass
        out["downloaded"] = time.strftime("%Y-%m-%d", time.gmtime(os.path.getmtime(jp)))
    return out


def reconcile_year(fy, ours, theirs, now, sector="download_agencies"):
    """one csv row (a dict with COLUMNS); theirs may be None (service did not answer)"""
    th = theirs or {}
    return {"fy": fy, "sector": sector, "our_transactions": ours["transactions"], "api_transactions": th.get("transactions"),
            "diff_transactions_pct": pct_diff(ours["transactions"], th.get("transactions")),
            "our_obligations": round(ours["obligations"], 2), "api_obligations": None if th.get("obligations") is None else round(th["obligations"], 2),
            "diff_obligations_pct": pct_diff(ours["obligations"], th.get("obligations")), "checked_utc": now, "endpoint": ENDPOINT}


def worst_pct(r):
    return max(abs(r["diff_transactions_pct"] or 0), abs(r["diff_obligations_pct"] or 0))


def summarize(rows, details, errors, now):
    main_rows = [r for r in rows if r["sector"] == "download_agencies"]
    wide_rows = [r for r in rows if r["sector"] == "all_agencies"]
    checked = [r for r in main_rows if r["api_transactions"] is not None]
    wide = [r for r in wide_rows if r["api_transactions"] is not None]
    big = sorted(checked, key=lambda r: -worst_pct(r))[:5]
    flagged = [r["fy"] for r in checked if worst_pct(r) > FLAG_PCT]
    wide_worst = max([abs(r["diff_transactions_pct"] or 0) for r in wide] or [0])
    if not main_rows:
        verdict = "No yearly tables were found, so nothing was checked."
    elif not checked:
        verdict = "USAspending's API did not answer, so no year could be checked this time. Nothing was changed; try again later."
    else:
        worst = max(worst_pct(r) for r in checked)
        verdict = (f"{len(checked)} of {len(main_rows)} fiscal years were compared with USAspending's own totals for the agencies our downloads ask for. "
                   + (f"Every year is within {FLAG_PCT:.0f} percent (largest difference {worst:.2f} percent). " if not flagged else
                      f"Years above {FLAG_PCT:.0f} percent: {', '.join('FY' + str(y) for y in flagged)}; see the caveats. ")
                   + (f"Counting every awarding agency, including ones missing from USAspending's top-tier list, our transaction counts are up to "
                      f"{wide_worst:.2f} percent lower. " if wide else "")
                   + "Small differences are expected because USAspending changes its records every day after our download.")
    return {"checked_utc": now, "years_present": [r["fy"] for r in main_rows], "years_checked": [r["fy"] for r in checked],
            "years_not_answered": [r["fy"] for r in main_rows if r["api_transactions"] is None], "flag_threshold_pct": FLAG_PCT,
            "years_above_threshold": flagged,
            "largest_differences": [{k: r[k] for k in ("fy", "diff_transactions_pct", "diff_obligations_pct")} for r in big],
            "all_agencies_largest_transaction_gap_pct": wide_worst if wide else None,
            "all_agencies_by_year": {str(r["fy"]): {"diff_transactions_pct": r["diff_transactions_pct"], "diff_obligations_pct": r["diff_obligations_pct"]}
                                     for r in wide},
            "table_file_date_by_year": {str(fy): d.get("downloaded") for fy, d in details.items()},
            "json_transactions_equal_n_tx": {str(fy): (d.get("json_transactions") == d.get("transactions")) for fy, d in details.items()
                                             if d.get("json_transactions") is not None},
            "verdict": verdict, "errors": errors,
            "caveats": ["One combined total per year (civilian and Defense together): our yearly tables hold awards, not agencies, so they cannot be split reliably.",
                        "Sector download_agencies = the API filtered to the same top-tier agency list as our downloads (the like-for-like check). "
                        "Sector all_agencies = no agency filter; the gap there is transactions of awarding agencies that are not on USAspending's top-tier reference list, which our downloads never ask for.",
                        "USAspending updates daily and Defense data are published 90 days late; our files have the date shown in table_file_date_by_year.",
                        "Our tables drop repeated transaction keys within a file; the API count is of its own current records.",
                        "The API's dollars are the sum of obligations by the fiscal year of the action date, for the same award types."]}


def run(fetch=fetch_api, processed=None, results_dir=None, years=None, pause=PAUSE, sleep=time.sleep, now=None, names=agency_names):
    """fetch(fy, names) -> {"transactions", "obligations"} or None; names() -> the agency list or None"""
    processed = processed or os.path.join(common.DATA, "history", "processed")
    results_dir = results_dir or common.RESULTS
    now = now or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    present = years_present(processed)
    todo = [y for y in (years or present) if y in present]
    rows, details, errors, down, asked = [], {}, [], 0, 0
    agencies = None
    if todo:
        try:
            agencies = names()
        except Exception as e:
            errors.append(f"agency list: {e!r}")
        if not agencies:
            errors.append("the agency list was not available: only the all-agencies comparison was made")
    for fy in todo:
        ours = details[fy] = our_totals(processed, fy)
        for sector, scope in (("download_agencies", agencies), ("all_agencies", None)):
            theirs = None
            if sector == "download_agencies" and not agencies:
                pass
            elif down < 2:               # two answers missing in a row: the service is down, stop asking
                if asked:
                    sleep(pause)
                asked += 1
                try:
                    theirs = fetch(fy, scope)
                except Exception as e:
                    errors.append(f"FY{fy} {sector}: {e!r}")
                down = 0 if theirs else down + 1
            rows.append(reconcile_year(fy, ours, theirs, now, sector))
    if down >= 2:
        errors.append("the API did not answer twice in a row; the remaining years were not asked")
    os.makedirs(results_dir, exist_ok=True)
    with open(os.path.join(results_dir, "reconciliation.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    summary = summarize(rows, details, errors, now)
    with open(os.path.join(results_dir, "reconciliation_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=1)
    return rows, summary


def settled(fy, today):
    """True when fiscal year `fy` ended (30 September) more than SETTLED_MONTHS months before `today`"""
    m = 9 + SETTLED_MONTHS                          # counted from January of calendar year fy
    y, m = fy + (m - 1) // 12, (m - 1) % 12 + 1
    return today > date(y, m, min(30, calendar.monthrange(y, m)[1]))


def strict_problems(rows, limit=STRICT_PCT, today=None, notes=None):
    """the reasons a strict check fails: a settled year whose like-for-like total differs by more than `limit` percent, a recent
    year whose transaction count is ABOVE USAspending's by more than `limit` percent, or a year that was not checked (no answer).
    Empty when every year passes. A recent year below USAspending's total is not a failure: a line about it goes to `notes`."""
    today = today or date.today()
    main_rows = [r for r in rows if r["sector"] == "download_agencies"]
    if not main_rows:
        return ["no yearly tables were found"]
    out = []
    for r in main_rows:
        if r["api_transactions"] is None or r["api_obligations"] is None:
            out.append(f"FY{r['fy']}: not checked (USAspending did not answer)")
        elif settled(int(r["fy"]), today):
            if worst_pct(r) > limit:
                out.append(f"FY{r['fy']}: differs from USAspending's total by {worst_pct(r):.2f} percent "
                           f"(transactions {r['diff_transactions_pct']}%, dollars {r['diff_obligations_pct']}%)")
        elif (r["diff_transactions_pct"] or 0) > limit:
            out.append(f"FY{r['fy']} (recent): our tables hold {r['diff_transactions_pct']}% MORE transactions than USAspending counts")
        elif notes is not None and worst_pct(r) > limit:
            notes.append(f"FY{r['fy']} (ended less than {SETTLED_MONTHS} months ago): transactions {r['diff_transactions_pct']}%, "
                         f"dollars {r['diff_obligations_pct']}%: expected growth since the download, not a failure")
    return out


def main(argv=None, run_fn=None):
    argv = sys.argv[1:] if argv is None else argv
    strict = "--strict" in argv
    years = [int(a) for a in argv if a.isdigit()] or None
    try:
        rows, summary = (run_fn or run)(years=years)
    except Exception as e:                # never break the weekly job (unless --strict)
        print("reconcile: could not finish:", repr(e))
        if strict:
            print("STRICT CHECK FAILED: USAspending could not be reached (or the comparison could not finish), so the yearly tables "
                  "were not checked. Nothing is committed this week; the next run tries again.")
            return 1
        return 0
    print(summary["verdict"])
    for r in rows:
        print(f"  FY{r['fy']} {r['sector']}: transactions {r['our_transactions']:,} vs {r['api_transactions']} ({r['diff_transactions_pct']}%), "
              f"obligations {r['diff_obligations_pct']}%")
    if strict:
        notes = []
        problems = strict_problems(rows, notes=notes)
        for n in notes:
            print("  note: " + n)
        if problems:
            print(f"STRICT CHECK FAILED (limit {STRICT_PCT} percent):")
            for p in problems:
                print("  " + p)
            if any("did not answer" in p for p in problems):
                print("USAspending did not answer for some years, so they were not checked. Nothing is committed this week; "
                      "the next run tries again.")
            return 1
        print(f"Strict check passed: every settled year is within {STRICT_PCT} percent of USAspending's like-for-like total, and no "
              "recent year holds more transactions than USAspending counts.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
