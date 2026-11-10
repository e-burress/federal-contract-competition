"""
build_explorer.py - build the data file behind the interactive explorer (docs/explorer.html).

Run from the repository root:
    python -I scripts/build_explorer.py [--results DIR] [--reference DIR] [--out FILE]
It reads only result files (results/*.csv, results/*.json) and two public reference tables (reference/market_depth_naics.csv and
reference/thresholds.csv), never data/, and writes docs/data/explorer-data.js, a small script that sets window.FCC_DATA = {...}.
A script file (not JSON loaded with fetch) lets the page work both on GitHub Pages and when opened from disk.
Standard library only.  Every input except the civilian atlas is optional: a missing file leaves its section out (null), so the page
can say "not available yet".  Files planned but not written yet (model_v2_card.json, daily_forecast_scores.json) are read when present.
Size: the output aims to stay under SIZE_CAP bytes.  When it would not, lower-priority detail is trimmed step by step (see TRIMS) and
the steps taken are listed in data["trimmed"], so the page can point readers to the full CSV files.
Privacy: agency level or coarser.  No buying offices and no companies are read or written.
"""
import argparse
import csv
import json
import math
import os
import re
import sys
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_RESULTS = os.path.join(ROOT, "results")
DEFAULT_OUT = os.path.join(ROOT, "docs", "data", "explorer-data.js")
SIZE_CAP = 400 * 1024
DEFAULT_REPO = "e-burress/federal-contract-competition"
GSA = "General Services Administration"
DOD = "Department of Defense"
SECTORS = ["civilian except GSA", "GSA", "Department of Defense"]
KINDS = ["all", "PO", "DEF", "IDV"]
# dollar-limit changes (scripts/20_threshold_history.py): the simplified acquisition threshold
SAT_CHANGES = [
    {"fy": 2011, "date": "2010-10-01", "label": "$100K to $150K", "note": "Inflation adjustment, from October 1, 2010."},
    {"fy": 2018, "date": "2018", "label": "$150K to $250K",
     "note": "Set by the FY2018 defense authorization law. Agencies switched on different dates from February 2018 to 2020."},
    {"fy": 2026, "date": "2025-10-01", "label": "$250K to $350K", "note": "Inflation adjustment, from October 1, 2025."},
]
QUARTER_CHANGE = {"fyq": "FY2026Q1", "date": "2025-10-01", "label": "$250K to $350K"}


