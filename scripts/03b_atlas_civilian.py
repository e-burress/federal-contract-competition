"""
03b_atlas_civilian.py - single-offer shares for EVERY civilian agency: the civilian-wide extension of 03_atlas.py.

Same definitions as 03_atlas.py (set in 02_build_awards.py):
  eval_set = base award that was COMPETED (extent competed A, D, E, F or CDO) with a usable offers count (1..998)
  single-offer share = awards with exactly one offer / eval_set, with a 95% Wilson interval
Agency = the awarding top-tier agency.  Agency level only: no buying office is named.
The five departments of the original analysis are measured again from this newer download and compared with the
published five-department numbers (atlas_civilian_five_check.csv), which shows how much late reporting moved them.

Input : data/processed/base_awards_civilian.parquet   (02_build_awards.py --civilian)
        data/usaspending_civilian/agencies.json and the ZIPs   (01c_download_civilian.py)
Output: results/atlas_civilian_by_agency.csv, atlas_civilian_by_agency_fy.csv, atlas_civilian_totals.csv, atlas_civilian_by_fy.csv,
        atlas_civilian_by_kind.csv, atlas_civilian_by_setaside.csv, atlas_civilian_five_check.csv, atlas_civilian_summary.txt
"""
import glob
import importlib.util
import json
import os
import sys
from datetime import datetime, timezone

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from atlas_lib import share_table  # noqa: E402
from common import DATA, RESULTS  # noqa: E402

# the five-department short names live in 02_build_awards.py (file name starts with a digit, so import it by path)
_spec = importlib.util.spec_from_file_location("build02", os.path.join(os.path.dirname(os.path.abspath(__file__)), "02_build_awards.py"))
b02 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(b02)
FULL = {v: k for k, v in b02.SHORT.items()}           # "VA" -> "Department of Veterans Affairs"
MIN_N = 200                                           # agencies with fewer competed awards are also shown pooled
GSA = "General Services Administration"               # mostly very small supply orders: also shown without it

SRC = os.path.join(DATA, "usaspending_civilian")
zips = glob.glob(os.path.join(SRC, "*.zip"))
snapshot = datetime.fromtimestamp(max(os.path.getmtime(p) for p in zips), timezone.utc).strftime("%Y-%m-%d")
abbr = {a["name"]: a.get("abbreviation") or "" for a in json.load(open(os.path.join(SRC, "agencies.json"), encoding="utf-8"))}

base = pd.read_parquet(os.path.join(DATA, "processed", "base_awards_civilian.parquet"))
base = base[base.fy.between(2024, 2026)].copy()
base["agency"] = base["agency"].astype(str).map(lambda a: FULL.get(a, a))
ev = base[base.eval_set].copy()
lines = []


def say(*a):
    s = " ".join(str(x) for x in a)
    print(s)
    lines.append(s)


def pooled(df, scope):
    t = share_table(df.assign(scope=scope), "scope")
    t["n_agencies"] = df.agency.nunique()
    t["median_award_value"] = df.value0.median()        # base-and-all-options value at the base action
    return t


# ---------------------------------------------------------------------------------------------------
say("=" * 100)
say(f"CIVILIAN-WIDE SINGLE-OFFER ATLAS  (download of {snapshot}; base awards dated Oct 2023 - Sep 2026; DoD excluded)")
cov = base.groupby("agency").agg(n_base=("award_kind", "size"), n_competed=("competed", "sum"), n_eval=("eval_set", "sum"))
say(f"agencies with at least one base award: {len(cov)} | with at least one competed award with usable offers: {(cov.n_eval > 0).sum()}")
say(f"TOTAL base awards {len(base):,} | competed {int(base.competed.sum()):,} ({100 * base.competed.mean():.1f}%) | "
    f"eval set {int(base.eval_set.sum()):,} ({100 * base.eval_set.sum() / base.competed.sum():.1f}% of competed)")
sent = (base[base.competed].offers == 999).sum()
say(f"999 sentinel among competed base awards: {sent:,}")

# ---------------------------------------------------------------------------------------------------
t = share_table(ev, "agency").merge(cov[["n_base", "n_competed"]], left_on="agency", right_index=True)
t.insert(1, "abbreviation", t.agency.map(abbr).fillna(""))
t["in_original_five"] = t.agency.isin(FULL.values())
t = t.sort_values("n_eval", ascending=False)
t.to_csv(os.path.join(RESULTS, "atlas_civilian_by_agency.csv"), index=False)
t2 = share_table(ev, ["agency", "fy"])
t2.to_csv(os.path.join(RESULTS, "atlas_civilian_by_agency_fy.csv"), index=False)

