"""
build_explorer.py - build the data file behind the interactive explorer (docs/explorer.html).

Run from the repository root:
    python -I scripts/build_explorer.py [--results DIR] [--out FILE]
It reads only result files (results/*.csv, results/*.json), never data/, and writes docs/data/explorer-data.js, a small script that sets
window.FCC_DATA = {...}.  A script file (not JSON loaded with fetch) lets the page work both on GitHub Pages and when opened from disk.
Standard library only.  Optional inputs (the long history and the threshold history) are skipped when they do not exist yet.
Privacy: agency level only.  No buying offices and no companies are read or written.
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

    def __init__(self, folder):
        self.folder = folder
        self.used = []

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
        self.used.append(name)
        return rows

    def json(self, name):
        if not self.exists(name):
            return None
        with open(self.path(name), encoding="utf-8") as f:
            obj = json.load(f)
        self.used.append(name)
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


def config_repo():
    """owner/name from config.toml (as build_dashboard.py does), else the default."""
    try:
        import tomllib
        with open(os.path.join(ROOT, "config.toml"), "rb") as f:
            return tomllib.load(f)["project"]["repository"]
    except Exception:
        return DEFAULT_REPO


def build(results_dir=DEFAULT_RESULTS, out_path=DEFAULT_OUT, repo=None, today=None):
    rd = Reader(results_dir)
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
    }
    data["sources"] = sorted(set(rd.used))
    data["snapshot"] = (data["gsa"] or {}).get("snapshot")
    text = json.dumps(data, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("/* Built by scripts/build_explorer.py from results/. Do not edit by hand. */\n")
        f.write("window.FCC_DATA = " + text + ";\n")
    return data


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("--results", default=DEFAULT_RESULTS, help="folder with the result files (default: results/)")
    ap.add_argument("--out", default=DEFAULT_OUT, help="output script (default: docs/data/explorer-data.js)")
    a = ap.parse_args(argv)
    if not os.path.isdir(a.results):
        sys.exit(f"no results folder: {a.results}")
    d = build(a.results, a.out)
    size = os.path.getsize(a.out)
    print(f"explorer data: {len(d['agencies'])} agencies, {len(d['sources'])} source files, {size / 1024:.1f} KB -> {os.path.relpath(a.out, ROOT)}")


if __name__ == "__main__":
    main()
