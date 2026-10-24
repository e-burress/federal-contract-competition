"""
01b_download_threshold_universe.py - ALL civilian agencies (everything except the Department of Defense),
purchase orders (B) and definitive contracts (D) only, with just 14 columns.

Why a second pull?  The threshold test (bunching just under $250K / $350K and the $10K / $15K micro-purchase line)
needs many awards near each line.  Five agencies give a few hundred per year; the whole civilian government gives thousands.
Same method as 01_download_usaspending.py (Advanced Search transaction download with a `columns` list).

DoD is excluded on purpose: its public data are delayed by 90 days.

Usage:  python -I scripts/01b_download_threshold_universe.py [--reverse] [FY2026Q4 ...]
          --reverse  work through the quarters from the newest to the oldest (so two copies of the script can share the work)
          labels     only these quarters
Output: data/usaspending_universe/CIVILIAN__FYyyyyQn.zip + manifest_<quarter>.json
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA, UA_USASPENDING, download_file, http_json  # noqa: E402

OUT = os.path.join(DATA, "usaspending_universe")
os.makedirs(OUT, exist_ok=True)
API = "https://api.usaspending.gov/api/v2"

COLUMNS = [
    "contract_transaction_unique_key", "contract_award_unique_key", "award_id_piid", "modification_number",
    "action_date", "award_type_code", "base_and_all_options_value", "federal_action_obligation",
    "awarding_agency_name", "extent_competed_code", "number_of_offers_received",
    "purchase_card_as_payment_method_code", "action_type_code", "type_of_set_aside_code",
]

WINDOWS = []
for fy in (2024, 2025, 2026):
    WINDOWS += [(f"FY{fy}Q1", f"{fy-1}-10-01", f"{fy-1}-12-31"), (f"FY{fy}Q2", f"{fy}-01-01", f"{fy}-03-31"),
                (f"FY{fy}Q3", f"{fy}-04-01", f"{fy}-06-30"), (f"FY{fy}Q4", f"{fy}-07-01", f"{fy}-09-30")]


def civilian_agencies():
    d = http_json(f"{API}/references/toptier_agencies/", ua=UA_USASPENDING)
    names = [a["agency_name"] for a in d["results"]]
    # keep every top-tier agency except the Department of Defense
    return [n for n in names if n != "Department of Defense"]


def main():
    agencies = civilian_agencies()
    print(len(agencies), "civilian top-tier agencies")
    wins = list(WINDOWS)
    only = [a for a in sys.argv[1:] if not a.startswith("--")]
    if only:
        wins = [w for w in wins if w[0] in only]
    if "--reverse" in sys.argv:
        wins = wins[::-1]
    for label, start, end in wins:
        if os.path.exists(os.path.join(OUT, f"CIVILIAN__{label}.zip")):
            continue                                   # already downloaded (possibly by the other copy of this script)
        body = {
            "filters": {
                "award_type_codes": ["B", "D"],
                "agencies": [{"type": "awarding", "tier": "toptier", "name": n} for n in agencies],
                "time_period": [{"start_date": start, "end_date": end, "date_type": "action_date"}],
            },
            "columns": COLUMNS, "file_format": "csv",
        }
        print(f"[{time.strftime('%H:%M:%S')}] {label}", flush=True)
        resp = http_json(f"{API}/download/transactions/", method="POST", body=body, ua=UA_USASPENDING)
        if not resp or "file_name" not in resp:
            print("  submit failed")
            time.sleep(10)
            continue
        st = {}
        t0 = time.time()
        while time.time() - t0 < 900:
            st = http_json(f"{API}/download/status?file_name={resp['file_name']}", ua=UA_USASPENDING) or {}
            if st.get("status") in ("finished", "failed"):
                break
            time.sleep(10)
        rec = {"window": label, "status": st.get("status"), "total_rows": st.get("total_rows"), "total_size_kb": st.get("total_size")}
        if st.get("status") == "finished" and float(st.get("total_size") or 0) <= 25_000:
            dest = os.path.join(OUT, f"CIVILIAN__{label}.zip")
            n = download_file(resp["file_url"], dest, ua=UA_USASPENDING)
            rec.update(bytes=n, path=os.path.basename(dest), done=bool(n))
            if st.get("total_rows") and int(st["total_rows"]) >= 499_000:
                rec.update(done=False, warning="hit the 500,000-row cap: split this window")
        json.dump(rec, open(os.path.join(OUT, f"manifest_{label}.json"), "w"), indent=1)
        print("  ", rec, flush=True)
        time.sleep(3)


if __name__ == "__main__":
    main()
