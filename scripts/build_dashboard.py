"""
build_dashboard.py - build the public dashboard page (docs/index.html) from the files in results/ and forecasts/.

Run from the repository root:
    python -I scripts/build_dashboard.py
It reads only local CSV/JSON files (no downloads), copies the charts into docs/figures/, and writes docs/index.html.
GitHub Pages serves the docs/ folder as the public website.
Scope: civilian agencies (DoD is not included); the deeper single-offer analysis and the forecasts cover five departments.
"""
import csv
import html
import json
import os
import re
import shutil
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, "results")
FC = os.path.join(ROOT, "forecasts")
DOCS = os.path.join(ROOT, "docs")
REPO = "https://github.com/e-burress/federal-contract-competition"
NAMES = {"HHS": "Health and Human Services", "VA": "Veterans Affairs", "DHS": "Homeland Security",
         "DOI": "Interior", "USDA": "Agriculture"}
KINDS = {"PO": "Purchase orders", "DEF": "Definitive contracts", "IDV": "Contract vehicles (IDVs)"}


def rows(name):
    with open(os.path.join(RES, name), encoding="utf-8") as f:
        return list(csv.DictReader(f))


def pct(x, d=1):
    return f"{100 * float(x):.{d}f}%"


def esc(s):
    return html.escape(str(s))


# ---- data -----------------------------------------------------------------------------------
agency = sorted(rows("atlas_by_agency.csv"), key=lambda r: -float(r["share"]))
fy = rows("atlas_by_agency_fy.csv")
kind = rows("atlas_by_kind.csv")
setaside = sorted(rows("atlas_by_setaside.csv"), key=lambda r: -float(r["share"]))
moved = {int(float(r["threshold"])): r for r in rows("threshold_sat_moved.csv")}
model = {r["model"]: r for r in rows("model_out_of_time.csv")}
total_k = sum(int(r["k_single"]) for r in agency)
total_n = sum(int(r["n_eval"]) for r in agency)
f2 = json.load(open(os.path.join(FC, "PIO-F2-threshold-fy2027q1-20261024.json"), encoding="utf-8"))
hashes = {}
for line in open(os.path.join(FC, "SHA256SUMS.txt"), encoding="utf-8"):
    parts = line.split()
    if len(parts) >= 2:
        hashes[parts[1]] = parts[0]

ours = next(v for k, v in model.items() if k.startswith("logistic"))
base = next(v for k, v in model.items() if k.startswith("agency-wide"))


def short_name(name):
    """'Department of the Interior' -> 'Interior'"""
    return re.sub(r"^Department of (the )?", "", name)


def nice(d):
    return date.fromisoformat(d).strftime("%b %d, %Y").replace(" 0", " ")


