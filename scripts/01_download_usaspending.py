"""
01_download_usaspending.py - pull contract TRANSACTIONS for a few civilian agencies from USAspending.gov.

How it works (no key, no account):
  1. POST https://api.usaspending.gov/api/v2/download/transactions/   (the same service the website's
     "Advanced Search > Download" button uses).  We pass:
        filters : awarding agency + award types + a date window (action_date)
        columns : ONLY the ~35 columns we need  -> files are ~100x smaller than the 297-column bulk files
  2. Poll   GET /api/v2/download/status?file_name=...   until it says "finished"
  3. Download the ZIP from files.usaspending.gov/generated_downloads/...

Award types:  B = purchase order, D = definitive contract, IDV_* = indefinite-delivery vehicles
(IDV_A GWAC, IDV_B IDC, IDV_B_A/B/C IDC subtypes, IDV_C FSS, IDV_D BOA, IDV_E BPA).
We skip A (BPA calls) and C (delivery orders): orders are mostly not solicited separately on SAM.gov
and number_of_offers_received is blank on ~90% of them (cycle 2 finding).

Every file is a "transaction" (a base award is the row with modification_number 0; later rows are mods).
The script is RESUMABLE: it records every job in data/usaspending/manifest_<agency>.json and skips finished ones.
Safety checks: each ZIP must be <= 25 MB; a window that returns 500,000 rows (the service cap) is flagged.

Usage:  python -I scripts/01_download_usaspending.py [agency-index ...]     (default: all agencies in common.AGENCIES)
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import AGENCIES, DATA, UA_USASPENDING, download_file, http_json  # noqa: E402

OUT = os.path.join(DATA, "usaspending")
os.makedirs(OUT, exist_ok=True)
# one manifest file per agency, so several copies of this script can run side by side without clobbering each other
def manifest_path(agency):
    return os.path.join(OUT, "manifest_" + agency.replace(" ", "_") + ".json")

API = "https://api.usaspending.gov/api/v2"
AWARD_TYPES = ["B", "D", "IDV_A", "IDV_B", "IDV_B_A", "IDV_B_B", "IDV_B_C", "IDV_C", "IDV_D", "IDV_E"]

COLUMNS = [
    "contract_transaction_unique_key", "contract_award_unique_key", "award_id_piid", "parent_award_id_piid",
    "modification_number", "action_date", "action_type_code", "award_type_code", "award_or_idv_flag",
    "federal_action_obligation", "base_and_all_options_value", "base_and_exercised_options_value",
    "awarding_agency_name", "awarding_sub_agency_name", "awarding_office_code", "awarding_office_name",
    "solicitation_identifier", "solicitation_date",
    "product_or_service_code", "naics_code",
    "extent_competed_code", "solicitation_procedures_code", "type_of_set_aside_code", "number_of_offers_received",
    "fair_opportunity_limited_sources_code", "other_than_full_and_open_competition_code",
    "type_of_contract_pricing_code", "commercial_item_acquisition_procedures_code",
    "multiple_or_single_award_idv_code", "idv_type_code",
    "recipient_uei", "recipient_parent_uei",
    "period_of_performance_start_date", "period_of_performance_potential_end_date",
    "purchase_card_as_payment_method_code", "cost_or_pricing_data_code",
]

# Quarter windows covering FY2024-FY2026 (US fiscal year starts 1 October)
WINDOWS = []
for fy in (2024, 2025, 2026):
    WINDOWS += [
        (f"FY{fy}Q1", f"{fy-1}-10-01", f"{fy-1}-12-31"),
        (f"FY{fy}Q2", f"{fy}-01-01", f"{fy}-03-31"),
        (f"FY{fy}Q3", f"{fy}-04-01", f"{fy}-06-30"),
        (f"FY{fy}Q4", f"{fy}-07-01", f"{fy}-09-30"),
    ]


def load_manifest(agency):
    p = manifest_path(agency)
    if os.path.exists(p):
        return json.load(open(p, encoding="utf-8"))
    return {}


def save_manifest(agency, m):
    json.dump(m, open(manifest_path(agency), "w", encoding="utf-8"), indent=1)


def submit(agency, start, end):
    body = {
        "filters": {
            "award_type_codes": AWARD_TYPES,
            "agencies": [{"type": "awarding", "tier": "toptier", "name": agency}],
            "time_period": [{"start_date": start, "end_date": end, "date_type": "action_date"}],
        },
        "columns": COLUMNS,
        "file_format": "csv",
    }
    return http_json(f"{API}/download/transactions/", method="POST", body=body, ua=UA_USASPENDING), body


def wait_finished(file_name, max_wait=600):
    t0 = time.time()
    while time.time() - t0 < max_wait:
        st = http_json(f"{API}/download/status?file_name={file_name}", ua=UA_USASPENDING)
        if st and st.get("status") in ("finished", "failed"):
            return st
        time.sleep(8)
    return {"status": "timeout"}


def main():
    idxs = [int(a) for a in sys.argv[1:]] or list(range(len(AGENCIES)))
    for ai in idxs:
        agency = AGENCIES[ai]
        manifest = load_manifest(agency)
        for label, start, end in WINDOWS:
            key = f"{agency}|{label}"
            if manifest.get(key, {}).get("done"):
                continue
            print(f"[{time.strftime('%H:%M:%S')}] {key} {start}..{end}", flush=True)
            resp, body = submit(agency, start, end)
            if not resp or "file_name" not in resp:
                print("  submit failed", resp)
                time.sleep(10)
                continue
            st = wait_finished(resp["file_name"])
            rec = {"agency": agency, "window": label, "start": start, "end": end,
                   "file_name": resp["file_name"], "status": st.get("status"),
                   "total_rows": st.get("total_rows"), "total_size_kb": st.get("total_size")}
            if st.get("status") == "finished":
                size_kb = float(st.get("total_size") or 0)
                if size_kb > 25_000:
                    rec["note"] = "ZIP above 25 MB - not downloaded; split the window"
                else:
                    safe = agency.replace(" ", "_")
                    dest = os.path.join(OUT, f"{safe}__{label}.zip")
                    n = download_file(resp["file_url"], dest, ua=UA_USASPENDING)
                    rec["bytes"] = n
                    rec["path"] = os.path.basename(dest)
                    rec["done"] = bool(n)
                    if st.get("total_rows") and int(st["total_rows"]) >= 499_000:
                        rec["warning"] = "row count at the 500,000 service cap - split the window further"
                        rec["done"] = False
            manifest[key] = rec
            save_manifest(agency, manifest)
            print("  ", {k: rec.get(k) for k in ("status", "total_rows", "total_size_kb", "bytes", "warning", "note")}, flush=True)
            time.sleep(3)


if __name__ == "__main__":
    main()