# ---- small readers ------------------------------------------------------------------------------------------------
class Reader:
    """Reads result files from one folder and remembers which ones were used."""

    def __init__(self, folder, prefix="", used=None):
        self.folder = folder
        self.prefix = prefix              # written in front of the file name in the list of sources (for example "reference/")
        self.used = [] if used is None else used

    def path(self, name):
        return os.path.join(self.folder, name)

    def exists(self, name):
        return os.path.isfile(self.path(name))

    def csv(self, name):
        """List of row dicts, or None when the file is missing or empty."""
        if not self.exists(name):
            return None
        with open(self.path(name), encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        if not rows:
            return None
        self.used.append(self.prefix + name)
        return rows

    def json(self, name):
        if not self.exists(name):
            return None
        with open(self.path(name), encoding="utf-8") as f:
            obj = json.load(f)
        self.used.append(self.prefix + name)
        return obj


def num(x, nd=4):
    """Float rounded to nd places, or None for blank / NaN / text."""
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    v = round(v, nd)
    return int(v) if nd == 0 else v


def integer(x):
    v = num(x, 0)
    return 0 if v is None else v


def truthy(x):
    return str(x).strip().lower() in ("true", "1", "yes")


def short_name(name):
    """'Department of the Interior' -> 'Interior'"""
    return re.sub(r"^Department of (the )?", "", name)


def sam_name(d):
    """'VETERANS AFFAIRS, DEPARTMENT OF' -> 'Veterans Affairs'"""
    if d in ("", "(blank)"):
        return "Department not given"
    n = d.split(",")[0] if (", DEPARTMENT OF" in d or d.endswith(", THE")) else d
    n = n.title()
    for w in ("And", "Of", "For", "The", "On", "In"):
        n = n.replace(f" {w} ", f" {w.lower()} ")
    return re.sub(r"\bUs\b", "US", n)


def share_point(r, fy, src):
    return [fy, integer(r.get("k_single")), integer(r.get("n_eval")), num(r.get("share")), num(r.get("ci_lo")), num(r.get("ci_hi")), src]


def fiscal_year(x):
    v = num(x, 0)
    return None if v is None else int(v)


def newest_live_fy(rd):
    """The newest fiscal year with a live_fy<year>_totals.json (as build_dashboard.py picks it), or None."""
    if not os.path.isdir(rd.folder):
        return None
    years = [int(m.group(1)) for f in os.listdir(rd.folder) if (m := re.fullmatch(r"live_fy(\d{4})_totals\.json", f))]
    return max(years) if years else None


# ---- sections -----------------------------------------------------------------------------------------------------
def build_agencies(rd):
    atlas = rd.csv("atlas_civilian_by_agency.csv") or []
    atlas_fy = rd.csv("atlas_civilian_by_agency_fy.csv") or []
    hist = rd.csv("history_by_agency_fy.csv") or []
    live_fy_file = newest_live_fy(rd)
    live_tot = rd.json(f"live_fy{live_fy_file}_totals.json") if live_fy_file else None
    live = (rd.csv(f"live_fy{live_fy_file}_by_agency.csv") or []) if live_fy_file else []
    abbr = {r["agency"]: r.get("abbreviation", "") for r in atlas}
    for r in live:
        abbr.setdefault(r["agency"], r.get("abbreviation", ""))
    abbr.setdefault(DOD, "DoD")
    totals = {r["agency"]: r for r in atlas}

    years = {}  # agency -> {fy: point}
    hist_years, recent_years = set(), set()
    for r in atlas_fy:
        fy = fiscal_year(r.get("fy"))
        if fy is None:
            continue
        years.setdefault(r["agency"], {})[fy] = share_point(r, fy, "r")
        recent_years.add(fy)
    for r in hist:  # the long history wins where both have the same year
        fy = fiscal_year(r.get("fy"))
        if fy is None:
            continue
        years.setdefault(r["agency"], {})[fy] = share_point(r, fy, "h")
        hist_years.add(fy)
    live_fy = live_tot.get("fiscal_year") if live_tot else None
    live_by = {r["agency"]: r for r in live}

    out = []
    for name in sorted(set(years) | set(totals)):
        t = totals.get(name)
        pts = [years.get(name, {})[y] for y in sorted(years.get(name, {}))]
        a = {"name": short_name(name), "full": name, "abbr": abbr.get(name, ""),
             "n": sum(p[2] for p in pts),
             "share": num(t["share"]) if t else None,
             "years": pts}
        if name in live_by and live_fy:
            lr = live_by[name]
            a["sofar"] = share_point(lr, int(live_fy), "l")
        out.append(a)
    out.sort(key=lambda a: -a["n"])
    meta = {"history_years": sorted(hist_years), "recent_years": sorted(recent_years)}
    if live_tot:
        meta["sofar"] = {"fy": live_tot.get("fiscal_year"), "from": live_tot.get("data_from"), "to": live_tot.get("data_to")}
    return out, meta


def build_gsa(rd):
    tot = {r["scope"]: r for r in (rd.csv("atlas_civilian_totals.csv") or [])}
    atlas_fy = [r for r in (rd.csv("atlas_civilian_by_agency_fy.csv") or []) if r["agency"] == GSA]
    allc, gsa, exg = tot.get("all civilian agencies"), tot.get("GSA only"), tot.get("all civilian agencies except GSA")
    if not (allc and gsa):
        return None
    out = {"award_share": num(integer(gsa["n_eval"]) / max(1, integer(allc["n_eval"])), 3),
           "median_award": num(gsa.get("median_award_value"), 0),
           "share": num(gsa["share"]), "n": integer(gsa["n_eval"]),
           "all_share": num(allc["share"]), "all_n": integer(allc["n_eval"]),
           "snapshot": allc.get("snapshot") or None}
    if exg:
        out["exg_share"], out["exg_n"] = num(exg["share"]), integer(exg["n_eval"])
    pts = sorted((fiscal_year(r["fy"]), num(r["share"])) for r in atlas_fy if fiscal_year(r["fy"]) is not None)
    if pts:
        out["first"], out["last"] = list(pts[0]), list(pts[-1])
    return out


def build_sectors(rd):
    hist = rd.csv("history_by_fy.csv") or []
    by_fy = rd.csv("atlas_civilian_by_fy.csv") or []
    series = {s: {k: {} for k in KINDS} for s in SECTORS}
    scope_map = {"all civilian agencies except GSA": "civilian except GSA", "GSA only": "GSA"}
    for r in by_fy:  # FY2024-FY2026, all award kinds together
        s = scope_map.get(r.get("scope"))
        fy = fiscal_year(r.get("fy"))
        if s and fy is not None:
            series[s]["all"][fy] = share_point(r, fy, "r")
    for r in hist:
        s, k, fy = r.get("sector"), (r.get("award_kind") or "all"), fiscal_year(r.get("fy"))
        if s in series and k in series[s] and fy is not None:
            series[s][k][fy] = share_point(r, fy, "h")
    out = {s: {k: [v[y] for y in sorted(v)] for k, v in ks.items() if v} for s, ks in series.items()}
    out = {s: v for s, v in out.items() if v}
    return {"has_history": bool(hist), "series": out} if out else None


def build_thresholds(rd):
    out = {"changes": SAT_CHANGES, "quarter_change": QUARTER_CHANGE}
    hist = rd.csv("threshold_history.csv")
    if hist:
        h = {}
        for r in hist:
            fy, line = fiscal_year(r.get("fy")), integer(r.get("line"))
            if fy is None:
                continue
            h.setdefault(r.get("sector", "civilian"), {}).setdefault(str(line), []).append(
                [fy, num(r.get("ratio"), 3), num(r.get("ci_lo"), 3), num(r.get("ci_hi"), 3), truthy(r.get("active")),
                 num(r.get("obs_below"), 0), num(r.get("cf_below"), 1)])
        for s in h.values():
            for v in s.values():
                v.sort(key=lambda p: p[0])
        out["history"] = h
    q = rd.csv("threshold_quarterly_bunching.csv")
    if q:
        qq = {}
        for r in q:
            qq.setdefault(str(integer(r.get("threshold"))), []).append(
                [r["fyq"], num(r.get("ratio"), 3), num(r.get("lo95"), 3), num(r.get("hi95"), 3),
                 num(r.get("obs_below"), 0), num(r.get("cf_below"), 1)])
        for v in qq.values():
            v.sort(key=lambda p: p[0])
        out["quarterly"] = qq
    moved = rd.csv("threshold_sat_moved.csv")
    if moved:
        out["moved"] = [[integer(r["threshold"]), num(r.get("ratio_pre"), 3), num(r.get("ratio_post"), 3), num(r.get("diff"), 3),
                         num(r.get("diff_lo"), 3), num(r.get("diff_hi"), 3)] for r in moved]
    st = rd.csv("threshold_staggered.csv")
    if st:
        s = {}
        for r in st:
            key = "placebo" if "placebo" in (r.get("dates") or "") else "actual"
            s.setdefault(key, {}).setdefault(r.get("sector", "civilian"), {}).setdefault(str(integer(r.get("line"))), []).append(
                [integer(r.get("event_quarter")), num(r.get("ratio"), 3), num(r.get("ci_lo"), 3), num(r.get("ci_hi"), 3),
                 integer(r.get("n_awards"))])
        for d in s.values():
            for sec in d.values():
                for v in sec.values():
                    v.sort(key=lambda p: p[0])
        out["staggered"] = s
    return out


def build_quality(rd, abbr_to_name):
    flags = rd.csv("quality_flags.csv")
    summ = rd.json("quality_summary.json")
    if not flags:
        return None
    out = []
    for r in flags:
        full = abbr_to_name.get(r["agency"], r["agency"])
        out.append([short_name(full), r.get("award_kind", ""), r.get("quarter", ""), r.get("metric", ""), num(r.get("before"), 2),
                    num(r.get("after"), 2), num(r.get("change_points"), 1), integer(r.get("n_before")), integer(r.get("n_after"))])
    res = {"flags": out}
    if summ:
        res["rules"] = summ.get("thresholds", {})
    return res


def build_sam(rd):
    latest = rd.json("sam_daily_latest.json")
    counts = rd.csv("sam_daily_counts.csv")
    if not (latest or counts):
        return None
    out = {}
    dod_label = "DEPT OF DEFENSE"
    if latest:
        op = latest.get("open_solicitations", {})
        dod_label = op.get("dod_department_label", dod_label)
        out.update({"date": latest.get("snapshot_date"),
                    "civilian": op.get("civilian", {}).get("distinct_sol_numbers"),
                    "dod": op.get("dod", {}).get("distinct_sol_numbers"),
                    "civilian_7d": op.get("deadline_next_7d_civilian", {}).get("distinct_sol_numbers"),
                    "civilian_30d": op.get("deadline_next_30d_civilian", {}).get("distinct_sol_numbers")})
    distinct = {}
    if latest:
        for d in latest.get("top10_civilian_departments_by_open_solicitations", []):
            distinct[d["department"]] = d.get("open_distinct_sol_numbers")
    if counts:
        days = {}
        for r in counts:
            if r.get("n_open", "") == "":
                continue
            day = days.setdefault(r["snapshot_date"], {})
            day[r["department"]] = day.get(r["department"], 0) + integer(r["n_open"])
        dates = sorted(days)
        last = out.get("date") if out.get("date") in days else dates[-1]
        out.setdefault("date", last)
        dep = [[sam_name(d), n, distinct.get(d)] for d, n in days[last].items() if d != dod_label and n > 0]
        dep.sort(key=lambda x: (-x[1], x[0]))
        out["departments"] = dep
        out["daily"] = [[d, sum(n for k, n in days[d].items() if k != dod_label), days[d].get(dod_label, 0)] for d in dates]
    return out


# ---- sections added in version 2 ----------------------------------------------------------------------------------
class Strings:
    """A table of repeated strings.  Rows refer to them by position, which keeps the data file small."""

    def __init__(self, first=None):
        self.items = list(first or [])
        self._ix = {s: i for i, s in enumerate(self.items)}

    def __call__(self, s):
        s = "" if s is None else str(s)
        if s not in self._ix:
            self._ix[s] = len(self.items)
            self.items.append(s)
        return self._ix[s]


def r2(x):
    return num(x, 2)


def r3(x):
    return num(x, 3)


def r4(x):
    return num(x, 4)


def hundredths(x):
    """A ratio as a whole number of hundredths (1.07 -> 107): shorter in JSON.  The page divides by 100."""
    v = num(x, 2)
    return None if v is None else int(round(v * 100))


def dollars(x):
    """A dollar value as an int when it is whole, else rounded to cents; None when blank."""
    v = num(x, 2)
    if v is None:
        return None
    return int(v) if v == int(v) else v


def flag(x):
    """True / False / blank -> 1 / 0 / None"""
    s = str(x).strip().lower()
    return 1 if s in ("true", "1", "yes") else 0 if s in ("false", "0", "no") else None


def cap_text(s, n):
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s if len(s) <= n else s[: max(0, n - 1)].rstrip() + chr(0x2026)   # an ellipsis


FAMILY_NAMES = {
    "micro_purchase": "Micro-purchase threshold",
    "simplified_acquisition": "Simplified acquisition threshold",
    "synopsis": "Public posting line ($25,000)",
    "synopsis_display_floor": "Public display floor",
    "cost_or_pricing_data": "Certified cost or pricing data (TINA)",
    "cost_or_pricing_data_pre2018_contracts": "TINA line for contracts from before July 2018",
    "commercial_simplified": "Simplified procedures for commercial items",
    "commercial_simplified_13_500c": "Commercial simplified procedures, special purchases",
    "subcontracting_plan": "Subcontracting plan",
    "subcontracting_plan_construction": "Subcontracting plan, construction",
    "justification_level1": "Justification approval, level 1",
    "justification_level2": "Justification approval, level 2",
    "justification_level3_civilian": "Justification approval, level 3 (civilian)",
    "justification_level3_dod": "Justification approval, level 3 (Defense)",
    "8a_competitive_threshold_nonmfg": "8(a) competition line, non-manufacturing",
    "8a_competitive_threshold_mfg": "8(a) competition line, manufacturing",
    "8a_sole_source_ja": "8(a) sole-source justification",
    "micro_purchase_construction": "Micro-purchase, construction (Davis-Bacon)",
    "micro_purchase_services_sca": "Micro-purchase, services (Service Contract Act)",
    "micro_purchase_contingency_us": "Micro-purchase, contingency inside the US",
    "micro_purchase_contingency_abroad": "Micro-purchase, contingency outside the US",
    "micro_purchase_higher_ed": "Micro-purchase, universities and research nonprofits",
    "small_business_reservation": "Small-business reservation band",
    "fpds_reporting_floor": "FPDS reporting floor",
    "other_note_cas": "Cost Accounting Standards (note)",
    "justification_dod_statute_cert_ceiling": "Defense justification law: certification ceiling",
    "justification_dod_statute_advocate_ceiling": "Defense justification law: advocate ceiling",
    "justification_dod_statute_hca_ceiling": "Defense justification law: head of activity ceiling",
}
FAMILY_ORDER = ["micro_purchase", "simplified_acquisition", "synopsis", "commercial_simplified", "cost_or_pricing_data", "subcontracting_plan",
                "subcontracting_plan_construction", "justification_level1", "justification_level2", "justification_level3_civilian",
                "justification_level3_dod", "8a_competitive_threshold_nonmfg", "8a_competitive_threshold_mfg", "8a_sole_source_ja"]


def family_name(f):
    return FAMILY_NAMES.get(f) or str(f).replace("_", " ").capitalize()


def family_key(f):
    return (FAMILY_ORDER.index(f) if f in FAMILY_ORDER else len(FAMILY_ORDER), f)


def build_scan(rd):
    """Every FAR dollar line (scripts/27_threshold_scan.py): bunching ratio by fiscal year for each value and placebo value,
    and the before/after test of each change.  Series are stored column-wise against one list of fiscal years."""
    rows = rd.csv("threshold_scan.csv") or []
    changes = rd.csv("threshold_scan_changes.csv") or []
    summ = rd.json("threshold_scan_summary.json") or {}
    if not (rows or changes):
        return None
    years = sorted({fy for r in rows if (fy := fiscal_year(r.get("fy"))) is not None})
    yi = {y: i for i, y in enumerate(years)}
    n = len(years)
    notes, others, txt = Strings([""]), Strings([""]), Strings([""])
    fams = {}
    for r in rows:
        fy, v = fiscal_year(r.get("fy")), dollars(r.get("value"))
        if fy is None or v is None:
            continue
        placebo = r.get("kind") == "placebo"
        key = (placebo, v, r.get("placebo_for", ""))
        series = fams.setdefault(r.get("family", ""), {}).setdefault(r.get("sector", "all"), {})
        s = series.get(key)
        if s is None:
            s = series[key] = {"v": v, "st": [None] * n, "r": [None] * n, "lo": [None] * n, "hi": [None] * n, "nt": [0] * n}
            if placebo:
                s["p"] = 1
                s["for"] = [d for d in (dollars(x) for x in (r.get("placebo_for") or "").split(";")) if d is not None]
            else:
                s["o"], s["of"] = [None] * n, [0] * n
        i = yi[fy]
        st = r.get("status", "")
        s["st"][i] = 1 if st == "active" else ((r2(r.get("active_share")) or 0.5) if st == "partial" else 0)
        s["r"][i], s["lo"][i], s["hi"][i] = hundredths(r.get("ratio")), hundredths(r.get("ci_lo")), hundredths(r.get("ci_hi"))
        note = r.get("note") or ""
        if not note and truthy(r.get("small")) and s["r"][i] is not None:
            note = "small sample: fewer than 10 awards expected just under the line"
        s["nt"][i] = notes(note)
        if not placebo:
            s["o"][i] = num(r.get("obs_below"), 0)
            s["of"][i] = others(r.get("other_family_active") or "")
    for ser in fams.values():          # drop what carries no information: all-zero note columns, the status of placebo values
        for sec in ser.values():
            for s in sec.values():
                for k in ("nt", "of"):
                    if k in s and not any(s[k]):
                        s.pop(k)
                if s.get("p"):   # a placebo value has a row in a year when it has a ratio or a note saying why not
                    s.pop("st")
    families = {}
    for f in sorted(fams, key=family_key):
        families[f] = {"name": family_name(f), "universe": (summ.get("universes") or {}).get(f),
                       "sectors": {sec: sorted(ser.values(), key=lambda s: (s.get("p", 0), s["v"])) for sec, ser in fams[f].items()}}
    ch = []
    for r in changes:
        same = [x for x in (r.get("same_day_changes") or "").split(";") if x.strip()]
        ch.append([r.get("family", ""), r.get("sector", ""), dollars(r.get("old_line")), dollars(r.get("new_line")), r.get("change_date", ""),
                   r.get("verdict", ""), r3(r.get("did")), r3(r.get("did_lo")), r3(r.get("did_hi")),
                   r3(r.get("d_placebo")), r3(r.get("d_placebo_lo")), r3(r.get("d_placebo_hi")),
                   r2(r.get("old_before")), r2(r.get("old_after")), r2(r.get("new_before")), r2(r.get("new_after")),
                   txt(r.get("reason")), flag(r.get("data_complete")), num(r.get("n_before"), 0), num(r.get("n_after"), 0),
                   txt(r.get("before_window")), txt(r.get("after_window")), txt((r.get("years_missing") or "").replace(";", ", ")), len(same)])
    ch.sort(key=lambda c: (family_key(c[0]), c[1], c[2] or 0))
    levels = []
    for l in summ.get("levels") or []:
        ai, ap = l.get("active_minus_inactive_ci") or [None, None], l.get("active_minus_placebo_ci") or [None, None]
        levels.append([l.get("family"), l.get("sector"), l.get("active_cells"), l.get("inactive_cells"), r2(l.get("active_minus_inactive")),
                       r2(ai[0]), r2(ai[1]), r2(l.get("active_minus_placebo")), r2(ap[0]), r2(ap[1])])
    names = {f: family_name(f) for f in set(fams) | {c[0] for c in ch}}
    for s in others.items:
        for f in s.split(";"):
            if f:
                names.setdefault(f, family_name(f))
    return {"scale": 100, "years": years, "notes": notes.items, "others": others.items, "txt": txt.items, "names": names,
            "families": families, "changes": ch, "levels": levels,
            "present": summ.get("fiscal_years_present") or years, "missing": summ.get("fiscal_years_missing") or [],
            "not_testable_counts": summ.get("not_testable_counts") or {}, "caveats": summ.get("caveats") or []}


ORDERED_LEVELS = {
    "size_band": ["<25K", "25-100K", "100-250K", "250K-1M", "1-10M", ">10M"],
    "timing": ["Q1 Oct-Dec", "Q2 Jan-Mar", "Q3 Apr-Jun", "Q4 Jul-Aug", "September 1-23", "September 24-30"],
    "fquarter": ["Q1", "Q2", "Q3", "Q4"],
    "september": ["October-August", "September"],
    "last_week": ["rest of year", "September 24-30"],
}
NUMERIC_LEVELS = {"workload_band", "depth_band", "fy"}


def level_key(factor, level, n):
    """Natural order for ordered factors (size, timing, bands, years); otherwise the largest level first."""
    if factor in ORDERED_LEVELS:
        lst = ORDERED_LEVELS[factor]
        return (0, lst.index(level), 0) if level in lst else (1, 0, -n)
    if factor in NUMERIC_LEVELS:
        m = re.match(r"\s*[<>]?\s*([\d,]+)", level)
        return (0, int(m.group(1).replace(",", "")), 0) if m else (1, 0, -n)
    return (0, -n, 0)


def build_drivers(rd):
    """Why only one offer (scripts/23_drivers.py): shares by factor, the model's average marginal effects, and the factor blocks."""
    shares = rd.csv("drivers_shares.csv") or []
    model = rd.csv("drivers_model.csv") or []
    blocks = rd.csv("drivers_blocks.csv") or []
    summ = rd.json("drivers_summary.json") or {}
    if not (shares or model or blocks):
        return None
    lab = Strings()
    fac = {}
    for r in shares:
        f = fac.setdefault(r.get("factor", ""), {"name": r.get("factor_name") or r.get("factor", ""), "lv": {}, "s": {}})
        lvl = r.get("level", "")
        e = f["lv"].setdefault(lvl, [lab(r.get("label") or lvl), 0])
        e[1] += integer(r.get("n_eval"))
        f["s"].setdefault(r.get("scope", "all sectors"), []).append(
            [lvl, integer(r.get("k_single")), integer(r.get("n_eval")), r3(r.get("share")), r3(r.get("ci_lo")), r3(r.get("ci_hi"))])
    factors = []
    for key, f in fac.items():
        order = sorted(f["lv"], key=lambda l: level_key(key, l, f["lv"][l][1]))
        pos = {l: i for i, l in enumerate(order)}
        factors.append({"f": key, "name": f["name"], "levels": [f["lv"][l][0] for l in order],
                        "s": {sc: sorted(([pos[x[0]]] + x[1:] for x in rows), key=lambda x: x[0]) for sc, rows in f["s"].items()}})
    blk = Strings()
    mod = {}
    for r in model:
        mod.setdefault(r.get("scope", "all sectors"), []).append(
            [blk(r.get("block")), lab(r.get("factor_name") or r.get("factor")), lab(r.get("label") or r.get("level")),
             lab(r.get("reference_label") or r.get("reference")), integer(r.get("n_level")), r3(r.get("single_share_level")),
             r2(r.get("ame_points")), r2(r.get("ci_lo_points")), r2(r.get("ci_hi_points"))])
    bl = []
    for r in blocks:
        bl.append({"block": r.get("block"), "factors": r.get("factors"),
                   "ll_up": num(r.get("log_loss_increase_without"), 5), "ll_lo": num(r.get("log_loss_increase_ci_lo"), 5),
                   "ll_hi": num(r.get("log_loss_increase_ci_hi"), 5), "lost": r3(r.get("share_of_gain_lost_without")),
                   "alone": r3(r.get("share_of_gain_alone")), "auc_drop": r4(r.get("auc_drop_without")),
                   "auc_lo": r4(r.get("auc_drop_ci_lo")), "auc_hi": r4(r.get("auc_drop_ci_hi")), "auc_alone": r4(r.get("auc_alone"))})
    bl.sort(key=lambda b: -(b["ll_up"] or 0))
    b0 = blocks[0] if blocks else {}
    keep = ("years_in_shares", "years_in_model", "defense_years_left_out_incomplete", "incumbency_years", "single_offer_share", "caveats",
            "market_depth", "top_five_factors_ranking", "top_five_factors")
    out = {"labels": lab.items, "factors": factors, "blocks_names": blk.items, "model": mod, "blocks": bl,
           "split": b0.get("split"), "n_train": num(b0.get("n_train"), 0), "n_test": num(b0.get("n_test"), 0),
           "auc_full": r4(b0.get("auc_full")), "ll_base": r4(b0.get("log_loss_base_rate")), "ll_full": r4(b0.get("log_loss_full"))}
    out.update({k: summ[k] for k in keep if k in summ})
    sm = summ.get("sample") or {}
    if sm:
        out["sample"] = {k: sm.get(k) for k in ("design", "quota_per_stratum") if k in sm}
    mo = summ.get("model") or {}
    if mo:
        out["model_notes"] = {k: mo.get(k) for k in ("controls", "standard_errors") if isinstance(mo.get(k), str)}
    return out


SPLIT_SECTORS = {"civilian_ex_gsa": "civilian except GSA", "gsa": "GSA", "defense": "Department of Defense"}


def build_split(rd):
    """Split-like clusters (scripts/26_split_screen.py): excess ratio by sector and year at the line in force, with the shares at
    every fake line, plus the pooled ratios and the natural experiments from the summary."""
    rows = rd.csv("split_screen.csv") or []
    summ = rd.json("split_screen_summary.json") or {}
    if not (rows or summ):
        return None
    mults = sorted({m for r in rows if (m := num(r.get("multiple"), 3)) is not None})
    groups = {}
    for r in rows:
        if r.get("basis") != "in_force":
            continue
        fy, m = fiscal_year(r.get("fy")), num(r.get("multiple"), 3)
        if fy is None or m not in mults:
            continue
        key = "|".join((r.get("sector", ""), r.get("threshold_type", ""), r.get("variant", "")))
        p = groups.setdefault(key, {}).setdefault(fy, {"sh": [None] * len(mults), "u": ["0"] * len(mults)})
        i = mults.index(m)
        p["sh"][i] = None if (v := num(r.get("share_awards"), 4)) is None else int(round(v * 10000))   # in hundredths of a percent
        p["u"][i] = "1" if truthy(r.get("placebo_line")) else "0"
        if m == 1.0:
            p.update(ex=r3(r.get("excess_ratio")), lo=r3(r.get("ci_lo")), hi=r3(r.get("ci_hi")), base=r4(r.get("baseline_share")),
                     under=num(r.get("awards_under"), 0), k=num(r.get("clusters_split_like"), 0), line=dollars(r.get("line_low")),
                     at=r3(r.get("share_awards_at_line_low")), sd=r4(r.get("share_dollars")))
    series = {}
    for key, ys in groups.items():
        series[key] = [[fy, p.get("ex"), p.get("lo"), p.get("hi"), p.get("base"), p.get("under"), p.get("k"), p.get("line"), p.get("at"),
                        p.get("sd"), p["sh"], "".join(p["u"])] for fy, p in sorted(ys.items())]
    pooled = []
    for key, v in (summ.get("excess_ratio_pooled") or {}).items():
        parts = key.split("|")
        if len(parts) == 3 and isinstance(v, dict):
            pooled.append(parts + [r3(v.get("ratio")), r3(v.get("ci_lo")), r3(v.get("ci_hi")), v.get("years"),
                                   num(v.get("actual_awards"), 0), num(v.get("expected_awards"), 0)])
    nat = []
    for e in summ.get("natural_experiment") or []:
        b, a, d = e.get("before") or {}, e.get("after") or {}, e.get("after_minus_before") or {}
        nat.append([e.get("threshold_type"), dollars(e.get("old_line")), dollars(e.get("new_line")), e.get("change_label"), e.get("sector"),
                    e.get("variant"), r3(b.get("ratio")), r3(b.get("ci_lo")), r3(b.get("ci_hi")), b.get("years"),
                    r3(a.get("ratio")), r3(a.get("ci_lo")), r3(a.get("ci_hi")), a.get("years"),
                    r3(d.get("change")), r3(d.get("ci_lo")), r3(d.get("ci_hi")), e.get("before_years"), e.get("after_years")])
    design = summ.get("design") or {}
    return {"multiples": mults, "share_scale": 10000, "series": series, "pooled": pooled, "natural": nat, "sectors": SPLIT_SECTORS,
            "years_used": summ.get("years_used") or sorted({p[0] for s in series.values() for p in s}),
            "years_missing": summ.get("years_not_present") or [], "what": summ.get("what"),
            "design": {k: design.get(k) for k in ("gap_days", "donut_cap", "near_other_line", "bootstrap", "bootstrap_unit", "baseline")
                       if k in design}}


def build_outcomes(rd):
    """After award (scripts/24_outcomes.py): single-offer minus multi-offer differences by outcome, scope and follow-up window,
    and by cohort year.  Placebo and balance rows are kept and labelled by their role."""
    est = rd.csv("outcomes_estimates.csv") or []
    coh = rd.csv("outcomes_by_cohort.csv") or []
    summ = rd.json("outcomes_summary.json") or {}
    if not (est or coh):
        return None
    outcomes, okey = [], {}
    scopes, follows = Strings(), Strings()
    stype = {}

    def oi(r):
        k = r.get("outcome", "")
        if k not in okey:
            okey[k] = len(outcomes)
            outcomes.append({"key": k, "label": r.get("label") or k, "role": r.get("role") or "outcome", "unit": r.get("unit") or ""})
        return okey[k]

    rows = []
    for r in est:
        s = r.get("scope", "")
        stype.setdefault(s, r.get("scope_type", ""))
        rows.append([oi(r), follows(r.get("follow_up")), scopes(s), num(r.get("coef"), 4), num(r.get("lo"), 4), num(r.get("hi"), 4),
                     num(r.get("mean_multi_offer"), 4), num(r.get("n_awards"), 0), num(r.get("n_cells"), 0),
                     num(r.get("n_offices"), 0), r3(r.get("raw_mean_single")), r3(r.get("raw_mean_multi"))])
    cohorts_text = {}
    for r in est:
        cohorts_text.setdefault(r.get("follow_up", ""), r.get("cohorts", ""))
    co = []
    for r in coh:
        if r.get("role") == "balance":
            continue
        k = r.get("outcome", "")
        if k not in okey:  # an outcome seen only in the cohort file
            oi({"outcome": k, "role": r.get("role")})
        co.append([okey[k], follows(r.get("follow_up")), scopes(r.get("scope", "")), fiscal_year(r.get("cohort_fy")), num(r.get("coef"), 4),
                   num(r.get("lo"), 4), num(r.get("hi"), 4), num(r.get("n_awards"), 0), 1 if truthy(r.get("window_ends_in_latest_year")) else 0])
    fu = {}
    for name, f in (summ.get("follow_ups") or {}).items():
        fu[name] = {k: f.get(k) for k in ("cohorts_text", "cohorts_ending_in_latest_year", "awards_at_or_above_floor", "single_offer_share",
                                          "awards_in_cells_with_both_kinds", "share_of_awards_carrying_the_estimate", "cells", "buying_offices")
                    if k in f}
    design = summ.get("design") or {}
    return {"outcomes": outcomes, "follow_ups": follows.items, "scopes": scopes.items, "scope_type": stype, "rows": rows, "cohort": co,
            "cohorts_text": cohorts_text, "follow_meta": fu, "headline_follow_up": summ.get("headline_follow_up"),
            "years_present": summ.get("years_present"), "latest_fy": summ.get("latest_fiscal_year"),
            "size_floor": design.get("size_floor"), "cells": design.get("cells"), "caveats": summ.get("caveats") or []}


def card_summary(card):
    """The public model card (results/model_v2_card.json, written by scripts/30_model_v2.py --train), reduced to what the page shows.
    Unknown shapes still give something: top-level text and numbers, and a flat 'metrics' object if there is one."""
    if not isinstance(card, dict):
        return None
    out = {}
    for k in ("model", "built_utc", "what_it_predicts", "outcome_rule", "no_leakage", "private_files", "description"):
        if isinstance(card.get(k), str):
            out[k] = cap_text(card[k], 600)
    data = card.get("data") if isinstance(card.get("data"), dict) else {}
    d = {k: data[k] for k in ("award_years_present", "solicitations_in_archive", "resolved_solicitations", "single_offer_share") if k in data}
    if d:
        out["data"] = d
    sh = card.get("shipped") if isinstance(card.get("shipped"), dict) else {}
    s = {k: sh[k] for k in ("kind", "name_in_evaluation", "training_window", "rows") if k in sh}
    why = sh.get("why")
    if isinstance(why, str):
        s["why"] = [cap_text(why, 400)]
    elif isinstance(why, list):
        s["why"] = [cap_text(w, 400) for w in why if isinstance(w, str)][:8]
    if s:
        out["shipped"] = s
    ev = card.get("evaluation") if isinstance(card.get("evaluation"), dict) else {}
    if ev:
        out["evaluation"] = {"design": cap_text(ev.get("design"), 600) if ev.get("design") else None, "test_years": ev.get("test_years")}
    met = card.get("metrics") or card.get("headline_metrics")
    if isinstance(met, dict):
        out["metrics"] = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in list(met.items())[:30]
                          if isinstance(v, (int, float, str)) and not isinstance(v, bool)}
    extra = [[k, (round(v, 4) if isinstance(v, float) else cap_text(v, 300) if isinstance(v, str) else v)] for k, v in card.items()
             if k not in out and isinstance(v, (int, float, str)) and not isinstance(v, bool)]
    if extra:
        out["extra"] = extra[:20]
    cav = card.get("caveats")
    if isinstance(cav, list):
        out["caveats"] = [cap_text(c, 400) for c in cav if isinstance(c, str)][:12]
    return out or None


