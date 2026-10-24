"""
04_threshold.py - did the pile-up of awards just under the simplified acquisition threshold move on 2025-10-01?

Background for a learner
  * Simplified acquisition threshold (SAT): purchases at or below it can use simpler, faster procedures.
    It was $250,000 until 2025-09-30 and became $350,000 on 2025-10-01 (FAC 2025-06 inflation adjustment).
  * Micro-purchase threshold (MPT): $10,000 until 2025-09-30, $15,000 from 2025-10-01.
  * If buyers keep awards just under a threshold, you see "bunching": more awards in the bins just below the line than
    a smooth curve would predict.  When the line moves, the pile-up should move with it.
  * Bunching is NOT proof of misconduct (buyers may legitimately scope work to fit simpler rules) - it is a screening
    indicator at the market level.

Method (fixed before looking at the post-period results)
  * Universe: every civilian agency (no DoD), base actions (modification 0) of purchase orders and definitive contracts.
    Value = base-and-all-options value at the base action (robustness: obligation at the base action).
  * Bins: $5,000 wide (SAT) / $250 wide (MPT); a bin (a, b] includes its upper edge, so an award of exactly $250,000.00 sits in the last bin below the line.
  * Counterfactual: Poisson regression of bin counts on a degree-5 polynomial in the bin position, fitted WITHOUT the
    bins from 20K below to 10K above the threshold (MPT: 1K below to 0.5K above).  "Bunching ratio" = observed / predicted
    awards in the bins just below the threshold.   1.00 = no excess; 1.30 = 30% more awards than the smooth curve predicts.
  * Uncertainty: 300 bootstrap resamples of the awards.
  * Placebo thresholds with no policy ($200,000 and $450,000) show how big the ratio gets by chance / round numbers.

Input : data/usaspending_universe/*.zip (from 01b_download_threshold_universe.py)
Output: results/threshold_*.csv and results/threshold_summary.txt
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA, RESULTS  # noqa: E402

lines = []


def say(*a):
    s = " ".join(str(x) for x in a)
    print(s)
    lines.append(s)


from bunching_lib import SPEC, boot, bunch, count_bins, load_universe, rng  # noqa: E402



def analyse(df, label, value_col, thresholds, rows, B=300):
    for T in thresholds:
        spec = SPEC[T]
        vals = df[value_col].dropna().values
        e, c = count_bins(vals, spec[0], spec[1], spec[2])
        r = bunch(c, e, T, spec[3], spec[4])
        bs = boot(vals, T, spec, B=B)
        rows.append(dict(period=label, value=value_col, threshold=T, n_in_window=int(c.sum()), obs_below=r["obs_below"], cf_below=round(r["cf_below"], 1),
                         ratio=r["ratio"], ci_lo=np.nanpercentile(bs, 2.5), ci_hi=np.nanpercentile(bs, 97.5),
                         obs_above=r["obs_above"], cf_above=round(r["cf_above"], 1)))
        # keep the raw bin counts for plotting later
        pd.DataFrame({"bin_upper_edge": e[1:], "count": c}).to_csv(
            os.path.join(RESULTS, f"threshold_bins_{label}_{value_col}_{T}.csv".replace(" ", "_")), index=False)


def simple_ratio(vals, T, band):
    below = ((vals > T - band) & (vals <= T)).sum()
    above = ((vals > T) & (vals <= T + band)).sum()
    return below, above, below / max(above, 1)


# --------------------------------------------------------------------------------------------------- main
tx, base = load_universe()
say("=" * 100)
say(f"UNIVERSE: civilian agencies, B+D transactions {len(tx):,}; base actions {len(base):,}; "
    f"{base.awarding_agency_name.nunique()} agencies; action dates {base.action_date.min().date()} .. {base.action_date.max().date()}")
say(base.groupby(["fy", "kind"]).size().unstack().to_string())
say("\nbase actions with a usable value: " + f"{base.base_and_all_options_value.notna().mean():.3f}" + " | value > 0: " + f"{(base.base_and_all_options_value > 0).mean():.3f}")

# period definitions
pre = base[(base.action_date >= "2024-10-01") & (base.action_date <= "2025-09-30")]      # FY2025: old thresholds
post = base[(base.action_date >= "2025-10-01") & (base.action_date <= "2026-09-30")]     # FY2026: new thresholds
fy24 = base[(base.action_date >= "2023-10-01") & (base.action_date <= "2024-09-30")]     # FY2024: old thresholds (stability check)
say(f"\nperiods: FY2024 {len(fy24):,} | FY2025 (pre) {len(pre):,} | FY2026 (post) {len(post):,} base actions")

# ---- raw counts in the bins that matter
say("\nRAW COUNTS (value = base-and-all-options at the base action): awards in the 10K band just below vs just above each line")
raw = []
for lab, d in (("FY2024", fy24), ("FY2025_pre", pre), ("FY2026_post", post)):
    v = d.base_and_all_options_value.dropna().values
    for T in (250_000, 350_000, 200_000, 300_000):
        b, a, r = simple_ratio(v, T, 10_000)
        raw.append(dict(period=lab, threshold=T, below_10K=b, above_10K=a, ratio_below_over_above=round(r, 3)))
raw = pd.DataFrame(raw)
say(raw.pivot(index="threshold", columns="period", values="ratio_below_over_above").to_string())
say(raw.pivot(index="threshold", columns="period", values="below_10K").to_string())
raw.to_csv(os.path.join(RESULTS, "threshold_raw_ratios.csv"), index=False)

# ---- bunching estimates
rows = []
for lab, d in (("FY2024", fy24), ("FY2025_pre", pre), ("FY2026_post", post)):
    analyse(d, lab, "base_and_all_options_value", [250_000, 350_000, 200_000, 450_000], rows)
res = pd.DataFrame(rows)
res.to_csv(os.path.join(RESULTS, "threshold_sat_bunching.csv"), index=False)
say("\nSAT BUNCHING RATIO (observed / counterfactual in the 4 bins ($20K) just under the line; 95% bootstrap interval)")
say(res.round(3).to_string(index=False))

# ---- did the pile-up move?  difference post - pre with a bootstrap interval
say("\nDID IT MOVE?  bunching ratio post minus pre (bootstrap on both periods)")
mv = []
for T in (250_000, 350_000, 200_000, 450_000):
    spec = SPEC[T]
    vpre = pre.base_and_all_options_value.dropna().values
    vpost = post.base_and_all_options_value.dropna().values
    bpre, bpost = boot(vpre, T, spec), boot(vpost, T, spec)
    d = bpost - bpre
    e1, c1 = count_bins(vpre, spec[0], spec[1], spec[2]); e2, c2 = count_bins(vpost, spec[0], spec[1], spec[2])
    r1, r2 = bunch(c1, e1, T, spec[3], spec[4])["ratio"], bunch(c2, e2, T, spec[3], spec[4])["ratio"]
    mv.append(dict(threshold=T, ratio_pre=r1, ratio_post=r2, diff=r2 - r1, diff_lo=np.nanpercentile(d, 2.5), diff_hi=np.nanpercentile(d, 97.5)))
mv = pd.DataFrame(mv)
say(mv.round(3).to_string(index=False))
mv.to_csv(os.path.join(RESULTS, "threshold_sat_moved.csv"), index=False)

# ---- robustness: obligation instead of potential value; FY2026 by half-year; PO vs DEF
rows = []
analyse(pre, "FY2025_pre", "federal_action_obligation", [250_000, 350_000], rows, B=200)
analyse(post, "FY2026_post", "federal_action_obligation", [250_000, 350_000], rows, B=200)
for k in ("PO", "DEF"):
    analyse(pre[pre.kind == k], f"FY2025_pre_{k}", "base_and_all_options_value", [250_000, 350_000], rows, B=200)
    analyse(post[post.kind == k], f"FY2026_post_{k}", "base_and_all_options_value", [250_000, 350_000], rows, B=200)
h1 = post[post.action_date < "2026-04-01"]; h2 = post[post.action_date >= "2026-04-01"]
analyse(h1, "FY2026_H1", "base_and_all_options_value", [250_000, 350_000], rows, B=200)
analyse(h2, "FY2026_H2", "base_and_all_options_value", [250_000, 350_000], rows, B=200)
rob = pd.DataFrame(rows)
rob.to_csv(os.path.join(RESULTS, "threshold_sat_robustness.csv"), index=False)
say("\nROBUSTNESS (obligation instead of value; PO vs DEF; first vs second half of FY2026)")
say(rob.round(3).to_string(index=False))

# ---- quarterly path of the simple ratio (10K band below / above)
base["q"] = base.action_date.dt.to_period("Q").astype(str)
qrows = []
for q, d in base.groupby("q"):
    v = d.base_and_all_options_value.dropna().values
    r = dict(quarter=q, n_awards=len(d))
    for T in (250_000, 350_000):
        b, a, rr = simple_ratio(v, T, 10_000)
        r[f"below_{T//1000}K"], r[f"above_{T//1000}K"], r[f"ratio_{T//1000}K"] = b, a, round(rr, 3)
    qrows.append(r)
qdf = pd.DataFrame(qrows)
qdf.to_csv(os.path.join(RESULTS, "threshold_quarterly_ratios.csv"), index=False)
say("\nQUARTERLY PATH of simple ratio (awards in the 10K band below the line / in the 10K band above)")
say(qdf.to_string(index=False))

# ---- micro-purchase threshold (use obligation; purchase-card buys below the MPT are mostly NOT reported to FPDS)
say("\nMICRO-PURCHASE LINE ($10K -> $15K): reported awards per $250 bin around each line; value = obligation at the base action")
rows = []
for lab, d in (("FY2025_pre", pre), ("FY2026_post", post)):
    d2 = d[d.kind == "PO"]
    analyse(d2, lab + "_PO", "federal_action_obligation", [10_000, 15_000], rows, B=200)
mp = pd.DataFrame(rows)
mp.to_csv(os.path.join(RESULTS, "threshold_mpt.csv"), index=False)
say(mp.round(3).to_string(index=False))
for lab, d in (("FY2025_pre", pre), ("FY2026_post", post)):
    v = d[d.kind == "PO"].federal_action_obligation.dropna().values
    for T in (10_000, 15_000):
        b, a, r = simple_ratio(v, T, 1_000)
        say(f"  {lab} T={T}: awards in ($ {T-1000:,}, {T:,}] = {b}; in ({T:,}, {T+1000:,}] = {a}; ratio below/above = {r:.2f}")

# table of reported purchase orders per $1,000 of obligation, $6K-$20K: the reporting step should move from $10K to $15K
tab = {}
for lab, d in (("FY2025_pre", pre), ("FY2026_post", post)):
    v = d[d.kind == "PO"].federal_action_obligation.dropna().values
    e, c = count_bins(v, 6_000, 20_000, 1_000)
    tab[lab] = c
    tab["bin_upper"] = (e[1:] / 1000).astype(int)
mt = pd.DataFrame(tab)[["bin_upper", "FY2025_pre", "FY2026_post"]]
mt.to_csv(os.path.join(RESULTS, "threshold_mpt_1k_bins.csv"), index=False)
say("\nreported purchase orders per $1,000 bin of obligation (bin = (upper-1K, upper]); columns are counts over the whole fiscal year\n" + mt.to_string(index=False))
n10pre = mt.loc[mt.bin_upper.between(11, 15), "FY2025_pre"].sum(); n10post = mt.loc[mt.bin_upper.between(11, 15), "FY2026_post"].sum()
n15pre = mt.loc[mt.bin_upper.between(16, 20), "FY2025_pre"].sum(); n15post = mt.loc[mt.bin_upper.between(16, 20), "FY2026_post"].sum()
say(f"reported POs in ($10K,$15K]: FY2025 {n10pre} -> FY2026 {n10post} ({100 * (n10post / n10pre - 1):+.1f}%); in ($15K,$20K]: {n15pre} -> {n15post} ({100 * (n15post / n15pre - 1):+.1f}%)")

# ---- EXPLORATORY: did competition change in the band that became "simplified" on 2025-10-01?
# Awards worth $250K-$350K could use simplified (Part 13) procedures only from 2025-10-01.  Compare the share competed under simplified
# procedures (code F) and the single-offer share in that band before/after, against a control band just above ($350K-$450K).
say("\nEXPLORATORY: the band that became 'simplified' ($250K-$350K) versus a control band ($350K-$450K), FY2025 vs FY2026")
COMPETED = {"A", "D", "E", "F", "CDO"}
base["offers"] = pd.to_numeric(base.number_of_offers_received, errors="coerce")
base["competed"] = base.extent_competed_code.isin(COMPETED)
base["eval"] = base.competed & base.offers.between(1, 998)
base["single"] = base.offers == 1
pre = base[(base.action_date >= "2024-10-01") & (base.action_date <= "2025-09-30")]
post = base[(base.action_date >= "2025-10-01") & (base.action_date <= "2026-09-30")]
brow = []
for band, (lo, hi) in {"treated 250-350K": (250_000, 350_000), "control 350-450K": (350_000, 450_000), "below 150-250K": (150_000, 250_000)}.items():
    for lab, d in (("FY2025_pre", pre), ("FY2026_post", post)):
        x = d[(d.base_and_all_options_value > lo) & (d.base_and_all_options_value <= hi)]
        e = x[x["eval"]]
        brow.append(dict(band=band, period=lab, n_awards=len(x), pct_competed=100 * x.competed.mean(), pct_competed_under_SAP_F=100 * (x.extent_competed_code == "F").mean(),
                         n_eval=len(e), single_offer_share=e.single.mean()))
brow = pd.DataFrame(brow)
say(brow.round(3).to_string(index=False))
brow.to_csv(os.path.join(RESULTS, "threshold_competition_bands.csv"), index=False)


def did(col):
    def cell(band, per):
        r = brow[(brow.band == band) & (brow.period == per)].iloc[0]
        return r[col]
    return (cell("treated 250-350K", "FY2026_post") - cell("treated 250-350K", "FY2025_pre")) - (cell("control 350-450K", "FY2026_post") - cell("control 350-450K", "FY2025_pre"))


# bootstrap the difference-in-differences of the single-offer share
def band_vec(d, lo, hi):
    x = d[(d.base_and_all_options_value > lo) & (d.base_and_all_options_value <= hi) & d["eval"]]
    return x.single.values.astype(float)


vt0, vt1 = band_vec(pre, 250_000, 350_000), band_vec(post, 250_000, 350_000)
vc0, vc1 = band_vec(pre, 350_000, 450_000), band_vec(post, 350_000, 450_000)
bs = []
for _ in range(500):
    bs.append((rng.choice(vt1, len(vt1)).mean() - rng.choice(vt0, len(vt0)).mean()) - (rng.choice(vc1, len(vc1)).mean() - rng.choice(vc0, len(vc0)).mean()))
est = (vt1.mean() - vt0.mean()) - (vc1.mean() - vc0.mean())
say(f"difference-in-differences of single-offer share (treated band minus control band, post minus pre) = {100 * est:+.2f} percentage points, 95% bootstrap interval [{100 * np.percentile(bs, 2.5):+.2f}, {100 * np.percentile(bs, 97.5):+.2f}]")
say(f"difference-in-differences of the share competed under simplified procedures (F) = {did('pct_competed_under_SAP_F'):+.2f} percentage points (no interval computed)")

open(os.path.join(RESULTS, "threshold_summary.txt"), "w", encoding="utf-8").write("\n".join(lines))
