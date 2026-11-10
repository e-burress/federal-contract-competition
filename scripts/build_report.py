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
        forecasts/PIO-F2-threshold-fy2027q1-20261024.json, forecasts/daily/ledger.csv (once the daily series has started),
        config.toml.  No downloads, no pandas.
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
from statistics import mean, median

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
# Caches are keyed by the full path, so the tests can point RESULTS, REFERENCE and FIGURES at a temporary folder.
REFERENCE = os.path.join(PROJECT, "reference")
FIGURES = os.path.join(PROJECT, "figures")
SCRIPTS = os.path.dirname(os.path.abspath(__file__))


@cache
def read_csv(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def rows(name):
    return read_csv(os.path.join(RESULTS, name))


@cache
def load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def res_json(name):
    return load_json(os.path.join(RESULTS, name))


@cache
def read_text(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def text(name):
    return read_text(os.path.join(RESULTS, name))


def opt_rows(name, folder=None):
    """The rows of an OPTIONAL result file: [] when it is missing or cannot be read (for example half written by a running script)."""
    path = os.path.join(folder or RESULTS, name)
    if not os.path.exists(path):
        return []
    try:
        return read_csv(path)
    except (OSError, csv.Error, UnicodeDecodeError, ValueError):
        return []


def opt_json(name, folder=None):
    """An OPTIONAL JSON result file, or None when it is missing or cannot be read."""
    path = os.path.join(folder or RESULTS, name)
    if not os.path.exists(path):
        return None
    try:
        return load_json(path)
    except (OSError, UnicodeDecodeError, ValueError):
        return None


def clear_caches():
    for f in (read_csv, load_json, read_text):
        f.cache_clear()


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
    """The size row of premium_prelim.csv, and its obligation rows keyed by (kind, outcome): ("PO", "overrun25") ..."""
    p = rows("premium_prelim.csv")
    val = next(r for r in p if r["outcome"] == "log_value0")
    obl = {(r["spec"].split(":")[0], r["outcome"]): r for r in p if r["outcome"] in ("overrun25", "oblig_ratio_w")}
    return val, obl


def obligation_pattern(obl):
    """How the four obligation estimates read together: 'none' (every interval includes zero), 'more' or 'less' (the ones
    that exclude zero all point one way: single-offer awards obligated more, or less, relative to their initial value), 'mixed'."""
    signs = {1 if float(r["lo"]) > 0 else -1 for r in obl.values() if not float(r["lo"]) <= 0 <= float(r["hi"])}
    return "none" if not signs else ("mixed" if len(signs) > 1 else ("more" if signs == {1} else "less"))


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
            f"{first_release_sentence()} The first forecast model (version 1) and the sealed forecasts PIO-F1 and PIO-F2 are from {long_date(FIRST_RELEASE['date'])} "
            f"and are unchanged.\n>\n"
            f"> The in-depth analysis and the forecasts cover five civilian departments ({five}) for {fy_span()}. "
            f"Single-offer shares cover every civilian agency, and the threshold test is civilian-wide. "
            f"These {fy_span()} tables leave out Defense because of DoD's 90-day reporting delay.\n>\n"
            f"> {history_status()}")


def history_status():
    """One sentence on the long history: which fiscal years its results use now, and which are still to come."""
    years = history_years()
    if not years:
        return ("**Long history (FY2008 onward), in progress:** its yearly tables have not been built yet, so findings 4 to 10 "
                "say \"not yet available\".")
    want = range(HISTORY_FIRST_FY, CONFIG["analysis"]["fiscal_years"][-1] + 1)
    missing = [y for y in want if y not in years]
    tail = f" {fy_text(missing)} {'is' if len(missing) == 1 else 'are'} still to come." if missing else ""
    return (f"**Long history, in progress:** findings 4 to 10 add civilian agencies and Defense from FY{HISTORY_FIRST_FY} on. "
            f"The single-offer history now uses {fy_text(years)}.{tail} Each finding names the fiscal years it uses, which can differ "
            f"while the history is being built.")


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
        f"purchase orders, mostly supply orders (median award "
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
    val, obl = premium()
    size = premium_pct(val["coef"])
    word = "*smaller*" if size < 0 else "*larger*"
    after = {"none": "found no detectable difference in dollars obligated after award, relative to the initial value",
             "more": "found that they were obligated slightly more after award, relative to their initial value",
             "less": "found that they were obligated slightly less after award, relative to their initial value",
             "mixed": "found only small differences, in both directions, in dollars obligated after award relative to the initial value"}
    return (f"A preliminary check found single-offer awards are {num(abs(size), 0)}% {word} within comparable categories, "
            f"and {after[obligation_pattern(obl)]}.")


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
    val, obl = premium()
    size = premium_pct(val["coef"])
    word = "smaller" if size < 0 else "larger"
    lo, hi = premium_pct(val["lo"]), premium_pct(val["hi"])
    po, de = obl[("PO", "overrun25")], obl[("DEF", "overrun25")]
    rpo, rde = obl[("PO", "oblig_ratio_w")], obl[("DEF", "oblig_ratio_w")]
    p = lambda r: f"{sgn(float(r['coef']) * 100)} percentage points ({sgn(float(r['lo']) * 100)} to {sgn(float(r['hi']) * 100)})"
    reading = {"none": "None of these differences can be told apart from zero.",
               "more": "Single-offer awards were obligated slightly more, relative to their initial value.",
               "less": "Single-offer awards were obligated slightly less, relative to their initial value.",
               "mixed": "The differences are small and do not all point the same way, so they show no clear pattern."}
    return (f"**Price premium (preliminary).** Federal award data has no unit prices, so a true price premium can't be measured. "
            f"Within agency × year × award type × 4-character product code cells ({n(val['n'])} awards, {n(val['n_cells'])} cells), "
            f"single-offer awards are {num(abs(size), 1)}% {word} in initial value (95% interval {num(lo, 1)}% to {num(hi, 1)}%). "
            f"That is a size difference, not a price. "
            f"After award, we compare the dollars obligated by every action in the window with the award's initial potential value "
            f"(base and all options), within agency × year × 2-character product code × value quintile cells, with standard errors "
            f"clustered by buying office. "
            f"Among multi-offer purchase orders in those cells, {pct(po['mean_multi_offer'])} were obligated at 125% or more of their initial value; "
            f"single-offer ones differed by {p(po)}. "
            f"For definitive contracts the base rate was {pct(de['mean_multi_offer'])} and the difference {p(de)}. "
            f"Dollars obligated as a percentage of initial value (limited to 0% to 300%) averaged {pct(rpo['mean_multi_offer'])} for "
            f"multi-offer purchase orders, and single-offer ones differed by {p(rpo)}; for definitive contracts, "
            f"{pct(rde['mean_multi_offer'])} and {p(rde)}. {reading[obligation_pattern(obl)]} "
            f"An earlier version reported growth in potential value of 25% or more, measured with the largest value on any single "
            f"action. In this data each action records a change, not a running total, so that measure missed most growth; it was "
            f"replaced by the obligation measures above. Growth of the potential value itself needs the sum of every action's change, "
            f"which the public data snapshot does not hold.")


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
            f"are GSA's, almost all very small purchase orders (median award "
            f"${num(gsa['median_award_value'], 0)}). In a check of FY2026 Q1, "
            f"the most common products were office supplies, hand tools and measuring tools. Their recorded offers changed "
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


# ---- the long history and the other newer analyses (README findings 4-9, methodology sections 7-17) ---------------------
# Every file read below is OPTIONAL.  While the history is being built a file can be missing, thin, or made from fewer years than
# the others.  A block then says "not yet available" (model-v2 prints nothing) instead of failing, and it always names the
# fiscal years it uses, read from its own file.
HISTORY_FIRST_FY = 2008     # the long history starts in FY2008 (scripts/17_history_download.py): a design choice, not a result
SECTOR = {"civilian except GSA": "civilian agencies except GSA", "civilian_ex_gsa": "civilian agencies except GSA",
          "GSA": "GSA", "gsa": "GSA", "Department of Defense": "Defense", "defense": "Defense",
          "civilian": "civilian agencies", "all": "civilian agencies and Defense together", "all sectors": "all sectors"}
KIND = {"PO": "purchase orders", "DEF": "definitive contracts", "IDV": "contract vehicles"}
FAMILY = {"micro_purchase": "micro-purchase", "simplified_acquisition": "simplified acquisition", "synopsis": "public posting",
          "cost_or_pricing_data": "certified cost or pricing data", "commercial_simplified": "simplified procedures for commercial items",
          "subcontracting_plan": "subcontracting plan", "subcontracting_plan_construction": "subcontracting plan (construction)",
          "justification_level1": "justification approval, level 1", "justification_level2": "justification approval, level 2",
          "justification_level3_civilian": "justification approval, level 3 (civilian)",
          "justification_level3_dod": "justification approval, level 3 (Defense)",
          "8a_competitive_threshold_nonmfg": "8(a) competition limit (not manufacturing)",
          "8a_competitive_threshold_mfg": "8(a) competition limit (manufacturing)", "8a_sole_source_ja": "8(a) sole-source justification"}
METRIC = {"volume": "award counts", "competed": "the share of awards marked competed", "offers_1": "the share with one offer",
          "offers_2": "the share with two offers", "offers_3": "the share with three offers",
          "offers_unusable": "the share with no usable offer count"}
OUTCOME = {"oblig_ratio_w": "dollars obligated, as a share of the initial value", "overrun25": "obligated 125% or more of the initial value",
           "value_jump_w": "largest single rise in potential value, as a share of the initial value",
           "value_jump25": "a single rise in potential value of 25% or more", "value_growth_w": "growth of potential value",
           "value_growth25": "potential value grew 25% or more", "any_scope": "any scope change",
           "n_scope_w": "number of scope changes", "any_termination": "any termination",
           "option_exercised": "an option exercised (awards with options)", "placebo_even_day": "signed on an even day (placebo)",
           "placebo_coin": "coin flip tied to the award number (placebo)", "bal_log_value": "log of the initial value",
           "bal_planned_years": "planned length in years", "bal_has_options": "has options", "bal_month": "month of award"}
OUTCOME_SHORT = {"oblig_ratio_w": "dollars obligated relative to the initial value", "overrun25": "awards obligated at 125% or more",
                 "any_scope": "scope changes", "any_termination": "terminations"}
LEVEL = {"SDVOSB": "service-disabled veteran-owned small business", "WOSB/EDWOSB": "women-owned small business",
         "VOSB": "veteran-owned small business", "HUBZone": "HUBZone small business"}    # set-aside groups of 23_drivers.py
FOLLOW_UP = {"t to t+3": "through the third fiscal year after award", "t to t+1": "through the next fiscal year"}
WHY = {"fiscal years not present yet": "wait for fiscal years that are not in the data yet",
       "too few awards near the line": "have too few awards near the line"}


def fl(x):
    """a float, or None for a blank, 'nan' or missing value"""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) or math.isinf(v) else v


