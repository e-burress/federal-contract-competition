"""
build_brief.py - build a one-page brief (docs/brief.html) from the files in results/ and forecasts/.

Run from the repository root:
    python -I scripts/build_brief.py                 writes docs/brief.html
    python -I scripts/build_brief.py --check-print   also prints it to a temporary PDF with Microsoft Edge or Chrome and checks
                                                     that it fits on one US Letter page
    python -I scripts/build_brief.py --pdf           also writes dist/brief.pdf (dist/ is not published)

The page is for a busy reader: headline numbers, five findings with small charts, how the project is made, and its limits.
Every number is read from the result files when the page is built; the charts are inline SVG drawn from the same files.
The page loads nothing from the internet. A finding whose result file is missing is left out, and the build says which file.
Nothing on the page is below agency level: no buying offices, no companies.
"""
import argparse
import csv
import html
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from datetime import date
from pathlib import Path
from statistics import mean

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAT_NOW = 350_000                 # the simplified-acquisition threshold since October 1, 2025 (FAR 2.101)
SAT_OLD = 250_000                 # the limit it replaced
SAT_CHANGE_DATE = "October 1, 2025"
WORDS = {2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten"}


# =====================================================================================================================
# reading the result files: every read goes through Src, which remembers the files that were missing or unreadable
# =====================================================================================================================
class Src:
    def __init__(self, root):
        self.root = root
        self.res = os.path.join(root, "results")
        self.missing = []                     # file names (relative to the root) that a part of the page needed and did not find
        self.cfg = {}
        try:
            with open(os.path.join(root, "config.toml"), "rb") as f:
                self.cfg = tomllib.load(f)
        except (OSError, tomllib.TOMLDecodeError):
            self.missing.append("config.toml")

    def _path(self, name, folder):
        return os.path.join(self.root, folder, name) if folder else os.path.join(self.root, name)

    def _miss(self, name, folder):
        rel = f"{folder}/{name}" if folder else name
        if rel not in self.missing:
            self.missing.append(rel)

    def csv(self, name, folder="results"):
        p = self._path(name, folder)
        try:
            with open(p, encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            return rows or self._miss(name, folder)
        except (OSError, csv.Error, UnicodeDecodeError):
            return self._miss(name, folder)

    def json(self, name, folder="results"):
        p = self._path(name, folder)
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError, UnicodeDecodeError):
            return self._miss(name, folder)

    def text(self, name):
        try:
            with open(os.path.join(self.root, name), encoding="utf-8") as f:
                return f.read()
        except (OSError, UnicodeDecodeError):
            return ""


# =====================================================================================================================
# formatting
# =====================================================================================================================
def esc(s):
    return html.escape(str(s))


def fl(x):
    """float, or None for blanks, NaN and junk"""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def pct(x, d=1):
    return f"{100 * float(x):.{d}f}%"


def r2(x):
    return f"{float(x):.2f}"


def n(x):
    return f"{int(round(float(x))):,}"


def big_count(x):
    """7106783 -> '7.1 million'; smaller numbers with commas"""
    x = float(x)
    return f"{x / 1e6:.1f} million" if x >= 1e6 else n(x)


def money(x):
    x = float(x)
    if x >= 1e6:
        return f"${x / 1e6:g}M"
    if x >= 1e4:
        return f"${x / 1e3:g}K"
    return f"${x:,.0f}"


def signed(x, d=0):
    return "0" if round(x, d) == 0 else f"{x:+.{d}f}".replace("-", "−")


def series(items):
    items = [i for i in items if i]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def fy_text(years):
    """[2008, ..., 2021] -> 'FY2008 to FY2021'; gaps are listed as separate runs"""
    runs = []
    for y in sorted({int(y) for y in years}):
        if runs and y == runs[-1][1] + 1:
            runs[-1][1] = y
        else:
            runs.append([y, y])
    return series([f"FY{a}" if a == b else f"FY{a} to FY{b}" for a, b in runs])


def long_date(iso):
    return date.fromisoformat(str(iso)[:10]).strftime("%B %d, %Y").replace(" 0", " ")


def one_in(share):
    """0.228 -> 'about one in four'"""
    k = round(1 / share) if share > 0 else 0
    return f"about one in {WORDS[k]}" if k in WORDS else ""


# =====================================================================================================================
# charts: small inline SVG line charts, labeled directly at the end of each line, readable in black and white
# =====================================================================================================================
def _nice_max(v, step):
    return step * math.ceil(v / step - 1e-9)


def _declutter(labels, lo, hi, gap=9.0):
    """labels [(y, text, cls)] -> the same, moved apart vertically so none overlap, kept inside [lo, hi]"""
    labels = sorted(labels)
    ys = [y for y, _, _ in labels]
    for i in range(1, len(ys)):
        ys[i] = max(ys[i], ys[i - 1] + gap)
    if ys and ys[-1] > hi:
        ys[-1] = hi
        for i in range(len(ys) - 2, -1, -1):
            ys[i] = min(ys[i], ys[i + 1] - gap)
    ys = [max(lo, y) for y in ys]
    return [(y, t, c) for y, (_, t, c) in zip(ys, labels)]


def svg_chart(panels, y_max, y_ticks, y_fmt, aria, ref=None, vline=None, w=340, h=124, label_w=50):
    """panels: [{title, xmin, xmax, xticks, xfmt, series: [{label, pts [(x, y)], cls, on (set of x drawn thick) or None}]}]"""
    left, top, bottom, gap = 30, 16, 16, 10
    k = len(panels)
    pw = (w - left - gap * (k - 1)) / k
    out = [f'<svg class="chart" viewBox="0 0 {w} {h}" role="img" aria-label="{esc(aria)}">']

    def Y(v):
        return top + (y_max - v) / y_max * (h - top - bottom)
    for i, p in enumerate(panels):
        x0 = left + i * (pw + gap)
        x1 = x0 + pw - label_w
        span = (p["xmax"] - p["xmin"]) or 1

        def X(v, x0=x0, x1=x1, p=p, span=span):
            return x0 + (v - p["xmin"]) / span * (x1 - x0)
        for t in y_ticks:
            out.append(f'<line class="grid" x1="{x0:.1f}" x2="{x1:.1f}" y1="{Y(t):.1f}" y2="{Y(t):.1f}"/>')
            if i == 0:
                out.append(f'<text x="{x0 - 4:.1f}" y="{Y(t) + 3:.1f}" text-anchor="end">{esc(y_fmt(t))}</text>')
        if ref is not None and ref <= y_max:
            out.append(f'<line class="ref" x1="{x0:.1f}" x2="{x1:.1f}" y1="{Y(ref):.1f}" y2="{Y(ref):.1f}"/>')
        if vline is not None and p["xmin"] <= vline[0] <= p["xmax"]:
            vx = X(vline[0])
            out.append(f'<line class="ref" x1="{vx:.1f}" x2="{vx:.1f}" y1="{top:.1f}" y2="{h - bottom:.1f}"/>')
            out.append(f'<text x="{vx + 3:.1f}" y="{top + 8:.1f}">{esc(vline[1])}</text>')
        if p.get("title"):
            out.append(f'<text class="pt" x="{x0:.1f}" y="{top - 5:.1f}">{esc(p["title"])}</text>')
        for t in p["xticks"]:
            out.append(f'<text x="{X(t):.1f}" y="{h - 4:.1f}" text-anchor="middle">{esc(p["xfmt"](t))}</text>')
        labels = []
        for s in p["series"]:
            pts = sorted(s["pts"])
            if not pts:
                continue
            path = " ".join(f"{X(x):.1f},{Y(y):.1f}" for x, y in pts)
            out.append(f'<polyline class="{s["cls"]}" points="{path}"/>')
            on = s.get("on")
            if on:
                for (xa, ya), (xb, yb) in zip(pts, pts[1:]):
                    if xa in on and xb in on:
                        out.append(f'<line class="on" x1="{X(xa):.1f}" y1="{Y(ya):.1f}" x2="{X(xb):.1f}" y2="{Y(yb):.1f}"/>')
                xs = [x for x, _ in pts]
                for j, (x, y) in enumerate(pts):
                    if x in on and (j == 0 or xs[j - 1] not in on) and (j == len(xs) - 1 or xs[j + 1] not in on):
                        out.append(f'<circle class="dot" cx="{X(x):.1f}" cy="{Y(y):.1f}" r="2.2"/>')
            labels.append((Y(pts[-1][1]) + 3, s["label"], "lab"))
        for y, t, c in _declutter(labels, top + 4, h - bottom):
            out.append(f'<text class="{c}" x="{x1 + 4:.1f}" y="{y:.1f}">{esc(t)}</text>')
    out.append("</svg>")
    return "".join(out)


# =====================================================================================================================
# the parts of the page.  Each returns None when its files are missing or unreadable.
# =====================================================================================================================
def five_years(src):
    rows = src.csv("atlas_by_agency_fy.csv") or []
    return sorted({int(fl(r["fy"])) for r in rows if fl(r.get("fy")) is not None})


def tile_single(src):
    h = src.json("atlas_headline.json")
    if not h:
        return None
    yrs = five_years(src)
    return (pct(h["share"]), f"of {n(h['n_eval'])} competed awards in five civilian departments got only one offer"
            + (f" ({fy_text(yrs)})" if yrs else ""))


def sat_moved(src):
    rows = src.csv("threshold_sat_moved.csv")
    return {int(fl(r["threshold"])): r for r in rows if fl(r.get("threshold")) is not None} if rows else None


def tile_pileup(src):
    m = sat_moved(src)
    if not m or SAT_NOW not in m:
        return None
    r = m[SAT_NOW]
    return (f"{r2(r['ratio_pre'])} → {r2(r['ratio_post'])}",
            f"pile-up just under the {money(SAT_NOW)} limit, the year before vs. the year after it took effect (1.0 = none)")


def history_all(src):
    """sector -> {fy: row} for all award kinds together (history_by_fy.csv)"""
    rows = src.csv("history_by_fy.csv")
    if not rows:
        return None
    out = {}
    for r in rows:
        if r.get("award_kind", "all") == "all" and fl(r.get("share")) is not None and fl(r.get("n_eval")):
            out.setdefault(r["sector"], {})[int(fl(r["fy"]))] = r
    return out or None


def tile_history(src):
    h = history_all(src)
    if not h:
        return None
    years = sorted({y for v in h.values() for y in v})
    total = sum(float(r["n_eval"]) for v in h.values() for r in v.values())
    return (big_count(total), f"competed awards in the long history, {fy_text(years)}, civilian agencies and Defense")


def model_rows(src):
    rows = src.csv("model_out_of_time.csv")
    if not rows:
        return None
    ours = next(r for r in rows if r["model"].startswith("logistic"))
    base = next(r for r in rows if r["model"].startswith("agency-wide"))
    return ours, base


V2_SHIPPED_FALLBACK = "logistic v2"       # used when the card names no shipped model (or one with no pooled row)
V2_BASELINES = ("sector rate (baseline)", "agency rate (baseline)", "office history only (baseline)")


def model_v2_rows(src):
    """(shipped model row, best simple baseline row) from the pooled out-of-time test in results/model_v2_card.json, or None.
    The card is optional: when it is missing, nothing is noted as missing (the tile falls back to version 1)."""
    if not os.path.exists(os.path.join(src.res, "model_v2_card.json")):
        return None
    card = src.json("model_v2_card.json") or {}
    pooled = {r.get("model"): r for r in (card.get("evaluation") or {}).get("pooled") or [] if r.get("sector") == "all"}
    ours = pooled.get((card.get("shipped") or {}).get("name_in_evaluation")) or pooled.get(V2_SHIPPED_FALLBACK)
    bases = [pooled[b] for b in V2_BASELINES if b in pooled and fl(pooled[b].get("auc")) is not None]
    if not ours or fl(ours.get("auc")) is None or not bases:
        return None
    return ours, max(bases, key=lambda r: float(r["auc"]))


def tile_model(src):
    v2 = model_v2_rows(src)
    if v2:
        ours, base = v2
        return (r2(ours["auc"]), f"forecast score (AUC) of model version 2, the model of the daily forecasts, on years it never saw, "
                f"vs. {r2(base['auc'])} for its best simple baseline (0.5 = coin flip)")
    m = model_rows(src)
    if not m:
        return None
    ours, base = m
    return (r2(ours["auc"]), f"forecast score (AUC) on awards the model never saw, vs. {r2(base['auc'])} for a department-wide "
            f"baseline (0.5 = coin flip)")


# ---- findings: (title, finding sentence(s), why-it-matters or caveat, chart svg or "") ---------------------------------------
def f_threshold(src):
    m = sat_moved(src)
    if not m or SAT_NOW not in m or SAT_OLD not in m:
        return None
    new, old = m[SAT_NOW], m[SAT_OLD]
    text = (f"On {SAT_CHANGE_DATE}, the simplified-acquisition threshold (the dollar line below which agencies can use faster, simpler "
            f"buying procedures) rose from {money(SAT_OLD)} to {money(SAT_NOW)}. Across civilian agencies, the pile-up just under "
            f"{money(SAT_OLD)} fell from {r2(old['ratio_pre'])} to {r2(old['ratio_post'])}, and a new one under {money(SAT_NOW)} grew "
            f"from {r2(new['ratio_pre'])} to {r2(new['ratio_post'])}.")
    plac = sorted(t for t in m if t not in (SAT_NOW, SAT_OLD))
    still = [t for t in plac if fl(m[t].get("diff_lo")) is not None and fl(m[t]["diff_lo"]) <= 0 <= fl(m[t]["diff_hi"])]
    why = ("The bunching ratio compares the awards just under a line with the number a smooth curve predicts; 1.0 means no pile-up.")
    if plac:
        why += (f" Placebo lines at {series([money(t) for t in plac])}, where nothing changed, "
                + ("did not clearly move." if len(still) == len(plac) else
                   f"did not clearly move at {series([money(t) for t in still])}." if still else "also moved, so read this with care."))
    followed = float(new["ratio_post"]) > float(new["ratio_pre"]) and float(old["ratio_post"]) < float(old["ratio_pre"])
    return ("When the limit moved, the pile-up moved with it" if followed else f"The move to {money(SAT_NOW)}", text, why, "")


def f_single(src):
    h = src.json("atlas_headline.json")
    if not h:
        return None
    share = float(h["share"])
    yrs = five_years(src)
    text = (f"In five civilian departments (VA, HHS, DHS, Interior and USDA), {pct(share)} of {n(h['n_eval'])} competed awards with a "
            f"usable offer count got exactly one offer" + (f" ({fy_text(yrs)})" if yrs else "") + ".")
    dep = src.csv("atlas_by_agency.csv")
    if dep:
        dep = sorted(dep, key=lambda r: float(r["share"]))
        text += f" By department it ranged from {pct(dep[0]['share'])} ({esc(dep[0]['agency'])}) to {pct(dep[-1]['share'])} ({esc(dep[-1]['agency'])})."
    tot = {r["scope"]: r for r in (src.csv("atlas_civilian_totals.csv") or [])}
    exg = tot.get("all civilian agencies except GSA")
    if exg:
        text += (f" Across every civilian agency except GSA, it was {pct(exg['share'])} of {n(exg['n_eval'])} awards.")
    why = ("A single offer means the buyer had no competing bid for that award. Offer counts are entered by contracting staff, and GSA is "
           "set apart because its very small supply orders changed how offers are recorded.")
    title = f"{one_in(share).capitalize()} competed awards got a single offer" if one_in(share) else "Competed awards with a single offer"
    return (title, text, why, "")


HIST = [("civilian except GSA", "Civilian", "s1"), ("Department of Defense", "Defense", "s2"), ("GSA", "GSA", "s3")]


def f_history(src):
    h = history_all(src)
    if not h:
        return None
    parts = []
    for sector, where in (("civilian except GSA", "in civilian agencies except GSA"), ("Department of Defense", "at Defense")):
        ys = h.get(sector) or {}
        if len(ys) >= 2:
            a, b = min(ys), max(ys)
            sa, sb = float(ys[a]["share"]), float(ys[b]["share"])
            verb = "fell" if sb < sa else "rose" if sb > sa else "was unchanged"
            parts.append(f"{verb} {where} from {pct(sa)} in FY{a} to {pct(sb)} in FY{b}")
    if not parts:
        return None
    text = "Among competed awards with a usable offer count, the single-offer share " + " and ".join(parts) + "."
    why = ("The data cannot tell a change in competition from a change in how offers were recorded. GSA's line says little about "
           "competition: its very small supply orders change in number and in how offers are recorded.")
    shown = [(lab, cls, ys) for key, lab, cls in HIST if len(ys := h.get(key) or {}) >= 2]
    years = sorted({y for _, _, ys in shown for y in ys})
    top = _nice_max(max(float(r["share"]) for _, _, ys in shown for r in ys.values()), 0.1)
    step = 0.1 if top <= 0.4 else 0.2
    chart = svg_chart(
        [{"xmin": years[0], "xmax": years[-1], "xticks": _xticks(years), "xfmt": lambda y: f"FY{str(y)[2:]}",
          "series": [{"label": lab, "pts": [(y, float(r["share"])) for y, r in ys.items()], "cls": cls} for lab, cls, ys in shown]}],
        top, [step * i for i in range(int(round(top / step)) + 1)], lambda v: f"{100 * v:.0f}%",
        "Line chart: share of competed awards with a single offer, by fiscal year, for civilian agencies except GSA, Defense and GSA")
    fell = all(p.startswith("fell") for p in parts)
    return (f"Since FY{years[0]}, single-offer shares fell" if fell else f"Single-offer shares since FY{years[0]}", text, why,
            _fig(chart, "Share with one offer, by year. Civilian: except GSA"))


def _xticks(years):
    a, b = years[0], years[-1]
    if b - a <= 4:
        return list(range(a, b + 1))
    mid = a + (b - a) // 2
    return [a, mid, b]


def _fig(svg, caption):
    return f'<figure>{svg}<figcaption>{esc(caption)}</figcaption></figure>'


# -- pile-ups under each simplified-acquisition limit, by fiscal year (threshold_history.csv); same rules as build_report.py
def th_table(src):
    t = {}
    for r in src.csv("threshold_history.csv") or []:
        if None not in (fl(r.get("ratio")), fl(r.get("ci_lo")), fl(r.get("ci_hi")), fl(r.get("line")), fl(r.get("fy"))):
            t[(r["sector"], int(fl(r["line"])), int(fl(r["fy"])))] = r
    return t


def _active(r):
    return str(r.get("active", "")).strip().lower() == "true"


def th_levels(t):
    by = {}
    for (s, ln, _), r in t.items():
        by.setdefault((s, ln), {True: [], False: []})[_active(r)].append(float(r["ratio"]))
    out = {}
    for (s, _), v in sorted(by.items()):
        if v[True] and v[False]:
            o = out.setdefault(s, {"on": [], "off": [], "higher": 0, "pairs": 0})
            o["on"] += v[True]
            o["off"] += v[False]
            o["pairs"] += 1
            o["higher"] += mean(v[True]) > mean(v[False])
    return out


def th_changes(t):
    """each time a line became the limit inside the years present: last year before the change and first year with only the new line"""
    out = []
    for sector in sorted({k[0] for k in t}):
        years = sorted({k[2] for k in t if k[0] == sector})
        lines = sorted({k[1] for k in t if k[0] == sector})
        on = {y: [ln for ln in lines if (sector, ln, y) in t and _active(t[(sector, ln, y)])] for y in years}
        for new in lines:
            first = next((y for y in years if new in on[y]), None)
            if first is None or first == years[0]:
                continue
            before = max(y for y in years if y < first)
            after = next((y for y in years if y >= first and on[y] == [new]), None)
            if after is None or len(on[before]) != 1:
                continue
            old = on[before][0]
            keys = [(sector, new, before), (sector, new, after), (sector, old, before), (sector, old, after)]
            if all(k in t for k in keys):
                nb, na, ob, oa = (float(t[k]["ratio"]) for k in keys)
                out.append({"ok": na > nb and oa < ob})
    return out


TH_SECTORS = {"civilian": "civilian agencies", "defense": "Defense"}


def f_limits(src):
    t = th_table(src)
    if not t:
        return None
    years = sorted({k[2] for k in t})
    lv = th_levels(t)
    ch = th_changes(t)
    if not lv and not ch:
        return None
    s = []
    if lv:
        pairs, higher = sum(o["pairs"] for o in lv.values()), sum(o["higher"] for o in lv.values())
        each = (f"for all {pairs} line-and-sector pairs" if pairs > 1 else "for the one line-and-sector pair") if higher == pairs \
            else f"for {higher} of the {pairs} line-and-sector pairs"
        s.append(f"In {fy_text(years)}, the pile-up under a simplified-acquisition limit was larger in the years it was the limit than in "
                 f"the years it was not, {each}.")
    if ch:
        ok = sum(c["ok"] for c in ch)
        each = (f"At each of the {len(ch)} changes" if len(ch) > 1 else "At the one change") if ok == len(ch) else f"At {ok} of the {len(ch)} changes"
        s.append(f"{each} of the limit (civilian and Defense counted separately), the pile-up under the new line grew and the one under "
                 f"the old line shrank.")
    why = ("A pile-up shows a pattern, not its cause: budgets written to fit the rules, small-business rules tied to the line and "
           "contracts split to stay under it all look the same here.")
    panels = []
    for sec, lab in TH_SECTORS.items():
        lines = sorted({k[1] for k in t if k[0] == sec})
        ser = []
        for ln in lines:
            pts = [(y, float(t[(sec, ln, y)]["ratio"])) for y in years if (sec, ln, y) in t]
            on = {y for y in years if (sec, ln, y) in t and _active(t[(sec, ln, y)])}
            ser.append({"label": money(ln), "pts": pts, "cls": "thin", "on": on})
        if ser:
            panels.append({"title": lab.capitalize() if sec == "civilian" else lab, "xmin": years[0], "xmax": years[-1],
                           "xticks": [years[0], years[-1]], "xfmt": lambda y: f"FY{str(y)[2:]}", "series": ser})
    chart = ""
    if panels:
        top = _nice_max(max(float(r["ratio"]) for r in t.values()), 0.5)
        chart = _fig(svg_chart(panels, top, [0.5 * i for i in range(int(top / 0.5) + 1) if i % 2 == 0 or top <= 2], lambda v: f"{v:g}",
                               "Line charts: bunching ratio under each simplified-acquisition limit by fiscal year, civilian agencies and "
                               "Defense; thick where the line was the limit that year", ref=1.0, label_w=34),
                     "Pile-up by year. Thick: it was the limit. Dashed: none")
    all_ok = (not lv or all(o["higher"] == o["pairs"] for o in lv.values())) and all(c["ok"] for c in ch)
    return ("The pile-up follows the limit" if all_ok else "Does the pile-up follow the limit?", " ".join(s), why, chart)


# -- the 2018 change, agency by agency (threshold_staggered.csv); same rules as build_report.py
def stag_table(src):
    d = {}
    for r in src.csv("threshold_staggered.csv") or []:
        if None not in (fl(r.get("ratio")), fl(r.get("ci_lo")), fl(r.get("ci_hi")), fl(r.get("line")), fl(r.get("event_quarter"))):
            kind = "actual" if r.get("dates") == "actual" else "placebo"
            d.setdefault((r["sector"], kind, int(fl(r["line"]))), {})[int(fl(r["event_quarter"]))] = r
    return d


def stag_summary(q):
    pre = [float(r["ratio"]) for k, r in q.items() if k < 0]
    post = [float(r["ratio"]) for k, r in q.items() if k >= 0]
    jumps = [(float(q[k]["ratio"]) - float(q[k - 1]["ratio"]), k) for k in sorted(q) if k - 1 in q]
    if len(pre) < 2 or len(post) < 2 or not jumps:
        return None
    at = max(jumps)[1]
    return {"pre": mean(pre), "post": mean(post), "npre": len(pre), "npost": len(post), "at": at, "from": q[at - 1], "to": q[at]}


def f_staggered(src):
    d = stag_table(src)
    act, plc = d.get(("civilian", "actual", 250_000), {}), d.get(("civilian", "placebo", 250_000), {})
    a = stag_summary(act)
    if not a:
        return None
    text = ("Civilian agencies raised the limit from $150K to $250K on their own dates in 2018 and after. Lined up on each agency's own "
            f"date, the pile-up under $250K averaged {r2(a['pre'])} in the {a['npre']} quarters before the switch and {r2(a['post'])} in "
            f"the {a['npost']} quarters from it on.")
    p = stag_summary(plc)
    if p:
        expect = a["at"] + 4
        text += (f" With every date moved one year earlier (a placebo), the largest rise came at quarter {signed(p['at'])}"
                 + (", four quarters later, as a real effect predicts." if p["at"] == expect else
                    f", not at quarter {signed(expect)} where a real effect would put it."))
    overlap = float(a["to"]["ci_lo"]) <= float(a["from"]["ci_hi"])
    why = (f"The largest one-quarter rise came at quarter {signed(a['at'])}. "
           + ("The 95% intervals of that quarter and the one before overlap, so the timing rests on the whole pattern, not on one quarter."
              if overlap else "The 95% intervals of that quarter and the one before do not overlap."))
    ser = [{"label": "Own dates", "pts": [(k, float(r["ratio"])) for k, r in act.items()], "cls": "s1"}]
    if plc:
        ser.append({"label": "Placebo", "pts": [(k, float(r["ratio"])) for k, r in plc.items()], "cls": "s2"})
    qs = sorted(set(act) | set(plc))
    top = _nice_max(max(v for s in ser for _, v in s["pts"]), 0.5)
    chart = _fig(svg_chart([{"xmin": qs[0], "xmax": qs[-1], "xticks": [q for q in qs if q % 4 == 0], "xfmt": lambda q: signed(q),
                             "series": ser}], top, [0.5 * i for i in range(int(top / 0.5) + 1)], lambda v: f"{v:g}",
                           "Line chart: bunching ratio under $250K by quarter around each civilian agency's own 2018 switch date, "
                           "with placebo dates one year earlier", ref=1.0, vline=(0, "switch")),
                 "Pile-up under $250K, by quarter from the switch")
    return ("The 2018 change, agency by agency", text, why, chart)


FINDINGS = [("threshold", f_threshold, "threshold_sat_moved.csv"), ("single offers", f_single, "atlas_headline.json"),
            ("since FY2008", f_history, "history_by_fy.csv"), ("limits over time", f_limits, "threshold_history.csv"),
            ("2018 agency by agency", f_staggered, "threshold_staggered.csv")]
TILES = [tile_single, tile_pileup, tile_history, tile_model]
PARSE_ERRORS = (KeyError, ValueError, TypeError, StopIteration, ZeroDivisionError, IndexError, AttributeError)


# ---- how it is made ------------------------------------------------------------------------------------------------------
def nice(iso):
    return date.fromisoformat(str(iso)[:10]).strftime("%b %d, %Y").replace(" 0", " ")


def forecasts_text(src):
    fc = src.cfg.get("forecasts") or {}
    f1, f2 = fc.get("F1") or {}, fc.get("F2") or {}
    if not f1 and not f2:
        return ""
    sealed = f1.get("sealed") or f2.get("sealed")
    t = (f"<b>Sealed forecasts.</b> Two forecasts were sealed{' on ' + nice(sealed) if sealed else ''}, before any outcome existed, and "
         f"fingerprinted (SHA-256), so any later change would show.")
    if f1.get("n_solicitations"):
        t += (f" One gives the chance of a single offer for {f1['n_solicitations']} open solicitations, scored against a "
              f"department-wide baseline" + (f" by {nice(f1['checks'][-1])}" if f1.get("checks") else "") + ".")
    q = None
    if f2.get("file"):
        f2j = src.json(os.path.basename(f2["file"]), os.path.dirname(f2["file"]))
        q = next((x for x in (f2j or {}).get("quantities", []) if x.get("name") == f"bunching_ratio_{SAT_NOW // 1000}K"), None)
        if q:
            t += (f" The other puts the FY2027 Q1 pile-up under {money(SAT_NOW)} at {float(q['forecast']):.2f} (80% range "
                  f"{float(q['interval80'][0]):.2f} to {float(q['interval80'][1]):.2f}), scored"
                  + (f" on {nice(f2j['resolution_date'])}" if f2j.get("resolution_date") else "") + " by whether the result falls in that range.")
    if q and f2.get("id"):
        trk = src.json(f"tracking_{f2['id']}.json")
        tq = next((x for x in (trk or {}).get("quantities", []) if x.get("name") == q["name"]), None)
        if tq and fl(tq.get("so_far")) is not None:
            t += f" Reading so far: {float(tq['so_far']):.2f} ({nice(trk['as_of'])}), not the score."
    return t


def made_items(src):
    snap = ""
    tot = src.csv("atlas_civilian_totals.csv") or []
    if tot and tot[0].get("snapshot"):
        snap = f" Results from one data snapshot, downloaded {nice(tot[0]['snapshot'])}."
    items = [
        "<b>Data.</b> USAspending.gov (contract awards) and SAM.gov (solicitations): public and free, no key or account needed." + snap,
        "<b>Reruns by itself.</b> A scheduled job refreshes the daily parts every morning: open solicitations, this fiscal year so "
        "far, forecast tracking and the dashboard.",
        "<b>Anyone can rerun it.</b> One command downloads the published data and rebuilds every result, chart and page: "
        "<code>python run.py quick</code>.",
    ]
    f = forecasts_text(src)
    if f:
        items.append(f)
    return items


def limits_items(src):
    h = src.json("atlas_headline.json") or {}
    q = src.json("quality_summary.json") or {}
    dq = "Offer counts are entered by contracting staff"
    if fl(h.get("offers_999_among_competed")):
        dq += f"; {n(h['offers_999_among_competed'])} competed awards in the five departments carry a \"999\" placeholder and were left out"
    dq += ". Agencies change how they record offers"
    if fl(q.get("n_flags")):
        dq += f" (a monitor flagged {n(q['n_flags'])} agency quarters with sudden changes)"
    dq += ", so a change over time can be a change in recording. FY2026 saw a government shutdown and a reporting-system move."
    hist = history_all(src) or {}
    yrs = sorted({y for v in hist.values() for y in v})
    cfg_years = (src.cfg.get("analysis") or {}).get("fiscal_years") or []
    still = ""
    if yrs and cfg_years and max(yrs) < max(cfg_years):
        still = f" The long history uses {fy_text(yrs)} so far; later years are still being added."
    return [
        "<b>Patterns, not proof of causes.</b> A high single-offer rate or a pile-up under a limit is a signal worth a closer look. It "
        "is not evidence of wrongdoing, and many have ordinary explanations. These numbers describe markets, not people.",
        f"<b>Data quality.</b> {dq}",
        "<b>Not covered.</b> Award data have no unit prices, so this cannot show whether single-offer awards cost more. Defense data "
        "arrive 90 days late, so Defense is only in the long history. Nothing here is below agency level." + still,
    ]


def author_line(src):
    """the README's own author line ('Built by ...' under '## About'), copied with its Markdown removed, or ''"""
    m = re.search(r"^## About\s*\n+(Built by [^\n]+)", src.text("README.md"), re.M)
    if not m:
        return ""
    line = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", m.group(1)).replace("**", "")
    return "" if "@" in line else line.strip()


def links(src):
    repo = os.environ.get("GITHUB_REPOSITORY") or (src.cfg.get("project") or {}).get("repository") or ""
    url = f"https://github.com/{repo}" if re.fullmatch(r"[\w.-]+/[\w.-]+", repo) else ""
    owner, _, name = repo.partition("/")
    site = f"{owner.lower()}.github.io/{name}" if url else ""
    return url, site


# =====================================================================================================================
CSS = """
:root { --bg:#f6f7f9; --card:#fff; --ink:#1b2430; --muted:#5a6573; --line:#e1e5ea; --accent:#1f5fa8; --soft:#e7eef8; --warn:#fbf0dc; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --bg:#11161c; --card:#1a2129; --ink:#e7ecf2; --muted:#a3aebb; --line:#2c3540; --accent:#86b5ee; --soft:#1d2b3c; --warn:#33291a; } }
:root[data-theme="dark"] { --bg:#11161c; --card:#1a2129; --ink:#e7ecf2; --muted:#a3aebb; --line:#2c3540; --accent:#86b5ee; --soft:#1d2b3c; --warn:#33291a; }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--ink); font:16px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; overflow-wrap:break-word; }
.wrap { max-width:960px; margin:0 auto; padding:28px 16px 48px; }
h1 { font-size:1.7rem; line-height:1.2; margin:0 0 6px; }
h2 { font-size:1.1rem; margin:26px 0 10px; }
h3 { font-size:1rem; margin:0 0 4px; }
p { margin:0 0 6px; }
.sub { color:var(--muted); margin:0 0 4px; }
a { color:var(--accent); }
.tiles { display:grid; gap:12px; grid-template-columns:repeat(auto-fit,minmax(200px,1fr)); }
.tiles > * { min-width:0; }
.card { background:var(--card); border:1px solid var(--line); border-radius:14px; padding:14px 16px; min-width:0; }
.tile b { display:block; font-size:1.8rem; color:var(--accent); line-height:1.1; margin-bottom:4px; }
.tile span { color:var(--muted); font-size:.9rem; }
.finding { display:grid; grid-template-columns:minmax(0,1fr) 300px; gap:8px 18px; align-items:start; margin-top:10px; }
.finding.nochart { grid-template-columns:minmax(0,1fr); }
.pair { display:grid; grid-template-columns:1fr 1fr; gap:0 18px; }
.why { color:var(--muted); font-size:.93rem; }
figure { margin:0; }
figcaption { color:var(--muted); font-size:.8rem; margin-top:2px; }
svg.chart { width:100%; height:auto; display:block; }
.chart text { fill:var(--muted); font-size:9.5px; font-family:inherit; }
.chart text.lab, .chart text.pt { fill:var(--ink); }
.chart .grid { stroke:var(--line); stroke-width:.8; }
.chart .ref { stroke:var(--muted); stroke-width:.8; stroke-dasharray:3 3; }
.chart polyline { fill:none; stroke-linejoin:round; stroke-linecap:round; }
.chart .s1 { stroke:var(--accent); stroke-width:2.2; }
.chart .s2 { stroke:var(--ink); stroke-width:1.5; stroke-dasharray:5 3; }
.chart .s3 { stroke:var(--muted); stroke-width:1.3; stroke-dasharray:1.5 2.5; }
.chart .thin { stroke:var(--muted); stroke-width:1; }
.chart .on { stroke:var(--accent); stroke-width:2.6; stroke-linecap:round; }
.chart .dot { fill:var(--accent); }
.cols { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
ul { margin:0; padding-left:18px; } li { margin:0 0 6px; }
code { font-size:.88em; background:var(--soft); padding:1px 4px; border-radius:4px; }
footer { margin-top:22px; color:var(--muted); font-size:.85rem; }
@media (max-width:640px) {
  h1 { font-size:1.4rem; } .finding, .cols, .pair { grid-template-columns:minmax(0,1fr); } .tile b { font-size:1.5rem; }
}
@media print {
  @page { size:letter; margin:0.36in 0.45in; }
  :root, :root:not([data-theme="light"]), :root[data-theme="dark"] { --bg:#fff; --card:#fff; --ink:#000; --muted:#333; --line:#c8c8c8; --accent:#1f5fa8; --soft:#fff; }
  body { font-size:8pt; line-height:1.27; background:#fff; color:#000; -webkit-print-color-adjust:exact; print-color-adjust:exact; }
  .wrap { max-width:none; padding:0; }
  h1 { font-size:15pt; margin:0 0 2pt; } h2 { font-size:10pt; margin:6pt 0 3pt; border-bottom:.6pt solid #999; padding-bottom:1pt; }
  h3 { font-size:8.6pt; margin:0 0 1pt; }
  p { margin:0 0 2pt; } .sub { margin:0 0 1pt; }
  .card { border:0; border-radius:0; padding:0; background:none; }
  .tiles { grid-template-columns:repeat(4,1fr); gap:10pt; }
  .tile { border-left:2pt solid var(--accent); padding-left:6pt; }
  .tile b { font-size:15pt; margin-bottom:1pt; } .tile span { font-size:7.6pt; }
  .finding { grid-template-columns:minmax(0,1fr) 2.35in; gap:2pt 14pt; margin-top:4pt; break-inside:avoid; }
  .pair { gap:0 14pt; }
  .why { font-size:7.6pt; }
  figcaption { font-size:7pt; }
  .chart text { font-size:9px; }
  li { margin:0 0 2pt; } ul { padding-left:12pt; }
  code { background:none; padding:0; }
  a { color:inherit; text-decoration:none; }
  footer { margin-top:6pt; font-size:7pt; }
}
"""


def build(root=ROOT, out=None, today=None):
    """build the page; returns (html, notes), where notes says which parts were left out and why"""
    src = Src(root)
    notes = []
    today = today or date.today()

    def run(fn, label):
        before = list(src.missing)
        try:
            v = fn(src)
        except PARSE_ERRORS as e:
            notes.append(f"left out {label}: could not read its result file ({type(e).__name__}: {e})")
            return None
        if v is None:
            new = [m for m in src.missing if m not in before]
            notes.append(f"left out {label}: " + (f"missing {', '.join(new)}" if new else "too little data in its result file"))
        return v

    tiles = [t for t in (run(fn, f"headline number {fn.__name__[5:]}") for fn in TILES) if t]
    finds = [f for f in (run(fn, f"finding '{lab}'") for lab, fn, _ in FINDINGS) if f]
    made = made_items(src)
    lims = limits_items(src)
    author = author_line(src)
    repo, site = links(src)
    name = (src.cfg.get("project") or {}).get("name") or "Federal Contract Competition: Findings & Forecasts"
    title = name.split(":")[0]

    tiles_html = "".join(f'<div class="card tile"><b>{esc(b)}</b><span>{esc(s)}</span></div>' for b, s in tiles)
    blocks, run_ = [], []                      # findings without a chart sit side by side, in pairs
    for i, (t, text, why, chart) in enumerate(finds, 1):
        body = f'<div><h3>{i}. {esc(t)}</h3><p>{text}</p><p class="why">{why}</p></div>'
        if chart:
            blocks.append(f'<div class="card finding">{body}{chart}</div>')
        else:
            run_.append(f'<div class="card finding nochart">{body}</div>')
            if len(run_) == 2:
                blocks.append(f'<div class="pair">{"".join(run_)}</div>')
                run_ = []
            continue
    if run_:
        blocks.append("".join(run_))
    find_html = "".join(blocks)
    link_bits = [f'<a href="index.html">Dashboard{": " + esc(site) if site else ""}</a>', '<a href="explorer.html">Explorer</a>']
    if repo:
        link_bits.append(f'<a href="{esc(repo)}">Code: {esc(repo.replace("https://", ""))}</a>')
    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}: One-Page Brief</title>
