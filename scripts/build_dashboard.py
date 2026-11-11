"""
build_dashboard.py - build the public dashboard page (docs/index.html) from the files in results/ and forecasts/.

Run from the repository root:
    python -I scripts/build_dashboard.py
It reads only local CSV/JSON files (no downloads), copies the charts into docs/figures/, and writes docs/index.html.
GitHub Pages serves the docs/ folder as the public website.
Scope: civilian agencies (DoD is not included); the deeper single-offer analysis and the forecasts cover five departments.
The long-history sections (FY2008 onward, civilian and Defense) are functions of a results folder: each returns an empty string when its
files are missing or too thin, so the page builds at every stage of the data.  Every number on the page is read from the files.
"""
import csv
import html
import itertools
import json
import math
import os
import re
import shutil
import tomllib
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, "results")
FC = os.path.join(ROOT, "forecasts")
DOCS = os.path.join(ROOT, "docs")
with open(os.path.join(ROOT, "config.toml"), "rb") as _f:
    CFG = tomllib.load(_f)
# in a fork, GITHUB_REPOSITORY points the links at the fork
REPO = "https://github.com/" + (os.environ.get("GITHUB_REPOSITORY") or CFG["project"]["repository"])
AUTHOR, AUTHOR_URL = CFG["project"]["author"], "https://github.com/" + CFG["project"]["repository"].split("/")[0]
F1, F2 = CFG["forecasts"]["F1"], CFG["forecasts"]["F2"]
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


def short_name(name):
    """'Department of the Interior' -> 'Interior'"""
    return re.sub(r"^Department of (the )?", "", name)


def nice(d):
    return date.fromisoformat(d).strftime("%b %d, %Y").replace(" 0", " ")


LABELS = {"bunching_ratio_350K": "Pile-up under $350K", "bunching_ratio_250K": "Pile-up under $250K",
          "po_share_10_15K": "Share of $10K–$20K orders under $15K"}


def fmt(name, x):
    return "—" if x is None else (pct(x) if name.startswith("po_share") else f"{x:.2f}")


def sam_name(d):
    """'VETERANS AFFAIRS, DEPARTMENT OF' -> 'Veterans Affairs'"""
    n = d.split(",")[0] if ", DEPARTMENT OF" in d else d
    n = n.title()
    for w in ("And", "Of", "For", "The"):
        n = n.replace(f" {w} ", f" {w.lower()} ")
    return n


# =====================================================================================================================
# Long-history sections.  Each takes a results folder (figures are looked up in the "figures" folder next to it) and
# returns HTML, or "" when its files are missing or too thin.  Headings carry class "num"; number_sections() numbers them.
# =====================================================================================================================
def load_csv(res, name):
    p = os.path.join(res, name)
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except (OSError, csv.Error, UnicodeDecodeError):
        return None


def load_json(res, name):
    p = os.path.join(res, name)
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def num(x):
    """float, or None for blanks, NaN and junk"""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def sgn(x, d=2):
    """signed number with a real minus sign: +0.55, −0.37"""
    return f"{x:+.{d}f}".replace("-", "−")


def money(x):
    x = float(x)
    if x >= 1e6:
        return f"${x / 1e6:g}M"
    if x >= 1e4:
        return f"${x / 1e3:g}K"
    return f"${x:,.0f}"


def years_text(years):
    """[2008, 2009, 2010, 2012] -> 'FY2008 to FY2012 (FY2011 not in the data yet)'"""
    years = sorted(set(years))
    if not years:
        return ""
    t = f"FY{years[0]}" if len(years) == 1 else f"FY{years[0]} to FY{years[-1]}"
    gaps = [y for y in range(years[0], years[-1] + 1) if y not in years]
    if gaps:
        t += " (" + ", ".join(f"FY{y}" for y in gaps) + " not in the data yet)"
    return t


def figure(res, name, alt):
    if not os.path.exists(os.path.join(os.path.dirname(os.path.abspath(res)), "figures", name)):
        return ""
    return f'<img src="figures/{name}" alt="{esc(alt)}" loading="lazy">'


def section(sid, title, lead, body, note):
    return (f'\n<h2 class="num" id="{sid}">{esc(title)}</h2>\n<div class="card"><p class="lead">{lead}</p>{body}'
            f'<p class="note"><b>How to read this.</b> {note}</p></div>')


def table(head, body_rows, sortable=True):
    cls = ' class="fit sortable"' if sortable else ' class="fit"'
    return (f'<div class="tablewrap"><table{cls}><thead><tr>{"".join(f"<th>{h}</th>" for h in head)}</tr></thead>'
            f'<tbody>{"".join(body_rows)}</tbody></table></div>')


HIST_SECTORS = [("civilian except GSA", "Civilian agencies except GSA"), ("GSA", "GSA"), ("Department of Defense", "Defense")]
HIST_MIN_YEARS = 3


def sec_history(res):
    data = load_csv(res, "history_by_fy.csv")
    if not data:
        return ""
    series, notes = {}, {}
    for r in data:
        if r.get("award_kind", "all") != "all":
            continue
        s, n, y = num(r.get("share")), num(r.get("n_eval")), num(r.get("fy"))
        if s is None or y is None or not n:
            continue
        series.setdefault(r.get("sector"), {})[int(y)] = (s, int(n))
        if (r.get("note") or "").strip():                                  # known reporting change (19_history_atlas.py)
            notes[(r.get("sector"), int(y))] = r["note"].strip()
    shown = [(k, lab, series[k]) for k, lab in HIST_SECTORS if len(series.get(k, {})) >= HIST_MIN_YEARS]
    if not shown:
        return ""
    trs = []
    for key, lab, ys in shown:
        a, b = min(ys), max(ys)
        ch = 100 * (ys[b][0] - ys[a][0])
        mark = "*" if (key, a) in notes else ""
        trs.append(f"<tr><td>{esc(lab)}</td><td data-v='{ys[a][0]}'>FY{a}: <b>{pct(ys[a][0])}</b>{mark}</td>"
                   f"<td data-v='{ys[b][0]}'>FY{b}: <b>{pct(ys[b][0])}</b></td><td data-v='{ch}'>{sgn(ch, 1)} points</td>"
                   f"<td data-v='{ys[b][1]}'>{ys[b][1]:,}</td></tr>")
    key, lab, ys = shown[0]
    a, b = min(ys), max(ys)
    allyears = set().union(*(s for _, _, s in shown))
    lead = f"{esc(lab)}: <b>{pct(ys[a][0])}</b> of competed awards got only one offer in FY{a}, and <b>{pct(ys[b][0])}</b> in FY{b}. "
    if (key, a) in notes:
        clean = [y for y in sorted(ys) if (key, y) not in notes]
        lead = (f"{esc(lab)}: FY{a} is not comparable with later years because of a reporting change (below). From FY{clean[0]}, "
                f"<b>{pct(ys[clean[0]][0])}</b> of competed awards got only one offer, and <b>{pct(ys[b][0])}</b> in FY{b}. "
                if clean and clean[0] < b else lead)
    lead += f"Years covered so far: {years_text(allyears)}."
    body = figure(res, "single_offer_history.png", "Single-offer share by fiscal year since FY2008: civilian agencies except GSA, GSA, and Defense")
    body += table(["Sector", "First year", "Latest year", "Change", "Awards, latest year"], trs)
    shown_keys = {k for k, _, _ in shown}
    cautions = [f"<li>{esc(dict(HIST_SECTORS).get(k, k))}, FY{y}: {esc(t)}</li>" for (k, y), t in sorted(notes.items()) if k in shown_keys]
    if cautions:
        body += f"<p class='note'><b>* Caution.</b></p><ul class='tight'>{''.join(cautions)}</ul>"
    note = ("Each line is the share of competed awards with a usable offer count that got exactly one offer, by fiscal year, with the "
            "same definitions as above. GSA is shown on its own because its many very small supply orders outnumber everything else "
            "and their offer coding changed. Defense data are published 90 days late, so the newest Defense year can be incomplete. "
            "A falling share can mean more bidders or a change in how offers are recorded; the chart cannot tell which "
            "(see <a href='#quality'>data quality</a>). Agency by agency: <a href='explorer.html#sectors'>explorer</a>.")
    return section("history", "Since 2008", lead, body, note)