def fy_text(years):
    """[2008, 2009, 2010, 2018] -> 'FY2008–FY2010 and FY2018'"""
    runs = []
    for y in sorted({int(y) for y in years}):
        if runs and y == runs[-1][1] + 1:
            runs[-1][1] = y
        else:
            runs.append([y, y])
    return series([f"FY{a}" if a == b else f"FY{a}–FY{b}" for a, b in runs]) or "no fiscal years"


def series(items):
    items = [i for i in items if i]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def usd(v):
    """$3,500, $150K, $2M, $5.5M"""
    v = float(v)
    if v >= 1e6:
        return f"${v / 1e6:g}M"
    return f"${v / 1e3:g}K" if v >= 1e4 else f"${v:,.0f}"


def rng(lo, hi, d=2):
    """an interval of levels, 1.64–1.94"""
    return f"{num(lo, d)}–{num(hi, d)}"


def srng(lo, hi, d=2):
    """an interval of differences, −0.74 to +0.10"""
    return f"{sgn(lo, d)} to {sgn(hi, d)}"


def ratio_ci(r):
    return f"{num(r['ratio'])} ({rng(r['ci_lo'], r['ci_hi'])})"


def share_ci(r, label=False):
    return f"{pct(r['share'])} ({'95% interval ' if label else ''}{pts(r['ci_lo'])}–{pct(r['ci_hi'])})"


def quarter_text(q):
    return re.sub(r"Q(\d)$", r" Q\1", q)


def includes_zero(e):
    return float(e["lo"]) <= 0 <= float(e["hi"])


def figure(name, alt):
    """a README image line, only when the figure exists"""
    return f"![{alt}](figures/{name})" if os.path.exists(os.path.join(FIGURES, name)) else ""


def cannot(s):
    return f"*What it cannot show:* {s}"


def not_yet(what):
    return f"*Not yet available:* {what} `python run.py history` builds it."


def cap(s):
    return s[:1].upper() + s[1:]


def paras(*parts):
    return "\n\n".join(p for p in parts if p)


# -- single-offer history (19_history_atlas.py)
def history_all():
    """sector -> rows of history_by_fy.csv (all award kinds together), in fiscal-year order"""
    out = {}
    for r in opt_rows("history_by_fy.csv"):
        if r.get("award_kind", "all") == "all" and fl(r.get("share")) is not None and fl(r.get("n_eval")):
            out.setdefault(r["sector"], []).append(r)
    return {s: sorted(v, key=lambda r: int(r["fy"])) for s, v in out.items()}


def history_years():
    return sorted({int(r["fy"]) for v in history_all().values() for r in v})


def r_history():
    h = history_all()
    parts, left = [], []
    for sector, where in (("civilian except GSA", "in civilian agencies except GSA"), ("Department of Defense", "at Defense")):
        rs = h.get(sector, [])
        ok = [r for r in rs if not (r.get("note") or "").strip()]       # a note marks a year that is not comparable
        if len(ok) >= 2:
            a, b = ok[0], ok[-1]
            verb = "fell" if float(b["share"]) < float(a["share"]) else "rose" if float(b["share"]) > float(a["share"]) else "was unchanged"
            parts.append(f"{verb} {where} from {share_ci(a, not parts)} in FY{a['fy']} to {share_ci(b)} in FY{b['fy']}")
            left += [(where, r) for r in rs if (r.get("note") or "").strip() and not int(a["fy"]) < int(r["fy"]) < int(b["fy"])]
    if not parts:
        return not_yet("the single-offer history (`results/history_by_fy.csv`) has not been built, or has fewer than two comparable years.")
    s = "Among competed awards with a usable offer count, the single-offer share " + " and ".join(parts) + "."
    if left:
        s += " Left out of that comparison, as not comparable: " + " ".join(
            f"FY{r['fy']} {where} ({pct(r['share'])} as recorded). {r['note'].strip()}" for where, r in left)
    g = h.get("GSA", [])
    if len(g) >= 2:
        lo, hi = min(g, key=lambda r: float(r["share"])), max(g, key=lambda r: float(r["share"]))
        counts = [int(float(r["n_eval"])) for r in g]
        s += (f" GSA's share ranged from {pct(lo['share'])} (FY{lo['fy']}) to {pct(hi['share'])} (FY{hi['fy']}), on {n(min(counts))} to "
              f"{n(max(counts))} awards a year. Its very small supply orders change in number and in how offers are recorded, so its "
              f"line says little about competition.")
    return paras(s, figure("single_offer_history.png", "Single-offer share by fiscal year, civilian agencies except GSA, GSA and Defense"),
                 cannot("whether competition itself changed or only how offers were recorded (agencies change their coding: see "
                        "*Data quality* below). A Defense year is incomplete until its last quarter is published, 90 days late."))


# -- pile-ups under every simplified-acquisition limit since FY2008 (20_threshold_history.py)
def th_table():
    t = {}
    for r in opt_rows("threshold_history.csv"):
        if fl(r.get("ratio")) is not None and fl(r.get("ci_lo")) is not None and fl(r.get("ci_hi")) is not None:
            t[(r["sector"], int(float(r["line"])), int(r["fy"]))] = r
    return t


def active(r):
    return str(r.get("active", "")).strip().lower() == "true"


def th_changes(t):
    """each time a line became the limit inside the years present: the new and the old line, the last year before the change and
    the first year when the new line was the only limit (2018 and 2019 were transition years with both)"""
    out = []
    for sector in sorted({k[0] for k in t}):
        years = sorted({k[2] for k in t if k[0] == sector})
        lines = sorted({k[1] for k in t if k[0] == sector})
        on = {y: [ln for ln in lines if (sector, ln, y) in t and active(t[(sector, ln, y)])] for y in years}
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
                out.append({"sector": sector, "old": old, "new": new, "before": before, "after": after,
                            "nb": t[keys[0]], "na": t[keys[1]], "ob": t[keys[2]], "oa": t[keys[3]]})
    return out


def th_levels(t):
    """sector -> ratios in years a line was the limit ('on') and years it was not ('off'), over the lines that had both kinds of
    year; 'higher' counts the lines whose mean ratio was higher while they were the limit"""
    by = {}
    for (s, ln, _), r in t.items():
        by.setdefault((s, ln), {True: [], False: []})[active(r)].append(float(r["ratio"]))
    out = {}
    for (s, _), v in sorted(by.items()):
        if v[True] and v[False]:
            o = out.setdefault(s, {"on": [], "off": [], "higher": 0, "pairs": 0})
            o["on"] += v[True]
            o["off"] += v[False]
            o["pairs"] += 1
            o["higher"] += mean(v[True]) > mean(v[False])
    return out


def moved_ok(c):
    return float(c["na"]["ratio"]) > float(c["nb"]["ratio"]) and float(c["oa"]["ratio"]) < float(c["ob"]["ratio"])


def threshold_history_text(t):
    years = sorted({k[2] for k in t})
    out = []
    lv = th_levels(t)
    if lv:
        pairs, higher = sum(o["pairs"] for o in lv.values()), sum(o["higher"] for o in lv.values())
        each = ("for the one line-and-sector pair" if pairs == 1 else f"for all {pairs} line-and-sector pairs") if higher == pairs             else f"for {higher} of the {pairs} line-and-sector pairs"
        avg = series([f"{num(mean(o['on']))} against {num(mean(o['off']))} for {SECTOR.get(s, s)}" for s, o in lv.items()])
        out.append(f"In {fy_text(years)}, the pile-up under a simplified-acquisition limit was larger in the years it was the limit than in "
                   f"the years it was not, {each} that had both kinds of year. Bunching ratio on average: {avg}.")
    ch = th_changes(t)
    if ch:
        ok = [c for c in ch if moved_ok(c)]
        each = ("At the one change" if len(ch) == 1 else f"At each of the {len(ch)} changes") if len(ok) == len(ch)             else f"At {len(ok)} of the {len(ch)} changes"
        big = max(ch, key=lambda c: float(c["na"]["ratio"]) - float(c["nb"]["ratio"]))
        out.append(f"{each} of the limit in these years (civilian and Defense counted separately), the pile-up under the new line grew "
                   f"and the one under the old line shrank, from the last year before the change to the first full year after it. "
                   f"The largest move: {SECTOR.get(big['sector'], big['sector'])} under {money(big['new'])}, from {ratio_ci(big['nb'])} in "
                   f"FY{big['before']} to {ratio_ci(big['na'])} in FY{big['after']} (95% intervals).")
    never = sorted({k[1] for k in t} - {k[1] for k, r in t.items() if active(r)})
    if never:
        one_ = len(never) == 1
        out.append(f"The {series([money(x) for x in never])} limit{'' if one_ else 's'} {'is' if one_ else 'are'} not in force in any of "
                   f"these years, so {'its' if one_ else 'their'} change cannot be tested here yet.")
    return " ".join(out)


def r_threshold_history():
    t = th_table()
    if not t:
        return not_yet("the pile-up history (`results/threshold_history.csv`) has not been built.")
    return paras(threshold_history_text(t) + " Finding 1 tests the move to $350K with the FY2024–FY2026 data.",
                 figure("threshold_history.png", "Bunching ratio under each simplified-acquisition limit by fiscal year"))


# -- the 2018 change, agency by agency (21_threshold_staggered.py)
def stag_table():
    d = {}
    for r in opt_rows("threshold_staggered.csv"):
        if fl(r.get("ratio")) is not None and fl(r.get("ci_lo")) is not None and fl(r.get("ci_hi")) is not None:
            kind = "actual" if r.get("dates") == "actual" else "placebo"
            d.setdefault((r["sector"], kind, int(float(r["line"]))), {})[int(r["event_quarter"])] = r
    return d


