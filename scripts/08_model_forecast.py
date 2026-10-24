"""
08_model_forecast.py - train a simple, transparent model of "P(single offer)" and score the open SAM.gov solicitations.

WHAT IS PREDICTED
  For a solicitation that is open today: the probability that the award it produces will be a COMPETED award with exactly ONE offer,
  given that it ends up as a competed award with a usable offers count (extent competed A/D/E/F/CDO, offers 1..998).
  This is a risk indicator about the market for that requirement, not a statement about anyone's conduct.

TRAINING DATA (the "historical slice")
  Population P : one row per solicitation number among FY2024-FY2026 base awards (5 civilian agencies) that were competed with
                 usable offers and carry a solicitation identifier.  Outcome y = 1 if the award had exactly one offer.
  Sample L     : the part of P for which 07_sam_train_lookup.py found the SAM.gov notice (needed for the response-window features).

FEATURES (all known when a solicitation is posted)
  agency, PSC (first 2 characters), NAICS (first 3), set-aside group, solicitation-number letter (Q = RFQ, R = RFP, B = IFB),
  notice type (Solicitation vs Combined Synopsis/Solicitation), office history = shrunk past single-offer rate of the buying office
  (out-of-fold target encoding) and log(office volume), original response window in days (and a "14 days or less" flag),
  whether a presolicitation / sources-sought notice exists for the same solicitation number,
  two keyword flags from the notice title / first 300 characters ("brand name"; sole/single/limited source or proprietary).
  NOT used because they would leak the future: number of amendments, final deadline after extensions.

MODEL: L2-regularised logistic regression (coefficients can be read directly).  Benchmarks: agency-wide rate, office history only.
EVALUATION: out-of-time (train on awards through 2025-09-30, test on FY2026 awards) and 5-fold cross-validation.

Input : data/processed/base_awards.parquet, data/sam/train_lookup.csv, data/sam/open_details.jsonl
Output: results/model_*.csv, forecasts/open_solicitations_forecast.csv  (the pre-registration JSON is written by 10_preregister.py)
"""
import json
import os
import re
import sys
import zlib

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.model_selection import StratifiedKFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA, FORECASTS, RESULTS  # noqa: E402

rng = np.random.default_rng(20261023)
lines = []
SHORT = {"Department of Veterans Affairs": "VA", "Department of Health and Human Services": "HHS", "Department of Homeland Security": "DHS",
         "Department of the Interior": "DOI", "Department of Agriculture": "USDA"}
M_SHRINK = 15          # prior strength for the office rate (pseudo-observations at the agency rate)
CUTOFF = pd.Timestamp("2025-09-30")
N_FOLDS = 5


def say(*a):
    s = " ".join(str(x) for x in a)
    print(s)
    lines.append(s)


def norm(s):
    return "".join(ch for ch in (s or "").upper() if ch.isalnum())


SA_MAP = {"NONE": "none", "": "none", "NAN": "none", "SBA": "small business", "SBP": "small business", "ESB": "small business",
          "RSB": "small business", "8A": "8(a)", "8AN": "8(a)", "HZC": "HUBZone", "HZS": "HUBZone", "SDVOSBC": "SDVOSB",
          "SDVOSBS": "SDVOSB", "WOSB": "WOSB", "WOSBSS": "WOSB", "EDWOSB": "WOSB", "EDWOSBSS": "WOSB", "VSA": "VOSB", "VSS": "VOSB"}


def setaside_group(code):
    c = str(code).upper() if code is not None else "NONE"
    return SA_MAP.get(c, "other")


def sol_letter(sol_norm):
    m = re.match(r"^[A-Z0-9]{6}\d{2}([A-Z])\d", sol_norm or "")
    return m.group(1) if m and m.group(1) in "QRB" else ("other" if m else "unknown")


def window_days(post, deadline):
    p = pd.to_datetime(post, utc=True, errors="coerce", format="ISO8601")
    d = pd.to_datetime(deadline, utc=True, errors="coerce", format="ISO8601")
    return (d - p).dt.total_seconds() / 86400