# ---- every civilian agency (03b_atlas_civilian.py); the section is left out until those results exist -------
CIV_MIN = 200
civ_html = civ_kpi = ""
if os.path.exists(os.path.join(RES, "atlas_civilian_by_agency.csv")):
    civ = rows("atlas_civilian_by_agency.csv")
    civ_fy = rows("atlas_civilian_by_agency_fy.csv")
    civ_tot = {r["scope"]: r for r in rows("atlas_civilian_totals.csv")}
    allc, exg = civ_tot["all civilian agencies"], civ_tot["all civilian agencies except GSA"]
    gsa, oth = civ_tot["GSA only"], civ_tot["other civilian agencies except GSA"]
    smallc = civ_tot[f"agencies with fewer than {CIV_MIN} competed awards"]
    max_change = max(abs(float(r["share_change_points"])) for r in rows("atlas_civilian_five_check.csv"))
    shown = sorted((r for r in civ if int(r["n_eval"]) >= CIV_MIN), key=lambda r: -float(r["share"]))
    top = max(float(r["share"]) for r in shown)
    civ_rows = []
    for r in shown:
        yrs = {x["fy"]: x for x in civ_fy if x["agency"] == r["agency"]}
        trend = " → ".join(pct(yrs[y]["share"]) for y in sorted(yrs))
        ab = f" ({esc(r['abbreviation'])})" if r["abbreviation"] else ""
        five = " <span class='pill'>section 2</span>" if r["in_original_five"] == "True" else ""
        civ_rows.append(
            f"<tr><td>{esc(short_name(r['agency']))}{ab}{five}</td>"
            f"<td data-v='{r['share']}'><span class='bar'><i style='width:{100 * float(r['share']) / top:.1f}%'></i></span><b>{pct(r['share'])}</b></td>"
            f"<td>{pct(r['ci_lo'])}–{pct(r['ci_hi'])}</td><td data-v='{r['n_eval']}'>{int(r['n_eval']):,}</td><td>{trend}</td></tr>")
    civ_kpi = (f'<div class="card kpi"><b>{pct(exg["share"])}</b><span>of {int(exg["n_eval"]):,} competed awards across '
               f'{exg["n_agencies"]} civilian agencies (all except GSA) got only one offer</span></div>')
    civ_html = f"""
<h2>3. Every civilian agency</h2>
<div class="card"><p class="sub" style="margin:0 0 12px">Across all {allc['n_agencies']} civilian agencies, <b>{pct(allc['share'])}</b> of {int(allc['n_eval']):,} competed awards got a single offer. GSA accounts for {pct(int(gsa['n_eval']) / int(allc['n_eval']), 0)} of those awards, mostly very small supply orders (median ${float(gsa['median_award_value']):,.0f}), so without GSA the share is <b>{pct(exg['share'])}</b> ({int(exg['n_eval']):,} awards). Outside GSA and the five departments above, it is <b>{pct(oth['share'])}</b> ({int(oth['n_eval']):,} awards).</p>
<div class="tablewrap"><table class="sortable civ">
  <thead><tr><th>Agency</th><th>Single-offer share</th><th>95% interval</th><th>Competed awards</th><th>FY2024 → FY2025 → FY2026</th></tr></thead>
  <tbody>{''.join(civ_rows)}</tbody></table></div>
  <p class="note">Tap a column heading to sort. Agencies with at least {CIV_MIN} competed awards with a usable offer count; the other {smallc['n_agencies']} smaller agencies together: {pct(smallc['share'])} of {int(smallc['n_eval']):,} awards. Same definitions as section 2. GSA's drop comes almost entirely from its supply orders, which recorded one offer on 55% of orders in FY2024 but 17% in FY2026; that may reflect a change in how those automated orders are recorded rather than more competition. Downloaded {nice(allc['snapshot'])}: agencies kept reporting late awards after the first analysis, so in this newer download the five departments' shares differ from section 2 by at most {max_change:.1f} percentage points.</p>
</div>"""

# ---- tables ---------------------------------------------------------------------------------
dept_rows = []
for r in agency:
    yrs = {x["fy"]: x for x in fy if x["agency"] == r["agency"]}
    trend = " → ".join(pct(yrs[y]["share"]) for y in sorted(yrs))
    dept_rows.append(
        f"<tr><td>{esc(NAMES.get(r['agency'], r['agency']))} ({esc(r['agency'])})</td>"
        f"<td data-v='{r['share']}'><b>{pct(r['share'])}</b></td>"
        f"<td data-v='{r['n_eval']}'>{int(r['n_eval']):,}</td><td>{trend}</td></tr>")

kind_rows = []
for r in sorted(kind, key=lambda r: (r["agency"], r["award_kind"])):
    kind_rows.append(
        f"<tr><td>{esc(r['agency'])}</td><td>{esc(KINDS.get(r['award_kind'], r['award_kind']))}</td>"
        f"<td data-v='{r['share']}'>{pct(r['share'])}</td><td data-v='{r['n_eval']}'>{int(r['n_eval']):,}</td></tr>")