def stag_summary(q):
    """mean ratio before (quarters < 0) and from the switch on (>= 0), and the quarter with the largest one-quarter rise"""
    pre = [float(r["ratio"]) for k, r in q.items() if k < 0]
    post = [float(r["ratio"]) for k, r in q.items() if k >= 0]
    jumps = [(float(q[k]["ratio"]) - float(q[k - 1]["ratio"]), k) for k in sorted(q) if k - 1 in q]
    if len(pre) < 2 or len(post) < 2 or not jumps:
        return None
    at = max(jumps)[1]
    return {"pre": mean(pre), "post": mean(post), "npre": len(pre), "npost": len(post), "at": at, "from": q[at - 1], "to": q[at]}


def adoption_civilian():
    """civilian agencies with a documented 2018 adoption date (high or medium confidence), as 21_threshold_staggered.py uses them"""
    return [r for r in opt_rows("threshold_adoption_2018.csv", REFERENCE)
            if r.get("sat_effective_date") and r.get("confidence") in ("high", "medium")
            and r.get("agency") != "Department of Defense" and not r.get("agency", "").startswith("FAR")]


def staggered_years():
    """the fiscal years the staggered test reads (YEARS in 21_threshold_staggered.py) that are present in the history"""
    try:
        m = re.search(r"^YEARS = range\((\d{4}), (\d{4})\)", read_text(os.path.join(SCRIPTS, "21_threshold_staggered.py")), re.M)
    except OSError:
        return []
    have = {k[2] for k in th_table()} or set(history_years())
    return sorted(y for y in have if m and int(m[1]) <= y < int(m[2]))


def staggered_text(d, sector, who=""):
    a = stag_summary(d.get((sector, "actual", 250000), {}))
    if not a:
        return ""
    yrs = staggered_years()
    when = f" ({fy_text(yrs)})" if yrs else ""
    overlap = float(a["to"]["ci_lo"]) <= float(a["from"]["ci_hi"])
    s = (f"Pooling the awards of {SECTOR.get(sector, sector)}{when} by quarter around {who or 'the adoption date'}, the ratio under $250K "
         f"averaged {num(a['pre'])} in the {a['npre']} quarters before the switch and {num(a['post'])} in the {a['npost']} quarters from "
         f"it on. Its largest one-quarter rise came at quarter {sgn(a['at'], 0)} ({num(a['from']['ratio'])} to {num(a['to']['ratio'])}). "
         f"The 95% intervals of those two quarters ({rng(a['from']['ci_lo'], a['from']['ci_hi'])} and {rng(a['to']['ci_lo'], a['to']['ci_hi'])}) "
         + ("overlap, so the timing rests on the whole pattern, not on one quarter." if overlap else "do not overlap."))
    p = stag_summary(d.get((sector, "placebo", 250000), {}))
    if p:
        expect = a["at"] + 4
        last_q = max(d.get((sector, "placebo", 250000), {}))
        s += (f" Placebo: with every date moved one year earlier, the largest rise came at quarter {sgn(p['at'], 0)}"
              + (", four quarters later, as a real effect predicts." if p["at"] == expect else
                 f"; a real effect would put it at quarter {sgn(expect, 0)}, outside the window." if expect > last_q else
                 f", not at quarter {sgn(expect, 0)} where a real effect would put it."))
    return s


def r_threshold_staggered():
    d = stag_table()
    civ = adoption_civilian()
    dates = sorted(r["sat_effective_date"] for r in civ)
    who = f" ({len(civ)} agencies with a documented date, from {long_date(dates[0])} to {long_date(dates[-1])})" if civ else ""
    body = staggered_text(d, "civilian", "each agency's own date")
    if not body:
        return not_yet("the 2018 agency-by-agency test (`results/threshold_staggered.csv`) has not been built.")
    med = sum(r["confidence"] == "medium" for r in civ)
    return paras(f"**The 2018 change, agency by agency.** Civilian agencies raised the limit from $150K to $250K on their own dates{who}. "
                 + body,
                 figure("threshold_staggered.png", "Bunching ratio by quarter around each agency's own adoption date, with the placebo dates"),
                 cannot("why the pile-up follows the limit: budgets written to fit the rules, small-business rules tied to the line and "
                        "contracts split to stay under it all look the same here. "
                        + (f"{med} of the {len(civ)} adoption dates {'is' if med == 1 else 'are'} of medium confidence, and " if civ else "")
                        + "agencies without a documented date are left out."))


# -- the scan of every FAR dollar limit (27_threshold_scan.py)
def fam(f):
    return FAMILY.get(f, f.replace("_", " "))


def scan_changes():
    return [r for r in opt_rows("threshold_scan_changes.csv") if r.get("sector") in ("civilian", "defense") and r.get("verdict")]


def scan_years():
    s = opt_json("threshold_scan_summary.json") or {}
    return s.get("fiscal_years_present") or sorted({int(r["fy"]) for r in opt_rows("threshold_scan.csv") if r.get("fy")})


def grouped(rs):
    """'simplified acquisition, $100K to $150K (civilian, Defense)'"""
    g = {}
    for r in rs:
        g.setdefault(f"{fam(r['family'])} {usd(r['old_line'])} to {usd(r['new_line'])}", []).append(
            "civilian" if r["sector"] == "civilian" else "Defense")
    return [f"{k} ({', '.join(v)})" for k, v in g.items()]


def untestable_counts():
    """why the untestable changes (civilian and Defense) cannot be tested, from the scan's summary"""
    s = opt_json("threshold_scan_summary.json") or {}
    c = {}
    for e in s.get("not_testable") or []:
        sector = e.get("change", "").split(":")[0].split()[-1:]
        if sector in (["civilian"], ["defense"]):
            why = e.get("why", "other reasons")
            why = WHY.get(why, "need years before or after the data's range" if "outside" in why else why)
            c[why] = c.get(why, 0) + 1
    return c


def r_threshold_scan():
    rs = scan_changes()
    if not rs:
        return not_yet("the scan of every FAR dollar limit (`results/threshold_scan_changes.csv`) has not been built.")
    fams = {r["family"] for r in opt_rows("threshold_scan.csv")} or {r["family"] for r in rs}
    tested = [r for r in rs if r["verdict"] != "not testable"]
    moved_ = [r for r in tested if r["verdict"] == "moved"]
    wrong = [r for r in tested if r["verdict"] == "moved the wrong way"]
    rest = len(tested) - len(moved_) - len(wrong)
    s = (f"The scan measures pile-ups under {len(fams)} families of FAR dollar limits, fiscal year by fiscal year, in {fy_text(scan_years())}. "
         f"Of the {len(rs)} changes of a limit (civilian agencies and Defense counted separately), {len(tested)} can be tested with these "
         f"years. In {len(moved_)} of them the pile-up moved with the line (the 95% interval of the difference-in-differences is above "
         f"zero): {'; '.join(grouped(moved_)) or 'none'}.")
    if wrong:
        s += f" In {len(wrong)} it moved the wrong way: {'; '.join(grouped(wrong))}."
    if rest:
        s += " The other tested change shows no clear move." if rest == 1 else f" The other {rest} tested changes show no clear move."
    why = untestable_counts()
    if why:
        s += f" Of the {len(rs) - len(tested)} that cannot be tested, " + series([f"{v} {k}" for k, v in why.items()]) + "."
    return paras(s, figure("threshold_scan.png", "Bunching ratio under each FAR dollar limit by fiscal year"),
                 cannot("why a pile-up forms. Many limits move on the same day, so a test of one change is confounded by the others, "
                        "and round values attract awards everywhere."))


# -- what goes with single offers (23_drivers.py)
def drivers():
    s = opt_json("drivers_summary.json") or {}
    return s, s.get("top_five_factors") or []


def factor_text(t, d=1):
    return (f"{t['factor']}: {LEVEL.get(t['level'], t['level'])} rather than {LEVEL.get(t['versus'], t['versus'])}, {sgn(t['marginal_effect_points'], d)} percentage points "
            f"({srng(t['ci_95_points'][0], t['ci_95_points'][1], d)})")


def best_block():
    b = [r for r in opt_rows("drivers_blocks.csv") if fl(r.get("auc_drop_without")) is not None and fl(r.get("auc_full")) is not None]
    return max(b, key=lambda r: float(r["auc_drop_without"])) if b else None


def r_drivers():
    s, top = drivers()
    if not top:
        return not_yet("the analysis of what goes with single offers (`results/drivers_summary.json`) has not been built.")
    years = s.get("years_in_model") or s.get("years_present") or []
    fit = ((s.get("model") or {}).get("fit") or {}).get("all sectors") or {}
    size = f"{n(fit['n'])} sampled" if fit.get("n") else "a sample of"
    txt = (f"In a model of {size} competed purchase orders and definitive contracts ({fy_text(years)}), holding agency and sector × year "
           f"constant, the clearest associations with a single offer (large and precisely measured) were: "
           + "; ".join(factor_text(t) for t in top[:3]) + ".")
    b = best_block()
    if b:
        txt += (f" On awards held out of the fit ({b['split']}), the model ranks awards with an AUC of {num(b['auc_full'], 3)}. Leaving "
                f"out {b['block']} lowers it the most, by {num(b['auc_drop_without'], 3)} ({num(b['auc_drop_ci_lo'], 3)} to "
                f"{num(b['auc_drop_ci_hi'], 3)}).")
    return paras(txt, figure("drivers.png", "What goes with single offers: marginal effects with 95% intervals"),
                 cannot("causes. The factors travel together and with things the data do not record, and differences in how "
                        "agencies code their data can look like drivers."))


# -- after award (24_outcomes.py)
def outcomes():
    s = opt_json("outcomes_summary.json") or {}
    h = s.get("headline_follow_up")
    fu = (s.get("follow_ups") or {}).get(h) or {}
    return s, h, fu


def outcome_units():
    return {r["outcome"]: r.get("unit", "") for r in opt_rows("outcomes_estimates.csv") if r.get("outcome")}


def diff_text(e, unit="share", d=2):
    """a difference single minus multi, in percentage points for shares and ratios"""
    k = 100 if unit in ("share", "ratio") else 1
    return f"{sgn(float(e['coef']) * k, d)} ({srng(float(e['lo']) * k, float(e['hi']) * k, d)})"