# ------------------------------------------------------------------------------------------------ population P
aw = pd.read_parquet(os.path.join(DATA, "processed", "base_awards.parquet"))
aw = aw[aw.eval_set & aw.has_sol_id & aw.fy.between(2024, 2026)].copy()
aw["sol_norm"] = aw.solicitation_identifier.map(norm)
aw = aw.sort_values(["sol_norm", "action_date", "value0"], ascending=[True, True, False]).drop_duplicates("sol_norm", keep="first")
aw["y"] = aw.single.astype(int)
aw["office"] = aw.awarding_office_code.astype(str)
aw["agency"] = aw.agency.astype(str)
aw["fold"] = aw.sol_norm.map(lambda s: zlib.crc32(s.encode()) % N_FOLDS)
say(f"Population P: {len(aw):,} solicitations (one per solicitation number) | single-offer share {aw.y.mean():.3f}")
say(aw.groupby("agency").y.agg(["size", "mean"]).round(3).to_string())


def agency_rates(pop):
    return pop.groupby("agency").y.mean().to_dict()


def office_tab(pop):
    g = pop.groupby(["agency", "office"]).y.agg(["sum", "count"])
    return {k: (v["sum"], v["count"]) for k, v in g.iterrows()}


def encode_office(df, tab, ag_rate, global_rate):
    """shrunk office rate (k + m*p_agency)/(n + m), and the office volume n"""
    k = np.array([tab.get((a, o), (0, 0))[0] for a, o in zip(df.agency, df.office)], dtype=float)
    n = np.array([tab.get((a, o), (0, 0))[1] for a, o in zip(df.agency, df.office)], dtype=float)
    p0 = np.array([ag_rate.get(a, global_rate) for a in df.agency])
    return (k + M_SHRINK * p0) / (n + M_SHRINK), n, p0


def add_office_features(L, pop):
    """For rows of L: out-of-fold encoding (rows never see their own fold)."""
    L = L.copy()
    L["office_rate"] = np.nan
    L["office_n"] = np.nan
    L["agency_rate"] = np.nan
    g = pop.y.mean()
    for j in range(N_FOLDS):
        tab, ar = office_tab(pop[pop.fold != j]), agency_rates(pop[pop.fold != j])
        idx = L.fold == j
        r, n, p0 = encode_office(L[idx], tab, ar, g)
        L.loc[idx, ["office_rate", "office_n", "agency_rate"]] = np.c_[r, n, p0]
    return L


# ------------------------------------------------------------------------------------------------ sample L (SAM lookups)
lk = pd.read_csv(os.path.join(DATA, "sam", "train_lookup.csv"), dtype=str, keep_default_na=False)
lk = lk.drop_duplicates("sol_norm", keep="last")                        # a solicitation may have been looked up twice (first version had no keyword flags)
n_noflag = int((~lk.kw_brand.isin(["0", "1"])).sum())
lk = lk[lk.kw_brand.isin(["0", "1"])].copy()                            # keep rows that carry the keyword flags
say(f"lookup rows without keyword flags (still waiting to be redone): {n_noflag}")
lk["found"] = lk.found.astype(int)
lk["kw_brand"] = lk.kw_brand.astype(int)
lk["kw_sole"] = lk.kw_sole.astype(int)
say(f"\nSAM lookups: {len(lk):,} solicitations looked up | found a Solicitation/Combined Synopsis notice: {lk.found.sum():,} "
    f"({100 * lk.found.mean():.1f}%) | any notice with that number: {(lk.n_same.astype(int) > 0).sum():,} ({100 * (lk.n_same.astype(int) > 0).mean():.1f}%)")
say(lk.assign(found=lk.found).groupby(["agency", "fy"]).found.agg(["size", "mean"]).round(3).to_string())
L = aw.merge(lk[lk.found == 1].drop(columns=["agency", "fy"]), on="sol_norm", how="inner")
L["window_orig"] = window_days(L.orig_post, L.orig_deadline)
L["window_final"] = window_days(L.orig_post, L.final_deadline)
bad = L.window_orig.isna() | (L.window_orig < 0) | (L.window_orig > 400)
say(f"Sample L: {len(L):,} matched solicitations; dropped {int(bad.sum())} with a missing/negative/over-400-day original window")
L = L[~bad].copy()
say("original response window (days) in L: " + ", ".join(f"p{q}={L.window_orig.quantile(q / 100):.0f}" for q in (5, 25, 50, 75, 95)))