# models shown year by year on the page; every model keeps its pooled rows
MODEL2_BY_YEAR = ("logistic v2", "gradient boosting", "version 1 feature set (refit)", "agency rate (baseline)", "sector rate (baseline)",
                  "office history only (baseline)")


def build_model2(rd):
    """Forecast model version 2 (scripts/30_model_v2.py): the card, the rolling-origin evaluation and the reliability table."""
    card = rd.json("model_v2_card.json")
    ev = rd.csv("model_v2_evaluation.csv") or []
    cal = rd.csv("model_v2_calibration.csv") or []
    if not (card or ev or cal):
        return None
    models, sectors = Strings(), Strings()
    rows, drops, train = [], [], {}
    for r in ev:
        t = r.get("test_fy", "")
        t = int(t) if t.isdigit() else t
        m = r.get("model", "")
        si = sectors(r.get("sector", "all"))
        if " without: " in m:
            drops.append([t, si, m.split(" without: ", 1)[1], r4(r.get("auc")), r4(r.get("logloss")),
                          num(r.get("delta_logloss_when_dropped"), 5), num(r.get("delta_auc_when_dropped"), 5), num(r.get("n"), 0)])
            continue
        if isinstance(t, int) and m not in MODEL2_BY_YEAR:
            continue
        rows.append([t, si, models(m), num(r.get("n"), 0), r4(r.get("single_share")), r4(r.get("mean_forecast")), r4(r.get("auc")),
                     r4(r.get("brier")), r4(r.get("logloss")), r3(r.get("cal_slope")),
                     flag(r.get("outcome_window_complete"))])
        if isinstance(t, int) and r.get("train_years"):
            train.setdefault(str(t), [r.get("train_years"), num(r.get("n_train"), 0)])
    crow, cyears = [], set()
    for r in cal:
        crow.append([models(r.get("model", "")), sectors(r.get("sector", "all")), r.get("band", ""), num(r.get("n"), 0),
                     r4(r.get("mean_forecast")), r4(r.get("observed_share"))])
        if r.get("test_years"):
            cyears.add(r["test_years"])
    out = {"card": card_summary(card) if card else None, "models": models.items, "sectors": sectors.items, "eval": rows, "drops": drops,
           "train": train, "cal": crow, "cal_years": sorted(cyears)}
    return out


