"""
01c_download_civilian.py - contract TRANSACTIONS for EVERY civilian agency (everything except the Department of Defense),
FY2024-FY2026, for the civilian-wide single-offer atlas (03b_atlas_civilian.py).

Same request as 01_download_usaspending.py (the same 36 columns; purchase orders, definitive contracts and all IDV types),
but one request per quarter covering all civilian top-tier agencies at once, instead of one request per agency.
DoD is excluded on purpose: its public data are delayed by 90 days.

Safety checks (as in 01, through its shared fetch_verified): a file must be <= 25 MB and below the 500,000-row service cap, and its
rows must match USAspending's own count for the same filters.  A quarter that breaks a limit or fails the count check is split into
its months automatically.  The script is RESUMABLE: verified windows are skipped.
The agencies queried (name, abbreviation, toptier code) are saved in agencies.json next to the files.

Usage:  python -I scripts/01c_download_civilian.py [FY2026Q4 ...]      (default: all twelve quarters)
Output: data/usaspending_civilian/CIVILIAN__<window>.zip, manifest_<window>.json, agencies.json
"""
import importlib.util
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA, UA_USASPENDING, http_json  # noqa: E402

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


# the month and half-window splitting lives in 01_download_usaspending.py (shared by every downloader)
months = dl.months


def fetch_window(label, start, end, agencies, out=OUT, force=False, allow_unverified=False, prefix="CIVILIAN", columns=None):
    """Download one window into `out` as <prefix>__<label>.zip through the shared verified download
    (01_download_usaspending.fetch_verified): the file's rows must match USAspending's own count, a window that is too big or
    cut off is split into smaller windows. Returns the labels that failed ([] = done and verified)."""
    return dl.fetch_verified(label, start, end, agencies, out, prefix=prefix, columns=columns, force=force,
                             allow_unverified=allow_unverified)


def main():
    agencies = civilian_agencies()
    print(len(agencies), "civilian top-tier agencies", flush=True)
    only = [a for a in sys.argv[1:] if not a.startswith("--")]
    failed = []
    for label, start, end in [w for w in dl.WINDOWS if not only or w[0] in only]:
        failed += fetch_window(label, start, end, agencies)
    print("FAILED (rerun to retry):", failed if failed else "none", flush=True)
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
