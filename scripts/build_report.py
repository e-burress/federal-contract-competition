"""
build_report.py - write every number in README.md and docs/methodology.md from the result files, so none is typed by hand.

What it does
  The two documents contain marked blocks:
      <!-- auto:NAME -->  ...generated text...  <!-- /auto:NAME -->
  For each block, a small function below (registered in BLOCKS) reads the result files and writes the text between the
  markers.  Text outside the markers is never touched.  A block whose markers sit on their own lines is a block of whole
  lines (a paragraph, a list or a table); a block inside a line is replaced inline.  Running it twice changes nothing.

Input : results/*.csv, results/*.json, results/threshold_summary.txt,
        results/model_summary.txt (frozen, from the October 2026 model build), forecasts/SHA256SUMS.txt,
        forecasts/PIO-F2-threshold-fy2027q1-20261024.json, config.toml.  No downloads, no pandas.
Output: README.md and docs/methodology.md, rewritten in place (only the marked blocks).

Usage (from the repository root):
    python -I scripts/build_report.py            rewrite the blocks
    python -I scripts/build_report.py --check    change nothing; exit 1 and list the blocks that would change (for CI)
"""
import ast
import csv
import json
import math
import os
import re
import sys
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from functools import cache

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import CONFIG, FORECASTS, PROJECT, RESULTS  # noqa: E402

DOCS = [os.path.join(PROJECT, "README.md"), os.path.join(PROJECT, "docs", "methodology.md")]
MARK = re.compile(r"<!-- auto:([\w-]+) -->(.*?)<!-- /auto:\1 -->", re.S)

# The first release (October 24, 2026) was built from an earlier USAspending download that was not kept, so its numbers
# cannot be recomputed from any file.  They are copied here from that release's README and methodology, only to say
# how far the rebuild moved them.  The two "max_" values compare every published share and ratio of the two builds.
FIRST_RELEASE = {
    "date": "2026-10-24",
    "five_k_single": 22230, "five_n_eval": 97643,     # five departments: single-offer awards / competed awards
    "ratio_350k_after": 1.95,                         # bunching ratio under $350K, FY2026 (after the change)
    "max_share_change_points": 0.05,                  # largest change of a single-offer share, percentage points
    "max_ratio_change": 0.02,                         # largest change of a bunching ratio
}
SHORT = {"DHS": "DHS", "DOI": "Interior", "HHS": "HHS", "USDA": "USDA", "VA": "VA"}
SHOW_ABBR = {"HHS", "VA", "DHS", "DOT", "USDA", "DOI"}   # departments shown with their abbreviation in the agency table


