"""
17_history_download.py - contract transactions for ONE fiscal year, for the long history (FY2008 onward), civilian agencies and/or
the Department of Defense.

Same request as 01c_download_civilian.py (the same 36 columns; purchase orders, definitive contracts and contract vehicles):
  civilian agencies (all top-tier agencies except DoD): one request per quarter
  Department of Defense: one request per month (it has many more records)
Every window goes through the shared verified download (01_download_usaspending.fetch_verified): it must stay under the download
service's limits (500,000 rows, and our 25 MB cap per file), and the rows in its file must match USAspending's own count for the same
filters and dates.  A window that is too big, or whose file comes back cut off, is split into smaller windows (new requests), and each
piece is checked the same way.  If the count service cannot be reached, the window is NOT accepted: the script fails, and you run it again
later.  Each window's manifest_<window>.json records the expected and received row counts.
The script is RESUMABLE: verified windows are skipped, files downloaded earlier without a check are checked (and replaced if they are cut
off), and windows that fail are asked again (three passes).  --force downloads every window again.
Defense data are published 90 days late, so the most recent Defense months are always incomplete.

This is meant to run on GitHub's computers (.github/workflows/history.yml, one job per year), but it works on any computer.

Usage:  python -I scripts/17_history_download.py FY [civilian|defense|all] [--force]       (default: all)
        To rebuild a year from scratch: delete data/history/FY<fy>, or use --force.
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


halves = civ.dl.halves


def fetch_split(label, start, end, agencies, out, prefix, force=False):
    """download start..end through the shared verified download (split when too big or cut off); returns the labels that failed"""
    return civ.dl.fetch_verified(label, start, end, agencies, out, prefix=prefix, columns=COLUMNS, force=force)


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


def top_labels(fy, scope="all", today=None):
    """the labels of the year's top-level windows that a download of `scope` asks for (civilian quarters, Defense months that have
    started); 18_history_build.py follows them to the files and their verification"""
    today = today or date.today()
    out = []
    if scope in ("all", "civilian"):
        out += [label for label, _, _ in windows(fy, monthly=False)]
    if scope in ("all", "defense"):
        out += [label for label, s, _ in windows(fy, monthly=True) if date.fromisoformat(s) <= today]
    return out


def one_pass(fy, scope, out, force=False):
    """request every window of the year once (verified windows are skipped unless force); returns the labels that failed"""
    failed = []
    if scope in ("all", "civilian"):
        agencies = civ.civilian_agencies(out)
        print(f"FY{fy}: {len(agencies)} civilian agencies, by quarter", flush=True)
        for label, s, e in windows(fy, monthly=False):
            failed += fetch_split(label, s, e, agencies, out, "CIVILIAN", force)
    if scope in ("all", "defense"):
        print(f"FY{fy}: Department of Defense, by month", flush=True)
        for label, s, e in windows(fy, monthly=True):
            if date.fromisoformat(s) > date.today():
                break
            failed += fetch_split(label, s, e, [DOD], out, "DEFENSE", force)
    return failed


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    force = "--force" in sys.argv[1:]
    fy = int(args[0])
    scope = args[1] if len(args) > 1 else "all"
    out = year_dir(fy)
    os.makedirs(out, exist_ok=True)
    failed = []
    for attempt in range(1, ROUNDS + 1):                    # the download service sometimes drops one window: ask again
        failed = one_pass(fy, scope, out, force=force and attempt == 1)      # --force only on the first pass
        if not failed or attempt == ROUNDS:
            break
        print(f"round {attempt}: {failed} failed; asking again in {RETRY_WAIT} seconds (finished windows are skipped)", flush=True)
        time.sleep(RETRY_WAIT)
    print("FAILED (rerun to retry):", failed if failed else "none", flush=True)
    if failed:
        sys.exit("Some windows could not be downloaded and verified (see above). Nothing unverified is used: run the same command "
                 "again later; verified windows are kept.")


if __name__ == "__main__":
    main()