# simplified-acquisition limit changes: (old line, new line, when, fiscal year before, fiscal year after).  The 2018 change was
# adopted agency by agency over FY2018-FY2019 (20_threshold_history.py), so it compares FY2017 with FY2020.
SAT_CHANGES = [(100_000, 150_000, "October 1, 2010", 2010, 2011),
               (150_000, 250_000, "2018, agency by agency", 2017, 2020),
               (250_000, 350_000, "October 1, 2025", 2025, 2026)]
TH_SECTORS = [("civilian", "Civilian agencies"), ("defense", "Defense")]


def _staggered(res):
    """sentence on the 2018 agency-by-agency test (21_threshold_staggered.py), or ''"""
    data = load_csv(res, "threshold_staggered.csv")
    if not data:
        return ""
    def avg(dates, line, qs):
        v = [num(r["ratio"]) for r in data if r.get("sector") == "civilian" and r.get("dates") == dates
             and num(r.get("line")) == line and num(r.get("event_quarter")) in qs and num(r.get("ratio")) is not None]
        return sum(v) / len(v) if len(v) >= 2 else None
    before, after = (-4, -3, -2, -1), (0, 1, 2, 3)
    a250, b250 = avg("actual", 250_000, before), avg("actual", 250_000, after)
    a150, b150 = avg("actual", 150_000, before), avg("actual", 150_000, after)
    p250, q250 = avg("placebo, one year earlier", 250_000, before), avg("placebo, one year earlier", 250_000, after)
    if None in (a250, b250, a150, b150):
        return ""
    t = (f"<p><b>The 2018 change, agency by agency.</b> Civilian agencies raised the limit from $150K to $250K on different dates. "
         f"Lined up on each agency's own date, the pile-up under $250K averaged {a250:.2f} in the four quarters before and "
         f"<b>{b250:.2f}</b> in the four after; under $150K it went from {a150:.2f} to {b150:.2f}.")
    if p250 is not None and q250 is not None:
        jump, pj = b250 - a250, q250 - p250
        t += (f" Placebo, with every date moved one year earlier: under $250K, {p250:.2f} before and {q250:.2f} after. The jump at the "
              f"true dates ({sgn(jump)}) is {'larger' if jump > pj else 'not larger'} than at the placebo dates ({sgn(pj)}).")
    t += "</p>"
    return t + figure(res, "threshold_staggered.png", "Pile-ups under $150K and $250K around each civilian agency's own 2018 switch date, with a placebo")


def sec_threshold_history(res):
    data = load_csv(res, "threshold_history.csv")
    cell = {}
    for r in data or []:
        y, L, v = num(r.get("fy")), num(r.get("line")), num(r.get("ratio"))
        if None not in (y, L, v):
            cell[(int(y), r.get("sector"), int(L))] = (v, num(r.get("ci_lo")), num(r.get("ci_hi")))
    items, tests, followed, waiting = [], 0, 0, []
    for old, new, when, y0, y1 in SAT_CHANGES:
        parts = []
        for s, lab in TH_SECTORS:
            k = [(y0, s, new), (y1, s, new), (y0, s, old), (y1, s, old)]
            if not all(x in cell for x in k):
                continue
            nb, na, ob, oa = (cell[x] for x in k)
            tests += 1
            clear = na[1] > nb[2] if None not in (na[1], nb[2]) else na[0] > nb[0]
            ok = clear and oa[0] < ob[0]
            followed += ok
            parts.append(f"{lab}: under {money(new)}, {nb[0]:.2f} → <b>{na[0]:.2f}</b>; under {money(old)}, {ob[0]:.2f} → {oa[0]:.2f}"
                         f" ({'followed the limit' if ok else 'no clear move with the limit'}).")
        if parts:
            items.append(f"<li><b>{money(old)} to {money(new)}</b> ({when}; FY{y0} vs. FY{y1}). {' '.join(parts)}</li>")
        else:
            waiting.append(f"{money(old)} to {money(new)} (needs FY{y0} and FY{y1})")
    n_changes = len(items)
    if items and waiting:
        items.append(f"<li>Not testable yet: {'; '.join(waiting)}.</li>")
    stag = _staggered(res)
    if not items and not stag:
        return ""
    if items and followed == tests:
        title = "The pile-up follows the limit, every time" if n_changes >= 2 else "The pile-up followed the limit"
    else:
        title = "Does the pile-up follow the limit?"
    years = sorted({y for y, _, _ in cell})
    if items:
        lead = (f"In <b>{followed} of {tests}</b> tests (each change of the simplified-acquisition limit, civilian agencies and Defense "
                f"separately), the pile-up under the new limit grew clearly and the one under the old limit shrank. "
                f"Years covered so far: {years_text(years)}.")
    else:
        lead = "No change of the limit can be tested yet with the years in the data; the 2018 agency-by-agency test is below."
    body = (figure(res, "threshold_history.png", "Pile-ups under the $100K, $150K, $250K and $350K lines by fiscal year, civilian agencies and Defense")
            + (f"<ul class='tight'>{''.join(items)}</ul>" if items else "") + stag)
    note = ("The bunching ratio counts the awards just under a line and divides by the number a smooth curve predicts there; 1.0 means "
            "no pile-up. Purchase orders and definitive contracts. \"Grew clearly\" means the 95% intervals of the two years do not "
            "overlap. A pile-up is a pattern, not proof of why it happens: budgets written to fit the rules and contracts split to "
            "stay under a line look the same here. Year by year: <a href='explorer.html#thresholds'>explorer</a>.")
    return section("limits", title, lead, body, note)


SCAN_FAMILIES = {
    "micro_purchase": "Micro-purchase", "simplified_acquisition": "Simplified acquisition", "synopsis": "$25,000 posting line",
    "cost_or_pricing_data": "Certified cost or pricing data", "commercial_simplified": "Commercial simplified procedures",
    "subcontracting_plan": "Subcontracting plan", "subcontracting_plan_construction": "Subcontracting plan, construction",
    "justification_level1": "Justification approval, level 1", "justification_level2": "Justification approval, level 2",
    "justification_level3_civilian": "Justification approval, level 3 (civilian)",
    "justification_level3_dod": "Justification approval, level 3 (Defense)",
    "8a_competitive_threshold_nonmfg": "8(a) competitive line, non-manufacturing",
    "8a_competitive_threshold_mfg": "8(a) competitive line, manufacturing", "8a_sole_source_ja": "8(a) sole-source justification"}
