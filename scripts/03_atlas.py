"""
03_atlas.py - the single-offer atlas slice: headline shares by agency, by buying office, and a first look at a "premium".

Definitions (set in 02_build_awards.py)
  eval_set = base award that was COMPETED and has a usable number_of_offers_received (1..998)
  single   = exactly one offer
  single-offer share = single / eval_set          (this is a RISK INDICATOR, not proof of anything)

Outputs (results/):
  atlas_by_agency.csv, atlas_by_agency_fy.csv, atlas_by_kind.csv, atlas_offices.csv, atlas_by_setaside.csv,
  atlas_by_size.csv, atlas_by_quarter.csv, premium_*.csv   and a printed summary (also saved as atlas_summary.txt)
"""
import os
import sys

import numpy as np
import pandas as pd
import statsmodels.api as sm
from statsmodels.stats.proportion import proportion_confint

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA, RESULTS  # noqa: E402

base = pd.read_parquet(os.path.join(DATA, "processed", "base_awards.parquet"))
base = base[base.fy.between(2024, 2026)].copy()
ev = base[base.eval_set].copy()
lines = []


def say(*a):
    s = " ".join(str(x) for x in a)
    print(s)
    lines.append(s)


def share_table(df, by):
    """single-offer share with a 95% Wilson interval for each group."""
    g = df.groupby(by, observed=True)["single"].agg(["sum", "count"]).reset_index()
    g.columns = list(g.columns[:-2]) + ["k_single", "n_eval"]
    lo, hi = proportion_confint(g.k_single, g.n_eval, alpha=0.05, method="wilson")
    g["share"] = g.k_single / g.n_eval
    g["ci_lo"], g["ci_hi"] = lo, hi
    return g


# ---------------------------------------------------------------------------------------------------
say("=" * 100)
say("COVERAGE  (FY2024-FY2026 base awards: purchase orders, definitive contracts, IDV awards; 5 civilian agencies)")
cov = base.groupby("agency", observed=True).agg(
    n_base=("award_kind", "size"), n_competed=("competed", "sum"), n_offers_ok=("offers_ok", "sum"), n_eval=("eval_set", "sum"))
cov["pct_competed"] = 100 * cov.n_competed / cov.n_base
cov["pct_eval_of_competed"] = 100 * cov.n_eval / cov.n_competed
say(cov.round(1).to_string())
say(f"TOTAL base awards {len(base):,} | competed {int(base.competed.sum()):,} ({100*base.competed.mean():.1f}%) | "
    f"eval set {int(base.eval_set.sum()):,} ({100*base.eval_set.sum()/base.competed.sum():.1f}% of competed)")
cov.to_csv(os.path.join(RESULTS, "atlas_coverage.csv"))

# offers fill rate by kind among competed (cycle 2 said: ~100% PO/DEF, ~88% IDV)
fill = base[base.competed].groupby("award_kind", observed=True).agg(n=("offers_ok", "size"), pct_offers_ok=("offers_ok", lambda s: 100 * s.mean()))
say("\nOffers usable among COMPETED base awards, by kind:\n" + fill.round(1).to_string())
sent = (base[base.competed].offers == 999).sum()
say(f"999 sentinel among competed base awards: {sent}  | blank offers among competed: {int(base[base.competed].offers.isna().sum())}")

# ---------------------------------------------------------------------------------------------------
say("\n" + "=" * 100)
say("HEADLINE 1: single-offer share among competed awards, by agency (all kinds pooled, FY2024-FY2026)")
t = share_table(ev, "agency")
say(t.round(4).to_string(index=False))
allp = share_table(ev.assign(all="ALL"), "all")
say(allp.round(4).to_string(index=False))
t.to_csv(os.path.join(RESULTS, "atlas_by_agency.csv"), index=False)

say("\nby agency x fiscal year")
t2 = share_table(ev, ["agency", "fy"])
say(t2.round(4).to_string(index=False))
t2.to_csv(os.path.join(RESULTS, "atlas_by_agency_fy.csv"), index=False)

say("\nby award kind (PO = purchase order, DEF = definitive contract, IDV = indefinite-delivery vehicle)")
t3 = share_table(ev, ["agency", "award_kind"])
say(t3.round(4).to_string(index=False))
t3.to_csv(os.path.join(RESULTS, "atlas_by_kind.csv"), index=False)

