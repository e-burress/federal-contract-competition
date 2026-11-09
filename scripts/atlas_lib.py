"""
atlas_lib.py - the single-offer share calculation shared by 03b_atlas_civilian.py and 15_civilian_fytd.py
(the same calculation as in 03_atlas.py, kept there unchanged for the original five-department analysis).

  share_table(df, by)   single-offer share and a 95% Wilson interval for each group of competed awards with usable offers
"""
from statsmodels.stats.proportion import proportion_confint


def share_table(df, by):
    """single-offer share with a 95% Wilson interval for each group."""
    g = df.groupby(by, observed=True)["single"].agg(["sum", "count"]).reset_index()
    g.columns = list(g.columns[:-2]) + ["k_single", "n_eval"]
    lo, hi = proportion_confint(g.k_single, g.n_eval, alpha=0.05, method="wilson")
    g["share"] = g.k_single / g.n_eval
    g["ci_lo"], g["ci_hi"] = lo, hi
    return g