SCORE_FIELDS = {"batches": ("batches", "n_batches"), "forecasts": ("forecasts", "n_forecasts"), "resolved": ("resolved", "n_resolved"),
                "brier": ("brier", "brier_score", "mean_brier"), "baseline": ("baseline_brier", "brier_baseline", "baseline")}


def _pick(d, names):
    for n in names:
        if isinstance(d, dict) and d.get(n) is not None:
            return d[n]
    return None


def _score_row(d):
    out = {}
    for k, names in SCORE_FIELDS.items():
        v = _pick(d, names)
        if isinstance(v, dict):
            v = _pick(v, ("brier", "value", "score"))
        out[k] = num(v, 5)
    return out


def build_scoreboard(rd):
    """The public scoreboard of daily sealed forecasts (results/daily_forecast_scores.json), when it exists.
    Planned shape: totals and a list by month of batches, forecasts, resolved, Brier score and the baseline's Brier score.
    Several key names are accepted, so a small change in the file does not break the page."""
    s = rd.json("daily_forecast_scores.json")
    if not isinstance(s, dict):
        return None
    tot = _pick(s, ("totals", "overall", "summary", "all"))
    months = _pick(s, ("by_month", "months", "monthly")) or []
    if isinstance(months, dict):
        months = [dict(v, month=k) for k, v in months.items() if isinstance(v, dict)]
    mrows = []
    for m in months:
        if not isinstance(m, dict):
            continue
        r = _score_row(m)
        mrows.append([str(_pick(m, ("month", "period", "label")) or ""), r["batches"], r["forecasts"], r["resolved"], r["brier"], r["baseline"]])
    mrows.sort(key=lambda x: x[0])
    notes = _pick(s, ("caveats", "notes")) or []
    return {"updated": _pick(s, ("updated", "as_of", "built", "built_utc", "generated", "generated_utc")),
            "totals": _score_row(tot if isinstance(tot, dict) else s), "months": mrows,
            "baseline_name": _pick(s, ("baseline_name", "baseline_label", "baseline_description")),
            "notes": [cap_text(x, 400) for x in notes if isinstance(x, str)][:10] if isinstance(notes, list) else []}