VERDICTS = ["moved", "no clear move", "moved the wrong way", "not testable"]


def _why(r):
    reason = r.get("reason") or ""
    if (r.get("years_missing") or "") and not reason.startswith(("too", "fit")):
        return "waiting for fiscal years not in the data yet"
    if "window in the years present" in reason:
        return "before or after window outside FY2008 to FY2026"
    return "too few awards near the line"


def sec_threshold_scan(res):
    data = load_csv(res, "threshold_scan_changes.csv")
    if not data:
        return ""
    if any(r.get("sector") in ("civilian", "defense") for r in data):
        data = [r for r in data if r.get("sector") in ("civilian", "defense")]
    data = [r for r in data if r.get("verdict") in VERDICTS]
    tested = [r for r in data if r["verdict"] != "not testable"]
    if not tested:
        return ""
    count = {v: sum(r["verdict"] == v for r in data) for v in VERDICTS}
    why = {}
    for r in data:
        if r["verdict"] == "not testable":
            why[_why(r)] = why.get(_why(r), 0) + 1
    fams = list(dict.fromkeys(r["family"] for r in data))
    trs = []
    for f in fams:
        c = [sum(r["family"] == f and r["verdict"] == v for r in data) for v in VERDICTS]
        trs.append(f"<tr><td>{esc(SCAN_FAMILIES.get(f, f.replace('_', ' ')))}</td>" + "".join(f"<td data-v='{x}'>{x}</td>" for x in c) + "</tr>")
    lead = (f"<b>{len(data)}</b> changes of a dollar limit were checked, for civilian agencies and Defense separately. The pile-up "
            f"moved with the line in <b>{count['moved']}</b>, did not clearly move in {count['no clear move']}, and moved the wrong "
            f"way in {count['moved the wrong way']}. {count['not testable']} cannot be tested yet"
            + (" (" + "; ".join(f"{n} {w}" for w, n in sorted(why.items(), key=lambda x: -x[1])) + ")" if why else "") + ".")
    moved = [r for r in tested if r["verdict"] == "moved" and num(r.get("did")) is not None]
    if moved:
        r = max(moved, key=lambda r: num(r["did"]))
        try:
            when = nice(r["change_date"])
        except (TypeError, ValueError):
            when = r["change_date"]
        lead += (f" The largest move: {esc(SCAN_FAMILIES.get(r['family'], r['family']))}, {esc(r['sector'])}, {money(r['old_line'])} to "
                 f"{money(r['new_line'])} ({esc(when)}): difference in differences {sgn(num(r['did']))} ({sgn(num(r['did_lo']))} to {sgn(num(r['did_hi']))}).")
    partial = sum(str(r.get("data_complete")).lower() == "false" for r in tested)
    summ = load_json(res, "threshold_scan_summary.json") or {}
    yrs = summ.get("fiscal_years_present") or []
    if yrs:
        lead += f" Years covered so far: {years_text(yrs)}."
    if partial:
        lead += f" {partial} of the tested changes use only part of their before and after years."
    body = figure(res, "threshold_scan.png", "Pile-ups just under every federal dollar limit by fiscal year, one panel per rule")
    body += table(["Rule", "Moved", "No clear move", "Moved the wrong way", "Not testable yet"], trs)
    note = ("Each test compares the pile-up under the old and the new value in the two years before and the two years after a change "
            "(difference in differences; above zero means the pile-up moved with the line). Many limits changed on the same day, so one "
            "test can pick up another rule's move. Awards also heap at round numbers, which is why each line is compared with round "
            "values that were never limits. A pile-up does not show why it happens. Detail: <a href='explorer.html#thresholds'>explorer</a>.")
    return section("scan", "Every dollar limit", lead, body, note)


def sec_drivers(res):
    s = load_json(res, "drivers_summary.json")
    top = (s or {}).get("top_five_factors") or []
    top = [t for t in top if num(t.get("marginal_effect_points")) is not None and len(t.get("ci_95_points") or []) == 2]
    if not top:
        return ""
    trs = [f"<tr><td>{esc(t.get('factor', ''))}</td><td>{esc(t.get('level', ''))}</td><td>{esc(t.get('versus', ''))}</td>"
           f"<td data-v='{t['marginal_effect_points']}'><b>{sgn(num(t['marginal_effect_points']), 1)}</b> "
           f"({sgn(num(t['ci_95_points'][0]), 1)} to {sgn(num(t['ci_95_points'][1]), 1)})</td></tr>" for t in top]
    t = top[0]
    eff = num(t["marginal_effect_points"])
    lead = (f"The clearest link: <b>{esc(t.get('level', ''))}</b> ({esc(t.get('factor', ''))}) went with a "
            f"<b>{abs(eff):.1f} percentage points {'higher' if eff > 0 else 'lower'}</b> chance of a single offer than "
            f"{esc(t.get('versus', ''))}.")
    yrs = s.get("years_in_model") or []
    if yrs:
        lead += f" Years in the model so far: {years_text(yrs)}."
    blocks = s.get("blocks") or {}
    auc = num(blocks.get("auc_full_model"))
    if auc is not None:
        lead += (f" Tested on data it was not fitted on ({esc(blocks.get('split', ''))}), the model's accuracy (AUC) is {auc:.2f}, "
                 f"where 0.5 is a coin flip.")
    body = figure(res, "drivers.png", "Circumstances that go with single offers: average marginal effects with 95% intervals")
    body += table(["Factor", "This kind of award", "Compared with", "Chance of one offer, points (95% interval)"], trs)
    note = ("These are associations, not causes. Each row compares one kind of award with the most common kind for the same factor, "
            "with the other factors, the agency and the year held fixed. Factors travel together and with things the data do not "
            "record, and agencies record offers differently (see <a href='#quality'>data quality</a>), so a factor can stand in for "
            "something else. The chart shows more factors than the table.")
    return section("drivers", "Why only one bid?", lead, body, note)


def _fu_text(fu):
    m = re.match(r"t to t\+(\d)", fu or "")
    if not m:
        return esc(fu)
    k = int(m.group(1))
    words = {2: "two", 3: "three", 4: "four", 5: "five"}
    return "the award year and the year after" if k == 1 else f"the award year and the {words.get(k, k)} years after"


