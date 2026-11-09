"""
check_counts_sql.py - an INDEPENDENT recount of the headline single-offer counts, using SQL instead of pandas.

Why this exists
  The main pipeline (02_build_awards.py, 03_atlas.py, 03b_atlas_civilian.py) counts everything with pandas.  If a pandas
  detail were quietly wrong, every number downstream would be wrong with it.  This script recounts the same numbers a
  completely different way: Python's built-in csv module reads the raw downloads, and SQLite (SQL) does the counting.
  It uses no pandas, no numpy, and none of the pipeline's code (from common.py it takes only the folder paths).
  If the two methods agree to the last award, the pandas pipeline counted correctly.

What it does (the rules are written out again here, in SQL, on purpose)
  1. Reads every transaction CSV inside data/usaspending_civilian/*.zip (only 9 of the 36 columns are kept).
  2. Drops duplicate transaction keys (the first copy wins).
  3. The base action of an award is its transaction with modification_number 0, 00, 000 or blank; if there are several,
     the one with the earliest action_date.  Awards with no base action in the files are not counted.
  4. Fiscal year of the base action = calendar year of action_date, plus one if the month is October or later.
     Only fiscal years 2024-2026 are kept.
  5. competed = extent_competed_code is A, D, E, F or CDO.
     usable offers = number_of_offers_received is a number from 1 to 998 (blank and 999 are excluded).
     eval set = competed AND usable offers.   single = the eval-set awards with exactly 1 offer.
  6. Counts per awarding agency: base awards, competed, eval set, single.

Input : data/usaspending_civilian/*.zip   (from 01c_download_civilian.py)
        results/atlas_civilian_by_agency.csv, results/atlas_civilian_totals.csv, results/atlas_by_agency.csv
        (the pipeline's published files that are being checked)
Output: a printed comparison table and a PASS/FAIL line.  Nothing is written to the project; the temporary SQLite
        database lives in a temporary folder and is deleted at the end.
Exit code: 0 = every count matches, 1 = at least one mismatch, 2 = an input file is missing.

Usage (from the repository root):
    python -I scripts/check_counts_sql.py

Needs a few hundred MB of free disk space for the temporary database and about 100-200 MB of memory.
"""
import csv
import glob
import io
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA, RESULTS  # noqa: E402  (only the folder paths are shared with the pipeline)

ZIPS = os.path.join(DATA, "usaspending_civilian", "*.zip")
F_AGENCY = os.path.join(RESULTS, "atlas_civilian_by_agency.csv")
F_TOTALS = os.path.join(RESULTS, "atlas_civilian_totals.csv")
F_FIVE = os.path.join(RESULTS, "atlas_by_agency.csv")

FIVE = {"VA": "Department of Veterans Affairs", "HHS": "Department of Health and Human Services",
        "DHS": "Department of Homeland Security", "DOI": "Department of the Interior", "USDA": "Department of Agriculture"}

WANTED = ["contract_transaction_unique_key", "contract_award_unique_key", "modification_number", "action_date",
          "awarding_agency_name", "extent_competed_code", "number_of_offers_received", "award_or_idv_flag", "award_type_code"]
BATCH = 50_000


def peak_memory_mb():
    """peak working set of this process in MB (Windows only); None if it cannot be measured"""
    try:
        import ctypes
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        ps = ctypes.WinDLL("psapi", use_last_error=True)
        ps.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
        ps.GetProcessMemoryInfo.restype = wintypes.BOOL
        ok = ps.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb)
        return pmc.PeakWorkingSetSize / 1e6 if ok else None
    except Exception:
        return None


def to_offers(text):
    """number_of_offers_received as a number, or None when blank / not a number"""
    try:
        v = float(text)
    except ValueError:
        return None
    return v if v == v else None          # NaN -> None


def load_zip(con, path):
    """read every 'Transactions' CSV in one ZIP and insert its rows (9 columns) into table tx; returns (rows read, rows new)"""
    read = new = 0
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            if "Transactions" not in name or not name.endswith(".csv"):
                continue
            with z.open(name) as raw:
                rd = csv.reader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline=""))
                header = next(rd)
                missing = [c for c in WANTED if c not in header]
                if missing:
                    raise SystemExit(f"{os.path.basename(path)}: column(s) {missing} not found in {name}")
                ix = [header.index(c) for c in WANTED]
                (i_tx, i_aw, i_mod, i_dt, i_ag, i_ext, i_off, i_flag, i_type) = ix
                rows = []
                for r in rd:
                    if len(r) <= max(ix):
                        continue                      # a short (damaged) line cannot be used
                    rows.append((r[i_tx], r[i_aw], r[i_mod], r[i_dt], r[i_ag], r[i_ext], to_offers(r[i_off]), r[i_flag], r[i_type]))
                    if len(rows) >= BATCH:
                        read += len(rows)
                        new += insert(con, rows)
                        rows = []
                if rows:
                    read += len(rows)
                    new += insert(con, rows)
    return read, new