# ---- reading ------------------------------------------------------------------------------------
@cache
def rows(name):
    with open(os.path.join(RESULTS, name), newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


@cache
def load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def res_json(name):
    return load_json(os.path.join(RESULTS, name))


@cache
def text(name):
    with open(os.path.join(RESULTS, name), encoding="utf-8") as fh:
        return fh.read()


def grab(pattern, name):
    m = re.search(pattern, text(name), re.M)
    if not m:
        sys.exit(f"build_report: could not find /{pattern}/ in results/{name}")
    return m


def text_table(name, title):
    """The whitespace-separated table printed under the line that contains `title` in a results/*.txt file."""
    lines = text(name).splitlines()
    for i, line in enumerate(lines):
        if title in line:
            header = lines[i + 1].split()
            out = []
            for line2 in lines[i + 2:]:
                if not line2.strip():
                    break
                out.append(dict(zip(header, line2.split())))
            return out
    sys.exit(f"build_report: could not find the table '{title}' in results/{name}")


def one(table, **match):
    hits = [r for r in table if all(str(r[k]) == str(v) for k, v in match.items())]
    if len(hits) != 1:
        sys.exit(f"build_report: expected one row matching {match}, found {len(hits)}")
    return hits[0]


# ---- formatting ---------------------------------------------------------------------------------
def rnd(x, d):
    return Decimal(repr(float(x))).quantize(Decimal(1).scaleb(-d), rounding=ROUND_HALF_UP)


def num(x, d=2):
    """A number with d decimals (half up), with a true minus sign."""
    return f"{rnd(x, d)}".replace("-", "−")


def sgn(x, d=2):
    s = num(x, d)
    return s if s.startswith("−") or rnd(x, d) == 0 else "+" + s


def pct(x, d=1):
    return num(float(x) * 100, d) + "%"


def pts(x, d=1):
    return num(float(x) * 100, d)


def n(x):
    return f"{int(float(x)):,}"


def money(t):
    return f"${int(float(t)) // 1000}K"


def long_date(iso):
    d = date.fromisoformat(iso)
    return f"{d:%B} {d.day}, {d.year}"


def short_date(iso):
    d = date.fromisoformat(iso)
    return f"{d:%b} {d.day}"


def fiscal_quarter(iso):
    d = date.fromisoformat(iso)
    return f"FY{d.year + (d.month >= 10)} Q{(d.month - 10) % 12 // 3 + 1}"


def fy_span():
    fys = CONFIG["analysis"]["fiscal_years"]
    return f"FY{fys[0]}–FY{fys[-1]}"


def snapshot_date():
    return CONFIG["data"]["snapshot"].removeprefix("data-")


def snapshot_link():
    snap = CONFIG["data"]["snapshot"]
    return f"[`{snap}`](https://github.com/{CONFIG['project']['repository']}/releases/tag/{snap})"


# ---- shared pieces ------------------------------------------------------------------------------
def five_by_agency():
    return sorted(rows("atlas_by_agency.csv"), key=lambda r: -float(r["share"]))


def headline():
    return res_json("atlas_headline.json")


def moved():
    return {int(r["threshold"]): r for r in rows("threshold_sat_moved.csv")}


def sat(period, threshold):
    return one(rows("threshold_sat_bunching.csv"), period=period, threshold=threshold)


def periods():
    """The three periods of the threshold test: FY2024, FY2025 (before), FY2026 (after the change)."""
    ps = list(dict.fromkeys(r["period"] for r in rows("threshold_sat_bunching.csv")))
    post = [p for p in ps if p.endswith("_post")]
    return ps, post[0]


def post_quarters(threshold):
    post_fy = periods()[1].split("_")[0]
    q = [r for r in rows("threshold_quarterly_bunching.csv") if int(r["threshold"]) == threshold and r["fyq"] >= post_fy + "Q1"]
    return sorted(q, key=lambda r: r["fyq"])


def quarter_path(threshold):
    q = post_quarters(threshold)
    vals = [float(r["ratio"]) for r in q]
    rising = all(b > a for a, b in zip(vals, vals[1:]))
    return " → ".join(num(v) for v in vals), rising


def civ_total(scope):
    return one(rows("atlas_civilian_totals.csv"), scope=scope)


def civ_fy(scope):
    return sorted([r for r in rows("atlas_civilian_by_fy.csv") if r["scope"] == scope], key=lambda r: r["fy"])


def first_release_sentence():
    fr, h = FIRST_RELEASE, headline()
    old_share, new_share = pct(fr["five_k_single"] / fr["five_n_eval"]), pct(h["share"])
    share_word = f"still {new_share}" if old_share == new_share else f"{old_share} → {new_share}"
    new350 = float(sat(periods()[1], 350000)["ratio"])
    return (f"The first release ({long_date(fr['date'])}) used an earlier download that was not kept. "
            f"Rebuilt from this snapshot, single-offer shares moved by at most {num(fr['max_share_change_points'])} "
            f"percentage points (five departments: {n(fr['five_n_eval'])} → {n(h['n_eval'])} awards, {share_word}) "
            f"and bunching ratios by at most {num(fr['max_ratio_change'])} ($350K after the change: "
            f"{num(fr['ratio_350k_after'])} → {num(new350)}).")


def premium():
    p = rows("premium_prelim.csv")
    val = next(r for r in p if r["outcome"] == "log_value0")
    po = next(r for r in p if r["outcome"] == "growth25" and r["spec"].startswith("PO"))
    return val, po


def premium_pct(x):
    return (math.exp(float(x)) - 1) * 100


def model_rows():
    return {r["model"]: r for r in rows("model_out_of_time.csv")}


BASELINE, OFFICE, MODEL = "agency-wide rate (naive baseline)", "office history only", "logistic regression (this model)"


def f1():
    return CONFIG["forecasts"]["F1"]


def f2_json():
    return load_json(os.path.join(PROJECT, CONFIG["forecasts"]["F2"]["file"]))


def hashes():
    out = {}
    with open(os.path.join(FORECASTS, "SHA256SUMS.txt"), encoding="utf-8") as fh:
        for line in fh:
            parts = line.split()
            if len(parts) >= 2:
                out[parts[1]] = parts[0]
    return out


def registered():
    m = grab(r"registered set: (\d+) of (\d+) eligible \(allocation (\{.*?\})\)", "model_summary.txt")
    return int(m[1]), int(m[2]), ast.literal_eval(m[3])


# ---- README blocks ------------------------------------------------------------------------------
def r_status():
    snap_day = long_date(snapshot_date())
    five = ", ".join(SHORT[a] for a in ["VA", "HHS", "DHS", "DOI"]) + " and USDA"
    return (f"> **Status ({snap_day}):** every result was rebuilt from one public data snapshot, {snapshot_link()}, "
            f"downloaded from USAspending on {snap_day}. `python run.py quick` downloads it and rebuilds everything. "
            f"{first_release_sentence()} The forecast model and the sealed forecasts are from {long_date(FIRST_RELEASE['date'])} "
            f"and are unchanged.\n>\n"
            f"> The in-depth analysis and the forecasts cover five civilian departments ({five}) for {fy_span()}. "
            f"Single-offer shares cover every civilian agency, and the threshold test is civilian-wide. "
            f"Defense data is excluded because of DoD's 90-day reporting delay.")


def r_threshold_table():
    m = moved()
    ps, post = periods()
    pre, aft = ps[1].split("_")[0], post.split("_")[0]
    out = [f"| Line | Bunching ratio, {pre} (before) | {aft} (after) | Change (95% interval) |", "|---|---|---|---|"]
    labels = {250000: ("Just under **$250K** (old line)", True), 350000: ("Just under **$350K** (new line)", True)}
    for t in (250000, 350000, 200000, 450000):
        r = m[t]
        label, bold = labels.get(t, (f"Placebo line {money(t)}", False))
        d = sgn(r["diff"])
        out.append(f"| {label} | {num(r['ratio_pre'])} | {num(r['ratio_post'])} | "
                   f"{f'**{d}**' if bold else d} ({sgn(r['diff_lo'])} to {sgn(r['diff_hi'])}) |")
    return "\n".join(out)


def r_threshold_quarters():
    path, rising = quarter_path(350000)
    return (f"The new pile-up grew every quarter after the change: {path}." if rising
            else f"The new pile-up by quarter after the change: {path}.")


def r_single_five():
    h = headline()
    by = five_by_agency()
    fy = rows("atlas_by_agency_fy.csv")
    fys = CONFIG["analysis"]["fiscal_years"]
    fell = [a["agency"] for a in by
            if float(one(fy, agency=a["agency"], fy=fys[-1])["share"]) < float(one(fy, agency=a["agency"], fy=fys[0])["share"])]
    trend = (f"- The share fell in every department from FY{fys[0]} to FY{fys[-1]}." if len(fell) == len(by)
             else f"- The share fell in {len(fell)} of {len(by)} departments from FY{fys[0]} to FY{fys[-1]}.")
    return "\n".join([
        f"Among {n(h['n_eval'])} competed awards with a usable offer count (purchase orders, definitive contracts and "
        f"contract vehicles, {fy_span()}):",
        "",
        f"- **{pct(h['share'])}** received exactly one offer (95% interval {pts(h['ci'][0])}–{pct(h['ci'][1])}).",
        "- By department: " + ", ".join(f"{SHORT[a['agency']]} **{pct(a['share'])}**" for a in by) + ".",
        f"- Weighted by dollars (purchase orders and definitive contracts), the share is **{pct(h['dollar_weighted_share_po_def'])}**, "
        f"close to GAO's figure of about 13% for FY2005–2009.",
        trend])


def r_civilian():
    allc, nog, gsa = civ_total("all civilian agencies"), civ_total("all civilian agencies except GSA"), civ_total("GSA only")
    other = civ_total("other civilian agencies except GSA")
    gfy, nfy = civ_fy("GSA only"), civ_fy("all civilian agencies except GSA")
    path = " → ".join(f"FY{r['fy']} {pct(r['share'])}" for r in nfy)
    check = rows("atlas_civilian_five_check.csv")
    exact = all(r["k_single_original"] == r["k_single_new"] and r["n_eval_original"] == r["n_eval_new"] for r in check)
    check_line = ("- Measured from the civilian-wide table, the five departments match the five-department table exactly."
                  if exact else
                  f"- Measured from the civilian-wide table, the five departments match the five-department table to within "
                  f"{num(max(abs(float(r['share_change_points'])) for r in check), 2)} percentage points.")
    return "\n".join([
        f"- Across all {allc['n_agencies']} civilian agencies with competed awards, **{pct(allc['share'])}** of "
        f"{n(allc['n_eval'])} competed awards got a single offer.",
        f"- **GSA** accounts for {pct(int(gsa['n_eval']) / int(allc['n_eval']), 0)} of those awards. Almost all are very small "
        f"purchase orders from GSA's Federal Acquisition Service, mostly supply orders (median award "
        f"${num(gsa['median_award_value'], 0)}), and their offer coding changed sharply: one offer on "
        f"{pct(gfy[0]['share'], 0)} of them in FY{gfy[0]['fy']}, {pct(gfy[-1]['share'], 0)} in FY{gfy[-1]['fy']}. "
        f"So the civilian-wide figure is also given **without GSA: {pct(nog['share'])}** of {n(nog['n_eval'])} awards ({path}).",
        f"- Outside GSA and the five departments above, the share is **{pct(other['share'])}** ({n(other['n_eval'])} awards).",
        check_line])


def agency_label(name, abbr):
    short = re.sub(r"^Department of (the )?", "", name)
    if short != name:                                   # a department
        return f"{short} ({abbr})" if abbr in SHOW_ABBR else short
    return f"{name} ({abbr})" if abbr else name


def r_civilian_table():
    small = next(r for r in rows("atlas_civilian_totals.csv") if r["scope"].startswith("agencies with fewer than"))
    cut = int(re.search(r"fewer than (\d+)", small["scope"])[1])
    big = sorted([r for r in rows("atlas_civilian_by_agency.csv") if int(r["n_eval"]) >= cut], key=lambda r: -float(r["share"]))
    fys = CONFIG["analysis"]["fiscal_years"]
    out = [f"| Agency (at least {cut} competed awards) | Single-offer share, FY{fys[0]}–{str(fys[-1])[-2:]} | Competed awards |",
           "|---|---|---|"]
    out += [f"| {agency_label(r['agency'], r['abbreviation'])} | {pct(r['share'])} | {n(r['n_eval'])} |" for r in big]
    out += ["", f"The {small['n_agencies']} smaller agencies together: {pct(small['share'])} of {n(small['n_eval'])} awards. "
            "Full tables: [`results/atlas_civilian_by_agency.csv`](results/atlas_civilian_by_agency.csv) and the other "
            "`atlas_civilian_*` files."]
    return "\n".join(out)


def r_model_table():
    m = model_rows()
    out = ["| Model | AUC | Brier score |", "|---|---|---|"]
    for key, label, bold in ((BASELINE, "Department-wide rate (baseline)", False), (OFFICE, "Office history only", False),
                             (MODEL, "This model", True)):
        cells = [label, num(m[key]["auc"]), num(m[key]["brier"], 3)]
        out.append("| " + " | ".join(f"**{c}**" if bold else c for c in cells) + " |")
    return "\n".join(out)


def r_model_applied():
    a, b = f1()["deadlines"]
    return (f"The model was then applied to **{f1()['n_solicitations']} currently open solicitations** with response deadlines "
            f"between {short_date(a)} and {short_date(b)}, {b[:4]}. Those forecasts are sealed below.")


def r_forecasts():
    F1, F2 = f1(), CONFIG["forecasts"]["F2"]
    j, h = f2_json(), hashes()
    q = {x["threshold"]: x for x in j["quantities"] if x["kind"] == "bunching_ratio"}
    checks = F1["checks"]
    check_words = " and ".join(short_date(c) for c in checks[:-1])
    a, b = q[350000], q[250000]
    f2_text = (f"{fiscal_quarter(j['period']['start'])} bunching ratios under $350K ({num(a['forecast'])}; 80% interval "
               f"{num(a['interval80'][0])}–{num(a['interval80'][1])}) and $250K ({num(b['forecast'])}; "
               f"{num(b['interval80'][0])}–{num(b['interval80'][1])}), plus the micro-purchase reporting share")
    short1, short2 = "-".join(F1["id"].split("-")[:2]), "-".join(F2["id"].split("-")[:2])
    return "\n".join([
        f"Forecasts were made on **{F1['sealed']}** and fingerprinted with SHA-256 before any outcome was known. "
        f"See [`forecasts/`](forecasts/).",
        "",
        "| ID | What it forecasts | Resolves | SHA-256 |",
        "|---|---|---|---|",
        f"| {short1} | Probability of a single offer for each of {F1['n_solicitations']} open civilian solicitations | "
        f"{checks[-1]} (checks {check_words}) | `{h[F1['id'] + '.json']}` |",
        f"| {short2} | {f2_text} | {j['resolution_date']} | `{h[F2['id'] + '.json']}` |"])


def r_limit_premium():
    val, po = premium()
    same = float(po["lo"]) <= 0 <= float(po["hi"])
    size = premium_pct(val["coef"])
    word = "*smaller*" if size < 0 else "*larger*"
    growth = "found no detectable difference in post-award growth" if same else "found a difference in post-award growth"
    return f"A preliminary check found single-offer awards are {num(abs(size), 0)}% {word} within comparable categories, and {growth}."


def r_limit_999():
    return f"{n(headline()['offers_999_among_competed'])} competed awards"


def m_999():
    return f"({r_limit_999()} in the five departments)"


def fy2026_drop():
    fy = rows("atlas_by_agency_fy.csv")
    fys = CONFIG["analysis"]["fiscal_years"]
    drops = [1 - int(one(fy, agency=a, fy=fys[-1])["n_eval"]) / int(one(fy, agency=a, fy=fys[0])["n_eval"]) for a in ("HHS", "VA")]
    return f"{pct(min(drops), 0)[:-1]}–{pct(max(drops), 0)}", fys


def r_limit_fy2026():
    span, fys = fy2026_drop()
    return f"HHS and VA competed-award counts fell {span} from FY{fys[0]} to FY{fys[-1]}."


# ---- methodology blocks ---------------------------------------------------------------------------
def m_header():
    snap_day = long_date(snapshot_date())
    return (f"First version built October 23–24, 2026. Every result was rebuilt on {snap_day} from one public data snapshot, "
            f"{snapshot_link()} (USAspending, downloaded {snap_day}). USAspending data runs through "
            f"{CONFIG['analysis']['window_end']}. The forecast model (section 5) and the sealed forecasts (section 6) are from the "
            f"{long_date(FIRST_RELEASE['date'])} build and are frozen.")


def m_download():
    civ = res_json("build_summary_civilian.json")
    return (f"**One download, one snapshot.** `01c_download_civilian.py` downloads the contract transactions of every civilian "
            f"agency ({n(civ['transaction_rows'])} transactions, {fy_span()}), one request per quarter. From them, "
            f"`02_build_awards.py` builds the five-department table (default) and the civilian table (`--civilian`), and "
            f"`bunching_lib.py --rebuild` builds the threshold test's base actions. These three tables are published as the "
            f"snapshot {snapshot_link()}, downloaded {long_date(snapshot_date())}; `python run.py quick` downloads it and "
            f"rebuilds every result. `01_download_usaspending.py` (the five departments, one request per agency) and "
            f"`01b_download_threshold_universe.py` (purchase orders and definitive contracts only) are older alternatives.\n\n"
            f"{first_release_sentence()}")


def m_coverage():
    b = res_json("build_summary_five.json")
    return (f"{n(b['awards_seen'])} awards → {n(b['awards_with_base_action'])} with a base action in the window → "
            f"{n(b['competed'])} competed → **{n(b['eval_set'])} with a usable offer count** ({pct(b['eval_set'] / b['competed'])}). "
            f"Every civilian agency: section 3b.")


def m_recount():
    b, h = res_json("build_summary_five.json"), headline()
    return (f"An independent recount, `scripts/check_counts_sql.py`, reads the raw download with Python's csv module and counts "
            f"in SQLite (SQL), with no pandas and none of the pipeline's code. It reproduces every count, for the five departments "
            f"and for every civilian agency (in the five departments: {n(b['awards_with_base_action'])} base awards, "
            f"{n(h['n_eval'])} competed with a usable offer count, {n(h['k_single'])} single-offer awards). It needs the raw download, so it runs in `python run.py all`.")


def m_five_table():
    fy = rows("atlas_by_agency_fy.csv")
    fys = CONFIG["analysis"]["fiscal_years"]
    h = headline()
    out = [f"| Department | Single-offer share, FY{fys[0]}–{str(fys[-1])[-2:]} | " + " | ".join(f"FY{y}" for y in fys) + " |",
           "|---" * (2 + len(fys)) + "|"]
    for a in five_by_agency():
        cells = [pct(one(fy, agency=a["agency"], fy=y)["share"]) for y in fys]
        out.append(f"| {SHORT[a['agency']]} | {pct(a['share'])} (n = {n(a['n_eval'])}) | " + " | ".join(cells) + " |")
    out.append(f"| **All five** | **{pct(h['share'])}** (n = {n(h['n_eval'])}) |" + " |" * len(fys))
    return "\n".join(out)


def m_other_cuts():
    h = headline()
    fo = sorted(h["full_and_open"], key=lambda r: -int(r["k_single"]) / int(r["n_eval"]))
    sol = h["by_solicitation_id"]
    share = lambda r: int(r["k_single"]) / int(r["n_eval"])  # noqa: E731
    return "\n".join([
        "Other cuts:",
        "- **Full-and-open competition only:** " + ", ".join(f"{SHORT[r['agency']]} {pct(share(r))}" for r in fo) + ".",
        f"- **By solicitation identifier:** awards carrying one had a {pct(share(sol['True']))} single-offer rate; "
        f"awards without one, {pct(share(sol['False']))}.",
        f"- **Dollar-weighted** (purchase orders and definitive contracts): {pct(h['dollar_weighted_share_po_def'])} of competed obligations.",
        f"- **Awards of $20M or more:** {pct(h['awards_20m_plus_share'])} (n = {n(h['awards_20m_plus_n'])})."])


def m_premium():
    val, po = premium()
    size = premium_pct(val["coef"])
    word = "smaller" if size < 0 else "larger"
    lo, hi = premium_pct(val["lo"]), premium_pct(val["hi"])
    same = float(po["lo"]) <= 0 <= float(po["hi"])
    return (f"**Price premium (preliminary).** Federal award data has no unit prices, so a true price premium can't be measured. "
            f"Within agency × year × award type × 4-character product code cells ({n(val['n'])} awards, {n(val['n_cells'])} cells), "
            f"single-offer awards are {num(abs(size), 1)}% {word} in initial value (95% interval {num(lo, 1)}% to {num(hi, 1)}%). "
            f"That is a size difference, not a price. Growth in potential value of 25% or more after award shows "
            f"{'no detectable difference' if same else 'a difference'}: {sgn(float(po['coef']) * 100)} percentage points for "
            f"purchase orders ({sgn(float(po['lo']) * 100)} to {sgn(float(po['hi']) * 100)}).")


def m_offices():
    o = headline()["offices"]
    return (f"Office-level shares were computed ({n(o['n_offices'])} offices, {n(o['n_offices_100_plus'])} with at least 100 awards). They are persistent over "
            f"time: the correlation between FY2024–25 and FY2026 office shares is {num(o['persistence_corr'])}. They are not published in this release.")


def m_civ_data():
    c = res_json("build_summary_civilian.json")
    allc = civ_total("all civilian agencies")
    return (f"**Data.** The same download, all civilian agencies, {fy_span()}: {n(c['transaction_rows'])} transactions. "
            f"`02_build_awards.py --civilian` applies the same rules as above: {n(c['awards_seen'])} awards → "
            f"{n(c['awards_with_base_action'])} with a base action in the window → {n(c['competed'])} competed "
            f"({pct(c['competed'] / c['awards_with_base_action'])}) → **{n(c['eval_set'])} with a usable offer count** "
            f"({pct(c['eval_set'] / c['competed'])} of competed; {n(c['offers_999_among_competed'])} carry the 999 placeholder). "
            f"{allc['n_agencies']} agencies have competed awards with a usable offer count.")


def m_civ_check():
    check = rows("atlas_civilian_five_check.csv")
    exact = all(r["k_single_original"] == r["k_single_new"] and r["n_eval_original"] == r["n_eval_new"] for r in check)
    how = ("match it exactly, award for award" if exact else
           f"match it to within {num(max(abs(float(r['share_change_points'])) for r in check), 2)} percentage points")
    return (f"**Check against the five-department table.** The five departments, measured from the civilian table, {how}. "
            f"See `results/atlas_civilian_five_check.csv`.")


def m_gsa():
    allc, gsa = civ_total("all civilian agencies"), civ_total("GSA only")
    g = civ_fy("GSA only")
    yrs = ", ".join(f"{pct(r['share'])} in FY{r['fy']}" for r in g[:-1]) + f" and {pct(g[-1]['share'])} in FY{g[-1]['fy']}"
    return (f"**GSA.** {n(gsa['n_eval'])} of the {n(allc['n_eval'])} awards ({pct(int(gsa['n_eval']) / int(allc['n_eval']), 0)}) "
            f"are GSA's, almost all very small purchase orders from GSA's Federal Acquisition Service (median award "
            f"${num(gsa['median_award_value'], 0)}). In a check of FY2026 Q1, most came from its supply-chain contracting division, "
            f"and the most common products were office supplies, hand tools and measuring tools. Their recorded offers changed "
            f"sharply: one offer on {yrs}, as most began recording more than one offer. That may be a change in how these "
            f"automated orders are recorded rather than in competition. Every civilian-wide total is therefore also given without GSA.")


def m_civ_table():
    fys = CONFIG["analysis"]["fiscal_years"]
    out = [f"| Scope | Single-offer share, FY{fys[0]}–{str(fys[-1])[-2:]} | Competed awards |", "|---|---|---|"]
    for scope, label, ci in (("all civilian agencies", "All civilian agencies", "95% interval "),
                             ("all civilian agencies except GSA", "All civilian agencies except GSA", ""),
                             ("other civilian agencies except GSA", "Other civilian agencies, not GSA and not the original five", ""),
                             ("GSA only", "GSA only", None)):
        r = civ_total(scope)
        share = pct(r["share"]) + ("" if ci is None else f" ({ci}{pts(r['ci_lo'])}–{pct(r['ci_hi'])})")
        out.append(f"| {label} | {share} | {n(r['n_eval'])} |")
    return "\n".join(out)


def m_civ_trend():
    f = civ_fy("all civilian agencies except GSA")
    vals = [float(r["share"]) for r in f]
    verb = "fell" if all(b < a for a, b in zip(vals, vals[1:])) else "went"
    steps = " and ".join([f"{pct(f[1]['share'])} (FY{f[1]['fy']})", f"{pct(f[2]['share'])} (FY{f[2]['fy']})"]) if len(f) == 3 else \
        ", ".join(f"{pct(r['share'])} (FY{r['fy']})" for r in f[1:])
    return f"Without GSA, the share {verb} from {pct(f[0]['share'])} (FY{f[0]['fy']}) to {steps}."


def m_threshold_data():
    m = grab(r"periods: FY2024 ([\d,]+) \| FY2025 \(pre\) ([\d,]+) \| FY2026 \(post\) ([\d,]+)", "threshold_summary.txt")
    a = grab(r"base actions [\d,]+; (\d+) agencies", "threshold_summary.txt")
    return (f"**Data:** civilian purchase orders and definitive contracts ({a[1]} agencies), base actions only, from the same "
            f"download. FY2024: {m[1]}. FY2025: {m[2]}. FY2026: {m[3]}.")


def m_threshold_table():
    ps, post = periods()
    m = moved()
    labels = [p.split("_")[0] for p in ps]
    out = ["| Line | " + " | ".join(labels) + f" | Change, {labels[1]} → {labels[2]} |", "|---" * (len(ps) + 2) + "|"]
    for t in (250000, 350000, 200000, 450000):
        placebo = t in (200000, 450000)
        cells = []
        for p in ps:
            r = sat(p, t)
            cells.append(num(r["ratio"]) if placebo else f"{num(r['ratio'])} ({num(r['ci_lo'])}–{num(r['ci_hi'])})")
        d = m[t]
        out.append(f"| {money(t)}{' (placebo)' if placebo else ''} | " + " | ".join(cells)
                   + f" | {sgn(d['diff'])} ({sgn(d['diff_lo'])} to {sgn(d['diff_hi'])}) |")
    return "\n".join(out)


def m_threshold_quarters():
    path, rising = quarter_path(350000)
    return f"By fiscal quarter after the change, the $350K ratio {'rose' if rising else 'went'} {path}."


def m_micro():
    b = {int(r["bin_upper"]): r for r in rows("threshold_mpt_1k_bins.csv")}
    pre, post = [c for c in rows("threshold_mpt_1k_bins.csv")[0] if c != "bin_upper"]
    band = lambda lo, hi, col: sum(int(b[k][col]) for k in range(lo + 1, hi + 1))  # noqa: E731  ($1K bins (k-1, k])
    a0, a1, c0, c1 = band(10, 15, pre), band(10, 15, post), band(15, 20, pre), band(15, 20, post)
    return (f"Reported purchase orders between $10K and $15K fell {num((1 - a1 / a0) * 100, 0)}% ({n(a0)} → {n(a1)}), "
            f"against {sgn((c1 / c0 - 1) * 100, 0)}% between $15K and $20K.")


def m_did():
    bands = rows("threshold_competition_bands.csv")
    s = lambda band, per: float(one(bands, band=band, period=per)["single_offer_share"])  # noqa: E731
    pre, post = sorted({r["period"] for r in bands})
    did = (s("treated 250-350K", post) - s("treated 250-350K", pre)) - (s("control 350-450K", post) - s("control 350-450K", pre))
    ci = grab(r"difference-in-differences of single-offer share .*?= [-+\d.]+ percentage points, 95% bootstrap interval "
              r"\[([-+\d.]+), ([-+\d.]+)\]", "threshold_summary.txt")
    lo, hi = float(ci[1]), float(ci[2])
    verdict = "no detectable effect so far" if lo <= 0 <= hi else "a detectable effect"
    return f"The result was {sgn(did * 100)} percentage points ({sgn(lo, 1)} to {sgn(hi, 1)}): {verdict}."


def m_model_frozen():
    return (f"These results are from the {long_date(FIRST_RELEASE['date'])} build and are frozen. Its SAM.gov training inputs "
            f"were not kept, so `run.py` does not rebuild them (scripts 05–08 and 11); the model will be rebuilt on SAM.gov's "
            f"official archive files. The files are `results/model_*` and `results/resolution_backtest_summary.csv`.")


def m_model_pop():
    card = res_json("model_card.json")
    pop = grab(r"Population P: ([\d,]+) solicitations .*?single-offer share ([\d.]+)", "model_summary.txt")
    look = grab(r"SAM lookups: ([\d,]+) solicitations looked up \| found a Solicitation/Combined Synopsis notice: ([\d,]+) "
                r"\(([\d.]+)%\)", "model_summary.txt")
    return "\n".join([
        f"- **Population:** {n(card['n_population'])} competed solicitations (one per solicitation number, {fy_span()}) with a "
        f"single-offer share of {pct(pop[2])}.",
        f"- **Response windows:** looked up in SAM.gov for a random sample. {look[1]} solicitation numbers were looked up and "
        f"{look[2]} found ({look[3]}%). After dropping missing, negative or extreme windows, {n(card['n_sample_L'])} were usable.",
        f"- **Model:** L2 logistic regression (C = {card['C']}, chosen by 5-fold cross-validation inside the training period)."])


def m_oot():
    card, m = res_json("model_card.json"), model_rows()
    t = grab(r"train on solicitations whose award came on/before (\d{4}-\d\d-\d\d)", "model_summary.txt")
    test = grab(r"test ([\d,]+) \(share ([\d.]+)\)", "model_summary.txt")
    # calibration rows look like "(0.1, 0.2]  272  0.142  0.140": bin, n, mean forecast, observed
    cal_lines = {mm[1]: mm for mm in re.finditer(r"^\((\d\.\d), \d\.\d\]\s+(\d+)\s+([\d.]+)\s+([\d.]+)", text("model_summary.txt"), re.M)}
    lo_bin, hi_bin = cal_lines["0.1"], cal_lines["0.2"]
    mod, base = m[MODEL], m[BASELINE]
    skill = 1 - float(mod["brier"]) / float(base["brier"])
    return "\n".join([
        f"**Out-of-time test** (train on awards through {t[1]}, n = {n(card['n_train_oot'])}; test on FY2026 awards, "
        f"n = {n(card['n_test_oot'])}, {pct(test[2])} single-offer):",
        f"- AUC {num(mod['auc'], 3)} (95% interval {num(card['oot_auc_ci'][0], 3)}–{num(card['oot_auc_ci'][1], 3)}), against "
        f"{num(base['auc'], 3)} for the department-wide baseline.",
        f"- Brier score {num(mod['brier'], 4)} against {num(base['brier'], 4)} ({pct(skill)} skill).",
        f"- Calibration is good below 0.2 ({lo_bin[3]} forecast vs {lo_bin[4]} observed in the 0.1–0.2 bin). The 0.2–0.3 bin "
        f"was under-forecast ({hi_bin[3]} vs {hi_bin[4]}, n = {hi_bin[2]})."])


def m_effects():
    co = {r["feature"]: float(r["odds_ratio"]) for r in rows("model_coefficients.csv")}
    items = [("cat__setaside_none", "no set-aside", ""), ("num__office_rate", "buying-office history", " per standard deviation"),
             ("cat__psc2_65", "medical-supply product codes", ""), ("cat__setaside_small business", "small-business set-aside", ""),
             ("cat__setaside_SDVOSB", "service-disabled-veteran set-aside", ""),
             ("num__wl", "longer response window", " per standard deviation")]
    return "\n".join(["**Largest effects** (odds ratios):"] + [f"- {label} {num(co[k])}{unit}" for k, label, unit in items])


def m_open():
    m = grab(r"OPEN SOLICITATIONS: (\d+) candidates in the five agencies \| [^:]*: (\d+) \| excluded by the date-plausibility "
             r"rule: (\d+) \| ELIGIBLE: (\d+)", "model_summary.txt")
    a, b = f1()["deadlines"]
    return (f"**Open solicitations** (pulled {f1()['sealed']}): {n(m[1])} candidate notices in the five modeled departments, "
            f"{n(m[2])} of them open, not canceled, of an eligible type and with a deadline between {a} and {b}.")


def m_registered():
    m = grab(r"excluded by the date-plausibility rule: (\d+) \| ELIGIBLE: (\d+)", "model_summary.txt")
    k, elig, alloc = registered()
    parts = ", ".join(f"{SHORT[a]} {c}" for a, c in sorted(alloc.items(), key=lambda kv: -kv[1]))
    return "\n".join([
        f"- {n(m[2])} notices met the rule. {n(m[1])} were excluded by the date-plausibility check, mostly for typo'd deadlines.",
        f"- **{k} were registered**, by stratified random sample (seed 20261023): {parts}."])


def m_backtest():
    bt = rows("resolution_backtest_summary.csv")
    tot = sum(int(r["n_notices"]) for r in bt)
    matched = sum(int(r["n_matched"]) for r in bt) / tot
    resolv = sum(int(r["n_resolvable"]) for r in bt) / tot
    k = f1()["n_solicitations"]
    expect = round(k * resolv / 10) * 10
    return (f"**Expected resolution rate.** A backtest on {n(tot)} solicitations with March–April 2026 deadlines found that "
            f"{pct(matched)} matched an award and {pct(resolv)} were scoreable. Expect about {expect} of the {k} forecasts to "
            f"resolve by {f1()['checks'][-1]}. Single-offer events among them will be few, so the first batch will measure "
            f"calibration roughly, not skill.")


def m_f2():
    j = f2_json()
    return (f"**Threshold forecast (PIO-F2):** {fiscal_quarter(j['period']['start'])} bunching ratios, scored once that quarter's "
            f"data is in USAspending (from {j['earliest_scoring_date']}).")


def m_limit_fy2026():
    span, fys = fy2026_drop()
    return f"HHS and VA competed-award counts fell {span} from FY{fys[0]} to FY{fys[-1]}."


BLOCKS = {
    # README.md
    "status": r_status, "threshold-table": r_threshold_table, "threshold-quarters": r_threshold_quarters,
    "single-offer-five": r_single_five, "civilian": r_civilian, "civilian-table": r_civilian_table,
    "model-table": r_model_table, "model-applied": r_model_applied, "forecasts": r_forecasts,
    "limit-premium": r_limit_premium, "limit-999": r_limit_999, "limit-fy2026": r_limit_fy2026,
    # docs/methodology.md
    "m-header": m_header, "m-download": m_download, "m-999": m_999, "m-coverage": m_coverage, "m-recount": m_recount,
    "m-five-table": m_five_table, "m-other-cuts": m_other_cuts, "m-premium": m_premium, "m-offices": m_offices,
    "m-civ-data": m_civ_data, "m-civ-check": m_civ_check, "m-gsa": m_gsa, "m-civ-table": m_civ_table,
    "m-civ-trend": m_civ_trend, "m-threshold-data": m_threshold_data, "m-threshold-table": m_threshold_table,
    "m-threshold-quarters": m_threshold_quarters, "m-micro": m_micro, "m-did": m_did, "m-model-frozen": m_model_frozen,
    "m-model-pop": m_model_pop, "m-oot": m_oot, "m-effects": m_effects, "m-open": m_open, "m-registered": m_registered,
    "m-backtest": m_backtest, "m-f2": m_f2, "m-limit-fy2026": m_limit_fy2026,
}


def fill(doc_text, changed, seen, label):
    def sub(m):
        name, old = m[1], m[2]
        if name not in BLOCKS:
            sys.exit(f"build_report: {label} has a block 'auto:{name}' with no function in BLOCKS")
        seen.add(name)
        body = BLOCKS[name]().strip("\n")
        new = f"\n{body}\n" if old.startswith("\n") else body     # a block of whole lines, or inline
        if new != old:
            changed.append(f"{label}: auto:{name}")
        return f"<!-- auto:{name} -->{new}<!-- /auto:{name} -->"
    return MARK.sub(sub, doc_text)


def main():
    check = "--check" in sys.argv[1:]
    changed, seen = [], set()
    for path in DOCS:
        label = os.path.relpath(path, PROJECT).replace(os.sep, "/")
        with open(path, encoding="utf-8", newline="") as fh:
            old = fh.read()
        new = fill(old, changed, seen, label)
        if new != old and not check:
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(new)
    unused = sorted(set(BLOCKS) - seen)
    if unused:
        print("note: blocks defined but not found in any document: " + ", ".join(unused))
    if check:
        if changed:
            print("These blocks are out of date (run: python -I scripts/build_report.py):")
            print("\n".join("  " + c for c in changed))
            sys.exit(1)
        print(f"report: all {len(seen)} blocks are up to date.")
    else:
        print(f"report: {len(seen)} blocks checked, {len(changed)} rewritten" + (":\n  " + "\n  ".join(changed) if changed else "."))


if __name__ == "__main__":
    main()