def build_market_depth(ref):
    """Registered companies per 6-digit NAICS code (reference/market_depth_naics.csv): counts only."""
    rows = ref.csv("market_depth_naics.csv")
    if not rows:
        return None
    codes = []
    for r in rows:
        c = (r.get("naics") or "").strip()
        if re.fullmatch(r"\d{6}", c):
            codes.append([int(c), integer(r.get("n_primary")), integer(r.get("n_any")), integer(r.get("n_small_any"))])
    codes.sort()
    return {"extract_date": rows[0].get("extract_date"), "codes": codes} if codes else None


NOTE_CAP = 160


def build_threshold_table(ref):
    """Every threshold and its value periods (reference/thresholds.csv)."""
    rows = ref.csv("thresholds.csv")
    if not rows:
        return None
    fams, out = {}, []
    for r in rows:
        f = r.get("threshold", "")
        fams.setdefault(f, [family_name(f), cap_text(r.get("description"), 200), cap_text(r.get("measure"), 110)])
        out.append([f, dollars(r.get("value_usd")), r.get("effective_date") or "", r.get("end_date") or "", r.get("applies_to") or "",
                    cap_text(r.get("far_citation"), 80), r.get("confidence") or "", (r.get("source_url") or "").strip(),
                    cap_text(r.get("notes"), NOTE_CAP)])
    out.sort(key=lambda x: (family_key(x[0]), x[2] or "0000", x[4]))
    return {"families": fams, "rows": out}


