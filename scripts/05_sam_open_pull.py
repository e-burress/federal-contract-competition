"""
05_sam_open_pull.py - list the currently OPEN SAM.gov solicitations whose response deadline falls in a window.

Source: the keyless search endpoint that the sam.gov website itself uses (no account, no key).
    https://sam.gov/api/prod/sgs/v1/search/?index=opp&is_active=true&notice_type=o,k&response_date.from=...&response_date.to=...
        notice_type  o = Solicitation, k = Combined Synopsis/Solicitation
        size         up to 100 per page; the service stops at 10,000 records per query
This endpoint is undocumented and could change; the documented alternative is the SAM.gov Opportunities
API (free key) or the daily Contract Opportunities CSV.

What we keep (only non-personal fields): notice id, solicitation number, notice type, posted and response
timestamps, title, and the organisation chain (department > agency > office names and codes).
We do NOT keep descriptions, contacts or street addresses.

Output: data/sam/open_search_all.jsonl  (one line per notice, ALL departments incl. DoD)
"""
import json
import os
import sys
import time
import urllib.parse
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA, UA_SAM, flatten, http_json  # noqa: E402

FROM = sys.argv[1] if len(sys.argv) > 1 else "2026-11-01"
TO = sys.argv[2] if len(sys.argv) > 2 else "2026-12-15"
OUT_DIR = os.path.join(DATA, "sam")
os.makedirs(OUT_DIR, exist_ok=True)
OUT = os.path.join(OUT_DIR, "open_search_all.jsonl")

BASE = "https://sam.gov/api/prod/sgs/v1/search/"


def main():
    pulled_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    seen = {}
    page = 0
    total = None
    while True:
        qs = {
            "index": "opp", "page": page, "mode": "search", "sort": "-modifiedDate", "size": 100,
            "qMode": "ALL", "is_active": "true", "notice_type": "o,k",
            # the website sends a UTC offset after the date; -04:00 / -05:00 are US Eastern
            "response_date.from": f"{FROM}-04:00", "response_date.to": f"{TO}-05:00",
        }
        url = BASE + "?" + urllib.parse.urlencode(qs, safe=",:-")
        d = http_json(url, ua=UA_SAM)
        if not d:
            print("page", page, "failed; stopping")
            break
        total = d["page"]["totalElements"]
        res = d.get("_embedded", {}).get("results", [])
        for r in res:
            seen[r["_id"]] = flatten(r)
        print(f"page {page}: {len(res)} results (total reported {total}, unique so far {len(seen)})", flush=True)
        page += 1
        if page >= d["page"]["totalPages"] or not res or page >= 100:
            break
        time.sleep(1.5)
    with open(OUT, "w", encoding="utf-8") as fh:
        for rec in seen.values():
            rec["pulled_at"] = pulled_at
            fh.write(json.dumps(rec) + "\n")
    print("saved", len(seen), "notices to", OUT, "at", pulled_at)


if __name__ == "__main__":
    main()
