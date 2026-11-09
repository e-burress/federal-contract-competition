"""
19_history_atlas.py - single-offer shares over the long history (FY2008 onward), for civilian agencies and the Department of Defense.

Same definitions as 03_atlas.py and 03b_atlas_civilian.py: among base awards that were competed (A, D, E, F, CDO) with a usable offer count
(1..998), the share that received exactly one offer, with 95% Wilson intervals.  GSA is shown separately from the other civilian agencies,
because its very small supply orders outnumber everything else and their offer coding changed (see 03b and 16_quality_monitor.py).
Defense data are published 90 days late, so the latest Defense year is incomplete until its last quarter is out.

Input : data/history/processed/base_actions_FY*.parquet (18_history_build.py; `python run.py get-history` downloads them)
Output: results/history_by_fy.csv (sector x fiscal year x award kind), results/history_by_agency_fy.csv (agency x fiscal year),
        figures/single_offer_history.png
"""
import glob
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from atlas_lib import share_table  # noqa: E402
from common import DATA, PROJECT, RESULTS  # noqa: E402

GSA = "General Services Administration"
COLS = ["awarding_agency_name", "award_kind", "fy", "is_dod", "eval_set", "single"]


def main():
    files = sorted(glob.glob(os.path.join(DATA, "history", "processed", "base_actions_FY*.parquet")))
    if not files:
        sys.exit("no history tables: run `python run.py get-history` (or the history workflow) first")
    b = pd.concat([pd.read_parquet(p, columns=COLS) for p in files], ignore_index=True)
    b = b[b.eval_set].copy()
    b["agency"] = b.awarding_agency_name.astype(str)
    b["sector"] = "civilian except GSA"
    b.loc[b.agency == GSA, "sector"] = "GSA"
    b.loc[b.is_dod, "sector"] = "Department of Defense"
    by_fy = pd.concat([share_table(b, ["sector", "fy"]).assign(award_kind="all"),
                       share_table(b, ["sector", "fy", "award_kind"])], ignore_index=True)
    by_fy = by_fy.sort_values(["sector", "award_kind", "fy"])
    by_fy.to_csv(os.path.join(RESULTS, "history_by_fy.csv"), index=False)
    by_ag = share_table(b, ["agency", "fy"])
    by_ag = by_ag[by_ag.n_eval >= 100].sort_values(["agency", "fy"])
    by_ag.to_csv(os.path.join(RESULTS, "history_by_agency_fy.csv"), index=False)
    print(by_fy[by_fy.award_kind == "all"].pivot(index="fy", columns="sector", values="share").round(3).to_string())
    plot(by_fy[by_fy.award_kind == "all"])


def plot(d):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    colors = {"civilian except GSA": "#2a78d6", "Department of Defense": "#eb6834", "GSA": "#8c8c8c"}
    fig, ax = plt.subplots(figsize=(8, 4))
    for sector, color in colors.items():
        e = d[d.sector == sector].sort_values("fy")
        if len(e):
            ax.plot(e.fy, 100 * e.share, color=color, lw=2, label=sector)
            ax.fill_between(e.fy, 100 * e.ci_lo, 100 * e.ci_hi, color=color, alpha=0.12, lw=0)
    ax.set_ylabel("% of competed awards with one offer")
    ax.set_xlabel("fiscal year")
    ax.set_title("Single-offer share of competed federal awards, FY2008 onward", fontsize=10)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(frameon=False, fontsize=8)
    fig.tight_layout()
    fig.savefig(os.path.join(PROJECT, "figures", "single_offer_history.png"), dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    main()