say("\nrobustness: only A (full and open) + D (full and open after exclusion of sources) -- excludes SAP-competed (F), follow-on (E), CDO")
ad = ev[ev.extent_competed_code.isin(["A", "D"])]
say(share_table(ad, "agency").round(4).to_string(index=False))
say("\nby extent-competed code (all five agencies)")
say(share_table(ev, "extent_competed_code").round(4).to_string(index=False))

# dollar-weighted view, comparable in spirit to GAO-10-833 (about 13% of competed obligations FY2005-FY2009) and to the Fed-Spend blog
# (17.0% of 453 competed $20M+ contracts expiring by Sep 2027 had one offer).  PO + DEF only: an IDV record carries its ceiling, not the money obligated.
po_def = ev[ev.award_kind.isin(["PO", "DEF"]) & (ev.obligation_sum_window > 0)]
dw = po_def.groupby("agency", observed=True).apply(
    lambda g: pd.Series({"obligations_competed_$M": g.obligation_sum_window.sum() / 1e6,
                         "obligations_single_offer_$M": g.loc[g.single, "obligation_sum_window"].sum() / 1e6,
                         "dollar_weighted_share": g.loc[g.single, "obligation_sum_window"].sum() / g.obligation_sum_window.sum(),
                         "count_share": g.single.mean()}), include_groups=False)
dw_all = pd.Series({"dollar_weighted_share": po_def.loc[po_def.single, "obligation_sum_window"].sum() / po_def.obligation_sum_window.sum(), "count_share": po_def.single.mean()})
say("\ndollar-weighted single-offer share (purchase orders + definitive contracts; obligations to date within the window)")
say(dw.round(3).to_string())
say(f"ALL FIVE: dollar-weighted {dw_all.dollar_weighted_share:.3f} vs count-based {dw_all.count_share:.3f}")
big = ev[ev.award_kind.isin(["PO", "DEF"]) & (ev.value0 >= 20_000_000)]
bigad = big[big.extent_competed_code.isin(["A", "D"])]
say(f"competed PO/DEF awards with base-and-all-options value >= $20M: n={len(big)}, single-offer share {big.single.mean():.3f}"
    f" | full-and-open (A/D) only: n={len(bigad)}, share {bigad.single.mean():.3f}" if len(big) else "no PO/DEF awards of $20M+ in the eval set")

# ---------------------------------------------------------------------------------------------------
say("\n" + "=" * 100)
say("HEADLINE 2: buying offices (office = contracting office code; n_eval = competed awards with usable offers)")
off = ev.groupby(["agency", "awarding_office_code", "awarding_office_name"], observed=True)["single"].agg(["sum", "count"]).reset_index()
off.columns = ["agency", "office_code", "office_name", "k_single", "n_eval"]
off["share"] = off.k_single / off.n_eval


def eb_shrink(g):
    """Empirical-Bayes shrinkage of office shares toward the agency mean (beta-binomial, method of moments)."""
    m = g[g.n_eval >= 10]
    p = m.k_single.sum() / m.n_eval.sum()
    obs_var = np.average((m.share - p) ** 2, weights=m.n_eval)
    exp_var = p * (1 - p) * np.average(1 / m.n_eval, weights=m.n_eval)
    between = max(obs_var - exp_var, 1e-4)
    strength = np.clip(p * (1 - p) / between - 1, 2, 200)      # alpha + beta
    a, b = p * strength, (1 - p) * strength
    g = g.copy()
    g["agency_share"] = p
    g["prior_strength"] = strength
    g["share_shrunk"] = (g.k_single + a) / (g.n_eval + strength)
    return g