# ---- size guard ---------------------------------------------------------------------------------------------------
def _trim_driver_levels(d, keep=30):
    dr, done = d.get("drivers"), False
    for f in (dr or {}).get("factors", []):
        for sc, rows in f["s"].items():
            if len(rows) > keep:
                f["s"][sc] = sorted(sorted(rows, key=lambda x: -x[2])[:keep], key=lambda x: x[0])
                done = True
    return done


def _trim_ref_notes(d, n=100):
    t, done = d.get("threshold_table"), False
    for r in (t or {}).get("rows", []):
        if len(r[8]) > n:
            r[8], done = cap_text(r[8], n), True
    return done


def _trim_scan_placebo_ci(d):
    done = False
    for f in ((d.get("scan") or {}).get("families") or {}).values():
        for ser in f["sectors"].values():
            for s in ser:
                if s.get("p") and "lo" in s:
                    s.pop("lo"), s.pop("hi")
                    done = True
    return done


def _trim_cohort(d):
    o = d.get("outcomes")
    if not o or "all sectors" not in o["scopes"]:
        return False
    k = o["scopes"].index("all sectors")
    before = len(o["cohort"])
    o["cohort"] = [r for r in o["cohort"] if r[2] == k]
    return len(o["cohort"]) < before


def _trim_outcome_checks(d):
    o = d.get("outcomes")
    if not o or not o.get("headline_follow_up") or o["headline_follow_up"] not in o["follow_ups"]:
        return False
    keep = o["follow_ups"].index(o["headline_follow_up"])
    checks = {i for i, sc in enumerate(o["scopes"]) if o["scope_type"].get(sc) == "check"}
    before = len(o["rows"])
    o["rows"] = [r for r in o["rows"] if r[2] not in checks or r[1] == keep]
    return len(o["rows"]) < before