def r_outcomes():
    s, h, fu = outcomes()
    pooled = fu.get("pooled") or {}
    items = [k for k in ("oblig_ratio_w", "overrun25", "any_scope", "any_termination") if k in pooled]
    if not items or not fu.get("cohorts"):
        return not_yet("the after-award comparison (`results/outcomes_summary.json`) has not been built.")
    units = outcome_units()
    floor = ((s.get("design") or {}).get("size_floor"))
    clear = [k for k in items if not includes_zero(pooled[k])]
    if not clear:
        reading = "None of these differences can be told apart from zero."
    else:
        up = [OUTCOME_SHORT[k] for k in clear if float(pooled[k]["coef"]) > 0]
        down = [OUTCOME_SHORT[k] for k in clear if float(pooled[k]["coef"]) < 0]
        biggest = max(abs(float(pooled[k]["coef"])) * 100 for k in clear)
        reading = ("Where the interval excludes zero, single-offer awards had "
                   + series([f"slightly more {series(up)}" if up else "", f"slightly fewer {series(down)}" if down else ""])
                   + f", by at most {num(biggest, 1)} percentage points."
                   + (f" The difference in {series([OUTCOME_SHORT[k] for k in items if k not in clear])} cannot be told apart from zero."
                      if len(clear) < len(items) else ""))
    bal = [OUTCOME.get(k, k) for k, e in (fu.get("balance") or {}).items() if "lo" in e and not includes_zero(e)]
    if bal:
        reading += (f" Single-offer awards also differ slightly on things fixed at award ({series(bal)}), so the cells do not make the "
                    f"two groups fully alike.")
    pl = fu.get("placebo") or {}
    placebo = ""
    if pl:
        placebo = (" The two placebo checks, which competition cannot affect (an even signing day and a coin flip), show no difference, as "
                   "they should." if all(includes_zero(e) for e in pl.values()) else
                   " At least one placebo check shows a difference, so the two groups also differ in ways the cells do not capture.")
    base = (f" ({pct(fu['share_of_awards_carrying_the_estimate'], 0)} of the {n(fu['awards_at_or_above_floor'])} awards"
            + (f" of {usd(floor)} or more" if floor else "") + ")") \
        if fu.get("awards_at_or_above_floor") and fu.get("share_of_awards_carrying_the_estimate") is not None else ""
    txt = (f"Single-offer and multi-offer awards were compared inside the same buying office, quarter, award type, product and size band: "
           f"{n(fu['awards_in_cells_with_both_kinds'])} competed purchase orders and definitive contracts{base} made in "
           f"{fy_text(fu['cohorts'])}, followed {FOLLOW_UP.get(h, 'over ' + str(h))}. Differences, single-offer minus multi-offer, in "
           f"percentage points (95% interval): "
           + "; ".join(f"{OUTCOME_SHORT[k]}: {diff_text(pooled[k], units.get(k, 'share'))}" for k in items) + f". {reading}{placebo}")
    growth = "" if "value_growth_w" in pooled else (" Total growth of an award's potential value is not in the yearly tables used here, "
                                                     "so it is not measured.")
    return paras(txt, figure("outcomes.png", "After award: single-offer minus multi-offer awards, with 95% intervals"),
                 cannot("prices, which the data do not hold, or whether extra obligations, scope changes or terminations were "
                        "justified. Only awards in cells with both kinds of award carry the estimate." + growth))


# -- split-like purchase clusters (26_split_screen.py)
def split():
    s = opt_json("split_screen_summary.json") or {}
    ex = s.get("excess_ratio_pooled") or {}

    def er(sector, typ, var):
        e = ex.get(f"{sector}|{typ}|{var}")
        return e if e and None not in (fl(e.get("ratio")), fl(e.get("ci_lo")), fl(e.get("ci_hi"))) else None
    return s, er


def split_phrase(er, typ, var):
    return series([f"{num(e['ratio'])} for {SECTOR[sec]} ({rng(e['ci_lo'], e['ci_hi'])})"
                   for sec in ("civilian_ex_gsa", "gsa", "defense") if (e := er(sec, typ, var))]) or "not available"


def natural(s):
    ne = [e for e in s.get("natural_experiment") or []
          if e.get("variant") == "all" and fl((e.get("after_minus_before") or {}).get("change")) is not None]
    fell = sum(float(e["after_minus_before"]["ci_hi"]) < 0 for e in ne)
    rose = sum(float(e["after_minus_before"]["ci_lo"]) > 0 for e in ne)
    last = max(s.get("years_used") or [0])
    partial = any(max(e.get("after_years") or [0]) > last for e in ne)
    return ne, fell, rose, partial


def r_split():
    s, er = split()
    if not any(er(sec, t, "all") for sec in ("civilian_ex_gsa", "gsa", "defense") for t in ("SAT", "MPT")):
        return not_yet("the split screen (`results/split_screen_summary.json`) has not been built.")
    d = s.get("design") or {}
    gap, donut = d.get("gap_days"), d.get("donut_cap")
    all_e = [e for sec in ("civilian_ex_gsa", "gsa", "defense") for t in ("SAT", "MPT") for v in ("all", "donut") if (e := er(sec, t, v))]
    above = sum(float(e["ci_lo"]) > 1 for e in all_e)
    near = f"within {num((1 - float(donut)) * 100, 0)}% of the limit" if donut else "just under the limit"
    txt = (f"A split-like cluster is two or more purchases from the same company, office and product"
           + (f" within {gap} days" if gap else "") + ", each at or under a limit but together above it. "
           f"In {fy_text(s.get('years_used') or [])}, the share of purchases at or under the limit that sit in such clusters, divided by "
           f"what placebo lines predict (1.00 = no excess, 95% interval): at the simplified-acquisition limit, {split_phrase(er, 'SAT', 'all')}; "
           f"leaving out the awards {near}, {split_phrase(er, 'SAT', 'donut')}. At the micro-purchase limit, {split_phrase(er, 'MPT', 'all')}; "
           f"without the awards {near}, {split_phrase(er, 'MPT', 'donut')}. {above} of these {len(all_e)} intervals lie above 1.00.")
    ne, fell, rose, partial = natural(s)
    if ne:
        txt += (f" When a limit rose, the excess in the band between the old and the new limit should fall. Of {len(ne)} such "
                f"before-and-after tests, it fell clearly in {fell}, rose clearly in {rose} and was unclear in {len(ne) - fell - rose}"
                + ("; some of them use only part of their years after the change so far." if partial else "."))
    return paras(txt, figure("split_screen.png", "Split-like clusters: excess at the real limits over placebo lines"),
                 cannot("a prohibited split from legitimate repeat buying, such as monthly supply orders. It is a screening pattern "
                        "at sector level, not evidence that any purchase was split."))


# -- data quality (16_quality_monitor.py)
def quality():
    q = opt_json("quality_summary.json")
    return q, opt_rows("quality_flags.csv")


def flag_change(r):
    if r["metric"] == "volume":
        return f"{n(r['before'])} → {n(r['after'])} awards"
    return f"{num(r['before'], 1)}% → {num(r['after'], 1)}%"


def r_quality():
    q, flags = quality()
    if q is None:
        return "**Data quality.** *Not yet available:* the data-quality monitor has not run. `python run.py quality` runs it."
    bm = sorted((q.get("by_metric") or {}).items(), key=lambda kv: -kv[1])
    txt = (f"**Data quality.** `16_quality_monitor.py` looks for quarters in which an agency changed how it records its data. It compares "
           f"each civilian agency, award type and quarter of the {fy_span()} table with the quarters before it and flags only large, clear "
           f"breaks. It flagged {q.get('n_flags', 0)}" + (": " + series([f"{v} on {METRIC.get(k, k)}" for k, v in bm]) if bm else "") + ".")
    o1 = [r for r in flags if r.get("metric") == "offers_1" and fl(r.get("change_points")) is not None]
    if o1:
        b = max(o1, key=lambda r: abs(float(r["change_points"])))
        txt += (f" The largest change in offer coding: {b['agency']} {KIND.get(b['award_kind'], b['award_kind'])} in "
                f"{quarter_text(b['quarter'])}, where the one-offer share went from {num(b['before'], 1)}% to {num(b['after'], 1)}%.")
    txt += (" A flag means \"check before you trust numbers from this agency and quarter\". It cannot say whether the change is in coding, "
            "in late reporting or in competition itself. Full list: [`results/quality_flags.csv`](results/quality_flags.csv).")
    return txt


# -- reference files
def reference_text(prefix):
    th = opt_rows("thresholds.csv", REFERENCE)
    ad = opt_rows("threshold_adoption_2018.csv", REFERENCE)
    md = opt_rows("market_depth_naics.csv", REFERENCE)
    out = []
    if th:
        out.append(f"[`reference/thresholds.csv`]({prefix}reference/thresholds.csv) lists {len(th)} value periods of "
                   f"{len({r['threshold'] for r in th})} FAR dollar thresholds, each with its source and the words that show the value "
                   f"([notes]({prefix}reference/thresholds.md)).")
    if ad:
        dated = sorted(r["sat_effective_date"] for r in ad if r.get("sat_effective_date"))
        med = sum(r.get("confidence") == "medium" and bool(r.get("sat_effective_date")) for r in ad)
        if dated:
            out.append(f"[`reference/threshold_adoption_2018.csv`]({prefix}reference/threshold_adoption_2018.csv) gives the date each "
                       f"agency adopted the 2018 limits: {len(dated)} of its {len(ad)} rows (agencies and the FAR itself) have a date, "
                       f"from {long_date(dated[0])} to {long_date(dated[-1])}, {med} of them of medium confidence "
                       f"([notes]({prefix}reference/README.md)).")
    vals = sorted(int(float(r["n_any"])) for r in md if fl(r.get("n_any")) is not None)
    if vals:
        out.append(f"[`reference/market_depth_naics.csv`]({prefix}reference/market_depth_naics.csv) counts the companies registered in "
                   f"SAM.gov to compete for federal contracts in each industry code: {n(len(vals))} codes, a median of "
                   f"{n(median(vals))} companies per code, on {long_date(md[0]['extract_date'])}. A registration is not a capable "
                   f"bidder ([notes]({prefix}reference/market_depth.md)).")
    return "**Reference data.** " + (" ".join(out) if out else "*Not yet available.*")


def r_reference():
    return reference_text("")


def m_reference():
    return reference_text("../")


# -- the forecast model, version 2 (30_model_v2.py).  Before its card exists, a line saying it is being built.
BASELINE_NAME = {"sector rate (baseline)": "the sector-wide rate", "agency rate (baseline)": "the agency's past rate",
                 "office history only (baseline)": "the buying office's past rate"}
TODAY = None            # the build date (date.today()); the tests set it


def today():
    return TODAY or date.today()


@cache
def stale_months():
    """STALE_MONTHS of 31_daily_forecast.py (the daily forecast refuses a model whose award history is older), read from the script
    so that the two never disagree; 9 if the script is not there."""
    try:
        m = re.search(r"^STALE_MONTHS\s*=\s*(\d+)", read_text(os.path.join(SCRIPTS, "31_daily_forecast.py")), re.M)
    except OSError:
        m = None
    return int(m[1]) if m else 9