def insert(con, rows):
    before = con.total_changes
    con.executemany("INSERT OR IGNORE INTO tx VALUES (?,?,?,?,?,?,?,?,?)", rows)   # PRIMARY KEY on tx_key = drop duplicates
    return con.total_changes - before


SQL_BASE = """
CREATE TABLE base AS
SELECT contract_award_unique_key AS award, agency, extent, offers,
       CAST(substr(action_date, 1, 4) AS INTEGER) + (CAST(substr(action_date, 6, 2) AS INTEGER) >= 10) AS fy
FROM (
    SELECT *, ROW_NUMBER() OVER (
               PARTITION BY contract_award_unique_key
               ORDER BY CASE WHEN action_date GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]*' THEN 0 ELSE 1 END, action_date) AS rn
    FROM tx
    WHERE modification_number IN ('0', '00', '000', '')
)
WHERE rn = 1
"""

SQL_COUNTS = """
SELECT agency,
       COUNT(*) AS n_base,
       SUM(extent IN ('A','D','E','F','CDO')) AS n_competed,
       SUM(extent IN ('A','D','E','F','CDO') AND offers >= 1 AND offers <= 998) AS n_eval,
       SUM(extent IN ('A','D','E','F','CDO') AND offers >= 1 AND offers <= 998 AND offers = 1) AS k_single
FROM base
WHERE fy BETWEEN 2024 AND 2026
GROUP BY agency
"""


