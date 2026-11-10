"""
17_history_download.py - contract transactions for ONE fiscal year, for the long history (FY2008 onward), civilian agencies and/or
the Department of Defense.

Same request as 01c_download_civilian.py (the same 36 columns; purchase orders, definitive contracts and contract vehicles):
  civilian agencies (all top-tier agencies except DoD): one request per quarter
  Department of Defense: one request per month (it has many more records)
Every window must stay under the download service's limits (500,000 rows, and our 25 MB cap per file); a window that is too big is split in
half, and again, until it fits.  The script is RESUMABLE: finished windows are skipped, and windows that fail are asked again (three passes).
Defense data are published 90 days late, so the most recent Defense months are always incomplete.

This is meant to run on GitHub's computers (.github/workflows/history.yml, one job per year), but it works on any computer.

Usage:  python -I scripts/17_history_download.py FY [civilian|defense|all]        (default: all)
Output: data/history/FY<fy>/CIVILIAN__<window>.zip and DEFENSE__<window>.zip, manifest_*.json, agencies.json
"""
import importlib.util
import os
import sys
import time
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA  # noqa: E402

_spec = importlib.util.spec_from_file_location("dl01c", os.path.join(os.path.dirname(os.path.abspath(__file__)), "01c_download_civilian.py"))
civ = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(civ)
DOD = "Department of Defense"
# the 36 columns of 01c, plus the pre-2022 company identifier (DUNS), so awards to the same company can be linked across the years
COLUMNS = civ.dl.COLUMNS + ["recipient_duns"]
ROUNDS, RETRY_WAIT = 3, 120          # passes over the year's windows, and seconds between passes


def year_dir(fy):
    return os.path.join(DATA, "history", f"FY{fy}")


def halves(start, end):
    """split start..end into two halves; None if it is a single day"""
    a, b = date.fromisoformat(start), date.fromisoformat(end)
    if a >= b:
        return None
    mid = a + (b - a) // 2
    return (a.isoformat(), mid.isoformat()), ((mid + timedelta(days=1)).isoformat(), b.isoformat())


def fetch_split(label, start, end, agencies, out, prefix):
    """download start..end, halving the window until each piece fits; returns the labels that failed"""
    r = civ.fetch(label, start, end, agencies, out=out, prefix=prefix, columns=COLUMNS)
    if r == "done":
        return []
    if r != "split":
        return [label]
    h = halves(start, end)
    if h is None:
        return [label]
    return fetch_split(label + "a", *h[0], agencies, out, prefix) + fetch_split(label + "b", *h[1], agencies, out, prefix)


def windows(fy, monthly):
    """the quarters (or months) of fiscal year `fy`"""
    out, d, last = [], date(fy - 1, 10, 1), date(fy, 9, 30)
    step = 1 if monthly else 3
    k = 1
    while d <= last:
        m_end = d.month + step - 1
        y_end = d.year + (m_end - 1) // 12
        m_end = (m_end - 1) % 12 + 1
        e = date(y_end, m_end, 1) + timedelta(days=32)
        e = date(e.year, e.month, 1) - timedelta(days=1)
        out.append((f"FY{fy}{'M' if monthly else 'Q'}{k:02d}", d.isoformat(), e.isoformat()))
        d, k = e + timedelta(days=1), k + 1
    return out


def one_pass(fy, scope, out):
    """request every window of the year once (finished windows are skipped); returns the labels that failed"""
    failed = []
    if scope in ("all", "civilian"):
        agencies = civ.civilian_agencies(out)
        print(f"FY{fy}: {len(agencies)} civilian agencies, by quarter", flush=True)
        for label, s, e in windows(fy, monthly=False):
            failed += fetch_split(label, s, e, agencies, out, "CIVILIAN")
    if scope in ("all", "defense"):
        print(f"FY{fy}: Department of Defense, by month", flush=True)
        for label, s, e in windows(fy, monthly=True):
            if date.fromisoformat(s) > date.today():
                break
            failed += fetch_split(label, s, e, [DOD], out, "DEFENSE")
    return failed


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    fy = int(sys.argv[1])
    scope = sys.argv[2] if len(sys.argv) > 2 else "all"
    out = year_dir(fy)
    os.makedirs(out, exist_ok=True)
    failed = []
    for attempt in range(1, ROUNDS + 1):                    # the download service sometimes drops one window: ask again
        failed = one_pass(fy, scope, out)
        if not failed or attempt == ROUNDS:
            break
        print(f"round {attempt}: {failed} failed; asking again in {RETRY_WAIT} seconds (finished windows are skipped)", flush=True)
        time.sleep(RETRY_WAIT)
    print("FAILED (rerun to retry):", failed if failed else "none", flush=True)
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