def months_before(d, k):
    y, m = divmod(d.year * 12 + d.month - 1 - k, 12)
    return date(y, m + 1, min(d.day, 28))


def model_v2_history_end(card):
    """the last day of the newest fiscal year in the card's award history (fiscal years end on September 30), or None"""
    years = [int(float(y)) for y in ((card or {}).get("data") or {}).get("award_years_present") or [] if fl(y) is not None]
    return date(max(years), 9, 30) if years else None


def model_v2_trained(card):
    """the day the model was trained (the card's built_utc), or the build date when the card does not say"""
    try:
        return date.fromisoformat(str((card or {}).get("built_utc", ""))[:10])
    except ValueError:
        return today()


def model_v2_stale(card):
    """True when the model's award history ends more than STALE_MONTHS months before the day it was trained.  Both dates come
    from the card, so the README does not change with the calendar.  (31_daily_forecast.py applies the same limit against
    the day of each forecast and refuses a model that has become too old.)"""
    end = model_v2_history_end(card)
    return end is None or end < months_before(model_v2_trained(card), stale_months())


def best_baseline(by):
    """(name, row) of the simple baseline with the highest out-of-time AUC, or (None, None)"""
    rs = [(k, r) for k, r in by.items() if k in BASELINE_NAME and fl(r.get("auc")) is not None]
    return max(rs, key=lambda kr: float(kr[1]["auc"])) if rs else (None, None)


V2_SHIPPED_FALLBACK = "logistic v2"       # used when the card names no shipped model (or one with no pooled row)


def model_v2_text(prefix, v1_where):
    card = opt_json("model_v2_card.json")
    if not card:
        return (f"- **Forecast model version 2 is being built** on SAM.gov's archive files (`22_sam_archive.py`, `30_model_v2.py`). "
                f"Until it is finished, the frozen version 1 ({v1_where}) is the published model.")
    try:
        by = {r["model"]: r for r in opt_rows("model_v2_evaluation.csv") if r.get("test_fy") == "pooled" and r.get("sector") == "all"}
        m = by.get((card.get("shipped") or {}).get("name_in_evaluation")) or by[V2_SHIPPED_FALLBACK]
        bname, base = best_baseline(by)
        refit = by.get("version 1 feature set (refit)")
        cmp_ = series([f"{num(base['auc'], 3)} for the best simple baseline, {BASELINE_NAME[bname]}," if base else "",
                       f"{num(refit['auc'], 3)} for version 1's features refitted" if refit else ""]).rstrip(",")
        data = card.get("data") or {}
        built = card.get("built_utc", "")[:10]
        end = model_v2_history_end(card)
        txt = (f"- **Forecast model, version 2" + (f" (built {long_date(built)})" if built else "") + ".** "
               f"It is the model of the daily forecasts (see *Pre-registered forecasts*). "
               f"Trained on {n(data['resolved_solicitations'])} solicitations from SAM.gov's archive that were matched to a competed award "
               f"(award years {fy_text(data['award_years_present'])}" + (f"; its award history ends {long_date(end.isoformat())}" if end else "")
               + f"). Tested out of time on {fy_text(card['evaluation']['test_years'])}, "
               f"the shipped {card['shipped']['kind']} ranks solicitations with an AUC of {num(m['auc'], 3)}"
               + (f" ({cmp_})" if cmp_ else "") + f", with a Brier score of {num(m['brier'], 3)}"
               + (f" against {num(base['brier'], 3)} for that baseline" if base and fl(base.get("brier")) is not None else "")
               + f" and a calibration slope of {num(m['cal_slope'])} (1.00 is ideal).")
        if model_v2_stale(card):
            txt += (f" Its award history ends more than {stale_months()} months before it was trained, so it must not score live "
                    "solicitations until it is retrained on newer award years; the daily forecast script refuses a model this old.")
        sel = card.get("selection") if isinstance(card.get("selection"), dict) else {}
        if sel.get("rule_pick") and sel.get("shipped"):
            txt += (" How it was chosen: by a rule written down before fitting (lowest out-of-time log loss over the last five "
                    "test years; among candidates within 0.5% of the best, the one closest to the earlier model).")
            if sel.get("amendment") and sel["rule_pick"] != sel["shipped"]:
                txt += (f" The rule picked \"{sel['rule_pick']}\"; \"{sel['shipped']}\" is shipped instead, because the simpler "
                        "pick left forecasts miscalibrated by sector. That choice was made after the results were known; the card "
                        "records it with the numbers (`selection.amendment`).")
        return (txt + f" Version 1 ({v1_where}) is the earlier model, frozen since the {long_date(FIRST_RELEASE['date'])} build. "
                f"Both are risk indicators about markets, not statements about anyone's conduct. "
                f"Details: [`results/model_v2_card.json`]({prefix}results/model_v2_card.json).")
    except (KeyError, TypeError, ValueError, IndexError):
        return ""


def r_model_v2():
    return model_v2_text("", "finding 3")


def m_model_v2():
    return model_v2_text("../", "section 5")


# -- the daily sealed forecasts (31_daily_forecast.py, 32_score_daily.py).  True before the first batch and after it: the series is
# described as running only when forecasts/daily/ledger.csv lists a batch.
DAILY = os.path.join(FORECASTS, "daily")


def daily_ledger():
    """[(forecast date, forecasts, fresh forecasts or None)] from forecasts/daily/ledger.csv, oldest first; [] before the first batch"""
    out = []
    for r in opt_rows("ledger.csv", DAILY):
        try:
            d = date.fromisoformat(str(r.get("forecast_date") or "")[:10]).isoformat()
            k = int(float(r["n_forecasts"]))
        except (KeyError, TypeError, ValueError):
            continue
        f = fl(r.get("n_fresh"))
        out.append((d, k, None if f is None else int(f)))
    return sorted(out)


def iso_day(s):
    s = str(s or "")[:10]
    return s if re.fullmatch(r"\d{4}-\d\d-\d\d", s) else ""


def daily_text(guide, folder, method=True):
    """method=False: the status sentences only (docs/methodology.md describes the method next to the block)"""
    led = daily_ledger()
    what = ("every new open solicitation on SAM.gov: the chance that its competed award gets only one offer, with forecast model "
            "version 2.")
    how = (f"A day's forecasts form one batch. The batch "
           f"stays private, because it names solicitations and buying offices. Its SHA-256 fingerprint, a row of counts and short hashes "
           f"of the solicitation numbers are made public in [`forecasts/daily/`]({folder}) the same morning, before any outcome exists. "
           f"An award counts only if it is dated on or after the day offers were due. Scores are published as totals only. "
           f"How it works: [`docs/daily-forecasts.md`]({guide}).")
    if not led:
        return ("**Daily forecasts.** The daily series is built and tested; the first batch has not been sealed yet."
                + (f" When it starts, a robot will forecast each morning {what} {how}" if method else ""))
    k = sum(x[1] for x in led)
    fresh = [x[2] for x in led if x[2] is not None]
    txt = ("**Daily forecasts.** " + (f"Each morning a robot forecasts {what} {how} " if method else "")
           + f"So far: {n(len(led))} {'batch' if len(led) == 1 else 'batches'} holding {n(k)} "
           f"{'forecast' if k == 1 else 'forecasts'}, the first on {long_date(led[0][0])}"
           + (f"; {n(sum(fresh))} of them fresh (first posted at most 3 days before the forecast)" if len(fresh) == len(led) else "") + ".")
    s = opt_json("daily_forecast_scores.json")
    if not isinstance(s, dict) or fl(s.get("n_resolved")) is None or fl(s.get("n_forecasts")) is None:
        return txt + " No forecast has been scored yet."
    as_of = iso_day(s.get("as_of"))
    txt += (f" {n(s['n_resolved'])} of the {n(s['n_forecasts'])} forecasts checked so far have resolved"
            + (f" (as of {long_date(as_of)})" if as_of else "") + ".")
    sc = s.get("scores") if s.get("scores_published") is True and isinstance(s.get("scores"), dict) else {}
    o = sc.get("overall") or {}
    mod, ag = o.get("model") or {}, o.get("agency_rate") or {}
    if fl(o.get("n_resolved")) is not None and fl(mod.get("brier")) is not None and fl(mod.get("auc")) is not None:
        sa = iso_day(sc.get("as_of"))
        txt += (" Scores" + (f" as of {long_date(sa)}" if sa else "")
                + f", on {n(o['n_resolved'])} resolved forecasts: Brier score {num(mod['brier'], 3)}"
                + (f" against {num(ag['brier'], 3)} for the agency's past rate" if fl(ag.get("brier")) is not None else "")
                + f" (lower is better), AUC {num(mod['auc'], 3)}"
                + (f" against {num(ag['auc'], 3)}" if fl(ag.get("auc")) is not None else "") + " (0.5 is a coin flip).")
    else:
        txt += " Scores are published once enough forecasts have resolved."
    return txt


def r_daily():
    return daily_text("docs/daily-forecasts.md", "forecasts/daily/")


def m_daily():
    return daily_text("daily-forecasts.md", "../forecasts/daily/", method=False)


# -- the completeness check against USAspending's own totals (28_reconcile.py)
def reconcile_text():
    s = opt_json("reconciliation_summary.json")
    years = s.get("years_checked") if isinstance(s, dict) else None
    if not years:
        return None
    diffs = [abs(v) for d in s.get("largest_differences") or [] for v in (fl(d.get("diff_transactions_pct")), fl(d.get("diff_obligations_pct")))
             if v is not None]
    gap = fl(s.get("all_agencies_largest_transaction_gap_pct"))
    txt = (f"`28_reconcile.py` compares our yearly transaction counts and dollars with USAspending's own totals. With the same filters as "
           f"our downloads, {fy_text(years)} ({len(years)} {'year' if len(years) == 1 else 'years'} checked)"
           + ((" match USAspending's totals exactly." if max(diffs) == 0 else f" differ by at most {num(max(diffs), 2)} percent.")
              if diffs else " were compared.")
           + (f" Awarding agencies missing from USAspending's top-tier agency list are not downloaded; they hold at most "
              f"{num(abs(gap), 2)} percent of a year's transactions." if gap is not None else ""))
    if s.get("years_not_answered"):
        txt += f" USAspending did not answer for {fy_text(s['years_not_answered'])}."
    return txt


