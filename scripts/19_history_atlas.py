"""
19_history_atlas.py - single-offer shares over the long history (FY2008 onward), for civilian agencies and the Department of Defense.

Same definitions as 03_atlas.py and 03b_atlas_civilian.py: among base awards that were competed (A, D, E, F, CDO) with a usable offer count
(1..998), the share that received exactly one offer, with 95% Wilson intervals.  GSA is shown separately from the other civilian agencies,
because its very small supply orders outnumber everything else and their offer coding changed (see 03b and 16_quality_monitor.py).
Defense data are published 90 days late, so the latest Defense year is incomplete until its last quarter is out.

Known reporting change (column `note`, a printed caution, and a marker on the chart; no data are dropped):
  FY2008, Department of Veterans Affairs.  In FY2008 VA reported about 800,000 purchase orders, most of them micro-purchases (at or under
  the $3,000 micro-purchase threshold, mostly paid by purchase card).  About 31,000 of them count as competed with a usable offer count,
  and 99% of those record one offer.  In FY2009 VA reported about 90,000 purchase orders, and fewer than 1,000 such micro-purchases.
  So the "civilian except GSA" share falls from 48% to 34% between FY2008 and FY2009 because of what VA reported, not because
  competition changed: without VA it is 30% in FY2008 and 29% in FY2009, and for awards above $3,000 it is 32% in both years.
  The quality monitor (16_quality_monitor.py --history) flags the same break: VA purchase orders fall about 90% in every FY2009 quarter.
  The numbers in the note are computed from the data each run.

Input : data/history/processed/base_actions_FY*.parquet (18_history_build.py; `python run.py get-history` downloads them)
Output: results/history_by_fy.csv (sector x fiscal year x award kind), results/history_by_agency_fy.csv (agency x fiscal year),
        both with a `note` column (blank unless a known reporting change affects the row), figures/single_offer_history.png
"""
import glob
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from atlas_lib import share_table  # noqa: E402
from common import DATA, PROJECT, RESULTS  # noqa: E402

GSA = "General Services Administration"
VA = "Department of Veterans Affairs"
COLS = ["awarding_agency_name", "award_kind", "fy", "is_dod", "eval_set", "single", "base_and_all_options_value"]
MICRO = 3_000          # micro-purchase threshold in FY2008-FY2015
# known reporting changes: agency, fiscal year, award kinds whose sector rows get the note
KNOWN_ISSUES = [dict(agency=VA, fy=2008, kinds=("all", "PO"))]


def sector_of(agency, is_dod):
    s = pd.Series("civilian except GSA", index=agency.index)
    s[agency == GSA] = "GSA"
    s[is_dod.to_numpy(dtype=bool)] = "Department of Defense"
    return s


def load(files):
    """eval-set awards of every year, one file at a time"""
    parts = []
    for p in files:
        b = pd.read_parquet(p, columns=COLS)
        b = b[b.eval_set].drop(columns="eval_set")
        b["agency"] = b.pop("awarding_agency_name").astype(str)
        b["sector"] = sector_of(b.agency, b.is_dod).astype("category")
        b["agency"] = b.agency.astype("category")
        parts.append(b)
    return pd.concat(parts, ignore_index=True)


def issue_notes(b):
    """(sector notes {(sector, fy, kind): text}, agency notes {(agency, fy): text}, printed cautions) for KNOWN_ISSUES present in b"""
    sec_notes, ag_notes, cautions = {}, {}, []
    for k in KNOWN_ISSUES:
        ag, fy = k["agency"], k["fy"]
        a = b[(b.agency == ag) & (b.fy == fy)]
        if not len(a):
            continue
        sector = a.sector.iloc[0]
        nxt = b[(b.agency == ag) & (b.fy == fy + 1)]
        micro = a.base_and_all_options_value <= MICRO
        n_micro, single_micro = int(micro.sum()), float(a.single[micro].mean()) if micro.any() else float("nan")
        n_micro_next = int((nxt.base_and_all_options_value <= MICRO).sum()) if len(nxt) else None
        s = b[(b.sector == sector) & (b.fy == fy)]
        without = float(s.single[s.agency != ag].mean())
        above = float(s.single[s.base_and_all_options_value > MICRO].mean())
        nxt_txt = f", against {n_micro_next:,} in FY{fy + 1}" if n_micro_next is not None else ""
        base = (f"FY{fy} reporting change: {ag} reported {n_micro:,} competed micro-purchases with a usable offer count (at or under ${MICRO:,}) in FY{fy}"
                f"{nxt_txt}; {100 * single_micro:.0f}% of them record one offer.")
        ag_notes[(ag, fy)] = base + " Not comparable with later years."
        for kind in k["kinds"]:
            if kind == "all":
                sec_notes[(sector, fy, kind)] = (base + f" Without {ag} this sector's FY{fy} share is {100 * without:.1f}%; for awards above "
                                                 f"${MICRO:,} it is {100 * above:.1f}%. Not comparable with later years.")
            else:
                sk = s[s.award_kind == kind]
                wk = float(sk.single[sk.agency != ag].mean()) if len(sk) else float("nan")
                sec_notes[(sector, fy, kind)] = (base + f" Without {ag} this sector's FY{fy} {kind} share is {100 * wk:.1f}%. "
                                                 "Not comparable with later years.")
        cautions.append(dict(sector=sector, fy=fy, agency=ag, share_without=without, share_above_micro=above, text=sec_notes[(sector, fy, "all")]))
    return sec_notes, ag_notes, cautions