sa_rows = []
for r in setaside:
    sa_rows.append(
        f"<tr><td>{esc(r['setaside_grp'])}</td><td data-v='{r['share']}'>{pct(r['share'])}</td>"
        f"<td data-v='{r['n_eval']}'>{int(r['n_eval']):,}</td></tr>")

q350 = next(q for q in f2["quantities"] if q["name"] == "bunching_ratio_350K")
q250 = next(q for q in f2["quantities"] if q["name"] == "bunching_ratio_250K")
trk_path = os.path.join(RES, f"tracking_{f2['id']}.json")
trk = json.load(open(trk_path, encoding="utf-8")) if os.path.exists(trk_path) else None
off_path = os.path.join(RES, f"threshold_resolution_{f2['id']}.json")
off = json.load(open(off_path, encoding="utf-8")) if os.path.exists(off_path) else None
LABELS = {"bunching_ratio_350K": "Pile-up under $350K", "bunching_ratio_250K": "Pile-up under $250K",
          "po_share_10_15K": "Share of $10K–$20K orders under $15K"}


def fmt(name, x):
    return "—" if x is None else (pct(x) if name.startswith("po_share") else f"{x:.2f}")


# ---- live, refreshed every morning: this fiscal year so far (15_civilian_fytd.py) and today's open solicitations
# (14_sam_daily_snapshot.py); each part is left out until its results exist ---------------------------------------
LIVE_MIN = 50


def sam_name(d):
    """'VETERANS AFFAIRS, DEPARTMENT OF' -> 'Veterans Affairs'"""
    n = d.split(",")[0] if ", DEPARTMENT OF" in d else d
    n = n.title()
    for w in ("And", "Of", "For", "The"):
        n = n.replace(f" {w} ", f" {w.lower()} ")
    return n


