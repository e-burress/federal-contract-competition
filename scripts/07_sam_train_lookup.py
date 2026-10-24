"""
07_sam_train_lookup.py - find the SAM.gov solicitation behind a SAMPLE of historical awards, to get the response window.

Why?  USAspending's own `solicitation_date` field is a placeholder (cycle 2: it equals the award date on ~85-90% of
rows), so the "how long was the solicitation open" feature cannot come from USAspending.  SAM.gov has it.

For each sampled award (competed, usable offers, has a solicitation identifier) we run the same keyless search the website
uses, restricted to notices that carry the same solicitation number, then:
  * pick the Solicitation / Combined Synopsis notice posted most recently (the live chain)
  * if it was amended, fetch its first version (parentNoticeId) to get the ORIGINAL posting date and original deadline
  * record windows in days, the notice type, and whether a presolicitation / sources-sought notice exists
No contact data is read or stored.  One request per second, resumable.

Usage:  python -I scripts/07_sam_train_lookup.py <cap_per_agency> [seed] [agency,agency,...]
        (VA gets 1.33x the cap because it has the most open solicitations; the optional agency list restricts the run to
         those short names - VA, HHS, DHS, DOI, USDA - so you can start before every download has finished).
        A row looked up by an earlier version of this script (no keyword flags) is looked up again; 08_model_forecast.py keeps the LAST row per solicitation.
Input : data/processed/base_awards.parquet
Output: data/sam/train_lookup.csv
"""
import csv
import os
import sys
import time
import urllib.parse

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA, UA_SAM, http_json, kw_flags  # noqa: E402

CAP = int(sys.argv[1]) if len(sys.argv) > 1 else 500
SEED = int(sys.argv[2]) if len(sys.argv) > 2 else 20261023
ONLY = sys.argv[3].split(",") if len(sys.argv) > 3 else None
OUT = os.path.join(DATA, "sam", "train_lookup.csv")
FIELDS = ["sol_norm", "sol_raw", "agency", "fy", "n_same", "types_seen", "primary_id", "primary_type", "n_mods",
          "orig_post", "orig_deadline", "final_deadline", "final_publish", "has_presol", "has_sources_sought", "found", "parent_ok",
          "title", "kw_brand", "kw_sole"]


def norm(s):
    return "".join(ch for ch in (s or "").upper() if ch.isalnum())


def parent_dates(pid):
    d = http_json(f"https://sam.gov/api/prod/opps/v2/opportunities/{pid}", ua=UA_SAM)
    if not d:
        return None, None
    d2 = d.get("data2") or {}
    return d.get("postedDate"), ((d2.get("solicitation") or {}).get("deadlines") or {}).get("response")


def lookup(sol):
    q = urllib.parse.quote(sol)
    s = http_json(f"https://sam.gov/api/prod/sgs/v1/search/?index=opp&page=0&mode=search&sort=-modifiedDate&size=50&qMode=ALL&q={q}", ua=UA_SAM)
    res = ((s or {}).get("_embedded") or {}).get("results", [])
    same = [x for x in res if norm(x.get("solicitationNumber")) == norm(sol)]
    out = {"sol_norm": norm(sol), "sol_raw": sol, "n_same": len(same), "found": 0, "parent_ok": "", "kw_brand": 0, "kw_sole": 0}
    types = sorted(set((x.get("type") or {}).get("value") for x in same))
    out["types_seen"] = "|".join(types)
    out["has_presol"] = int("Presolicitation" in types)
    out["has_sources_sought" ] = int("Sources Sought" in types)
    ok = [x for x in same if (x.get("type") or {}).get("code") in ("o", "k")]
    if not ok:
        return out
    prim = max(ok, key=lambda x: x.get("publishDate") or "")
    desc = prim.get("descriptions") or []
    snippet = (desc[0].get("content") if desc and isinstance(desc[0], dict) else "") or ""
    kb, ks = kw_flags(prim.get("title"), snippet)
    out.update(title=prim.get("title"), kw_brand=kb, kw_sole=ks)
    out.update(found=1, primary_id=prim["_id"], primary_type=(prim.get("type") or {}).get("value"),
               n_mods=(prim.get("modifications") or {}).get("count"), final_deadline=prim.get("responseDate"),
               final_publish=prim.get("publishDate"))
    if (out["n_mods"] or 0) > 0 and prim.get("parentNoticeId"):
        time.sleep(1.0)
        op, od = parent_dates(prim["parentNoticeId"])
        out.update(orig_post=op, orig_deadline=od, parent_ok=int(op is not None))
    else:
        out.update(orig_post=prim.get("publishDate"), orig_deadline=prim.get("responseDate"), parent_ok=1)
    return out


def main():
    aw = pd.read_parquet(os.path.join(DATA, "processed", "base_awards.parquet"),
                         columns=["agency", "fy", "eval_set", "has_sol_id", "solicitation_identifier", "action_date"])
    aw = aw[aw.eval_set & aw.has_sol_id & aw.fy.between(2024, 2026)].copy()
    if ONLY:
        aw = aw[aw.agency.isin(ONLY)]
    aw["sol_norm"] = aw.solicitation_identifier.map(norm)
    aw = aw.drop_duplicates("sol_norm")
    print("distinct eligible solicitations by agency:\n", aw.groupby("agency", observed=True).size().to_string())
    parts = []
    for ag, g in aw.groupby("agency", observed=True):
        cap = int(CAP * 4 / 3) if ag == "VA" else CAP
        parts.append(g.sample(n=min(len(g), cap), random_state=SEED))
    smp = pd.concat(parts).sample(frac=1, random_state=SEED)       # shuffled so that stopping early still leaves a random sample
    done = set()
    if os.path.exists(OUT):
        prev = pd.read_csv(OUT, dtype=str, keep_default_na=False)
        if "kw_brand" not in prev.columns:                 # file written by the first version of this script: add the new columns
            for col in ("title", "kw_brand", "kw_sole"):
                prev[col] = ""
            prev.to_csv(OUT, index=False)
        done = set(prev.loc[prev.kw_brand.isin(["0", "1"]), "sol_norm"])           # rows without flags are looked up again
    todo = smp[~smp.sol_norm.isin(done)]
    print(f"sample {len(smp)}; already done {len(done)}; to do {len(todo)}", flush=True)
    new_file = not os.path.exists(OUT)
    with open(OUT, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        if new_file:
            w.writeheader()
        for i, (_, r) in enumerate(todo.iterrows()):
            rec = lookup(r.solicitation_identifier.strip())
            rec["agency"], rec["fy"] = r.agency, r.fy
            w.writerow(rec)
            fh.flush()
            if i % 50 == 0:
                print(i, rec["sol_raw"], rec["n_same"], rec["found"], rec.get("orig_post"), flush=True)
            time.sleep(1.0)
    print("finished")


if __name__ == "__main__":
    main()