def build_features(df):
    X = pd.DataFrame(index=df.index)
    X["agency"] = df.agency.values
    psc = df.psc.astype(str).str.upper()
    X["psc2"] = psc.str[:2].values
    X["naics3"] = df.naics.astype(str).str[:3].values
    X["setaside"] = df.setaside.values
    X["sol_letter"] = df.sol_norm.map(sol_letter).values
    X["notice_type"] = df.notice_type.values
    X["office_rate"] = df.office_rate.values
    X["log_office_n"] = np.log1p(df.office_n.values)
    X["wl"] = np.log1p(df.window_orig.clip(0, 120).values)
    X["short14"] = (df.window_orig <= 14).astype(float).values
    X["has_presol"] = df.has_presol.astype(float).values
    X["has_ss"] = df.has_sources_sought.astype(float).values
    X["kw_brand"] = df.kw_brand.astype(float).values
    X["kw_sole"] = df.kw_sole.astype(float).values
    return X


CAT = ["agency", "psc2", "naics3", "setaside", "sol_letter", "notice_type"]
CONT = ["office_rate", "log_office_n", "wl"]                          # continuous: standardised
BIN = ["short14", "has_presol", "has_ss", "kw_brand", "kw_sole"]     # 0/1 flags: NOT standardised (a rare flag would otherwise get a huge z-score and an extreme forecast)
NUM = CONT + BIN


def make_model(C):
    ct = ColumnTransformer([
        ("cat", OneHotEncoder(handle_unknown="infrequent_if_exist", min_frequency=25), CAT),
        ("num", StandardScaler(), CONT),
        ("bin", "passthrough", BIN)])
    return Pipeline([("prep", ct), ("lr", LogisticRegression(C=C, max_iter=2000))])


def prep_L(L):
    L = L.copy()
    L["psc"] = L.product_or_service_code
    L["naics"] = L.naics_code
    L["setaside"] = L.type_of_set_aside_code.map(setaside_group)
    L["notice_type"] = L.primary_type
    return L


def metrics(y, p):
    p = np.clip(p, 0.001, 0.999)
    return dict(n=len(y), auc=roc_auc_score(y, p) if len(set(y)) > 1 else np.nan, brier=brier_score_loss(y, p), logloss=log_loss(y, p, labels=[0, 1]))


def boot_ci(y, p, p_base, B=500):
    y, p, p_base = np.asarray(y), np.asarray(p), np.asarray(p_base)
    aucs, dbrier = [], []
    for _ in range(B):
        i = rng.integers(0, len(y), len(y))
        if len(set(y[i])) < 2:
            continue
        aucs.append(roc_auc_score(y[i], p[i]))
        dbrier.append(brier_score_loss(y[i], np.clip(p_base[i], .001, .999)) - brier_score_loss(y[i], np.clip(p[i], .001, .999)))
    return np.percentile(aucs, [2.5, 97.5]), np.percentile(dbrier, [2.5, 97.5])


L = prep_L(L)

# ------------------------------------------------------------------------------------------------ out-of-time evaluation
say("\n" + "=" * 100)
say(f"OUT-OF-TIME TEST: train on solicitations whose award came on/before {CUTOFF.date()}, test on later ones (FY2026)")
pop_pre = aw[aw.action_date <= CUTOFF]
tr = L[L.action_date <= CUTOFF].copy()
te_ = L[L.action_date > CUTOFF].copy()
say(f"train {len(tr):,} (single-offer share {tr.y.mean():.3f}) | test {len(te_):,} (share {te_.y.mean():.3f})")
tr = add_office_features(tr, pop_pre)
tab_pre, ar_pre = office_tab(pop_pre), agency_rates(pop_pre)
r, n, p0 = encode_office(te_, tab_pre, ar_pre, pop_pre.y.mean())
te_["office_rate"], te_["office_n"], te_["agency_rate"] = r, n, p0
Xtr, Xte = build_features(tr), build_features(te_)
# choose C by cross-validated log loss on the training period only
best = None
for C in (0.03, 0.1, 0.3, 1.0):
    ll = []
    for a, b in StratifiedKFold(5, shuffle=True, random_state=1).split(Xtr, tr.y):
        m = make_model(C).fit(Xtr.iloc[a], tr.y.iloc[a])
        ll.append(log_loss(tr.y.iloc[b], m.predict_proba(Xtr.iloc[b])[:, 1], labels=[0, 1]))
    say(f"  C={C}: 5-fold CV log loss in training period {np.mean(ll):.4f}")
    if best is None or np.mean(ll) < best[1]:
        best = (C, np.mean(ll))
