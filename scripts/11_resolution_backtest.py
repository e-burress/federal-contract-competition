"""
11_resolution_backtest.py - how many open solicitations eventually RESOLVE to a scoreable award?  (a dry run of the ledger)

The pre-registered forecast can only be scored when an open SAM.gov solicitation shows up in USAspending as an award with the
same solicitation identifier, competed, with a usable number of offers.  Cycle 2 estimated this at 20-90% depending on the filter.
Here we measure it on a comparable PAST set:  every Solicitation / Combined Synopsis notice of our five departments with a response
deadline between 2026-03-01 and 2026-04-15 (7+ months ago), matched to the FY2024-FY2026 base awards we downloaded (through Sep 2026).

Steps
  1. Pull those notices from the keyless SAM search (organization_id filter per department; inactive notices included).
  2. Normalise solicitation numbers and match to base awards (purchase orders, definitive contracts, IDV awards) with a solicitation id.
  3. Report match rate and the resolution funnel (matched -> competed -> usable offers), time from deadline to award.
Output: data/sam/hist_solicitations.jsonl, results/resolution_backtest_*.csv/.txt
"""
import json
import os
import sys
import time
import urllib.parse

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA, RESULTS, UA_SAM, flatten, http_json  # noqa: E402

ORGS = {"VA": "100006568", "USDA": "100006809", "DHS": "100011942", "DOI": "100010393", "HHS": "100004222"}
FROM, TO = "2026-03-01", "2026-04-15"
OUT = os.path.join(DATA, "sam", "hist_solicitations.jsonl")
lines = []


def say(*a):
    s = " ".join(str(x) for x in a)
    print(s)
    lines.append(s)


def norm(s):
    return "".join(ch for ch in (s or "").upper() if ch.isalnum())


def pull():
    recs = {}
    for short, oid in ORGS.items():
        page = 0
        while True:
            qs = {"index": "opp", "page": page, "mode": "search", "sort": "-modifiedDate", "size": 100, "qMode": "ALL",
                  "organization_id": oid, "notice_type": "o,k",
                  "response_date.from": f"{FROM}-05:00", "response_date.to": f"{TO}-04:00"}
            d = http_json("https://sam.gov/api/prod/sgs/v1/search/?" + urllib.parse.urlencode(qs, safe=",:-"), ua=UA_SAM)
            if not d:
                break
            res = d.get("_embedded", {}).get("results", [])
            for r in res:
                f = flatten(r)
                f["short"] = short
                recs[r["_id"]] = f
            page += 1
            if page >= d["page"]["totalPages"] or not res or page >= 100:
                break
            time.sleep(1.5)
        say(f"  pulled {short}: {sum(1 for r in recs.values() if r['short'] == short)} notices")
    with open(OUT, "w", encoding="utf-8") as fh:
        for r in recs.values():
            fh.write(json.dumps(r) + "\n")
    return list(recs.values())


def main():
    if os.path.exists(OUT):
        recs = [json.loads(l) for l in open(OUT, encoding="utf-8")]
        say(f"re-using {len(recs)} notices already pulled ({OUT})")
    else:
        recs = pull()
    h = pd.DataFrame(recs)
    h["sol_norm"] = h.sol.map(norm)
    h = h[(h.sol_norm != "") & (~h.canceled.astype(bool))]
    h["deadline"] = pd.to_datetime(h.response, utc=True, format="ISO8601").dt.tz_localize(None)
    h = h.sort_values("deadline").drop_duplicates("sol_norm", keep="last")
    say(f"\nhistorical solicitations (deadline {FROM}..{TO}), not canceled, distinct solicitation numbers: {len(h):,}")
    aw = pd.read_parquet(os.path.join(DATA, "processed", "base_awards.parquet"))
    aw = aw[aw.has_sol_id].copy()
    aw["sol_norm"] = aw.solicitation_identifier.map(norm)
    aw = aw.sort_values(["sol_norm", "action_date", "value0"], ascending=[True, True, False]).drop_duplicates("sol_norm", keep="first")
    m = h.merge(aw[["sol_norm", "agency", "award_kind", "action_date", "competed", "offers_ok", "eval_set", "single"]].rename(columns={"agency": "aw_agency"}),
                on="sol_norm", how="left")
    # an award counts only if its base action is after the notice's first possible posting (deadline - 120 days) - guards against reused numbers
    m.loc[m.action_date < m.deadline - pd.Timedelta(days=120), ["award_kind", "action_date", "competed", "offers_ok", "eval_set", "single"]] = np.nan
    m["matched"] = m.action_date.notna()
    m["days_deadline_to_award"] = (m.action_date - m.deadline).dt.days
    # departments whose USAspending data we have for the whole period
    complete = ["VA", "DOI", "HHS"]
    for short in ORGS:
        pass
    g = m.groupby("short").agg(n_notices=("sol_norm", "size"), n_matched=("matched", "sum"),
                               n_competed=("competed", lambda s: int(s.fillna(False).astype(bool).sum())),
                               n_resolvable=("eval_set", lambda s: int(s.fillna(False).astype(bool).sum())))
    g["pct_matched"] = 100 * g.n_matched / g.n_notices
    g["pct_resolvable"] = 100 * g.n_resolvable / g.n_notices
    g["pct_resolvable_of_matched"] = 100 * g.n_resolvable / g.n_matched
    say("\nRESOLUTION FUNNEL  (notice -> USAspending base award with the same solicitation id -> competed with usable offers)")
    say(g.round(1).to_string())
    tot = g[["n_notices", "n_matched", "n_competed", "n_resolvable"]].sum()
    say(f"ALL FIVE: {int(tot.n_notices):,} notices | matched {int(tot.n_matched):,} ({100 * tot.n_matched / tot.n_notices:.1f}%) | "
        f"resolvable {int(tot.n_resolvable):,} ({100 * tot.n_resolvable / tot.n_notices:.1f}%)")
    say("\nmatched awards by kind:\n" + m[m.matched].groupby(["short", "award_kind"], observed=True).size().unstack(fill_value=0).to_string())
    d = m.loc[m.matched, "days_deadline_to_award"]
    say("\ndays from response deadline to award base action (matched): " + ", ".join(f"p{q}={d.quantile(q / 100):.0f}" for q in (10, 25, 50, 75, 90, 95)))
    say(f"share of matched awards dated after the deadline: {100 * (d >= 0).mean():.1f}% (awards before the deadline are reused numbers or early awards)")
    r = m[m.eval_set.fillna(False).astype(bool)]
    say(f"single-offer share among resolvable historical solicitations: {r.single.astype(float).mean():.3f} (n={len(r)})")
    m.drop(columns=["title"]).to_csv(os.path.join(RESULTS, "resolution_backtest_notices.csv"), index=False)
    g.to_csv(os.path.join(RESULTS, "resolution_backtest_summary.csv"))
    open(os.path.join(RESULTS, "resolution_backtest.txt"), "w", encoding="utf-8").write("\n".join(lines))


if __name__ == "__main__":
    main()