small = t[t.n_eval < MIN_N].agency
tot = pd.concat([
    pooled(ev, "all civilian agencies"),
    pooled(ev[ev.agency.isin(FULL.values())], "original five departments"),
    pooled(ev[~ev.agency.isin(FULL.values())], "all other civilian agencies"),
    pooled(ev[ev.agency != GSA], "all civilian agencies except GSA"),
    pooled(ev[ev.agency == GSA], "GSA only"),
    pooled(ev[~ev.agency.isin(FULL.values()) & (ev.agency != GSA)], "other civilian agencies except GSA"),
    pooled(ev[ev.agency.isin(small)], f"agencies with fewer than {MIN_N} competed awards"),
], ignore_index=True)
po_def = ev[ev.award_kind.isin(["PO", "DEF"]) & (ev.obligation_sum_window > 0)]
tot["dollar_weighted_share_po_def"] = [
    po_def.loc[po_def.single, "obligation_sum_window"].sum() / po_def.obligation_sum_window.sum()] + [None] * (len(tot) - 1)
tot["snapshot"] = snapshot
tot.to_csv(os.path.join(RESULTS, "atlas_civilian_totals.csv"), index=False)
say("\nTOTALS\n" + tot.round(4).to_string(index=False))

say(f"\nBY AGENCY (competed awards with usable offers >= {MIN_N}), highest share first")
big = t[t.n_eval >= MIN_N].sort_values("share", ascending=False)
say(big[["agency", "abbreviation", "n_eval", "k_single", "share", "ci_lo", "ci_hi"]].round(3).to_string(index=False))
say(f"\n{len(small)} smaller agencies (fewer than {MIN_N} competed awards each) are pooled in the totals above.")

# ---------------------------------------------------------------------------------------------------
# GSA's purchase orders (mostly very small supply orders) outnumber everything else and their offer coding changed over time
# (one offer on 55% in FY2024, 17% in FY2026), so every pooled breakdown is given with and without GSA.
SCOPES = {"all civilian agencies": ev, "all civilian agencies except GSA": ev[ev.agency != GSA], "GSA only": ev[ev.agency == GSA]}
sa_map = {"NONE": "no set-aside", "SBA": "small business", "SBP": "small business", "ESB": "small business", "RSB": "small business",
          "8A": "8(a)", "8AN": "8(a)", "HZC": "HUBZone", "HZS": "HUBZone", "SDVOSBC": "SDVOSB", "SDVOSBS": "SDVOSB",
          "WOSB": "WOSB/EDWOSB", "WOSBSS": "WOSB/EDWOSB", "EDWOSB": "WOSB/EDWOSB", "EDWOSBSS": "WOSB/EDWOSB",
          "VSA": "VOSB", "VSS": "VOSB", "IEE": "other", "ISBEE": "other", "BICiv": "other"}      # same groups as 03_atlas.py
SCOPES = {name: d.assign(setaside_grp=d.type_of_set_aside_code.astype(str).map(sa_map).fillna("other/blank")) for name, d in SCOPES.items()}


def by_scope(col):
    return pd.concat([share_table(d, col).assign(scope=name) for name, d in SCOPES.items()], ignore_index=True)


fyt = by_scope("fy")
fyt.to_csv(os.path.join(RESULTS, "atlas_civilian_by_fy.csv"), index=False)
say("\nBY FISCAL YEAR\n" + fyt.round(4).to_string(index=False))
k = by_scope("award_kind")
k.to_csv(os.path.join(RESULTS, "atlas_civilian_by_kind.csv"), index=False)
say("\nBY AWARD KIND (PO = purchase order, DEF = definitive contract, IDV = contract vehicle)\n" + k.round(4).to_string(index=False))
sa = by_scope("setaside_grp").sort_values(["scope", "share"], ascending=[True, False])
sa.to_csv(os.path.join(RESULTS, "atlas_civilian_by_setaside.csv"), index=False)
say("\nBY SET-ASIDE TYPE\n" + sa.round(4).to_string(index=False))

# ---------------------------------------------------------------------------------------------------
orig = pd.read_csv(os.path.join(RESULTS, "atlas_by_agency.csv"))
orig["agency"] = orig.agency.map(FULL)
chk = orig[["agency", "k_single", "n_eval", "share"]].merge(
    t[["agency", "k_single", "n_eval", "share"]], on="agency", suffixes=("_original", "_new"))
chk["share_change_points"] = 100 * (chk.share_new - chk.share_original)
chk.to_csv(os.path.join(RESULTS, "atlas_civilian_five_check.csv"), index=False)
say("\nCHECK: the original five departments, published download (Oct 24, 2026) vs this download\n" + chk.round(4).to_string(index=False))

open(os.path.join(RESULTS, "atlas_civilian_summary.txt"), "w", encoding="utf-8").write("\n".join(lines) + "\n")
print("\nsaved results to", RESULTS)