C_BEST = best[0]
say(f"chosen C = {C_BEST}")
mdl = make_model(C_BEST).fit(Xtr, tr.y)
p_model = mdl.predict_proba(Xte)[:, 1]
ag_tr = tr.groupby("agency").y.mean().to_dict()                 # baseline 1: agency-wide rate among SAM-matched training solicitations
p_agency = te_.agency.map(ag_tr).fillna(tr.y.mean()).values
p_office = te_.office_rate.values                               # baseline 2: shrunk office history only
rows = []
for name, p in (("agency-wide rate (naive baseline)", p_agency), ("office history only", p_office), ("logistic regression (this model)", p_model)):
    mm = metrics(te_.y.values, p)
    mm["model"] = name
    rows.append(mm)
# non-transparent benchmark: how much would a gradient-boosted tree add?
hgb = Pipeline([("prep", ColumnTransformer([("cat", OneHotEncoder(handle_unknown="infrequent_if_exist", min_frequency=25, sparse_output=False), CAT), ("num", "passthrough", NUM)], sparse_threshold=0)),
                ("gb", HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=150, random_state=1))])
hgb.fit(Xtr, tr.y)
mm = metrics(te_.y.values, hgb.predict_proba(Xte)[:, 1]); mm["model"] = "gradient boosting (benchmark only)"; rows.append(mm)
oot = pd.DataFrame(rows)[["model", "n", "auc", "brier", "logloss"]]
say(oot.round(4).to_string(index=False))
auc_ci, db_ci = boot_ci(te_.y.values, p_model, p_agency)
say(f"model AUC 95% bootstrap interval {auc_ci.round(3)}; Brier improvement over agency baseline {db_ci.round(4)} (positive = better)")
bs_model, bs_base = oot.brier.iloc[2], oot.brier.iloc[0]
say(f"Brier skill score vs agency baseline = {1 - bs_model / bs_base:.3f}")
cal = pd.DataFrame({"p": p_model, "y": te_.y.values})
cal["bin"] = pd.cut(cal.p, [0, .1, .2, .3, .4, .5, .6, 1.0])
say("calibration on the test period:\n" + cal.groupby("bin", observed=True).agg(n=("y", "size"), mean_forecast=("p", "mean"), observed=("y", "mean")).round(3).to_string())
oot.to_csv(os.path.join(RESULTS, "model_out_of_time.csv"), index=False)

# ablation: does the response window matter?
Xtr2, Xte2 = Xtr.copy(), Xte.copy()
for c in ("wl", "short14"):
    Xtr2[c], Xte2[c] = 0.0, 0.0
m2 = make_model(C_BEST).fit(Xtr2, tr.y)
say(f"ablation: without window features AUC {roc_auc_score(te_.y, m2.predict_proba(Xte2)[:, 1]):.3f} vs with {roc_auc_score(te_.y, p_model):.3f}")
for drop in (["office_rate", "log_office_n"], ["kw_brand", "kw_sole"]):
    Xtr3, Xte3 = Xtr.copy(), Xte.copy()
    for c in drop:
        Xtr3[c], Xte3[c] = 0.0, 0.0
    m3 = make_model(C_BEST).fit(Xtr3, tr.y)
    say(f"ablation: without {drop} AUC {roc_auc_score(te_.y, m3.predict_proba(Xte3)[:, 1]):.3f}")

# ------------------------------------------------------------------------------------------------ 5-fold CV over all of L (secondary check)
Lall = add_office_features(L.copy(), aw)
Xall = build_features(Lall)
oof = np.zeros(len(Lall))
for a, b in StratifiedKFold(N_FOLDS, shuffle=True, random_state=2).split(Xall, Lall.y):
    oof[b] = make_model(C_BEST).fit(Xall.iloc[a], Lall.y.iloc[a]).predict_proba(Xall.iloc[b])[:, 1]