def main():
    files = sorted(glob.glob(os.path.join(DATA, "history", "processed", "base_actions_FY*.parquet")))
    if not files:
        sys.exit("no history tables: run `python run.py get-history` (or the history workflow) first")
    b = load(files)
    sec_notes, ag_notes, cautions = issue_notes(b)
    by_fy = pd.concat([share_table(b, ["sector", "fy"]).assign(award_kind="all"),
                       share_table(b, ["sector", "fy", "award_kind"])], ignore_index=True)
    by_fy["sector"], by_fy["award_kind"] = by_fy.sector.astype(str), by_fy.award_kind.astype(str)
    by_fy["note"] = [sec_notes.get((s, int(y), k), "") for s, y, k in zip(by_fy.sector, by_fy.fy, by_fy.award_kind)]
    by_fy = by_fy.sort_values(["sector", "award_kind", "fy"])
    by_fy.to_csv(os.path.join(RESULTS, "history_by_fy.csv"), index=False)
    by_ag = share_table(b, ["agency", "fy"])
    by_ag["agency"] = by_ag.agency.astype(str)
    by_ag = by_ag[by_ag.n_eval >= 100].sort_values(["agency", "fy"])
    by_ag["note"] = [ag_notes.get((a, int(y)), "") for a, y in zip(by_ag.agency, by_ag.fy)]
    by_ag.to_csv(os.path.join(RESULTS, "history_by_agency_fy.csv"), index=False)
    print(by_fy[by_fy.award_kind == "all"].pivot(index="fy", columns="sector", values="share").round(3).to_string())
    for c in cautions:
        print(f"\nCAUTION ({c['sector']}, FY{c['fy']}): {c['text']}")
    plot(by_fy[by_fy.award_kind == "all"], cautions)


def plot(d, cautions=()):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    colors = {"civilian except GSA": "#2a78d6", "Department of Defense": "#eb6834", "GSA": "#8c8c8c"}
    fig, ax = plt.subplots(figsize=(8, 4.3))
    for sector, color in colors.items():
        e = d[d.sector == sector].sort_values("fy")
        if len(e):
            ax.plot(e.fy, 100 * e.share, color=color, lw=2, label=sector)
            ax.fill_between(e.fy, 100 * e.ci_lo, 100 * e.ci_hi, color=color, alpha=0.12, lw=0)
    for c in cautions:
        e = d[(d.sector == c["sector"]) & (d.fy == c["fy"])]
        if not len(e):
            continue
        color = colors.get(c["sector"], "#333333")
        y = 100 * float(e.share.iloc[0])
        ax.scatter([c["fy"]], [y], s=70, facecolors="none", edgecolors="#222222", lw=1.2, zorder=4)
        ax.scatter([c["fy"]], [100 * c["share_without"]], s=30, marker="D", color=color, zorder=4,
                   label=f"FY{c['fy']} without {short(c['agency'])}")
        ax.annotate(f"FY{c['fy']}: {short(c['agency'])} reporting change\n(micro-purchases at or under \\${MICRO:,});\nnot comparable",
                    xy=(c["fy"], y), xytext=(c["fy"] + 1.6, y - 1.5), fontsize=7, color="#333333", va="center",
                    arrowprops=dict(arrowstyle="-", color="#555555", lw=0.8))
    years = sorted(int(y) for y in d.fy.unique())
    ax.set_xticks(years if len(years) <= 10 else years[::2])
    ax.set_ylabel("% of competed awards with one offer")
    ax.set_xlabel("fiscal year")
    ax.set_title("Single-offer share of competed federal awards, FY2008 onward", fontsize=10)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(frameon=False, fontsize=8)
    fig.tight_layout()
    fig.savefig(os.path.join(PROJECT, "figures", "single_offer_history.png"), dpi=150)
    plt.close(fig)


def short(agency):
    return {VA: "VA"}.get(agency, agency)


if __name__ == "__main__":
    main()