def sec_outcomes(res):
    est = load_csv(res, "outcomes_estimates.csv")
    if not est:
        return ""
    summ = load_json(res, "outcomes_summary.json") or {}
    fu = summ.get("headline_follow_up") or "t to t+3"
    pick = [r for r in est if r.get("follow_up") == fu and r.get("scope_type") == "pooled"
            and num(r.get("coef")) is not None and num(r.get("lo")) is not None and num(r.get("hi")) is not None]
    outs = [r for r in pick if r.get("role") == "outcome"]
    plac = [r for r in pick if r.get("role") == "placebo"]
    if not outs:
        return ""

    def diff(r):
        k = 100 if r.get("unit") == "share" else 1
        d = 1 if k == 100 else 3
        if any(0 < abs(num(r[c]) * k) < 0.5 * 10 ** -d for c in ("coef", "lo", "hi")):
            d += 1                                                     # never print an interval end as "−0.0"
        u = " points" if k == 100 else ""
        return f"{sgn(num(r['coef']) * k, d)}{u} ({sgn(num(r['lo']) * k, d)} to {sgn(num(r['hi']) * k, d)})"

    def base(r):
        m = num(r.get("mean_multi_offer"))
        if m is None:
            return ""
        return pct(m) if r.get("unit") == "share" else (f"{m:.2f}×" if r.get("unit") == "ratio" else f"{m:.2f}")

    clear = [r for r in outs if num(r["lo"]) > 0 or num(r["hi"]) < 0]
    floor = num((summ.get("design") or {}).get("size_floor"))
    lead = (f"Awards{' of ' + money(floor) + ' or more' if floor else ''} made in {esc(outs[0].get('cohorts', ''))}, followed over "
            f"{_fu_text(fu)}: <b>{len(clear)} of {len(outs)}</b> outcomes differ clearly (the 95% interval excludes zero) between "
            f"awards that drew one offer and similar awards that drew two or more.")
    if clear:
        r = max(clear, key=lambda r: abs(num(r["coef"]) / (num(r["hi"]) - num(r["lo"]) or 1)))
        lead += f" Clearest: {esc(r.get('label', r['outcome']))}, {diff(r)}."
    if plac:
        bad = [r for r in plac if num(r["lo"]) > 0 or num(r["hi"]) < 0]
        ptxt = "; ".join(f"{esc(r.get('label', '').replace('Placebo: ', ''))}: {diff(r)}" for r in plac)
        if bad:
            lead += (f" Placebo checks, which competition cannot affect ({ptxt}): at least one shows a difference, so the two groups "
                     f"differ in ways the comparison does not capture. Read the rows with care.")
        else:
            lead += f" Placebo checks, which competition cannot affect, show no difference, as they should ({ptxt})."
    trs = [f"<tr><td>{esc(r.get('label', r['outcome']))}</td><td>{base(r)}</td><td data-v='{r['coef']}'>{diff(r)}</td></tr>" for r in outs]
    body = figure(res, "outcomes.png", "After award: single-offer minus multi-offer awards on several outcomes, with 95% intervals, by sector")
    body += table(["Outcome", "Awards with two or more offers", "One offer minus two or more (95% interval)"], trs)
    fuinfo = (summ.get("follow_ups") or {}).get(fu) or {}
    carry = num(fuinfo.get("share_of_awards_carrying_the_estimate"))
    note = ("Each comparison is made inside groups of awards from the same buying office, quarter, award type, product and size, so "
            "only groups holding both kinds of award count" + (f" ({pct(carry, 0)} of awards)" if carry is not None else "") + ". "
            "One-offer awards can still differ in ways the data do not record, and extra money or scope changes are often legitimate. "
            "Award data have no unit prices, so this cannot show whether one-offer awards cost more.")
    return section("outcomes", "Do one-bid awards turn out worse?", lead, body, note)


SPLIT_SECTORS = {"civilian_ex_gsa": "Civilian except GSA", "gsa": "GSA", "defense": "Defense"}
SPLIT_LINES = {"SAT": "Simplified acquisition limit", "MPT": "Micro-purchase limit"}


def sec_split(res):
    s = load_json(res, "split_screen_summary.json")
    pooled = (s or {}).get("excess_ratio_pooled") or {}
    pairs = []
    for sec, slab in SPLIT_SECTORS.items():
        for line, llab in SPLIT_LINES.items():
            a, d = pooled.get(f"{sec}|{line}|all") or {}, pooled.get(f"{sec}|{line}|donut") or {}
            if num(a.get("ratio")) is not None:
                pairs.append((slab, llab, a, d))
    if not pairs:
        return ""

    def cell(x):
        r = num(x.get("ratio"))
        if r is None:
            return "<td>not enough data</td>"
        lo, hi = num(x.get("ci_lo")), num(x.get("ci_hi"))
        ci = f" ({lo:.2f} to {hi:.2f})" if None not in (lo, hi) else ""
        return f"<td data-v='{r}'><b>{r:.2f}</b>{ci}</td>"
    trs = [f"<tr><td>{esc(sl)}</td><td>{esc(ll)}</td>{cell(a)}{cell(d)}</tr>" for sl, ll, a, d in pairs]
    ra = [num(a["ratio"]) for _, _, a, _ in pairs]
    rd = [num(d.get("ratio")) for _, _, _, d in pairs if num(d.get("ratio")) is not None]
    lead = (f"At the real limits, clusters that look like split purchases were <b>{min(ra):.2f} to {max(ra):.2f} times</b> as common "
            f"as fake limits predict (1.0 = no excess)")
    lead += (f"; leaving out awards just under the line, {min(rd):.2f} to {max(rd):.2f} times." if rd else ".")
    yrs = s.get("years_used") or []
    if yrs:
        lead += f" Years covered so far: {years_text(yrs)}."
    body = table(["Sector", "Limit", "Excess ratio, all awards (95% interval)", "Without awards just under the line"], trs)
    note = ("A cluster is two or more awards from the same company, buying office and product within 30 days, each at or under a "
            "limit but together above it. The excess ratio compares how often that happens at the real limit with fake limits where no "
            "rule applies. Repeat buying of supplies makes such clusters everywhere, and single awards bunched just under a limit add "
            "some too; the last column removes that. This is a screen about markets: it cannot tell a prohibited split from ordinary "
            "repeat buying.")
    return section("split", "Split purchases?", lead, body, note)


def sec_supplier(res):
    """Who wins: the supplier base (scripts/33_supplier_base.py), from its summary file only"""
    s = load_json(res, "supplier_base_summary.json") or {}
    c = s.get("companies_above_25k") or {}
    if None in (num(c.get("first")), num(c.get("last")), num(c.get("change"))):
        return ""
    ch = num(c["change"])
    lead = (f"<b>{int(c['last']):,}</b> companies won at least one new award above $25,000 in FY{c['last_year']}, "
            f"against {int(c['first']):,} in FY{c['first_year']} ({pct(abs(ch), 0)} {'fewer' if ch < 0 else 'more'}).")
    nw, st = s.get("new_companies_above_25k") or {}, s.get("staying_above_25k") or {}
    if num(nw.get("share_of_companies_mean")) is not None:
        lead += (f" In an average year ({years_text(nw.get('years') or [])}), <b>{pct(nw['share_of_companies_mean'], 0)}</b> of them had won "
                 f"no such award in the three years before, and together they won {pct(nw['share_of_awards_mean'], 0)} of the awards.")
    if num(st.get("next_year_mean")) is not None:
        lead += f" About {pct(st['next_year_mean'], 0)} won again the next year."
    cp = s.get("competition_above_25k_pooled") or {}
    a, b = cp.get("single_offer_share_new") or {}, cp.get("single_offer_share_established") or {}
    if num(a.get("share")) is not None and num(b.get("share")) is not None:
        lead += (f" Competed awards won by new companies drew a single offer {pct(a['share'])} of the time, against {pct(b['share'])} "
                 f"for established companies ({esc(str(cp.get('period', '')).replace('-', ' to '))}).")
    body = figure(res, "supplier_base.png", "Who wins new federal awards: companies, new companies, staying, and single-offer shares")
    k = s.get("concentration_above_25k") or {}
    if num(k.get("top1pct_last")) is not None:
        body += (f"<p>The top 1% of companies won {pct(k['top1pct_last'], 0)} of the initial dollars of purchase orders and definitive "
                 f"contracts above $25,000 in FY{k['last_year']}.</p>")
    note = ("A company is one registered identifier: parents and subsidiaries count separately. Only new prime awards are counted; "
            "orders under existing contract vehicles are not in these tables. Dollars are not adjusted for inflation. Company "
            "identifiers changed from DUNS to UEI in 2022; years that could not be checked across that switch are hollow on the chart "
            "and marked not comparable in the files. No company is named, and cells with fewer than 10 companies are left out. "
            "These are patterns, not proof of causes.")
    return section("supplier", "Who wins: the supplier base", lead, body, note)