<meta name="description" content="A one-page summary of the project: how often competed US federal contracts get only one offer, pile-ups under dollar limits, and sealed forecasts.">
<style>{CSS}</style>
<script>try {{ var s = localStorage.getItem("fcc-theme"); if (s) document.documentElement.setAttribute("data-theme", s); }} catch (e) {{}}</script>
</head>
<body><div class="wrap">
<h1>{esc(name)}</h1>
<p>An open, reproducible look at how competitive US federal contracting is: single offers, pile-ups under dollar limits, and sealed
forecasts scored in public. Every number below is read from the project's result files.</p>
<p class="sub">Brief built {nice(today.isoformat())} · {' · '.join(link_bits)}</p>

{'<h2>The short version</h2><div class="tiles">' + tiles_html + '</div>' if tiles else ''}

{'<h2>Findings</h2>' + find_html if finds else ''}

<div class="cols">
<div><h2>How it is made</h2><ul>{''.join(f'<li>{x}</li>' for x in made)}</ul></div>
<div><h2>Limits</h2><ul>{''.join(f'<li>{x}</li>' for x in lims)}</ul></div>
</div>

<footer>{esc(author) + ' ' if author else ''}Code: MIT License. Text and figures: CC BY 4.0.</footer>
</div>
</body></html>
"""
    if out:
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8", newline="\n") as f:
            f.write(page)
    return page, notes


# =====================================================================================================================
# printing to PDF with a local browser (Microsoft Edge or Chrome), to check that the page fits on one sheet
# =====================================================================================================================
def find_browser():
    env = os.environ.get("BRIEF_BROWSER")
    cands = [env] if env else []
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA"),
                 r"C:\Program Files (x86)", r"C:\Program Files"):
        if base:
            cands += [os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"),
                      os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")]
    cands += ["/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
              "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
    for c in cands:
        if c and os.path.isfile(c):
            return c
    for exe in ("msedge", "microsoft-edge", "google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"):
        p = shutil.which(exe)
        if p:
            return p
    return None


def pdf_pages(path):
    """number of pages in a PDF written by a browser (counts page objects; falls back to the page tree's /Count)"""
    with open(path, "rb") as f:
        data = f.read()
    k = len(re.findall(rb"/Type\s*/Page(?![a-zA-Z])", data))
    if k:
        return k
    counts = [int(x) for x in re.findall(rb"/Count\s+(\d+)", data)]
    return max(counts) if counts else 0


def print_pdf(html_path, pdf_path, browser):
    """print html_path to pdf_path with a headless browser; returns the page count, or None if it failed"""
    with tempfile.TemporaryDirectory() as prof:
        cmd = [browser, "--headless", "--disable-gpu", "--no-first-run", "--no-default-browser-check", "--disable-extensions",
               f"--user-data-dir={prof}", "--no-pdf-header-footer", "--print-to-pdf-no-header", f"--print-to-pdf={pdf_path}",
               Path(html_path).resolve().as_uri()]
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return None
    return pdf_pages(pdf_path) if os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 0 else None


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build the one-page brief (docs/brief.html).")
    ap.add_argument("--pdf", action="store_true", help="also print it to dist/brief.pdf with a local Edge or Chrome")
    ap.add_argument("--check-print", action="store_true", help="print it to a temporary PDF and check that it fits on one page")
    a = ap.parse_args(argv)
    out = os.path.join(ROOT, "docs", "brief.html")
    page, notes = build(ROOT, out)
    print(f"wrote docs/brief.html ({page.count('<svg')} charts)")
    for x in notes:
        print("  " + x)
    if not (a.pdf or a.check_print):
        return 0
    browser = find_browser()
    if not browser:
        print("No Microsoft Edge or Chrome found, so no PDF was made and the one-page fit was not checked. "
              "Set BRIEF_BROWSER to a browser's path to use another one.")
        return 0
    rc = 0
    if a.check_print:
        with tempfile.TemporaryDirectory() as tmp:
            pages = print_pdf(out, os.path.join(tmp, "brief.pdf"), browser)
        if pages is None:
            print(f"Printing with {browser} failed; the one-page fit was not checked.")
        else:
            print(f"Print check: {pages} page{'s' if pages != 1 else ''} on US Letter" + ("" if pages == 1 else " (should be 1)"))
            rc = 0 if pages == 1 else 1
    if a.pdf:
        os.makedirs(os.path.join(ROOT, "dist"), exist_ok=True)
        pdf = os.path.join(ROOT, "dist", "brief.pdf")
        pages = print_pdf(out, pdf, browser)
        if pages is None:
            print(f"Printing with {browser} failed; dist/brief.pdf was not written.")
            rc = rc or 1
        else:
            print(f"wrote dist/brief.pdf ({pages} page{'s' if pages != 1 else ''})")
            rc = rc or (0 if pages == 1 else 1)
    return rc


if __name__ == "__main__":
    sys.exit(main())