off = pd.concat([eb_shrink(g) for _, g in off.groupby("agency", observed=True)])
lo, hi = proportion_confint(off.k_single, off.n_eval, alpha=0.05, method="wilson")
off["ci_lo"], off["ci_hi"] = lo, hi
off = off.sort_values(["agency", "share"], ascending=[True, False])
off.to_csv(os.path.join(RESULTS, "atlas_offices.csv"), index=False)
say(f"offices with at least one eval award: {len(off)} | with n_eval >= 100: {(off.n_eval >= 100).sum()} | with n_eval >= 50: {(off.n_eval >= 50).sum()}")
big = off[off.n_eval >= 100].sort_values("share", ascending=False)
say("\nTop 12 offices by raw single-offer share (n_eval >= 100):")
say(big.head(12)[["agency", "office_code", "office_name", "n_eval", "k_single", "share", "ci_lo", "ci_hi", "agency_share", "share_shrunk"]].round(3).to_string(index=False))
say("\nLowest 8 offices (n_eval >= 100):")
say(big.tail(8)[["agency", "office_code", "office_name", "n_eval", "k_single", "share", "ci_lo", "ci_hi", "agency_share", "share_shrunk"]].round(3).to_string(index=False))
say("\nSpread of office shares (n_eval >= 100): min %.3f  p25 %.3f  median %.3f  p75 %.3f  max %.3f" % tuple(
    big.share.quantile([0, .25, .5, .75, 1]).values))

# persistence of office rates: FY2024-25 vs FY2026 (does an office's past rate predict its next year? -> why office history is a model feature)
a = ev[ev.fy <= 2025].groupby("awarding_office_code", observed=True)["single"].agg(["sum", "count"])
b = ev[ev.fy == 2026].groupby("awarding_office_code", observed=True)["single"].agg(["sum", "count"])
j = a.join(b, lsuffix="_prior", rsuffix="_fy26", how="inner")
j = j[(j.count_prior >= 30) & (j.count_fy26 >= 30)]
j["p_prior"] = j.sum_prior / j.count_prior
j["p_fy26"] = j.sum_fy26 / j.count_fy26
if len(j) > 5:
    r = np.corrcoef(j.p_prior, j.p_fy26)[0, 1]
    say(f"\nPersistence: across {len(j)} offices with >= 30 eval awards in both periods, corr(FY24-25 share, FY26 share) = {r:.2f}")

# ---------------------------------------------------------------------------------------------------
say("\n" + "=" * 100)
say("DRIVERS (descriptive, all five agencies pooled)")
sa_map = {"NONE": "no set-aside", "SBA": "small business", "SBP": "small business", "ESB": "small business", "RSB": "small business",
          "8A": "8(a)", "8AN": "8(a)", "HZC": "HUBZone", "HZS": "HUBZone", "SDVOSBC": "SDVOSB", "SDVOSBS": "SDVOSB",
          "WOSB": "WOSB/EDWOSB", "WOSBSS": "WOSB/EDWOSB", "EDWOSB": "WOSB/EDWOSB", "EDWOSBSS": "WOSB/EDWOSB",
          "VSA": "VOSB", "VSS": "VOSB", "IEE": "other", "ISBEE": "other", "BICiv": "other"}
ev["setaside_grp"] = ev.type_of_set_aside_code.astype(str).map(sa_map).fillna("other/blank")
t4 = share_table(ev, "setaside_grp")
say(t4.round(4).to_string(index=False))
t4.to_csv(os.path.join(RESULTS, "atlas_by_setaside.csv"), index=False)
bins = [0, 25_000, 100_000, 250_000, 1_000_000, 10_000_000, np.inf]
labels = ["<25K", "25-100K", "100-250K", "250K-1M", "1-10M", ">10M"]
ev["size_band"] = pd.cut(ev.value0.clip(lower=0), bins=bins, labels=labels, right=False)
t5 = share_table(ev, "size_band")
say("\nby size of award (base-and-all-options value at the base action)\n" + t5.round(4).to_string(index=False))
t5.to_csv(os.path.join(RESULTS, "atlas_by_size.csv"), index=False)
ev["quarter"] = ev.action_date.dt.to_period("Q").astype(str)
t6 = share_table(ev, "quarter")
say("\nby calendar quarter of the base action\n" + t6.round(4).to_string(index=False))
t6.to_csv(os.path.join(RESULTS, "atlas_by_quarter.csv"), index=False)
t7 = share_table(ev, "solicitation_procedures_code")
say("\nby solicitation procedure (SP1 simplified acquisition, NP negotiated proposal, SB sealed bid, AE architect-engineer, MAFO multiple-award/fair opportunity ...)\n" + t7.round(4).to_string(index=False))
t8 = share_table(ev, "has_sol_id")
say("\nwith / without a solicitation identifier on the award\n" + t8.round(4).to_string(index=False))

