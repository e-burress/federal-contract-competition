"""
01c_download_civilian.py - contract TRANSACTIONS for EVERY civilian agency (everything except the Department of Defense),
FY2024-FY2026, for the civilian-wide single-offer atlas (03b_atlas_civilian.py).

Same request as 01_download_usaspending.py (the same 36 columns; purchase orders, definitive contracts and all IDV types),
but one request per quarter covering all civilian top-tier agencies at once, instead of one request per agency.
DoD is excluded on purpose: its public data are delayed by 90 days.

Safety checks (as in 01): a file must be <= 25 MB and below the 500,000-row service cap.  A quarter that breaks either
limit is split into its three months automatically.  The script is RESUMABLE: finished windows are skipped.
The agencies queried (name, abbreviation, toptier code) are saved in agencies.json next to the files.

Usage:  python -I scripts/01c_download_civilian.py [FY2026Q4 ...]      (default: all twelve quarters)
Output: data/usaspending_civilian/CIVILIAN__<window>.zip, manifest_<window>.json, agencies.json
"""
import calendar
import importlib.util
import json
import os
import sys
import time
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA, UA_USASPENDING, download_file, http_json  # noqa: E402

# reuse the column list, award types and quarter windows of the five-department downloader
# (its file name starts with a digit, so import it by path)
_spec = importlib.util.spec_from_file_location("dl01", os.path.join(os.path.dirname(os.path.abspath(__file__)), "01_download_usaspending.py"))
dl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dl)

OUT = os.path.join(DATA, "usaspending_civilian")
os.makedirs(OUT, exist_ok=True)


def civilian_agencies(out=OUT):
    d = http_json(f"{dl.API}/references/toptier_agencies/", ua=UA_USASPENDING)
    rows = [{"name": a["agency_name"], "abbreviation": a.get("abbreviation"), "toptier_code": a.get("toptier_code")}
            for a in d["results"] if a["agency_name"] != "Department of Defense"]
    json.dump(rows, open(os.path.join(out, "agencies.json"), "w", encoding="utf-8"), indent=1)
    return [r["name"] for r in rows]


def months(start, end):
    """the calendar-month windows inside start..end (YYYY-MM-DD), the last one cut at `end`"""
    out, d, last = [], date.fromisoformat(start), date.fromisoformat(end)
    while d <= last:
        month_end = date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])
        out.append((d.isoformat(), min(month_end, last).isoformat()))
        d = month_end + timedelta(days=1)
    return out


def fetch(label, start, end, agencies, out=OUT, force=False):
    """Download one window into `out`. Returns 'done', 'split' (too big: use smaller windows) or 'failed'.
    A window already downloaded is skipped unless force=True."""
    mpath = os.path.join(out, f"manifest_{label}.json")
    if not force and os.path.exists(mpath) and json.load(open(mpath, encoding="utf-8")).get("done"):
        return "done"
    body = {"filters": {"award_type_codes": dl.AWARD_TYPES,
                        "agencies": [{"type": "awarding", "tier": "toptier", "name": n} for n in agencies],
                        "time_period": [{"start_date": start, "end_date": end, "date_type": "action_date"}]},
            "columns": dl.COLUMNS, "file_format": "csv"}
    print(f"[{time.strftime('%H:%M:%S')}] {label} {start}..{end}", flush=True)
    resp = http_json(f"{dl.API}/download/transactions/", method="POST", body=body, ua=UA_USASPENDING)
    if not resp or "file_name" not in resp:
        print("  submit failed", resp, flush=True)
        return "failed"
    st, t0 = {}, time.time()
    while time.time() - t0 < 1800:
        st = http_json(f"{dl.API}/download/status?file_name={resp['file_name']}", ua=UA_USASPENDING) or {}
        if st.get("status") in ("finished", "failed"):
            break
        time.sleep(10)
    rec = {"window": label, "start": start, "end": end, "status": st.get("status"),
           "total_rows": st.get("total_rows"), "total_size_kb": st.get("total_size")}
    result = "failed"
    if st.get("status") == "finished":
        if float(st.get("total_size") or 0) > 25_000 or int(st.get("total_rows") or 0) >= 499_000:
            rec.update(split=True, note="above the 25 MB / 500,000-row limits: split into months")
            result = "split"
        else:
            dest = os.path.join(out, f"CIVILIAN__{label}.zip")
            n = download_file(resp["file_url"], dest, ua=UA_USASPENDING)
            rec.update(bytes=n, path=os.path.basename(dest), done=bool(n), downloaded_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
            result = "done" if n else "failed"
    json.dump(rec, open(mpath, "w", encoding="utf-8"), indent=1)
    print("  ", {k: rec.get(k) for k in ("status", "total_rows", "total_size_kb", "bytes", "note")}, flush=True)
    time.sleep(3)
    return result


def fetch_window(label, start, end, agencies, out=OUT, force=False):
    """Download one window, splitting it into months if it is too big. Returns the labels that failed."""
    mpath = os.path.join(out, f"manifest_{label}.json")
    known_split = not force and os.path.exists(mpath) and json.load(open(mpath, encoding="utf-8")).get("split")
    r = "split" if known_split else fetch(label, start, end, agencies, out, force)
    if r == "split":
        return [f"{label}_m{k}" for k, (ms, me) in enumerate(months(start, end), 1)
                if fetch(f"{label}_m{k}", ms, me, agencies, out, force) != "done"]
    return [] if r == "done" else [label]


def main():
    agencies = civilian_agencies()
    print(len(agencies), "civilian top-tier agencies", flush=True)
    only = [a for a in sys.argv[1:] if not a.startswith("--")]
    failed = []
    for label, start, end in [w for w in dl.WINDOWS if not only or w[0] in only]:
        failed += fetch_window(label, start, end, agencies)
    print("FAILED (rerun to retry):", failed if failed else "none", flush=True)


if __name__ == "__main__":
    main()