def r_reconcile():
    t = reconcile_text()
    if t is None:
        return "**Completeness check.** *Not yet available:* `python run.py reconcile` runs it."
    return f"**Completeness check.** {t} Full table: [`results/reconciliation.csv`](results/reconciliation.csv)."


def m_reconcile():
    t = reconcile_text()
    return t if t is not None else "*Not yet available:* `python run.py reconcile` runs it."


def r_limit_history():
    years = history_years()
    if not years:
        return "The long-history tables have not been built yet."
    missing = [y for y in range(HISTORY_FIRST_FY, CONFIG["analysis"]["fiscal_years"][-1] + 1) if y not in years]
    return (f"The single-offer history uses {fy_text(years)} so far"
            + (f"; {fy_text(missing)} {'is' if len(missing) == 1 else 'are'} still to come." if missing else "."))


# ---- methodology blocks for the newer analyses ----------------------------------------------------------------------------
def m_quality():
    q, flags = quality()
    if q is None:
        return "*Not yet available:* the monitor has not run (`python run.py quality`)."
    th = q.get("thresholds") or {}
    rules = ""
    if th:
        rules = (f"**Rules** (from `results/quality_summary.json`): both sides need at least {n(th['min_n'])} awards; a share is flagged "
                 f"only if |z| ≥ {num(th['z_bar'], 0)} and it moved by at least {num(th['min_points'], 0)} percentage points; award counts "
                 f"are flagged when they move by {num(th['volume_pct'], 0)}% or more against the {th.get('volume_baseline', 'same quarter a year earlier')}. "
                 f"Share baseline: {th.get('baseline', 'the previous four quarters')}.")
    bm = sorted((q.get("by_metric") or {}).items(), key=lambda kv: -kv[1])
    qs = sorted({r["quarter"] for r in flags if r.get("quarter")})
    head = (f"**Results** ({fy_span()} civilian table): {q.get('n_flags', 0)} flags"
            + (f", in quarters from {quarter_text(qs[0])} to {quarter_text(qs[-1])}" if qs else "") + ". The largest of each kind:")
    out = [rules, "", head, "", "| What changed | Flags | Largest flag |", "|---|---|---|"]
    for k, v in bm:
        fs = [r for r in flags if r.get("metric") == k and fl(r.get("change_points")) is not None]
        big = max(fs, key=lambda r: abs(float(r["change_points"]))) if fs else None
        cell = (f"{big['agency']}, {KIND.get(big['award_kind'], big['award_kind'])}, {quarter_text(big['quarter'])}: {flag_change(big)}"
                if big else "")
        out.append(f"| {METRIC.get(k, k)} | {v} | {cell} |")
    return "\n".join(x for x in out if x is not None).strip()


def m_history():
    h = history_all()
    years = history_years()
    if not years:
        return not_yet("The yearly tables have not been built.")
    order = [s for s in ("civilian except GSA", "GSA", "Department of Defense") if s in h]
    cell = {(s, int(r["fy"])): r for s in order for r in h[s]}
    tot = {s: sum(int(float(r["n_eval"])) for r in h[s]) for s in order}
    out = [f"**Results** ({fy_text(years)}). Competed awards with a usable offer count: "
           + series([f"{n(tot[s])} ({SECTOR[s]})" for s in order]) + ". Single-offer share (95% interval):", "",
           "| Fiscal year | " + " | ".join(cap(SECTOR[s]) for s in order) + " |", "|---" * (len(order) + 1) + "|"]
    for y in years:
        out.append(f"| FY{y} | " + " | ".join(f"{share_ci(cell[(s, y)])}, n = {n(cell[(s, y)]['n_eval'])}" if (s, y) in cell else ""
                                             for s in order) + " |")
    return "\n".join(out)


def m_threshold_history():
    t = th_table()
    if not t:
        return not_yet("`results/threshold_history.csv` has not been built.")
    years = sorted({k[2] for k in t})
    sectors = sorted({k[0] for k in t})
    lines = sorted({k[1] for k in t})
    cols = [(s, ln) for s in sectors for ln in lines]
    out = [f"**Results** ({fy_text(years)}; bold = the line was the limit that year):", "",
           "| Fiscal year | " + " | ".join(f"{cap(SECTOR.get(s, s))}, {money(ln)}" for s, ln in cols) + " |",
           "|---" * (len(cols) + 1) + "|"]
    for y in years:
        cells = []
        for s, ln in cols:
            r = t.get((s, ln, y))
            cells.append("" if r is None else f"**{num(r['ratio'])}**" if active(r) else num(r["ratio"]))
        out.append(f"| FY{y} | " + " | ".join(cells) + " |")
    out += ["", threshold_history_text(t)]
    ch = th_changes(t)
    if ch:
        out += ["", "Each change, from the last year before it to the first full year after it (95% intervals):", ""]
        for c in ch:
            out.append(f"- {cap(SECTOR.get(c['sector'], c['sector']))}, {money(c['old'])} → {money(c['new'])}: under {money(c['new'])}, "
                       f"{ratio_ci(c['nb'])} in FY{c['before']} → {ratio_ci(c['na'])} in FY{c['after']}; under {money(c['old'])}, "
                       f"{ratio_ci(c['ob'])} → {ratio_ci(c['oa'])}.")
    return "\n".join(out)


def m_staggered():
    d = stag_table()
    if not d:
        return not_yet("`results/threshold_staggered.csv` has not been built.")
    civ = adoption_civilian()
    dod = [r for r in opt_rows("threshold_adoption_2018.csv", REFERENCE) if r.get("agency") == "Department of Defense"
           and r.get("sat_effective_date")]
    out = []
    if civ:
        out.append(f"**Agencies:** {len(civ)} civilian agencies with a documented date of high or medium confidence "
                   f"({sum(r['confidence'] == 'medium' for r in civ)} medium).")
    for sector, who in (("civilian", "each agency's own date"), ("defense", "its adoption date")):
        txt = staggered_text(d, sector, who)
        if txt:
            out.append(f"**{cap(SECTOR[sector])}.** {txt}")
    if dod:
        out.append(f"Defense has one adoption date ({long_date(dod[0]['sat_effective_date'])}), so its test is a before-and-after "
                   f"comparison around that date, not a staggered one.")
    cols = [(s, k, ln) for s in ("civilian", "defense") for (k, ln) in (("actual", 150000), ("actual", 250000), ("placebo", 250000))
            if d.get((s, k, ln))]
    qs = sorted({q for c in cols for q in d[c]})
    table = ["Bunching ratio (95% interval) by quarter around the adoption date (quarter 0 starts on adoption day):", "",
             "| Quarter | " + " | ".join(f"{cap(SECTOR[s])}, {money(ln)}{', placebo dates' if k == 'placebo' else ''}"
                                     for s, k, ln in cols) + " |", "|---" * (len(cols) + 1) + "|"]
    table += [f"| {sgn(q, 0)} | " + " | ".join(ratio_ci(d[c][q]) if q in d[c] else "" for c in cols) + " |" for q in qs]
    return "\n\n".join(out + ["\n".join(table)])


def m_scan():
    rs = scan_changes()
    if not rs:
        return not_yet("`results/threshold_scan_changes.csv` has not been built.")
    tested = [r for r in rs if r["verdict"] != "not testable"]
    partial = [r for r in rs if r["verdict"] == "not testable" and fl(r.get("did")) is not None]
    out = [f"**Results** ({fy_text(scan_years())}; civilian agencies and Defense; the combined rows are in the file). "
           f"{len(tested)} of {len(rs)} changes can be tested:", "",
           "| Limit | Change | Date | Sector | Difference-in-differences (95% interval) | Reading |", "|---|---|---|---|---|---|"]
    for r in tested:
        out.append(f"| {fam(r['family'])} | {usd(r['old_line'])} → {usd(r['new_line'])} | {r['change_date']} | "
                   f"{'civilian' if r['sector'] == 'civilian' else 'Defense'} | {sgn(r['did'])} ({srng(r['did_lo'], r['did_hi'])}) | "
                   f"{r['verdict']}; {r.get('reason', '')} |")
    if partial:
        out += ["", "Waiting for later years (the numbers so far use part of the windows and are not a test): "
                + "; ".join(f"{fam(r['family'])} {usd(r['old_line'])} → {usd(r['new_line'])}, "
                            f"{'civilian' if r['sector'] == 'civilian' else 'Defense'}, {sgn(r['did'])} ({srng(r['did_lo'], r['did_hi'])})"
                            for r in partial) + "."]
    why = untestable_counts()
    if why:
        out += ["", f"Not testable ({len(rs) - len(tested)}): " + series([f"{v} {k}" for k, v in why.items()]) + "."]
    lv = [x for x in (opt_json("threshold_scan_summary.json") or {}).get("levels") or []
          if x.get("sector") in ("civilian", "defense") and x.get("active_minus_inactive_ci")]
    if lv:
        up = sum(float(x["active_minus_inactive_ci"][0]) > 0 for x in lv)
        out += ["", f"**Level check.** In {up} of {len(lv)} limit-and-sector pairs with both kinds of year, the pile-up was larger in the "
                    f"years the value was the limit than in years it was not (95% interval of the difference above zero)."]
    return "\n".join(out)


def m_drivers():
    s, top = drivers()
    if not top:
        return not_yet("`results/drivers_summary.json` has not been built.")
    sample = s.get("sample") or {}
    fit = (s.get("model") or {}).get("fit") or {}
    out = [" ".join(x for x in (
               f"**Results** (model years {fy_text(s.get('years_in_model') or [])}; shares use {fy_text(s.get('years_in_shares') or [])}).",
               f"Quota per fiscal year × sector: {n(sample['quota_per_stratum'])} awards (seed {sample.get('seed')})." if sample.get("quota_per_stratum") else "",
               "Incumbency could not be measured yet: it needs the three previous fiscal years." if not s.get("incumbency_years") else
               f"Incumbency is measured for {fy_text(s['incumbency_years'])}.",
               f"Defense years left out as incomplete: {fy_text(s['defense_years_left_out_incomplete'])}." if s.get("defense_years_left_out_incomplete") else "")
               if x),
           "", "| Scope | Sampled awards | Single-offer share | Buying offices (clusters) | McFadden R² | AUC (in sample) |",
           "|---|---|---|---|---|---|"]
    for k, f in fit.items():
        out.append(f"| {SECTOR.get(k, k)} | {n(f['n'])} | {pct(f['single_share'])} | {n(f['n_clusters_offices'])} | "
                   f"{num(f['mcfadden_r2'], 3)} | {num(f['auc_in_sample'], 3)} |")
    out += ["", "**Strongest associations** (one level per factor, ranked by the end of the 95% interval nearest zero; average marginal "
                "effects in percentage points against the most common level):", ""]
    out += [f"- {factor_text(t)}" for t in top]
    blocks = [r for r in opt_rows("drivers_blocks.csv") if fl(r.get("auc_drop_without")) is not None]
    if blocks:
        out += ["", f"**Blocks** ({blocks[0]['split']}; {n(blocks[0]['n_train'])} training and {n(blocks[0]['n_test'])} test awards; "
                    f"full model AUC {num(blocks[0]['auc_full'], 3)}):", "",
                "| Block | AUC drop without it (95% interval) | Log-loss increase without it (95% interval) | AUC with this block alone |",
                "|---|---|---|---|"]
        for r in sorted(blocks, key=lambda r: -float(r["auc_drop_without"])):
            out.append(f"| {r['block']} | {num(r['auc_drop_without'], 4)} ({num(r['auc_drop_ci_lo'], 4)} to {num(r['auc_drop_ci_hi'], 4)}) | "
                       f"{num(r['log_loss_increase_without'], 4)} ({num(r['log_loss_increase_ci_lo'], 4)} to "
                       f"{num(r['log_loss_increase_ci_hi'], 4)}) | {num(r['auc_alone'], 3) if fl(r.get('auc_alone')) is not None else ''} |")
    return "\n".join(out)