QUALITY_METRICS = {"offers_1": "share with one offer", "offers_2": "share with two offers", "offers_3": "share with three offers",
                   "offers_unusable": "share with no usable offer count", "competed": "share marked competed",
                   "volume": "number of awards, vs. the same quarter a year earlier"}
QUALITY_GROUPS = [("offer counts", ("offers_1", "offers_2", "offers_3", "offers_unusable")), ("the share marked competed", ("competed",)),
                  ("the number of awards", ("volume",))]


def quality_tops(q):
    """(largest share flags, largest volume flags) from a quality summary: the separate lists of 16_quality_monitor.py, or, in an
    older summary, its mixed top_15 list split by metric"""
    if "top_share_flags" in q or "top_volume_flags" in q:
        return list(q.get("top_share_flags") or []), list(q.get("top_volume_flags") or [])
    top = list(q.get("top_15") or [])
    return [f for f in top if f.get("metric") != "volume"], [f for f in top if f.get("metric") == "volume"]


def sec_quality(res):
    q = load_json(res, "quality_summary.json")
    if not q or num(q.get("n_flags")) is None:
        return ""
    n = int(q["n_flags"])
    bym = q.get("by_metric") or {}
    groups = [(lab, sum(int(bym.get(m, 0)) for m in ms)) for lab, ms in QUALITY_GROUPS]
    groups = [f"{c} in {lab}" for lab, c in groups if c]
    lead = (f"The monitor flagged <b>{n}</b> agency quarters where recording or reporting shifted sharply"
            + (": " + ", ".join(groups) + "." if groups else "."))
    share_top, vol_top = quality_tops(q)
    share_top, vol_top = share_top[:3], vol_top[:3]
    body = ""
    if share_top or vol_top:
        lead += (" The largest are below, in two lists: shares move in percentage points and award counts in percent, so they are "
                 "not ranked together.")

        def val(f, x):
            v = num(x)
            if v is None:
                return ""
            return f"{v:,.0f}" if f.get("metric") == "volume" else f"{v:.1f}%"

        def change(f):
            v = num(f.get("change_points"))
            if v is None:
                return ""
            return f"{sgn(v, 0)}%" if f.get("metric") == "volume" else f"{sgn(v, 1)} points"

        def rows_for(lst):
            return [f"<tr><td>{esc(short_name(str(f.get('agency', ''))))}</td><td>{esc(KINDS.get(f.get('award_kind'), f.get('award_kind', '')))}</td>"
                    f"<td>{esc(f.get('quarter', ''))}</td><td>{esc(QUALITY_METRICS.get(f.get('metric'), f.get('metric', '')))}</td>"
                    f"<td>{val(f, f.get('before'))} → <b>{val(f, f.get('after'))}</b></td><td>{change(f)}</td></tr>" for f in lst]
        if share_top:
            body += "<p><b>Largest shifts in shares</b> (percentage points)</p>"
            body += table(["Agency", "Award type", "Quarter", "What changed", "Before → after", "Change"], rows_for(share_top), sortable=False)
        if vol_top:
            body += "<p><b>Largest changes in the number of awards</b> (percent, against the same quarter a year earlier)</p>"
            body += table(["Agency", "Award type", "Quarter", "What changed", "Before → after", "Change"], rows_for(vol_top), sortable=False)
    th = q.get("thresholds") or {}
    rule = ""
    if None not in (num(th.get("min_points")), num(th.get("z_bar")), num(th.get("volume_pct"))):
        rule = (f"Shares are flagged when they move at least {num(th['min_points']):g} points with a z-score of at least "
                f"{num(th['z_bar']):g}; award counts when they move {num(th['volume_pct']):g}% or more. ")
    note = ("A flag does not say whether the cause is a coding change, late reporting or a real change. It says: check before trusting "
            f"numbers for that agency and quarter. {rule}Every flag: <a href='explorer.html#quality'>explorer</a>.")
    qh = load_json(res, "quality_history_summary.json") or {}
    if num(qh.get("n_flags")) is not None:
        span = ""
        yrs = [x for x in (qh.get("inputs") or []) if re.fullmatch(r"base_actions_FY\d{4}\.parquet", str(x))]
        if yrs:
            span = " (" + years_text(int(re.search(r"\d{4}", x).group(0)) for x in yrs) + ", Defense included)"
        lead += (f" The same check over the long history{span} flags <b>{int(qh['n_flags'])}</b> agency quarters "
                 f"(results/quality_history_flags.csv).")
    return section("quality", "Data quality", lead, body, note)