def _trim_scan_obs(d):
    done = False
    for f in ((d.get("scan") or {}).get("families") or {}).values():
        for ser in f["sectors"].values():
            for s in ser:
                if "o" in s:
                    s.pop("o")
                    done = True
    return done


def _trim_scan_all(d):
    done = False
    for f in ((d.get("scan") or {}).get("families") or {}).values():
        if "all" in f["sectors"] and len(f["sectors"]) > 1:
            f["sectors"].pop("all")
            done = True
    return done


TRIMS = [
    ("Every dollar limit: intervals of placebo values are left out", _trim_scan_placebo_ci),
    ("Why only one bid: factors with many levels list the 30 largest levels per sector", _trim_driver_levels),
    ("All thresholds: notes are cut to 100 characters", _trim_ref_notes),
    ("After award: the method checks are kept for the main follow-up window only", _trim_outcome_checks),
    ("After award: results by cohort year are kept for all sectors together only", _trim_cohort),
    ("Every dollar limit: the 'both sectors together' view is left out", _trim_scan_all),
    ("Every dollar limit: the count of awards just under each line is left out", _trim_scan_obs),
]


def dump(data):
    return json.dumps(data, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def fit_to_cap(data, cap=SIZE_CAP):
    """Apply TRIMS in order until the data fit under cap bytes.  Returns the JSON text."""
    data["trimmed"] = []
    text = dump(data)
    for label, fn in TRIMS:
        if len(text) + 120 <= cap:
            break
        if fn(data):
            data["trimmed"].append(label)
            text = dump(data)
    return text


def config_repo():
    """owner/name from config.toml (as build_dashboard.py does), else the default."""
    try:
        import tomllib
        with open(os.path.join(ROOT, "config.toml"), "rb") as f:
            return tomllib.load(f)["project"]["repository"]
    except Exception:
        return DEFAULT_REPO


def build(results_dir=DEFAULT_RESULTS, out_path=DEFAULT_OUT, repo=None, today=None, reference_dir=None, cap=SIZE_CAP):
    rd = Reader(results_dir)
    # the reference folder sits next to the results folder (repository layout; tests use a temporary copy of it)
    ref = Reader(reference_dir or os.path.join(os.path.dirname(os.path.abspath(results_dir)), "reference"), "reference/", rd.used)
    repo = repo or os.environ.get("GITHUB_REPOSITORY") or config_repo()
    agencies, ameta = build_agencies(rd)
    abbr_to_name = {a["abbr"]: a["full"] for a in agencies if a["abbr"]}
    data = {
        "built": (today or date.today()).isoformat(),
        "repo": "https://github.com/" + repo,
        "agencies": agencies,
        "agency_meta": ameta,
        "gsa": build_gsa(rd),
        "sectors": build_sectors(rd),
        "thresholds": build_thresholds(rd),
        "quality": build_quality(rd, abbr_to_name),
        "sam": build_sam(rd),
        "scan": build_scan(rd),
        "drivers": build_drivers(rd),
        "split": build_split(rd),
        "outcomes": build_outcomes(rd),
        "model2": build_model2(rd),
        "scoreboard": build_scoreboard(rd),
        "market_depth": build_market_depth(ref),
        "threshold_table": build_threshold_table(ref),
    }
    data["sources"] = sorted(set(rd.used))
    data["snapshot"] = (data["gsa"] or {}).get("snapshot")
    text = fit_to_cap(data, cap)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("/* Built by scripts/build_explorer.py from results/ and reference/. Do not edit by hand. */\n")
        f.write("window.FCC_DATA = " + text + ";\n")
    return data


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("--results", default=DEFAULT_RESULTS, help="folder with the result files (default: results/)")
    ap.add_argument("--reference", default=None, help="folder with the reference tables (default: reference/ next to the results folder)")
    ap.add_argument("--out", default=DEFAULT_OUT, help="output script (default: docs/data/explorer-data.js)")
    a = ap.parse_args(argv)
    if not os.path.isdir(a.results):
        sys.exit(f"no results folder: {a.results}")
    d = build(a.results, a.out, reference_dir=a.reference)
    size = os.path.getsize(a.out)
    print(f"explorer data: {len(d['agencies'])} agencies, {len(d['sources'])} source files, {size / 1024:.1f} KB -> {os.path.relpath(a.out, ROOT)}")
    if d.get("trimmed"):
        print("trimmed to stay under the size cap: " + "; ".join(d["trimmed"]))
    if size > SIZE_CAP:
        print(f"warning: the data file is over the {SIZE_CAP // 1024} KB aim even after trimming")


if __name__ == "__main__":
    main()