def read_csv_rows(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def yn(ok):
    return "yes" if ok else "NO"


def main():
    t0 = time.time()
    zips = sorted(glob.glob(ZIPS))
    missing = [p for p in (F_AGENCY, F_TOTALS, F_FIVE) if not os.path.exists(p)]
    if len(zips) == 0:
        print("INPUT MISSING: no ZIP files found in data/usaspending_civilian/.")
        print("Run the download first:  python -I scripts/01c_download_civilian.py")
        return 2
    if missing:
        print("INPUT MISSING: the published results file(s) to compare against are not there:")
        for p in missing:
            print("  ", p)
        print("Run the pipeline first (02_build_awards.py, 03_atlas.py, 03b_atlas_civilian.py).")
        return 2

    csv.field_size_limit(10_000_000)
    tmp = tempfile.mkdtemp(prefix="check_counts_sql_")
    db_path = os.path.join(tmp, "recount.sqlite")
    try:
        con = sqlite3.connect(db_path)
        con.executescript("PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; PRAGMA temp_store=FILE; PRAGMA cache_size=-65536;")
        con.execute("""CREATE TABLE tx (
            tx_key TEXT PRIMARY KEY, contract_award_unique_key TEXT, modification_number TEXT, action_date TEXT,
            agency TEXT, extent TEXT, offers REAL, award_or_idv_flag TEXT, award_type_code TEXT)""")
        print(f"Recount in SQL (SQLite {sqlite3.sqlite_version}); temporary database: {db_path}")
        print(f"Reading {len(zips)} ZIP files ...")
        total_read = total_new = 0
        for i, p in enumerate(zips, 1):
            ts = time.time()
            read, new = load_zip(con, p)
            con.commit()
            total_read += read
            total_new += new
            print(f"  [{i:2d}/{len(zips)}] {os.path.basename(p)}: {read:>9,} rows read, {new:>9,} new "
                  f"({read - new:,} duplicate keys)  {time.time() - ts:5.1f}s")
        print(f"Transaction rows read: {total_read:,} | kept after dropping duplicate transaction keys: {total_new:,} "
              f"(dropped {total_read - total_new:,})")

        print("Indexing and picking each award's base action ...")
        con.execute("CREATE INDEX ix_tx_award ON tx(contract_award_unique_key)")
        con.execute("CREATE INDEX ix_tx_mod ON tx(modification_number)")
        n_awards = con.execute("SELECT COUNT(DISTINCT contract_award_unique_key) FROM tx").fetchone()[0]
        con.execute(SQL_BASE)
        con.execute("CREATE INDEX ix_base_agency ON base(agency)")
        n_base_all = con.execute("SELECT COUNT(*) FROM base").fetchone()[0]
        print(f"Awards seen: {n_awards:,} | with a base action in the files: {n_base_all:,}")
        counts = {r[0]: tuple(int(x or 0) for x in r[1:]) for r in con.execute(SQL_COUNTS)}   # agency -> (n_base, n_comp, n_eval, k)
        con.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("Temporary database deleted:", not os.path.exists(tmp))

    # ---- compare with the published files ------------------------------------------------------------
    bad = []                                           # descriptions of mismatches
    print("\n" + "=" * 130)
    print("1) CIVILIAN, BY AGENCY  (results/atlas_civilian_by_agency.csv)   columns: base / competed / eval set / single")
    print("=" * 130)
    print(f"{'agency':<58}{'recount (SQL)':>34}{'file':>34}  match")
    filerows = {r["agency"]: r for r in read_csv_rows(F_AGENCY)}
    for ag in sorted(filerows, key=lambda a: -int(filerows[a]["n_eval"])):
        r = filerows[ag]
        want = (int(r["n_base"]), int(r["n_competed"]), int(r["n_eval"]), int(r["k_single"]))
        got = counts.get(ag, (0, 0, 0, 0))
        ok = got == want
        if not ok:
            bad.append(f"civilian by agency: {ag}")
        fmt = lambda t: f"{t[0]:,}/{t[1]:,}/{t[2]:,}/{t[3]:,}"
        print(f"{ag[:56]:<58}{fmt(got):>34}{fmt(want):>34}  {yn(ok)}")
    extra = [a for a in counts if a not in filerows and counts[a][2] > 0]
    for a in extra:
        bad.append(f"civilian by agency: {a} has eval-set awards in the recount but no row in the file")
        print(f"{a[:56]:<58}{str(counts[a]):>34}{'(no row)':>34}  NO")
    quiet = [a for a in counts if a not in filerows and counts[a][2] == 0]
    if quiet:
        print(f"({len(quiet)} further agencies have base awards but none in the eval set; the file lists no row for them, as expected.)")

    tot_k = sum(c[3] for c in counts.values())
    tot_n = sum(c[2] for c in counts.values())
    print("\n" + "=" * 130)
    print("2) CIVILIAN TOTALS  (results/atlas_civilian_totals.csv, row 'all civilian agencies')")
    print("=" * 130)
    trow = [r for r in read_csv_rows(F_TOTALS) if r["scope"] == "all civilian agencies"]
    if not trow:
        bad.append("totals: row 'all civilian agencies' not found")
        print("row 'all civilian agencies' not found in the file  NO")
    else:
        fk, fn = int(trow[0]["k_single"]), int(trow[0]["n_eval"])
        for label, got, want in (("single (k_single)", tot_k, fk), ("eval set (n_eval)", tot_n, fn)):
            ok = got == want
            if not ok:
                bad.append(f"totals: {label}")
            print(f"{label:<24} recount {got:>10,}   file {want:>10,}   match: {yn(ok)}")
        print(f"{'base awards':<24} recount {sum(c[0] for c in counts.values()):>10,}   (not published in the totals file)")
        print(f"{'competed':<24} recount {sum(c[1] for c in counts.values()):>10,}   (not published in the totals file)")

    print("\n" + "=" * 130)
    print("3) FIVE DEPARTMENTS  (results/atlas_by_agency.csv)   columns: single / eval set")
    print("=" * 130)
    print(f"{'agency':<10}{'recount (SQL)':>22}{'file':>22}{'difference':>20}  match")
    for r in read_csv_rows(F_FIVE):
        code = r["agency"]
        full = FIVE.get(code)
        if full is None:
            continue
        got = counts.get(full, (0, 0, 0, 0))
        gk, gn = got[3], got[2]
        wk, wn = int(r["k_single"]), int(r["n_eval"])
        ok = (gk, gn) == (wk, wn)
        if not ok:
            bad.append(f"five departments: {code}")
        print(f"{code:<10}{f'{gk:,}/{gn:,}':>22}{f'{wk:,}/{wn:,}':>22}{f'{wk - gk:+,}/{wn - gn:+,}':>20}  {yn(ok)}")

    elapsed = time.time() - t0
    mem = peak_memory_mb()
    print(f"\nRuntime: {elapsed:.0f} s ({elapsed / 60:.1f} min)" + (f" | peak memory of this process: {mem:.0f} MB" if mem else ""))
    if bad:
        print(f"FAIL: {len(bad)} mismatch(es):")
        for b in bad:
            print("  -", b)
        return 1
    print("PASS: every recounted number matches the published files exactly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
