"""
01b_download_threshold_universe.py - ALL civilian agencies (everything except the Department of Defense),
purchase orders (B) and definitive contracts (D) only, with just 14 columns.

Why a second pull?  The threshold test (bunching just under $250K / $350K and the $10K / $15K micro-purchase line)
needs many awards near each line.  Five agencies give a few hundred per year; the whole civilian government gives thousands.
Same method as 01_download_usaspending.py (Advanced Search transaction download with a `columns` list), through its shared verified
download (fetch_verified): every file's rows must match USAspending's own count; a window that is too big or cut off is split.

DoD is excluded on purpose: its public data are delayed by 90 days.

Usage:  python -I scripts/01b_download_threshold_universe.py [--reverse] [FY2026Q4 ...]
          --reverse  work through the quarters from the newest to the oldest (so two copies of the script can share the work)
          labels     only these quarters
Output: data/usaspending_universe/CIVILIAN__FYyyyyQn.zip + manifest_<quarter>.json
"""
import importlib.util
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA, UA_USASPENDING, http_json  # noqa: E402

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
    _spec = importlib.util.spec_from_file_location("dl01", os.path.join(os.path.dirname(os.path.abspath(__file__)), "01_download_usaspending.py"))
    DL = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(DL)
    failed = []
    for label, start, end in wins:
        failed += DL.fetch_verified(label, start, end, agencies, OUT, prefix="CIVILIAN", columns=COLUMNS, award_types=["B", "D"])
    print("FAILED (rerun to retry):", failed if failed else "none", flush=True)
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