cvm = metrics(Lall.y.values, oof)
base_oof = np.zeros(len(Lall))
for a, b in StratifiedKFold(N_FOLDS, shuffle=True, random_state=2).split(Xall, Lall.y):
    rates = Lall.iloc[a].groupby("agency").y.mean()
    base_oof[b] = Lall.iloc[b].agency.map(rates).fillna(Lall.y.iloc[a].mean()).values
cvb = metrics(Lall.y.values, base_oof)
say(f"\n5-fold CV on all of L: model AUC {cvm['auc']:.3f} Brier {cvm['brier']:.4f} | agency-rate baseline Brier {cvb['brier']:.4f} (skill {1 - cvm['brier'] / cvb['brier']:.3f})")

# ------------------------------------------------------------------------------------------------ final fit on everything and coefficient table
mdl_f = make_model(C_BEST).fit(Xall, Lall.y)
names = mdl_f.named_steps["prep"].get_feature_names_out()
coef = pd.DataFrame({"feature": names, "coef": mdl_f.named_steps["lr"].coef_[0]})
coef["odds_ratio"] = np.exp(coef.coef)
coef = coef.reindex(coef.coef.abs().sort_values(ascending=False).index)
coef.to_csv(os.path.join(RESULTS, "model_coefficients.csv"), index=False)
say("\nLargest coefficients of the final model (standardised numeric features; one-hot categories):")
say(coef.head(18).round(3).to_string(index=False))

# ------------------------------------------------------------------------------------------------ live: open solicitations
op = pd.DataFrame([json.loads(l) for l in open(os.path.join(DATA, "sam", "open_details.jsonl"), encoding="utf-8")])
op["sol_norm"] = op.sol.map(norm)
# open_details.jsonl keeps old versions of amended notices; only the notice ids of the NEWEST search pull are current
latest = {}
for l in open(os.path.join(DATA, "sam", "open_search_all.jsonl"), encoding="utf-8"):
    j = json.loads(l)
    latest[j["notice_id"]] = j["pulled_at"]
latest_ids = set(latest)
op = op[op.notice_id.isin(latest_ids)].copy()
op["pulled_at"] = op.notice_id.map(latest)                                # time of the newest search pull that still listed this notice
op = op.sort_values("publish").drop_duplicates("sol_norm", keep="last")
op["deadline_dt"] = pd.to_datetime(op.response, utc=True, format="ISO8601")
op["window_orig"] = window_days(op.orig_post, op.orig_deadline)
op["orig_post_dt"] = pd.to_datetime(op.orig_post, utc=True, errors="coerce", format="ISO8601")
base_ok = (op.type_code.isin(["o", "k"])) & (~op.canceled) & (op.active) & op.deadline_dt.between("2026-11-01", "2026-12-16", inclusive="left")
# data-quality rule fixed in advance: a notice must have been first posted on/after 2026-06-01 and show a plausible original window
# (0-120 days).  This removes typo'd deadlines (e.g. a notice posted Nov 2025 with a deadline in Dec 2026), standing/continuous
# solicitations and notices whose first version has a deadline before its posting date.
plausible = (op.orig_post_dt >= "2026-06-01") & op.window_orig.between(0, 120)
elig = op[base_ok & plausible].copy()
say(f"\nOPEN SOLICITATIONS: {len(op)} candidates in the five agencies | open, not canceled, type o/k, deadline 2026-11-01..2026-12-15: {int(base_ok.sum())} | "
    f"excluded by the date-plausibility rule: {int((base_ok & ~plausible).sum())} | ELIGIBLE: {len(elig)}")