live_parts, live_tiles = [], []
lt_files = sorted(f for f in os.listdir(RES) if f.startswith("live_fy") and f.endswith("_totals.json"))
if lt_files:
    lt = json.load(open(os.path.join(RES, lt_files[-1]), encoding="utf-8"))
    lx = lt["excluding_gsa"]
    lrows = sorted((r for r in rows(f"live_fy{lt['fiscal_year']}_by_agency.csv") if int(r["n_eval"]) >= LIVE_MIN),
                   key=lambda r: -float(r["share"]))
    ltop = max([float(r["share"]) for r in lrows] or [1])
    ltr = "".join(
        f"<tr><td>{esc(short_name(r['agency']))}{' (' + esc(r['abbreviation']) + ')' if r['abbreviation'] else ''}</td>"
        f"<td data-v='{r['share']}'><span class='bar'><i style='width:{100 * float(r['share']) / ltop:.1f}%'></i></span><b>{pct(r['share'])}</b></td>"
        f"<td>{pct(r['ci_lo'])}–{pct(r['ci_hi'])}</td><td data-v='{r['n_eval']}'>{int(r['n_eval']):,}</td></tr>" for r in lrows)
    live_tiles.append(f'<div class="card kpi"><b>{pct(lx["share"])}</b><span>of {lx["n_eval"]:,} competed awards so far in FY{lt["fiscal_year"]} got only one offer (civilian agencies except GSA)</span></div>')
    live_parts.append(f"""<p class="sub" style="margin:0 0 10px">Fiscal year {lt['fiscal_year']} so far: awards dated {nice(lt['data_from'])} to {nice(lt['data_to'])}, every civilian agency. With GSA's very small supply orders included, the share is {pct(lt['share'])} of {lt['n_eval']:,} awards. USAspending's latest data load: {nice(lt['usaspending_last_updated']) if lt['usaspending_last_updated'] else 'unknown'}.</p>
<div class="tablewrap"><table class="sortable civ">
  <thead><tr><th>Agency</th><th>Single-offer share so far</th><th>95% interval</th><th>Competed awards so far</th></tr></thead>
  <tbody>{ltr}</tbody></table></div>
<p class="note">Agencies with at least {LIVE_MIN} competed awards so far. The newest weeks are always incomplete: agencies have three business days to report an award, data loads happen on business days, and corrections keep arriving for weeks. Early-year shares are based on few awards and move a lot; compare them with the full-year shares in section 3.</p>""")
sam_path = os.path.join(RES, "sam_daily_latest.json")
if os.path.exists(sam_path):
    sd = json.load(open(sam_path, encoding="utf-8"))
    op = sd["open_solicitations"]
    live_tiles.append(f'<div class="card kpi"><b>{op["civilian"]["distinct_sol_numbers"]:,}</b><span>civilian solicitations open today ({op["dod"]["distinct_sol_numbers"]:,} more at Defense); {op["deadline_next_7d_civilian"]["distinct_sol_numbers"]:,} civilian ones close within 7 days</span></div>')
    srows = "".join(f"<tr><td>{esc(sam_name(d['department']))}</td><td data-v='{d['open_distinct_sol_numbers']}'>{d['open_distinct_sol_numbers']:,}</td></tr>"
                    for d in sorted(sd["top10_civilian_departments_by_open_solicitations"], key=lambda d: -d["open_distinct_sol_numbers"]))
    live_parts.append(f"""<h3 style="margin:22px 0 8px;font-size:1.05rem">Open solicitations today (SAM.gov, {nice(sd['snapshot_date'])})</h3>
<div class="tablewrap"><table class="sortable">
  <thead><tr><th>Civilian department</th><th>Open solicitations</th></tr></thead>
  <tbody>{srows}</tbody></table></div>
<p class="note">Distinct solicitation numbers with a response deadline on or after today, from SAM.gov's nightly list of active notices. Defense solicitations are public right away, even though Defense awards are published 90 days late.</p>""")
live_html = ""
if live_parts:
    live_html = f"""
<h2 id="live">Live: updated every morning</h2>
<div class="kpis" style="margin-top:0">{''.join(live_tiles)}</div>
<div class="card" style="margin-top:12px">{''.join(live_parts)}</div>"""

if off:
    hits = sum(r["inside_interval80"] for r in off["results"])
    f2_result = f"<span class='pill'>Scored: {hits} of {len(off['results'])} inside the 80% range</span>"
else:
    f2_result = "<span class='pill'>Pending</span>" + (" <a href='#tracking'>see progress</a>" if trk else "")

track_html = ""
if trk:
    trs = []
    for q in trk["quantities"]:
        lo, hi = q["interval80"]
        ci = q["so_far_95ci"]
        state = "—" if q["inside_interval80_so_far"] is None else ("inside" if q["inside_interval80_so_far"] else "outside")
        trs.append(f"<tr><td>{esc(LABELS.get(q['name'], q['name']))}</td><td>{fmt(q['name'], q['forecast'])} "
                   f"({fmt(q['name'], lo)}–{fmt(q['name'], hi)})</td><td><b>{fmt(q['name'], q['so_far'])}</b></td>"
                   f"<td>{'—' if not ci else fmt(q['name'], ci[0]) + '–' + fmt(q['name'], ci[1])}</td><td>{state}</td></tr>")
    track_html = f"""
<h2 id="tracking">5. Forecast progress (updated daily)</h2>
<div class="card"><div class="tablewrap"><table>
  <thead><tr><th>PIO-F2 quantity</th><th>Forecast (80% range)</th><th>So far</th><th>95% interval so far</th><th>So far vs. range</th></tr></thead>
  <tbody>{''.join(trs)}</tbody></table></div>
  <p class="note">Provisional reading from {trk['n_base_actions']:,} civilian awards dated {nice(trk['data_from'])} to {nice(trk['data_to'])} (checked {nice(trk['as_of'])}). This is not the score: the quarter runs through Dec 31, agencies keep reporting for weeks after, and early readings swing widely, as the wide intervals show. The official score uses data pulled on or after {nice(f2['resolution_date'])}.</p>
</div>"""

