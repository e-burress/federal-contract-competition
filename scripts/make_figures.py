"""
make_figures.py - draw the README charts from the CSV files in results/.

Run from the repository root:
    python -I scripts/make_figures.py
It only reads results/*.csv and results/threshold_summary.txt (no downloads) and writes PNGs to figures/.
"""
import os
import re
import sys

import matplotlib
matplotlib.use("Agg")                      # draw to files, no window
import matplotlib.pyplot as plt
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, "results")
FIG = os.path.join(ROOT, "figures")
os.makedirs(FIG, exist_ok=True)

PRE, POST = "#8c8c8c", "#1f5fa8"            # grey = before the change, blue = after
plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False})


def base_actions_per_period():
    """Read the number of civilian base actions per period from threshold_summary.txt,
    so the bin counts can be put on the same scale (per 100,000 base actions)."""
    text = open(os.path.join(RES, "threshold_summary.txt"), encoding="utf-8").read()
    m = re.search(r"FY2025 \(pre\) ([\d,]+) \| FY2026 \(post\) ([\d,]+)", text)
    if not m:
        sys.exit("could not find the period totals in threshold_summary.txt")
    return int(m.group(1).replace(",", "")), int(m.group(2).replace(",", ""))


# ---- Figure 1: where award values pile up, before and after the threshold change -----------------
n_pre, n_post = base_actions_per_period()
fig, axes = plt.subplots(1, 2, figsize=(11, 4.2), sharey=False)
for ax, t in zip(axes, (250000, 350000)):
    pre = pd.read_csv(os.path.join(RES, f"threshold_bins_FY2025_pre_base_and_all_options_value_{t}.csv"))
    post = pd.read_csv(os.path.join(RES, f"threshold_bins_FY2026_post_base_and_all_options_value_{t}.csv"))
    for df, n, color, label in ((pre, n_pre, PRE, "FY2025 (before)"), (post, n_post, POST, "FY2026 (after)")):
        x = df["bin_upper_edge"] / 1000.0
        y = df["count"] / n * 100000
        ax.step(x, y, where="pre", color=color, lw=2, label=label)   # each count covers (edge - 5K, edge]
    ax.axvline(250, color="black", ls="--", lw=1)
    ax.axvline(350, color="#c0392b", ls="--", lw=1)
    ax.set_xlabel(r"award value incl. options (\$ thousands, \$5K bins)")
    ax.set_title(rf"Awards near \${t // 1000}K")   # \$ = a literal dollar sign (plain $ starts math text)
    lo = min(pre["bin_upper_edge"].min(), post["bin_upper_edge"].min()) / 1000 - 5
    hi = max(pre["bin_upper_edge"].max(), post["bin_upper_edge"].max()) / 1000
    ax.set_xlim(lo, hi)
axes[0].set_ylabel("awards per 100,000 base actions")
axes[0].text(251, axes[0].get_ylim()[1] * 0.92, r"old line \$250K", fontsize=9)
axes[1].text(351, axes[1].get_ylim()[1] * 0.92, "new line $350K\n(Oct 1, 2025)", fontsize=9, color="#c0392b")
axes[1].legend(frameon=False, loc="upper right")
fig.suptitle(r"Civilian contract awards piled up under \$250K until the simplified-acquisition threshold moved to \$350K",
             fontsize=11)
fig.tight_layout()
fig.savefig(os.path.join(FIG, "threshold_shift.png"), dpi=150)
plt.close(fig)

# ---- Figure 2: the bunching ratio by fiscal quarter --------------------------------------------
q = pd.read_csv(os.path.join(RES, "threshold_quarterly_bunching.csv"))
fig, ax = plt.subplots(figsize=(11, 4))
for t, color in ((250000, PRE), (350000, POST)):
    d = q[q["threshold"] == t].reset_index(drop=True)
    xs = range(len(d))
    ax.plot(xs, d["ratio"], "-o", color=color, lw=2, label=rf"just under \${t // 1000}K")
    ax.fill_between(xs, d["lo95"], d["hi95"], color=color, alpha=0.15)
    labels = list(d["fyq"])
ax.axhline(1.0, color="black", lw=0.8)
change = labels.index("FY2026Q1") - 0.5
ax.axvline(change, color="#c0392b", ls="--", lw=1)
ax.text(change + 0.1, ax.get_ylim()[1] * 0.95, "threshold raised\nOct 1, 2025", color="#c0392b", fontsize=9, va="top")
ax.set_xticks(range(len(labels)))
ax.set_xticklabels(labels, rotation=45, ha="right")
ax.set_ylabel("observed / expected awards\n(1.0 = no pile-up)")
ax.set_title("Bunching ratio by fiscal quarter, with 95% bootstrap intervals (civilian purchase orders and definitive contracts)")
ax.legend(frameon=False, loc="upper left")
fig.tight_layout()
fig.savefig(os.path.join(FIG, "bunching_by_quarter.png"), dpi=150)
plt.close(fig)

# ---- Figure 3: single-offer share by agency and fiscal year ------------------------------------
a = pd.read_csv(os.path.join(RES, "atlas_by_agency_fy.csv"))
order = ["HHS", "VA", "DHS", "DOI", "USDA"]
years = sorted(a["fy"].unique())
colors = {2024: "#bcd0e8", 2025: "#6f9fd0", 2026: "#1f5fa8"}
fig, ax = plt.subplots(figsize=(9, 4))
w = 0.26
for i, fy in enumerate(years):
    d = a[a["fy"] == fy].set_index("agency").reindex(order)
    xs = [k + (i - 1) * w for k in range(len(order))]
    err = [100 * (d["share"] - d["ci_lo"]), 100 * (d["ci_hi"] - d["share"])]
    ax.bar(xs, 100 * d["share"], width=w, color=colors.get(fy, "#888"), label=f"FY{fy}", yerr=err, capsize=2)
ax.set_xticks(range(len(order)))
ax.set_xticklabels(order)
ax.set_ylabel("% of competed awards with one offer")
ax.set_title("Single-offer share among competed awards (purchase orders, definitive contracts, contract vehicles)")
ax.legend(frameon=False)
fig.tight_layout()
fig.savefig(os.path.join(FIG, "single_offer_by_agency.png"), dpi=150)
plt.close(fig)

print("wrote", sorted(os.listdir(FIG)))
