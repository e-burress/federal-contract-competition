"""
06_sam_open_details.py - for each open civilian solicitation in our agencies, fetch the features the model needs.

Keyless calls, about 1 second apart:
  (a) notice detail   https://sam.gov/api/prod/opps/v2/opportunities/<notice_id>
        -> PSC (classificationCode), NAICS, set-aside code, response deadline
        The detail JSON also contains contact people and e-mail addresses.  We NEVER write it to disk:
        we copy only a short list of non-personal fields and throw the rest away.
  (b) solicitation-number search  https://sam.gov/api/prod/sgs/v1/search/?q=<sol#>
        -> every notice that shares the solicitation number (presolicitation, sources sought, amendments, award notices)
  (c) ORIGINAL posting: SAM keeps only the latest version of an amended notice in search results (its publishDate is the date
        of the LAST amendment).  The search result's parentNoticeId points at the first version, so for notices with
        amendments we fetch that first version's detail -> original posting date and original response deadline.

Definitions used for the model (same for the open notices and for the historical training rows):
  orig_post       date the notice was first posted
  orig_deadline   response deadline in the first version
  deadline        current (latest) response deadline
  window_orig     orig_deadline - orig_post (days)      <- the window bidders were first given
  window_final    deadline - orig_post (days)           <- total time the solicitation was open if the deadline holds

Input : data/sam/open_search_all.jsonl   (from 05_sam_open_pull.py)
Output: data/sam/open_details.jsonl      (one line per solicitation; resumable - rerun fills in any missing fields)
"""
import json
import os
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA, UA_SAM, http_json  # noqa: E402

SRC = os.path.join(DATA, "sam", "open_search_all.jsonl")
OUT = os.path.join(DATA, "sam", "open_details.jsonl")

# SAM department label -> USAspending toptier agency name (only the agencies in our slice)
DEPT_MAP = {
    "VETERANS AFFAIRS, DEPARTMENT OF": "Department of Veterans Affairs",
    "HEALTH AND HUMAN SERVICES, DEPARTMENT OF": "Department of Health and Human Services",
    "HOMELAND SECURITY, DEPARTMENT OF": "Department of Homeland Security",
    "INTERIOR, DEPARTMENT OF THE": "Department of the Interior",
    "AGRICULTURE, DEPARTMENT OF": "Department of Agriculture",
}


def norm(s):
    return "".join(ch for ch in (s or "").upper() if ch.isalnum())


def detail_fields(notice_id):
    """Fetch one notice and return ONLY non-personal fields."""
    d = http_json(f"https://sam.gov/api/prod/opps/v2/opportunities/{notice_id}", ua=UA_SAM)
    d2 = (d or {}).get("data2") or {}
    codes = []
    for x in d2.get("naics") or []:
        c = x.get("code")
        if isinstance(c, list):
            codes += [str(y) for y in c]
        elif c:
            codes.append(str(c))
    return {
        "psc": d2.get("classificationCode"),
        "naics": codes[0] if codes else None,
        "naics_all": codes,
        "set_aside": (d2.get("solicitation") or {}).get("setAside"),
        "deadline_detail": ((d2.get("solicitation") or {}).get("deadlines") or {}).get("response"),
        "posted_detail": (d or {}).get("postedDate"),
        "ok": d is not None,
    }


def main():
    notices = [json.loads(l) for l in open(SRC, encoding="utf-8")]
    todo = [n for n in notices if n["dept"] in DEPT_MAP]
    done = {}
    if os.path.exists(OUT):
        for l in open(OUT, encoding="utf-8"):
            r = json.loads(l)
            done[r["notice_id"]] = r
    print(len(todo), "notices in our agencies;", len(done), "already have a record")
    for i, n in enumerate(todo):
        rec = done.get(n["notice_id"])
        if rec is None:
            rec = dict(n)
            rec["usas_agency"] = DEPT_MAP[n["dept"]]
            f = detail_fields(n["notice_id"])                      # (a)
            rec.update({k: v for k, v in f.items() if k != "ok"})
            time.sleep(1.1)
            q = urllib.parse.quote(n["sol"])                        # (b)
            s = http_json(f"https://sam.gov/api/prod/sgs/v1/search/?index=opp&page=0&mode=search&sort=-modifiedDate&size=50&qMode=ALL&q={q}", ua=UA_SAM)
            same = [x for x in ((s or {}).get("_embedded", {}).get("results", [])) if norm(x.get("solicitationNumber")) == norm(n["sol"])]
            rec["n_same_sol_notices"] = len(same)
            rec["notice_types_seen"] = sorted(set((x.get("type") or {}).get("value") for x in same))
            time.sleep(1.1)
        rec["kw_brand"], rec["kw_sole"] = n.get("kw_brand"), n.get("kw_sole")   # text flags come from the newest search pull
        if "orig_post" not in rec:                                  # (c)
            if (n.get("n_mods") or 0) > 0 and n.get("parent_notice"):
                p = detail_fields(n["parent_notice"])
                rec["orig_post"] = p["posted_detail"]
                rec["orig_deadline"] = p["deadline_detail"]
                rec["parent_ok"] = p["ok"]
                time.sleep(1.1)
            else:
                rec["orig_post"] = n["publish"]
                rec["orig_deadline"] = n["response"]
                rec["parent_ok"] = True
        done[n["notice_id"]] = rec
        if i % 20 == 0:
            print(i, n["sol"], rec.get("psc"), rec.get("naics"), rec.get("set_aside"), rec.get("orig_post"), flush=True)
            with open(OUT, "w", encoding="utf-8") as fh:            # checkpoint
                for r in done.values():
                    fh.write(json.dumps(r) + "\n")
    with open(OUT, "w", encoding="utf-8") as fh:
        for r in done.values():
            fh.write(json.dumps(r) + "\n")
    print("done:", len(done), "records")


if __name__ == "__main__":
    main()