f1_hash = next(v for k, v in hashes.items() if k.startswith("PIO-F1"))
f2_hash = next(v for k, v in hashes.items() if k.startswith("PIO-F2"))

# ---- copy charts ----------------------------------------------------------------------------
os.makedirs(os.path.join(DOCS, "figures"), exist_ok=True)
for png in ("threshold_shift.png", "bunching_by_quarter.png", "single_offer_by_agency.png"):
    shutil.copy2(os.path.join(ROOT, "figures", png), os.path.join(DOCS, "figures", png))

m250, m350 = moved[250000], moved[350000]
page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Federal Contract Competition Dashboard</title>
<meta name="description" content="How competitive is US federal contracting? Single-bid rates, a $250K-to-$350K threshold experiment, and sealed forecasts scored in public.">
<style>
:root {{ --bg:#f6f7f9; --card:#fff; --ink:#1b2430; --muted:#5a6573; --line:#e1e5ea; --accent:#1f5fa8; --soft:#e7eef8; --warn:#fbf0dc; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{ --bg:#11161c; --card:#1a2129; --ink:#e7ecf2; --muted:#a3aebb; --line:#2c3540; --accent:#86b5ee; --soft:#1d2b3c; --warn:#33291a; }} }}
:root[data-theme="dark"] {{ --bg:#11161c; --card:#1a2129; --ink:#e7ecf2; --muted:#a3aebb; --line:#2c3540; --accent:#86b5ee; --soft:#1d2b3c; --warn:#33291a; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--ink); font:16px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; }}
.wrap {{ max-width:1040px; margin:0 auto; padding:28px 16px 56px; }}
h1 {{ font-size:1.8rem; line-height:1.2; margin:0 0 6px; }} h2 {{ font-size:1.25rem; margin:36px 0 10px; }}
.sub {{ color:var(--muted); margin:0 0 4px; }}
.kpis {{ display:grid; gap:12px; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); margin-top:20px; }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:14px; padding:16px 18px; }}
.kpi b {{ display:block; font-size:1.9rem; color:var(--accent); line-height:1.1; }} .kpi span {{ color:var(--muted); font-size:.92rem; }}
img {{ max-width:100%; height:auto; display:block; background:#fff; border-radius:10px; }}
.note {{ color:var(--muted); font-size:.92rem; margin:8px 0 0; }}
.warn {{ background:var(--warn); border-color:transparent; }}
.tablewrap {{ overflow-x:auto; }}
table {{ width:100%; border-collapse:collapse; font-size:.94rem; }}
th, td {{ text-align:left; padding:8px 8px; border-bottom:1px solid var(--line); white-space:nowrap; }}
th {{ color:var(--muted); font-weight:600; cursor:pointer; user-select:none; }} th:hover {{ color:var(--accent); }}
table.wrapcells td {{ white-space:normal; }} table.wrapcells td:last-child {{ white-space:nowrap; }}
table.civ td:first-child {{ white-space:normal; min-width:190px; }}
.bar {{ display:inline-block; width:84px; height:8px; margin-right:8px; vertical-align:middle; background:var(--soft); border-radius:4px; overflow:hidden; }}
.bar i {{ display:block; height:100%; background:var(--accent); border-radius:4px; }}
code {{ font-size:.8rem; word-break:break-all; white-space:normal; }}
.pill {{ display:inline-block; padding:2px 8px; border-radius:999px; background:var(--soft); color:var(--accent); font-size:.8rem; font-weight:600; }}
a {{ color:var(--accent); }} .theme {{ float:right; border:1px solid var(--line); background:var(--card); color:var(--ink); border-radius:999px; padding:4px 10px; cursor:pointer; font:inherit; font-size:.85rem; }}
footer {{ margin-top:40px; color:var(--muted); font-size:.88rem; }}
</style>
</head>
<body><div class="wrap">
<button class="theme" id="theme" type="button">Theme</button>
<h1>Federal Contract Competition: Findings &amp; Forecasts</h1>
<p class="sub">How often do competed federal contracts get only one bid, and do purchases bunch under dollar limits? Civilian agencies, fiscal years 2024–2026; the Department of Defense is not included. The threshold test and the agency table cover every civilian agency. The in-depth single-bid analysis and the forecasts cover five departments: VA, HHS, DHS, Interior and USDA.</p>
<p class="sub">By <a href="https://github.com/e-burress">Ellie Burress</a> · data through Sep 30, 2026 · <a href="{REPO}">code and data</a> · <a href="methodology">methodology</a></p>

<div class="kpis">
  <div class="card kpi"><b>{pct(total_k / total_n)}</b><span>of {total_n:,} competed awards in five departments got only one offer</span></div>
  {civ_kpi}
  <div class="card kpi"><b>{float(m350['ratio_pre']):.2f} → {float(m350['ratio_post']):.2f}</b><span>pile-up just under the new $350K line, before vs. after it took effect (1.0 = none)</span></div>
  <div class="card kpi"><b>{float(ours['auc']):.2f}</b><span>accuracy (AUC) of the single-bid forecast on unseen data, vs. {float(base['auc']):.2f} for a simple baseline</span></div>
  <div class="card kpi"><b>150</b><span>open solicitations with sealed single-bid forecasts, scored by Mar 31, 2027</span></div>
</div>

{live_html}

<h2>1. When the threshold moved, the pile-up moved</h2>
<div class="card">
  <img src="figures/threshold_shift.png" alt="Award counts near $250K and $350K before and after the October 2025 threshold change">
  <p class="note">On Oct 1, 2025, the simplified-acquisition threshold rose from $250K to $350K. Across all civilian agencies, the pile-up just under $250K fell from {float(m250['ratio_pre']):.2f} to {float(m250['ratio_post']):.2f}, and a new one formed under $350K ({float(m350['ratio_pre']):.2f} → {float(m350['ratio_post']):.2f}). Comparison lines at $200K and $450K, where nothing changed, did not move.</p>
</div>
<div class="card" style="margin-top:12px">
  <img src="figures/bunching_by_quarter.png" alt="Bunching ratio by fiscal quarter for $250K and $350K">
  <p class="note">Bunching ratio by fiscal quarter, with 95% intervals. Purchase orders and definitive contracts of all civilian agencies.</p>
</div>

<h2>2. Five departments: about one in four competed awards got a single offer</h2>
<div class="card">
  <img src="figures/single_offer_by_agency.png" alt="Single-offer share by department and fiscal year">
</div>
<div class="card" style="margin-top:12px"><div class="tablewrap"><table class="sortable">
  <thead><tr><th>Department</th><th>Single-offer share</th><th>Competed awards</th><th>FY2024 → FY2025 → FY2026</th></tr></thead>
  <tbody>{''.join(dept_rows)}</tbody></table></div>
  <p class="note">Tap a column heading to sort. Competed awards with a usable offer count (purchase orders, definitive contracts and contract vehicles).</p>
</div>

<div class="card" style="margin-top:12px"><div class="tablewrap"><table class="sortable">
  <thead><tr><th>Department</th><th>Award type</th><th>Single-offer share</th><th>Competed awards</th></tr></thead>
  <tbody>{''.join(kind_rows)}</tbody></table></div></div>

<div class="card" style="margin-top:12px"><div class="tablewrap"><table class="sortable">
  <thead><tr><th>Set-aside type</th><th>Single-offer share</th><th>Competed awards</th></tr></thead>
  <tbody>{''.join(sa_rows)}</tbody></table></div>
  <p class="note">Awards open to everyone (no set-aside) drew a single offer far more often than small-business set-asides.</p>
</div>
{civ_html}

<h2>4. Forecast scoreboard</h2>
<div class="card"><div class="tablewrap"><table class="wrapcells">
  <thead><tr><th>Forecast</th><th>What it predicts</th><th>Resolves</th><th>Result</th></tr></thead>
  <tbody>
  <tr><td>PIO-F1</td><td>Chance of a single offer for each of 150 open civilian solicitations</td><td>Jan 31, Feb 28, Mar 31, 2027</td><td><span class="pill">Pending</span></td></tr>
  <tr><td>PIO-F2</td><td>FY2027 Q1 pile-up under $350K: {q350['forecast']:.2f} (80% range {q350['interval80'][0]:.2f}–{q350['interval80'][1]:.2f}); under $250K: {q250['forecast']:.2f} ({q250['interval80'][0]:.2f}–{q250['interval80'][1]:.2f})</td><td>Feb 28, 2027</td><td>{f2_result}</td></tr>
  </tbody></table></div>
  <p class="note">Both forecasts were sealed on Oct 24, 2026, before any outcome existed. Fingerprints (SHA-256): PIO-F1 <code>{f1_hash}</code> · PIO-F2 <code>{f2_hash}</code>. PIO-F2 is <a href="{REPO}/blob/main/forecasts/PIO-F2-threshold-fy2027q1-20261024.json">published in full</a>; PIO-F1 will be posted unchanged when it is scored.</p>
</div>
{track_html}

<h2>How to read this</h2>
<div class="card warn">
  These are signals about markets and buying patterns, not about people. A single bid or a pile-up under a threshold can have ordinary explanations, and none of these numbers shows wrongdoing. Award data has no unit prices, so this does not measure whether single-bid contracts cost more. Offer counts are entered by contracting staff and can contain errors. FY2026 included a government shutdown and reporting changes.
</div>

<footer>Built by Ellie Burress with AI assistance (Claude Code). Data: <a href="https://www.usaspending.gov">USAspending.gov</a> and <a href="https://sam.gov">SAM.gov</a> (public). Code MIT; text and figures CC BY 4.0. Found an error? <a href="{REPO}/issues">Open an issue</a>. Page built {date.today():%b %d, %Y}.</footer>
</div>
<script>
document.querySelectorAll("table.sortable").forEach(function (t) {{
  t.querySelectorAll("th").forEach(function (th, i) {{
    var asc = false;
    th.addEventListener("click", function () {{
      asc = !asc;
      var body = t.tBodies[0], rs = Array.from(body.rows);
      rs.sort(function (a, b) {{
        var x = a.cells[i], y = b.cells[i];
        var xv = x.dataset.v !== undefined ? parseFloat(x.dataset.v) : x.textContent;
        var yv = y.dataset.v !== undefined ? parseFloat(y.dataset.v) : y.textContent;
        return (xv > yv ? 1 : xv < yv ? -1 : 0) * (asc ? 1 : -1);
      }});
      rs.forEach(function (r) {{ body.appendChild(r); }});
    }});
  }});
}});
(function () {{
  var root = document.documentElement, key = "fcc-theme";
  try {{ var s = localStorage.getItem(key); if (s) root.setAttribute("data-theme", s); }} catch (e) {{}}
  document.getElementById("theme").addEventListener("click", function () {{
    var dark = root.getAttribute("data-theme") === "dark" || (!root.getAttribute("data-theme") && matchMedia("(prefers-color-scheme: dark)").matches);
    var next = dark ? "light" : "dark"; root.setAttribute("data-theme", next);
    try {{ localStorage.setItem(key, next); }} catch (e) {{}}
  }});
}})();
</script>
</body></html>
"""
with open(os.path.join(DOCS, "index.html"), "w", encoding="utf-8") as f:
    f.write(page)
print("wrote docs/index.html")