def m_outcomes():
    s, h, fu = outcomes()
    if not fu.get("pooled"):
        return not_yet("`results/outcomes_summary.json` has not been built.")
    units = outcome_units()
    fus = s.get("follow_ups") or {}
    yp = (s.get("years_present") or {}).get("award_year") or []
    out = [f"**Results** (award-year tables present: {fy_text(yp)}). "
           + " ".join(f"Follow-up {k} ({FOLLOW_UP.get(k, k)}): cohorts {fy_text(v.get('cohorts') or [])}, "
                      f"{n(v.get('awards_at_or_above_floor', 0))} awards at or above the size floor ({pct(v.get('single_offer_share', 0))} "
                      f"single-offer), of which {n(v.get('awards_in_cells_with_both_kinds', 0))} in {n(v.get('cells', 0))} cells with both "
                      f"kinds of award, from {n(v.get('buying_offices', 0))} buying offices."
                      for k, v in fus.items() if v.get("pooled")), ""]
    keys = list(fus)
    out += ["| Outcome | " + " | ".join(f"Multi-offer mean, {k} | Single minus multi, {k} (95% interval)" for k in keys) + " |",
            "|---" * (1 + 2 * len(keys)) + "|"]
    for o in [k for k in OUTCOME if any(k in (fus[f].get("pooled") or {}) for f in keys)]:
        cells = []
        for f in keys:
            e = (fus[f].get("pooled") or {}).get(o)
            u = units.get(o, "share")
            if e:
                m_ = pct(e["mean_multi_offer"]) if u in ("share", "ratio") else num(e["mean_multi_offer"], 3)
                cells += [m_, diff_text(e, u, 2 if u in ("share", "ratio") else 3)]
            else:
                cells += ["", ""]
        out.append(f"| {OUTCOME[o]} | " + " | ".join(cells) + " |")
    out += ["", "Shares and ratios are in percent and percentage points; the number of scope changes is a count."]
    for title, part in (("Placebo checks", "placebo"), ("Balance on things fixed at award", "balance")):
        es = fu.get(part) or {}
        if es:
            out += ["", f"**{title}** ({h}): " + "; ".join(
                f"{OUTCOME.get(k, k)} {diff_text(e, units.get(k, 'share'), 3)}" + ("" if includes_zero(e) else " (interval excludes zero)")
                for k, e in es.items()) + "."]
    ck = fu.get("checks") or {}
    tested = [(o, name, e) for o, d in ck.items() if isinstance(d, dict) for name, e in d.items() if isinstance(e, dict) and "lo" in e]
    if tested:
        flips = [(o, name) for o, name, e in tested if o in fu["pooled"] and includes_zero(e) != includes_zero(fu["pooled"][o])]
        out += ["", f"**Checks** ({h}; other size floors and other cells): {len(tested)} alternative estimates. In {len(flips)} of them the "
                    f"interval {'' if flips else 'never '}changes side of zero compared with the main design"
                    + (": " + "; ".join(f"{OUTCOME.get(o, o)}, {name}" for o, name in flips) if flips else "") + "."]
    return "\n".join(out)


def m_split():
    s, er = split()
    rows_ = [(sec, [er(sec, t, v) for t in ("SAT", "MPT") for v in ("all", "donut")]) for sec in ("civilian_ex_gsa", "gsa", "defense")]
    if not any(e for _, es in rows_ for e in es):
        return not_yet("`results/split_screen_summary.json` has not been built.")
    d = s.get("design") or {}
    out = [f"**Results** ({fy_text(s.get('years_used') or [])}"
           + (f"; links at most {d['gap_days']} days apart; placebo multiples {', '.join(num(m, 2) for m in d['multiples'] if float(m) != 1)}; "
              f"{d.get('bootstrap')} bootstrap resamples of {d.get('bootstrap_unit')}" if d.get("multiples") else "") + "). "
           "Excess ratio, pooled over the years (1.00 = no excess; 95% interval):", "",
           "| Sector | Simplified acquisition | Same, donut | Micro-purchase | Same, donut |", "|---|---|---|---|---|"]
    for sec, es in rows_:
        out.append(f"| {SECTOR[sec]} | " + " | ".join(ratio_ci(e) if e else "" for e in es) + " |")
    ne, fell, rose, partial = natural(s)
    if ne:
        last = max(s.get("years_used") or [0])
        out += ["", "**When a limit rose** (band between the old and the new limit, all awards; change in the excess ratio, after minus before):", "",
                "| Limit | Change | Sector | Before | After | Change (95% interval) |", "|---|---|---|---|---|---|"]
        for e in ne:
            b, a = e.get("before_years") or [], e.get("after_years") or []
            a_txt = (fy_text([y for y in range(a[0], a[-1] + 1) if y <= last]) + (" so far" if a[-1] > last else "")) if a else ""
            c = e["after_minus_before"]
            label = {"SAT": "simplified acquisition", "MPT": "micro-purchase"}.get(e["threshold_type"], e["threshold_type"])
            out.append(f"| {label} | {usd(e['old_line'])} → {usd(e['new_line'])} | {SECTOR.get(e['sector'], e['sector'])} | "
                       f"{num(e['before']['ratio'])} ({fy_text(range(b[0], b[-1] + 1)) if b else ''}) | {num(e['after']['ratio'])} ({a_txt}) | "
                       f"{sgn(c['change'])} ({srng(c['ci_lo'], c['ci_hi'])}) |")
        out += ["", f"It fell clearly in {fell}, rose clearly in {rose} and was unclear in {len(ne) - fell - rose} of {len(ne)} tests."]
    return "\n".join(out)


# -- who wins the awards: the supplier base (33_supplier_base.py)
def supplier():
    return opt_json("supplier_base_summary.json") or {}


def ci_text(e):
    """'22.9% (22.6–23.1%)' from a {share, ci_lo, ci_hi} dict"""
    return f"{pct(e['share'])} ({pts(e['ci_lo'])}–{pct(e['ci_hi'])})"


def known_change(s, fy):
    """' (FY2008 includes 741,240 purchase orders of the Department of Veterans Affairs at or under $3,000)' when the summary records a
    known reporting change in that year, else ''"""
    for k in s.get("known_reporting_changes") or []:
        if k.get("fy") == fy and k.get("at_or_under_micro_purchase_threshold"):
            return (f" (FY{fy} includes {n(k['at_or_under_micro_purchase_threshold'])} purchase orders of the {k['agency']} at or under "
                    f"{usd(k['micro_purchase_threshold'])})")
    return ""


def r_supplier():
    s = supplier()
    c, a = s.get("companies_above_25k"), s.get("companies_all_awards")
    if not c or not a:
        return not_yet("the supplier-base analysis (`results/supplier_base_summary.json`) has not been built.")
    fall = "fell" if c["change"] < 0 else "rose"
    txt = [f"In FY{c['first_year']}, {n(c['first'])} companies won at least one new award above $25,000. In FY{c['last_year']}, "
           f"{n(c['last'])} did, {pct(abs(c['change']), 0)} {'fewer' if c['change'] < 0 else 'more'}. Counting every award, the number "
           f"{'fell' if a['change'] < 0 else 'rose'} from {n(a['first'])} to {n(a['last'])}, but much of that change is among companies with "
           f"only small purchases, whose reporting changed over time{known_change(s, a['first_year'])}; "
           "the above-$25,000 count is the steadier measure. Dollars are not adjusted for inflation."]
    if fall == "rose":
        txt[0] = txt[0].replace("much of that change", "part of that change")
    if abs(a["change"]) <= abs(c["change"]):
        txt[0] = txt[0].replace("but much of that change", "and part of that change")
    nw, st = s.get("new_companies_above_25k"), s.get("staying_above_25k")
    if nw:
        txt.append(f"In an average year ({fy_text(nw['years'])}), {pct(nw['share_of_companies_mean'], 0)} of the companies winning "
                   f"above $25,000 had won no such award in the three years before. These new companies won {pct(nw['share_of_awards_mean'], 0)} "
                   f"of those awards and {pct(nw['share_of_dollars_mean'], 0)} of the initial dollars of purchase orders and definitive "
                   "contracts above $25,000.")
    if st and st.get("next_year_mean") is not None:
        txt.append(f"About {pct(st['next_year_mean'], 0)} of the companies won again the next year"
                   + (f", and {pct(st['within_3_years_mean'], 0)} within three years." if st.get("within_3_years_mean") is not None else "."))
    k = s.get("concentration_above_25k")
    if k and k.get("top1pct_last") is not None:
        top = [fl(r.get("top1pct_share_dollars")) for r in opt_rows("supplier_base_concentration.csv")
               if r.get("sector") == "all sectors" and r.get("award_scope") == "above_25k"]
        top = [v for v in top if v is not None]
        span = (f"; from FY{k['first_year']} to FY{k['last_year']} the yearly share moved between {pct(min(top), 0)} and "
                f"{pct(max(top), 0)}, and a few very large awards can move it from one year to the next." if len(top) > 2 else ".")
        txt.append(f"The top 1% of companies won {pct(k['top1pct_last'], 0)} of the initial dollars of purchase orders and definitive "
                   f"contracts above $25,000 in FY{k['last_year']}" + span)
    cp = s.get("competition_above_25k_pooled") or {}
    new, old = cp.get("single_offer_share_new"), cp.get("single_offer_share_established")
    if new and old:
        word = "less" if new["share"] < old["share"] else "more"
        txt.append(f"Pooled over {cp['period'].replace(' pooled', '').replace('-', '–')}, competed awards won by new companies got a single "
                   f"offer {word} often than awards won by established companies: {pct(new['share'])} (95% interval "
                   f"{pts(new['ci_lo'])}–{pct(new['ci_hi'])}) against {ci_text(old)}.")
    sw = s.get("identifier_switch") or {}
    if sw.get("years_without_link_columns") and any(y >= 2019 for y in sw["years_without_link_columns"]):
        txt.append("Years whose measures span the 2022 switch from DUNS to UEI company identifiers are marked not comparable until "
                   "every table can link the two.")
    return paras(" ".join(txt), figure("supplier_base.png", "Who wins new federal awards: companies, new companies, staying, and "
                                                            "single-offer shares by company status"),
                 cannot("why companies enter or leave, or whether fewer companies means less competition. A company here is one "
                        "registered identifier: parents and subsidiaries count separately. Only prime awards are counted, and orders "
                        "under existing contract vehicles are not in these tables. These are patterns, not proof of causes."))