# ---- the daily sealed forecasts (31_daily_forecast.py, 32_score_daily.py): a small block under the scoreboard.  True in both states:
# before the first batch it says so in one sentence; it calls the series running only when forecasts/daily/ledger.csv lists a batch.
def daily_block(res, daily):
    led = []
    for r in load_csv(daily, "ledger.csv") or []:
        k, d = num(r.get("n_forecasts")), str(r.get("forecast_date") or "")[:10]
        if k is not None and re.fullmatch(r"\d{4}-\d\d-\d\d", d):
            led.append((d, int(k), num(r.get("n_fresh"))))
    guide = f'<a href="{REPO}/blob/main/docs/daily-forecasts.md">how it works</a>'
    if not led:
        return (f'<div class="card" style="margin-top:12px"><p class="note" style="margin:0"><b>Daily forecasts.</b> A daily series of '
                f"sealed forecasts, one for each new open solicitation, is built and tested; the first batch has not been sealed yet "
                f"({guide}).</p></div>")
    led.sort()
    k = sum(x[1] for x in led)
    fresh = [x[2] for x in led if x[2] is not None]
    tiles = [(f"{len(led):,}", "daily batches sealed since " + nice(led[0][0])), (f"{k:,}", "forecasts in them")]
    if fresh and len(fresh) == len(led) and k:
        tiles.append((pct(sum(fresh) / k, 0), "fresh: first posted at most 3 days before the forecast"))
    s = load_json(res, "daily_forecast_scores.json")
    score = ""
    if isinstance(s, dict) and num(s.get("n_resolved")) is not None and num(s.get("n_forecasts")) is not None:
        tiles.append((f"{int(num(s['n_resolved'])):,}", f"of {int(num(s['n_forecasts'])):,} forecasts resolved so far"))
        sc = s.get("scores") if s.get("scores_published") is True and isinstance(s.get("scores"), dict) else {}
        o = sc.get("overall") or {}
        mod, ag = o.get("model") or {}, o.get("agency_rate") or {}
        if num(o.get("n_resolved")) is not None and num(mod.get("brier")) is not None and num(mod.get("auc")) is not None:
            score = (f" Scores on {int(num(o['n_resolved'])):,} resolved forecasts: Brier score {num(mod['brier']):.3f}"
                     + (f" against {num(ag['brier']):.3f} for the agency's past rate" if num(ag.get("brier")) is not None else "")
                     + f" (lower is better), AUC {num(mod['auc']):.3f} (0.5 is a coin flip).")
        else:
            score = " Scores are published once enough forecasts have resolved."
    kp = "".join(f'<div class="card kpi"><b>{esc(b)}</b><span>{esc(t)}</span></div>' for b, t in tiles)
    return (f'<div class="card" style="margin-top:12px"><p class="lead"><b>Daily forecasts.</b> Every morning each new open solicitation '
            f"gets a forecast of its chance of only one offer. The day's batch stays private (it names buying offices); its fingerprint "
            f'and counts are published the same morning in <a href="{REPO}/tree/main/forecasts/daily">forecasts/daily</a>.</p>'
            f'<div class="kpis" style="margin-top:0">{kp}</div>'
            f'<p class="note">An award counts only if it is dated on or after the day offers were due. Only totals are published.{score} '
            f"({guide})</p></div>")


# (function, anchor, label for the "What's new" line, figures it may show)
NEW_SECTIONS = [(sec_history, "history", "Since 2008", ["single_offer_history.png"]),
                (sec_threshold_history, "limits", "Pile-ups by year", ["threshold_history.png", "threshold_staggered.png"]),
                (sec_threshold_scan, "scan", "Every dollar limit", ["threshold_scan.png"]),
                (sec_drivers, "drivers", "Why only one bid?", ["drivers.png"]),
                (sec_outcomes, "outcomes", "After award", ["outcomes.png"]),
                (sec_split, "split", "Split purchases?", []),
                (sec_supplier, "supplier", "Who wins", ["supplier_base.png"]),
                (sec_quality, "quality", "Data quality", [])]


def new_sections(res):
    """[(anchor, label, html)] for the sections whose files are present"""
    out = []
    for fn, sid, label, _ in NEW_SECTIONS:
        h = fn(res)
        if h:
            out.append((sid, label, h))
    return out


def whats_new(present):
    if not present:
        return ""
    return ('<p class="new"><b>What\'s new:</b> the long history, FY2008 onward, with Defense shown separately: ' + " · ".join(f'<a href="#{sid}">{esc(label)}</a>' for sid, label, _ in present)
            + "</p>")


def gsa_trend_note(res):
    """sentence on GSA's single-offer share, first vs. last fiscal year, from atlas_civilian_by_fy.csv (scope "GSA only"), or ''"""
    data = load_csv(res, "atlas_civilian_by_fy.csv") or []
    ys = {}
    for r in data:
        y, v = num(r.get("fy")), num(r.get("share"))
        if r.get("scope") == "GSA only" and y is not None and v is not None:
            ys[int(y)] = v
    if len(ys) < 2:
        return ""
    a, b = min(ys), max(ys)
    move = "fell" if ys[b] < ys[a] else "rose"
    return (f"GSA's own share {move} from {pct(ys[a], 0)} in FY{a} to {pct(ys[b], 0)} in FY{b}, almost entirely in its supply orders; "
            f"that may reflect a change in how those automated orders are recorded rather than a change in competition. ")


def scope_text(present):
    """the page's scope line: what the FY2024-FY2026 sections cover and, once the long-history sections are on the page, what they cover"""
    t = ("How often do competed federal contracts get only one bid, and do purchases bunch under dollar limits? "
         "The FY2024 to FY2026 sections cover civilian agencies only; the Department of Defense is not in them. "
         "The threshold test and the agency table cover every civilian agency. The in-depth single-bid analysis and the forecasts cover "
         "five departments: VA, HHS, DHS, Interior and USDA.")
    if present:
        t += (" The long-history sections, FY2008 onward, also cover the Department of Defense, whose data are published 90 days late.")
    return t


def live_verification_note(lt):
    """a visible warning for the live table when 15_civilian_fytd.py could not check its download against USAspending's own count
    ("verified": false in live_fy<FY>_totals.json), or ''. Older files without the field get no note."""
    if not isinstance(lt, dict) or lt.get("verified") is not False:
        return ""
    return ('<p class="note"><b>Not verified:</b> on the day of this update, USAspending\'s count service could not confirm that '
            "the download was complete. These numbers may be too low. They will be checked again at the next update.</p>")


def number_sections(page):
    n = itertools.count(1)
    return re.sub(r'(<h2 class="num"[^>]*>)', lambda m: m.group(1) + f"{next(n)}. ", page)


# =====================================================================================================================
def main():
    # ---- data -----------------------------------------------------------------------------------
    agency = sorted(rows("atlas_by_agency.csv"), key=lambda r: -float(r["share"]))
    fy = rows("atlas_by_agency_fy.csv")
    kind = rows("atlas_by_kind.csv")
    setaside = sorted(rows("atlas_by_setaside.csv"), key=lambda r: -float(r["share"]))
    moved = {int(float(r["threshold"])): r for r in rows("threshold_sat_moved.csv")}
    model = {r["model"]: r for r in rows("model_out_of_time.csv")}
    total_k = sum(int(r["k_single"]) for r in agency)
    total_n = sum(int(r["n_eval"]) for r in agency)
    f2 = json.load(open(os.path.join(ROOT, F2["file"]), encoding="utf-8"))
    hashes = {}
    for line in open(os.path.join(FC, "SHA256SUMS.txt"), encoding="utf-8"):
        parts = line.split()
        if len(parts) >= 2:
            hashes[parts[1]] = parts[0]

    ours = next(v for k, v in model.items() if k.startswith("logistic"))
    base = next(v for k, v in model.items() if k.startswith("agency-wide"))

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
        gsa_change = gsa_trend_note(RES)
        civ_kpi = (f'<div class="card kpi"><b>{pct(exg["share"])}</b><span>of {int(exg["n_eval"]):,} competed awards across '
                   f'{exg["n_agencies"]} civilian agencies (all except GSA) got only one offer</span></div>')
        civ_html = f"""
<h2 class="num" id="civilian">Every civilian agency</h2>
<div class="card"><p class="sub" style="margin:0 0 12px">Across all {allc['n_agencies']} civilian agencies, <b>{pct(allc['share'])}</b> of {int(allc['n_eval']):,} competed awards got a single offer. GSA accounts for {pct(int(gsa['n_eval']) / int(allc['n_eval']), 0)} of those awards, mostly very small supply orders (median ${float(gsa['median_award_value']):,.0f}), so without GSA the share is <b>{pct(exg['share'])}</b> ({int(exg['n_eval']):,} awards). Outside GSA and the five departments above, it is <b>{pct(oth['share'])}</b> ({int(oth['n_eval']):,} awards).</p>
<div class="tablewrap"><table class="sortable civ">
  <thead><tr><th>Agency</th><th>Single-offer share</th><th>95% interval</th><th>Competed awards</th><th>FY2024 → FY2025 → FY2026</th></tr></thead>
  <tbody>{''.join(civ_rows)}</tbody></table></div>
  <p class="note">Tap a column heading to sort. Agencies with at least {CIV_MIN} competed awards with a usable offer count; the other {smallc['n_agencies']} smaller agencies together: {pct(smallc['share'])} of {int(smallc['n_eval']):,} awards. Same definitions as section 2. {gsa_change}Data downloaded {nice(allc['snapshot'])}, the same snapshot as every other result on this page.</p>
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

    # ---- live, refreshed every morning: this fiscal year so far (15_civilian_fytd.py) and today's open solicitations
    # (14_sam_daily_snapshot.py); each part is left out until its results exist ---------------------------------------
    LIVE_MIN = 50
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
        live_tiles.append(f'<div class="card kpi"><b>{pct(lx["share"])}</b><span>of {lx["n_eval"]:,} competed awards so far in FY{lt["fiscal_year"]} got only one offer (civilian agencies except GSA){" (not verified)" if live_verification_note(lt) else ""}</span></div>')
        live_parts.append(live_verification_note(lt) + f"""<p class="sub" style="margin:0 0 10px">Fiscal year {lt['fiscal_year']} so far: awards dated {nice(lt['data_from'])} to {nice(lt['data_to'])}, every civilian agency. With GSA's very small supply orders included, the share is {pct(lt['share'])} of {lt['n_eval']:,} awards. USAspending's latest data load: {nice(lt['usaspending_last_updated']) if lt['usaspending_last_updated'] else 'unknown'}.</p>