say("original window (days) of open solicitations: " + ", ".join(f"p{q}={elig.window_orig.quantile(q / 100):.0f}" for q in (5, 25, 50, 75, 95)))
elig["agency_full"] = elig.usas_agency                                 # USAspending name (used by the resolver)
elig["agency"] = elig.usas_agency.map(SHORT)                           # short name (used in the training tables)
elig["office"] = elig.office_code.astype(str)
elig["setaside"] = elig.set_aside.map(setaside_group)
elig["notice_type"] = np.where(elig.type_code == "k", "Combined Synopsis/Solicitation", "Solicitation")
elig["has_presol"] = elig.notice_types_seen.map(lambda t: int("Presolicitation" in t))
elig["has_sources_sought"] = elig.notice_types_seen.map(lambda t: int("Sources Sought" in t))
elig["window_orig"] = elig.window_orig.fillna(elig.window_orig.median())
elig["kw_brand"] = elig.kw_brand.fillna(0).astype(int)
elig["kw_sole"] = elig.kw_sole.fillna(0).astype(int)
tab_all, ar_all = office_tab(aw), agency_rates(aw)
r, n, p0 = encode_office(elig, tab_all, ar_all, aw.y.mean())
elig["office_rate"], elig["office_n"], elig["agency_rate_all"] = r, n, p0
known = (elig.office_n > 0).mean()
say(f"office join: {int((elig.office_n > 0).sum())} of {len(elig)} open solicitations ({100 * known:.1f}%) have a buying office with history in the atlas population")
Xlive = build_features(elig)
elig["p_model"] = mdl_f.predict_proba(Xlive)[:, 1]
# baseline: agency-wide single-offer rate among SAM-matched solicitations (FY2026 awards when at least 30, else all years)
ag26 = {}
for a_, g_ in Lall.groupby("agency"):
    g26 = g_[g_.fy == 2026]
    ag26[a_] = float(g26.y.mean() if len(g26) >= 30 else g_.y.mean())
elig["p_baseline_agency"] = elig.agency.map(ag26)
elig["p_office_history"] = elig.office_rate
say("\nforecast summary by agency (mean P(single offer): model vs agency baseline):")
say(elig.groupby("agency").agg(n=("sol", "size"), model=("p_model", "mean"), baseline=("p_baseline_agency", "mean"),
                               min=("p_model", "min"), max=("p_model", "max")).round(3).to_string())

# ---- choose the registered set: at most 150, stratified by agency, seeded random
TARGET = 150
cnt = elig.agency.value_counts()
alloc = (cnt / cnt.sum() * TARGET).round().astype(int).clip(lower=min(8, cnt.min()))
alloc = alloc.clip(upper=cnt)
while alloc.sum() > TARGET:
    alloc[alloc.idxmax()] -= 1
sel = pd.concat([g.sample(n=int(alloc[a]), random_state=20261023) for a, g in elig.groupby("agency")])
say(f"registered set: {len(sel)} of {len(elig)} eligible (allocation {alloc.to_dict()})")
sel["in_registered_set"] = True
elig["in_registered_set"] = elig.notice_id.isin(sel.notice_id)

keep = ["sol", "notice_id", "agency", "agency_full", "office", "office_chain", "type", "deadline_dt", "psc", "naics", "set_aside", "window_orig", "office_n",
        "office_rate", "p_model", "p_baseline_agency", "p_office_history", "in_registered_set", "pulled_at"]
out = elig[keep].rename(columns={"sol": "solicitation_number", "type": "notice_type", "deadline_dt": "response_deadline_utc"})
out["response_deadline_utc"] = out.response_deadline_utc.dt.strftime("%Y-%m-%dT%H:%M:%SZ")
out = out.sort_values(["in_registered_set", "agency", "solicitation_number"], ascending=[False, True, True])
out.to_csv(os.path.join(FORECASTS, "open_solicitations_forecast_all_eligible.csv"), index=False)
reg = out[out.in_registered_set].copy()
reg.to_csv(os.path.join(FORECASTS, "open_solicitations_forecast_registered.csv"), index=False)
say(f"saved forecasts: {len(out)} eligible rows; {len(reg)} registered rows")

# model card numbers for the pre-registration file
card = dict(n_population=int(len(aw)), n_sample_L=int(len(L)), n_train_oot=int(len(tr)), n_test_oot=int(len(te_)), C=C_BEST,
            oot=oot.round(4).to_dict(orient="records"), oot_auc_ci=[float(x) for x in auc_ci], oot_brier_gain_ci=[float(x) for x in db_ci],
            cv5=dict(auc=cvm["auc"], brier=cvm["brier"], brier_baseline=cvb["brier"]),
            office_join_share=float(known), baseline_agency_rate_fy2026={k: float(v) for k, v in ag26.items()})
json.dump(card, open(os.path.join(RESULTS, "model_card.json"), "w"), indent=1, default=float)
open(os.path.join(RESULTS, "model_summary.txt"), "w", encoding="utf-8").write("\n".join(lines))