def m_supplier():
    s = supplier()
    yrs = s.get("years_present") or []
    rows_ = [r for r in opt_rows("supplier_base_by_fy.csv") if r.get("sector") == "all sectors" and r.get("award_kind") == "all"
             and r.get("award_scope") == "above_25k" and fl(r.get("n_companies")) is not None]
    if not s or not rows_:
        return not_yet("`results/supplier_base_summary.json` has not been built.")
    conc = {r["fy"]: r for r in opt_rows("supplier_base_concentration.csv") if r.get("sector") == "all sectors"
            and r.get("award_scope") == "above_25k"}
    sw = s.get("identifier_switch") or {}

    def cell(r, col, flag=None):
        v = fl(r.get(col))
        if v is None:
            return ""
        return pct(v) + (" (not comparable)" if flag and r.get(flag) == "False" else "")
    out = [f"**Results** (fiscal years present: {fy_text(yrs)}" + (f"; incomplete: {fy_text(s['incomplete_years'])}" if s.get("incomplete_years") else "")
           + "). All sectors, awards above $25,000:", "",
           "| Fiscal year | Awards | Companies | New companies (3-year look-back) | Never seen since the first year | Won again next year | "
           "Won again within 3 years | Top 1% share of dollars | Top 10% share | Median HHI (award-weighted) |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    for r in sorted(rows_, key=lambda r: int(r["fy"])):
        k = conc.get(r["fy"], {})
        hhi = fl(k.get("hhi_median_award_weighted"))
        out.append(f"| FY{r['fy']} | {n(r['n_awards'])} | {n(r['n_companies'])} | {cell(r, 'new_share_companies', 'new_measures_comparable')} | "
                   f"{cell(r, 'new_share_companies_never_seen', 'never_seen_comparable')} | {cell(r, 'stay_share_next_year', 'stay_next_year_comparable')} | "
                   f"{cell(r, 'stay_share_within_3_years', 'stay_3_years_comparable')} | {cell(k, 'top1pct_share_dollars')} | "
                   f"{cell(k, 'top10pct_share_dollars')} | {n(round(hhi)) if hhi is not None else ''} |")
    by = sw.get("by_year") or {}
    linked = sw.get("years_with_link_columns") or []
    miss = sw.get("years_without_link_columns") or []
    sw_txt = (f"**Identifier switch.** Tables with both identifier codes: {fy_text(linked) if linked else 'none yet'}"
              + (f"; without them: {fy_text(miss)}" if miss else "") + f". {n(sw.get('duns_uei_links', 0))} DUNS codes are linked to a UEI "
              f"code; {n(sw.get('linked_groups_with_more_than_two_codes', 0))} linked groups hold more than two codes.")
    uni = [(y, d) for y, d in by.items() if d.get("has_link_columns") and d.get("share_with_uei") is not None]
    if uni:
        lo = min(uni, key=lambda x: x[1]["share_with_uei"])
        duns_only = max(d.get('share_duns_only_unlinked') or 0 for _, d in uni)
        if lo[1]["share_with_uei"] >= 0.99995 and duns_only < 0.00005:
            sw_txt += " In those tables every award carries a UEI, and none carries only a DUNS that no record links to a UEI."
        else:
            sw_txt += (f" In those tables, at least {pct(lo[1]['share_with_uei'])} of awards carry a UEI (lowest: FY{lo[0]}), and at most "
                       f"{pct(duns_only, 2)} carry only a DUNS that no record links to a UEI.")
    old = [(y, d) for y, d in by.items() if not d.get("has_link_columns") and d.get("share_id_matching_a_known_duns_code") is not None]
    if old:
        sw_txt += (f" In the tables without the codes, at most {pct(max(d['share_id_matching_a_known_duns_code'] for _, d in old), 2)} of "
                   "awards carry a company id that matches a DUNS code seen in the newer tables, a sign that the older records carry UEIs "
                   "too (USAspending has added UEIs to old records); the check covers only companies whose DUNS appears in a newer table.")
    nw = s.get("new_companies_above_25k") or {}
    if nw.get("largest_gap_linked_vs_unlinked_points") is not None:
        g = nw["largest_gap_linked_vs_unlinked_points"]
        sw_txt += (f" Linking changes the new-company share by at most {num(g, 2)} percentage points in any year"
                   + (f" (largest in FY{nw['largest_gap_year']})." if g > 0 else "."))
    bf = sw.get("usaspending_sample")
    if bf and bf.get("transactions"):
        sw_txt += (f" A one-time sample from the USAspending download service ({n(bf['transactions'])} transactions of "
                   f"{bf.get('request', {}).get('awarding_agency', 'one agency')}, {bf.get('request', {}).get('action_dates', '')}, "
                   f"checked {long_date(bf['checked_on'])}) found {pct(bf['share_with_uei'])} with a UEI "
                   "([reference/uei_backfill_check.json](../reference/uei_backfill_check.json)).")
    out += ["", sw_txt]
    cp = s.get("competition_above_25k_pooled") or {}
    if cp.get("single_offer_share_new"):
        parts = [f"single-offer share of competed awards won by new companies {ci_text(cp['single_offer_share_new'])}, by established "
                 f"companies {ci_text(cp['single_offer_share_established'])}"]
        if cp.get("new_share_one_offer") and cp.get("new_share_several_offers"):
            parts.append(f"new companies won {ci_text(cp['new_share_one_offer'])} of competed awards with one offer and "
                         f"{ci_text(cp['new_share_several_offers'])} of those with two or more")
        sa = cp.get("new_share_by_setaside") or {}
        if sa:
            parts.append("new companies' share of awards by set-aside: " + "; ".join(f"{LEVEL.get(g, g)} {ci_text(e)}" for g, e in sa.items()))
        out += ["", f"**Link to competition** ({cp['period'].replace('-', '–')}, all sectors, awards above $25,000): " + "; ".join(parts) + "."]
    return "\n".join(out)


BLOCKS = {
    # README.md
    "status": r_status, "threshold-table": r_threshold_table, "threshold-quarters": r_threshold_quarters,
    "single-offer-five": r_single_five, "civilian": r_civilian, "civilian-table": r_civilian_table,
    "model-table": r_model_table, "model-applied": r_model_applied, "forecasts": r_forecasts,
    "limit-premium": r_limit_premium, "limit-999": r_limit_999, "limit-fy2026": r_limit_fy2026,
    # README.md, the long history and the other newer analyses (optional files)
    "history": r_history, "threshold-history": r_threshold_history, "threshold-staggered": r_threshold_staggered,
    "threshold-scan": r_threshold_scan, "drivers": r_drivers, "outcomes": r_outcomes, "split-screen": r_split,
    "quality": r_quality, "reference": r_reference, "limit-history": r_limit_history, "model-v2": r_model_v2,
    "m-header": m_header, "m-download": m_download, "m-999": m_999, "m-coverage": m_coverage, "m-recount": m_recount,
    "m-five-table": m_five_table, "m-other-cuts": m_other_cuts, "m-premium": m_premium, "m-offices": m_offices,
    "m-civ-data": m_civ_data, "m-civ-check": m_civ_check, "m-gsa": m_gsa, "m-civ-table": m_civ_table,
    "m-civ-trend": m_civ_trend, "m-threshold-data": m_threshold_data, "m-threshold-table": m_threshold_table,
    "m-threshold-quarters": m_threshold_quarters, "m-micro": m_micro, "m-did": m_did, "m-model-frozen": m_model_frozen,
    "m-model-pop": m_model_pop, "m-oot": m_oot, "m-effects": m_effects, "m-open": m_open, "m-registered": m_registered,
    "m-backtest": m_backtest, "m-f2": m_f2, "m-limit-fy2026": m_limit_fy2026,
    # docs/methodology.md, the newer analyses (optional files)
    "m-quality": m_quality, "m-history": m_history, "m-threshold-history": m_threshold_history, "m-staggered": m_staggered,
    "m-scan": m_scan, "m-drivers": m_drivers, "m-outcomes": m_outcomes, "m-split": m_split, "m-reference": m_reference,
    "m-model-v2": m_model_v2,
    # the supplier base (optional files)
    "supplier-base": r_supplier, "m-supplier-base": m_supplier,
    # the completeness check and the daily sealed forecasts (optional files)
    "reconcile": r_reconcile, "m-reconcile": m_reconcile, "daily-forecasts": r_daily, "m-daily-forecasts": m_daily,
}
# blocks that read only optional files: they must never fail, whatever is missing (tests/test_report.py checks this)
OPTIONAL_BLOCKS = ["history", "threshold-history", "threshold-staggered", "threshold-scan", "drivers", "outcomes", "split-screen",
                   "quality", "reference", "limit-history", "model-v2", "m-quality", "m-history", "m-threshold-history", "m-staggered",
                   "m-scan", "m-drivers", "m-outcomes", "m-split", "m-reference", "m-model-v2", "supplier-base", "m-supplier-base",
                   "reconcile", "m-reconcile", "daily-forecasts", "m-daily-forecasts"]


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
        try:
            label = os.path.relpath(path, PROJECT).replace(os.sep, "/")
        except ValueError:                      # Windows: the file is on another drive than the project (tests in a temp folder)
            label = os.path.basename(path)
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