# ---------------------------------------------------------------------------------------------------
say("\n" + "=" * 100)
say("PRELIMINARY 'PREMIUM' INDICATORS  (FPDS has no quantities or unit prices, so a true price premium cannot be computed here)")
pp = ev[ev.award_kind.isin(["PO", "DEF"]) & (ev.value0 > 0)].copy()
pp["log_value0"] = np.log(pp.value0)
pp["psc2"] = pp.product_or_service_code.astype(str).str[:2]
pp["psc4"] = pp.product_or_service_code.astype(str).str[:4]
pp["growth"] = (pp.value_all_options_max / pp.value0) - 1
pp["growth25"] = (pp.growth >= 0.25).astype(float)
pp["growth_w"] = pp.growth.clip(0, 3)
pp["oblig_over_initial"] = (pp.obligation_sum_window / pp.value0)
pp["single_f"] = pp.single.astype(float)


def fe_ols(df, y, cells, cluster="awarding_office_code", min_cell=5):
    """Regress y on `single` with cell fixed effects (done by subtracting cell means) and standard errors clustered by office.
    Cells with fewer than `min_cell` awards or with no variation in `single` carry no information and are dropped."""
    d = df.dropna(subset=[y]).copy()
    d["cell"] = d[cells].astype(str).agg("|".join, axis=1)
    cs = d.groupby("cell")["single_f"].agg(["size", "mean"])
    ok = cs[(cs["size"] >= min_cell) & (cs["mean"] > 0) & (cs["mean"] < 1)].index
    d = d[d.cell.isin(ok)]
    yd = d[y] - d.groupby("cell")[y].transform("mean")
    xd = d["single_f"] - d.groupby("cell")["single_f"].transform("mean")
    res = sm.OLS(yd.values, xd.values.reshape(-1, 1)).fit(cov_type="cluster", cov_kwds={"groups": pd.factorize(d[cluster].astype(str))[0]})
    ymean_multi = d.loc[d.single_f == 0, y].mean()
    return dict(outcome=y, n=len(d), n_cells=d.cell.nunique(), n_single=int(d.single_f.sum()), coef=float(res.params[0]),
                se=float(res.bse[0]), lo=float(res.conf_int()[0, 0]), hi=float(res.conf_int()[0, 1]), mean_multi_offer=float(ymean_multi))


rows = []
# (1) size difference: how much bigger/smaller are single-offer awards than multi-offer awards for the same agency x year x PSC4 x kind?
r = fe_ols(pp, "log_value0", ["agency", "fy", "award_kind", "psc4"]); r["spec"] = "log award value; cells agency x FY x kind x PSC4"; rows.append(r)
# (2) growth of the potential value after award (needs months to pass -> control by FY inside the cell)
for kind in ("PO", "DEF"):
    sub = pp[pp.award_kind == kind]
    pp_size = sub.copy()
    pp_size["size_dec"] = pd.qcut(pp_size.log_value0, 5, labels=False, duplicates="drop")
    for y in ("growth25", "growth_w"):
        r = fe_ols(pp_size, y, ["agency", "fy", "psc2", "size_dec"]); r["spec"] = f"{kind}: {y}; cells agency x FY x PSC2 x value quintile"; rows.append(r)
prem = pd.DataFrame(rows)
prem.to_csv(os.path.join(RESULTS, "premium_prelim.csv"), index=False)
say(prem[["spec", "n", "n_cells", "n_single", "coef", "se", "lo", "hi", "mean_multi_offer"]].round(4).to_string(index=False))
b0 = prem.iloc[0]
say(f"\nReading: within the same agency x year x PSC4 x kind, single-offer awards are {100*(np.exp(b0.coef)-1):.1f}% "
    f"({100*(np.exp(b0.lo)-1):.1f}% to {100*(np.exp(b0.hi)-1):.1f}%) different in initial award value than multi-offer awards. "
    "This is a SIZE difference (not a price), reported only because it shows what a comparison on FPDS value alone can and cannot say.")

# raw growth comparison with plain numbers
say("\nShare of PO / DEF awards whose potential value grew >= 25% after the base action (raw, no controls):")
rg = pp.groupby(["award_kind", "single"], observed=True)["growth25"].agg(["mean", "size"]).round(4)
say(rg.to_string())

open(os.path.join(RESULTS, "atlas_summary.txt"), "w", encoding="utf-8").write("\n".join(lines))
print("\nsaved results to", RESULTS)