<div class="tablewrap"><table class="sortable civ">
  <thead><tr><th>Agency</th><th>Single-offer share so far</th><th>95% interval</th><th>Competed awards so far</th></tr></thead>
  <tbody>{ltr}</tbody></table></div>
<p class="note">Agencies with at least {LIVE_MIN} competed awards so far. The newest weeks are always incomplete: agencies have three business days to report an award, data loads happen on business days, and corrections keep arriving for weeks. Early-year shares are based on few awards and move a lot; compare them with the full-year shares in the <a href="#civilian">every civilian agency</a> section.</p>""")
    sam_path = os.path.join(RES, "sam_daily_latest.json")
    if os.path.exists(sam_path):
        sd = json.load(open(sam_path, encoding="utf-8"))
    if os.path.exists(sam_path) and sd.get("schema") == 2:          # from SAM.gov's API (14_sam_daily_snapshot.py)
        sp, win = sd["solicitation_notices_posted"], sd["window"]
        span = f"{nice(win['from'])} to {nice(win['to'])}"
        part = "" if win["days_pulled"] >= win["days"] else f" ({win['days_pulled']} of the {win['days']} days collected so far)"
        live_tiles.append(f'<div class="card kpi"><b>{sp["civilian"]["distinct_sol_numbers"]:,}</b><span>civilian solicitations '
                          f'posted or updated on SAM.gov, {span}{part} ({sp["dod"]["distinct_sol_numbers"]:,} more at Defense)</span></div>')
        srows = "".join(f"<tr><td>{esc(sam_name(d['department']))}</td><td data-v='{d['distinct_sol_numbers']}'>{d['distinct_sol_numbers']:,}</td></tr>"
                        for d in sd["top10_civilian_departments"])
        live_parts.append(f"""<h3 style="margin:22px 0 8px;font-size:1.05rem">Solicitations posted or updated (SAM.gov, {span})</h3>
<div class="tablewrap"><table class="sortable">
  <thead><tr><th>Civilian department</th><th>Solicitations</th></tr></thead>
  <tbody>{srows}</tbody></table></div>
<p class="note">Distinct solicitation numbers with a solicitation notice posted on these days, new or amended, from SAM.gov's official Get Opportunities API. Defense solicitations are public right away, even though Defense awards are published 90 days late.</p>""")
    elif os.path.exists(sam_path):
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
<h2 class="num" id="tracking">Forecast progress (updated daily)</h2>
<div class="card"><div class="tablewrap"><table>
  <thead><tr><th>PIO-F2 quantity</th><th>Forecast (80% range)</th><th>So far</th><th>95% interval so far</th><th>So far vs. range</th></tr></thead>
  <tbody>{''.join(trs)}</tbody></table></div>
  <p class="note">Provisional reading from {trk['n_base_actions']:,} civilian awards dated {nice(trk['data_from'])} to {nice(trk['data_to'])} (checked {nice(trk['as_of'])}). This is not the score: the quarter runs through Dec 31, agencies keep reporting for weeks after, and early readings swing widely, as the wide intervals show. The official score uses data pulled on or after {nice(f2['resolution_date'])}.</p>
</div>"""

    f1_hash = next(v for k, v in hashes.items() if k.startswith("PIO-F1"))
    f2_hash = next(v for k, v in hashes.items() if k.startswith("PIO-F2"))

    # ---- long-history sections (FY2008 onward) ---------------------------------------------------
    present = new_sections(RES)
    daily_html = daily_block(RES, os.path.join(FC, "daily"))
    new_html = "".join(h for _, _, h in present)
    new_line = whats_new(present)

    # ---- copy charts ----------------------------------------------------------------------------
    os.makedirs(os.path.join(DOCS, "figures"), exist_ok=True)
    for png in ("threshold_shift.png", "bunching_by_quarter.png", "single_offer_by_agency.png"):
        shutil.copy2(os.path.join(ROOT, "figures", png), os.path.join(DOCS, "figures", png))
    for _, _, _, pngs in NEW_SECTIONS:
        for png in pngs:
            if f"figures/{png}" in new_html:
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
body {{ margin:0; background:var(--bg); color:var(--ink); font:16px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; overflow-wrap:break-word; }}
.wrap {{ max-width:1040px; margin:0 auto; padding:28px 16px 56px; }}
h1 {{ font-size:1.8rem; line-height:1.2; margin:0 0 6px; }} h2 {{ font-size:1.25rem; margin:36px 0 10px; }}
.sub {{ color:var(--muted); margin:0 0 4px; }}
.new {{ margin:10px 0 0; padding:8px 12px; background:var(--soft); border-radius:10px; font-size:.92rem; }}
.lead {{ margin:0 0 12px; }}
ul.tight {{ margin:12px 0 0; padding-left:20px; }} ul.tight li {{ margin:0 0 6px; }}
.kpis {{ display:grid; gap:12px; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); margin-top:20px; }}
.kpis > * {{ min-width:0; }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:14px; padding:16px 18px; min-width:0; }}
.card > img + * {{ margin-top:12px; }}
.kpi b {{ display:block; font-size:1.9rem; color:var(--accent); line-height:1.1; }} .kpi span {{ color:var(--muted); font-size:.92rem; }}
img {{ max-width:100%; height:auto; display:block; background:#fff; border-radius:10px; }}
.note {{ color:var(--muted); font-size:.92rem; margin:8px 0 0; }}
.warn {{ background:var(--warn); border-color:transparent; }}
.tablewrap {{ overflow-x:auto; max-width:100%; -webkit-overflow-scrolling:touch; }}
table {{ width:100%; border-collapse:collapse; font-size:.94rem; }}
th, td {{ text-align:left; padding:8px 8px; border-bottom:1px solid var(--line); white-space:nowrap; }}
th {{ color:var(--muted); font-weight:600; cursor:pointer; user-select:none; }} th:hover {{ color:var(--accent); }}
table.wrapcells td {{ white-space:normal; }} table.wrapcells td:last-child {{ white-space:nowrap; }}
table.civ td:first-child {{ white-space:normal; min-width:190px; }}
table.fit th, table.fit td {{ white-space:normal; min-width:5.5em; }} table.fit td[data-v] {{ white-space:nowrap; }}
.bar {{ display:inline-block; width:84px; height:8px; margin-right:8px; vertical-align:middle; background:var(--soft); border-radius:4px; overflow:hidden; }}
.bar i {{ display:block; height:100%; background:var(--accent); border-radius:4px; }}
code {{ font-size:.8rem; word-break:break-all; white-space:normal; }}
.pill {{ display:inline-block; padding:2px 8px; border-radius:999px; background:var(--soft); color:var(--accent); font-size:.8rem; font-weight:600; }}
a {{ color:var(--accent); }} .theme {{ float:right; border:1px solid var(--line); background:var(--card); color:var(--ink); border-radius:999px; padding:4px 10px; cursor:pointer; font:inherit; font-size:.85rem; }}
footer {{ margin-top:40px; color:var(--muted); font-size:.88rem; }}
@media (max-width:560px) {{
  h1 {{ font-size:1.45rem; }} .card {{ padding:14px 12px; }} .kpi b {{ font-size:1.6rem; }}
  table {{ font-size:.88rem; }} th, td {{ padding:7px 6px; }} table.civ td:first-child {{ min-width:150px; }}
}}
</style>
</head>
<body><div class="wrap">
<button class="theme" id="theme" type="button">Theme</button>
<h1>Federal Contract Competition: Findings &amp; Forecasts</h1>
<p class="sub">{scope_text(present)}</p>
<p class="sub">By <a href="{AUTHOR_URL}">{AUTHOR}</a> · data through {nice(CFG["analysis"]["window_end"])} · <a href="explorer.html"><b>explore the data</b></a> · <a href="brief.html">one-page brief</a> · <a href="{REPO}">code and data</a> · <a href="methodology">methodology</a></p>
{new_line}

<div class="kpis">
  <div class="card kpi"><b>{pct(total_k / total_n)}</b><span>of {total_n:,} competed awards in five departments got only one offer</span></div>
  {civ_kpi}
  <div class="card kpi"><b>{float(m350['ratio_pre']):.2f} → {float(m350['ratio_post']):.2f}</b><span>pile-up just under the new $350K line, before vs. after it took effect (1.0 = none)</span></div>
  <div class="card kpi"><b>{float(ours['auc']):.2f}</b><span>accuracy (AUC) of the single-bid forecast on unseen data, vs. {float(base['auc']):.2f} for a simple baseline</span></div>
  <div class="card kpi"><b>{F1["n_solicitations"]}</b><span>open solicitations with sealed single-bid forecasts, scored by {nice(F1["checks"][-1])}</span></div>
</div>

{live_html}

<h2 class="num" id="threshold">When the threshold moved, the pile-up moved</h2>
<div class="card">
  <img src="figures/threshold_shift.png" alt="Award counts near $250K and $350K before and after the October 2025 threshold change">
  <p class="note">On Oct 1, 2025, the simplified-acquisition threshold rose from $250K to $350K. Across all civilian agencies, the pile-up just under $250K fell from {float(m250['ratio_pre']):.2f} to {float(m250['ratio_post']):.2f}, and a new one formed under $350K ({float(m350['ratio_pre']):.2f} → {float(m350['ratio_post']):.2f}). Comparison lines at $200K and $450K, where nothing changed, did not move.</p>
</div>
<div class="card" style="margin-top:12px">
  <img src="figures/bunching_by_quarter.png" alt="Bunching ratio by fiscal quarter for $250K and $350K">
  <p class="note">Bunching ratio by fiscal quarter, with 95% intervals. Purchase orders and definitive contracts of all civilian agencies.</p>
</div>

<h2 class="num" id="five">Five departments: about one in four competed awards got a single offer</h2>
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
{new_html}

<h2 class="num" id="scoreboard">Forecast scoreboard</h2>
<div class="card"><div class="tablewrap"><table class="wrapcells">
  <thead><tr><th>Forecast</th><th>What it predicts</th><th>Resolves</th><th>Result</th></tr></thead>
  <tbody>
  <tr><td>PIO-F1</td><td>Chance of a single offer for each of {F1["n_solicitations"]} open civilian solicitations</td><td>{", ".join(nice(d) for d in F1["checks"])}</td><td><span class="pill">Pending</span></td></tr>
  <tr><td>PIO-F2</td><td>FY2027 Q1 pile-up under $350K: {q350['forecast']:.2f} (80% range {q350['interval80'][0]:.2f}–{q350['interval80'][1]:.2f}); under $250K: {q250['forecast']:.2f} ({q250['interval80'][0]:.2f}–{q250['interval80'][1]:.2f})</td><td>Feb 28, 2027</td><td>{f2_result}</td></tr>
  </tbody></table></div>
  <p class="note">Both forecasts were sealed on {nice(F1["sealed"])}, before any outcome existed. Fingerprints (SHA-256): PIO-F1 <code>{f1_hash}</code> · PIO-F2 <code>{f2_hash}</code>. PIO-F2 is <a href="{REPO}/blob/main/forecasts/PIO-F2-threshold-fy2027q1-20261024.json">published in full</a>; PIO-F1 will be posted unchanged when it is scored.</p>
</div>
{daily_html}
{track_html}

<h2>How to read this</h2>
<div class="card warn">
  These are signals about markets and buying patterns, not about people. A single bid or a pile-up under a threshold can have ordinary explanations, and none of these numbers shows wrongdoing. Award data has no unit prices, so this does not measure whether single-bid contracts cost more. Offer counts are entered by contracting staff and can contain errors. FY2026 included a government shutdown and reporting changes.
</div>

<footer>Built by {AUTHOR} with AI assistance (Claude Code). Data: <a href="https://www.usaspending.gov">USAspending.gov</a> and <a href="https://sam.gov">SAM.gov</a> (public). Code MIT; text and figures CC BY 4.0. Found an error? <a href="{REPO}/issues">Open an issue</a>. Page built {date.today():%b %d, %Y}.</footer>
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
    page = number_sections(page)
    with open(os.path.join(DOCS, "index.html"), "w", encoding="utf-8") as f:
        f.write(page)
    print("wrote docs/index.html" + (f" (long-history sections: {', '.join(s for s, _, _ in present)})" if present else ""))


if __name__ == "__main__":
    main()
