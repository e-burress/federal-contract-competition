"""
22_sam_archive.py - turn SAM.gov's yearly ARCHIVE files of past notices into small public tables of solicitations.

Why: the forecast model (chance that a solicitation gets only one offer) needs the history of each solicitation: when it was
first posted, its response deadline, whether a presolicitation came first, whether an award notice followed.  Looking that
up one notice at a time gave only 1,802 solicitations.  SAM.gov publishes one archive file per fiscal year with every past
notice, so the same facts can be built for hundreds of thousands of solicitations.

Source (public, no key, no account)
  Listing:  https://sam.gov/api/prod/fileextractservices/v1/api/listfiles?domain=Contract%20Opportunities/Archived%20Data&privacy=Public
  Files:    https://s3.amazonaws.com/falextracts/Contract%20Opportunities/Archived%20Data/FY<year>_archived_opportunities.csv
  Each file has the same 47 columns as the nightly active-notices file (read with sam_csv.py; checked at run time).
  The big years are about 0.5 to 1.3 GB each.  The listing gives no sizes, so --list asks S3 for each file's size.

How the yearly files are defined (checked against the data, see the JSON "fiscal_year_definition" note)
  A notice sits in the file of the federal fiscal year (October to September) of its PostedDate, not of its ArchiveDate.
  For example FY2024 holds notices posted 2023-10-01 ... 2024-09-30, some archived in FY2025.  The rows are sorted by
  PostedDate, newest first.  Each row is one VERSION of a notice (an amendment gets a new NoticeId with the same Sol#), so a
  solicitation's versions can sit in two yearly files.  Use --merge to join the years.

What it does for one fiscal year
  1. Streams the file to a temporary folder OUTSIDE the repository (size cap 8 GB, 4 attempts; a broken download resumes
     where it stopped with an HTTP Range request).  The file is deleted at the end, also when something fails.
  2. Reads it in chunks of 10,000 rows with the mixed-encoding reader of script 14.  Only whitelisted columns are loaded.
     The Description is used in memory for the two keyword flags (common.kw_flags) and then thrown away.
  3. Writes to <DATA>/sam_archive/ (temp name first, then moved into place):
       notices_FY<fy>.parquet        one row per notice version (no title text, no names, no contacts)
       solicitations_FY<fy>.parquet  one row per normalised Sol# among Solicitation / Combined Synopsis/Solicitation notices
       sam_archive_FY<fy>.json       counts and data-quality notes

The notices table
  notice_id, file_fy (the fiscal year of the file), sol_number (as written, e-mails and phones masked), sol_norm (upper-case
  letters and digits only; empty -> missing), type, base_type (type of the first notice in the chain), archive_type,
  posted_date (no time zone in the file), response_deadline (UTC; a date-only deadline counts as midnight UTC),
  archive_date, set_aside_code, naics, psc, department, cgac (department code), sub_tier, fpds_code (sub-tier agency code),
  aac_code (buying office code; the office NAME is left out), award_number, award_date, award_amount, kw_brand, kw_sole,
  title_len (characters in the original title; the title itself is not kept).

The solicitations table (one row per sol_norm; built the same way per year and, with --merge, across all years)
  sol_norm, sol_number (as written on the first version), first_posted (earliest PostedDate), last_posted,
  first_deadline (deadline on the earliest version), final_deadline (deadline on the latest version that gives one),
  n_versions, n_deadlines (distinct deadlines), type_first, base_type_first, set_aside_code, naics, psc, department, cgac,
  sub_tier, fpds_code, aac_code, kw_brand, kw_sole (all from the first version), n_offices and n_departments (distinct
  values over the versions; above 1 can mean two buyers used the same number), has_presol (a Presolicitation or Sources
  Sought notice with the same sol_norm, or a version whose base type is one of those), has_award_notice, award_number and
  award_date (from the earliest award notice), window_first_days (first_deadline minus first_posted, in days),
  window_final_days (final_deadline minus first_posted), first_file_fy, last_file_fy.

Privacy (hard rules, same as script 14)
  Never written: all PrimaryContact* / SecondaryContact* columns, Awardee, PopStreetAddress, Description, AdditionalInfoLink,
  Link, the Title text and the Office name.  A whitelist decides what is kept, so a new SAM column is dropped automatically.
  E-mail addresses and phone numbers inside Sol# and award numbers are masked as [email] / [phone].

Memory
  About 400,000 notices a year fit in a few hundred MB.  --merge never loads all years at once: it splits the notices by a
  hash of sol_norm into 16 parts on disk and builds one part at a time.

Usage (from the repository root)
  python -I scripts/22_sam_archive.py --list                    # every archive file with its size
  python -I scripts/22_sam_archive.py 2024                      # build the FY2024 tables
  python -I scripts/22_sam_archive.py 2024 --max-bytes 150000000
        # TESTING ONLY: read just the first N bytes (an HTTP Range request) and drop the last partial row.  The file is sorted
        # newest first, so this gives the notices posted at the END of the fiscal year.  The JSON says "partial": true.
  python -I scripts/22_sam_archive.py 2024 --from-file PATH.csv # use a local CSV (not deleted)
  python -I scripts/22_sam_archive.py --merge                   # all notices_FY*.parquet -> solicitations_all.parquet
Set PIO_CONTACT (a web address, never an e-mail) so the User-Agent says who is downloading.
"""
import argparse
import csv
import glob
import json
import os
import re
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import warnings
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA, UA_SAM, http_json  # noqa: E402

# the reader, header check, scrub and cleaning of SAM.gov's CSV files (moved from script 14 to sam_csv.py in November 2026)
import sam_csv as s14  # noqa: E402

LIST_URL = ("https://sam.gov/api/prod/fileextractservices/v1/api/listfiles?"
            "domain=Contract%20Opportunities/Archived%20Data&privacy=Public")
S3 = "https://s3.amazonaws.com/falextracts/"
KEY = "Contract Opportunities/Archived Data/FY{fy}_archived_opportunities.csv"
MAX_BYTES = 8_000_000_000
CHUNK_ROWS = 10_000
OUT_DIR = os.path.join(DATA, "sam_archive")
N_PARTS = 16

SOLICITATION_TYPES = s14.SOLICITATION_TYPES            # ("Solicitation", "Combined Synopsis/Solicitation")
PRESOL_TYPES = ("Presolicitation", "Sources Sought")
AWARD_TYPE = "Award Notice"

# columns of the notices table, in order.  Everything else that script 14's cleaning returns is dropped here.
NOTICE_COLS = ["notice_id", "file_fy", "sol_number", "sol_norm", "type", "base_type", "archive_type", "posted_date",
               "response_deadline", "archive_date", "set_aside_code", "naics", "psc", "department", "cgac", "sub_tier",
               "fpds_code", "aac_code", "award_number", "award_date", "award_amount", "kw_brand", "kw_sole", "title_len"]
CATEGORICAL = ["type", "base_type", "archive_type", "set_aside_code", "naics", "psc", "department", "cgac", "sub_tier",
               "fpds_code", "aac_code"]
FIRST_COLS = ["sol_number", "type", "base_type", "set_aside_code", "naics", "psc", "department", "cgac", "sub_tier",
              "fpds_code", "aac_code", "kw_brand", "kw_sole"]
SOL_COLS = ["sol_norm", "sol_number", "first_posted", "last_posted", "first_deadline", "final_deadline", "n_versions",
            "n_deadlines", "type_first", "base_type_first", "set_aside_code", "naics", "psc", "department", "cgac",
            "sub_tier", "fpds_code", "aac_code", "kw_brand", "kw_sole", "n_offices", "n_departments", "has_presol",
            "has_award_notice", "award_number", "award_date", "window_first_days", "window_final_days",
            "first_file_fy", "last_file_fy"]
# columns the merge needs from each yearly notices file
MERGE_COLS = ["notice_id", "file_fy", "sol_number", "sol_norm", "type", "base_type", "posted_date", "response_deadline",
              "set_aside_code", "naics", "psc", "department", "cgac", "sub_tier", "fpds_code", "aac_code",
              "award_number", "award_date", "kw_brand", "kw_sole"]

log = s14.log


# ------------------------------------------------------------------------------------------------------------------
# small helpers
# ------------------------------------------------------------------------------------------------------------------
def file_url(fy):
    return S3 + urllib.parse.quote(KEY.format(fy=fy))


def norm_sol(s):
    """Sol# -> upper-case letters and digits only (W912NW-26-q-a027 -> W912NW26QA027).  Empty results become missing."""
    s = pd.Series(s, dtype="string").str.upper().str.replace(r"[^A-Z0-9]+", "", regex=True)
    return s.mask(s == "")


def fiscal_year(ts):
    """Federal fiscal year of each timestamp (October to September; FY2024 = 2023-10-01 ... 2024-09-30)."""
    y, m = ts.dt.year.astype("Int64"), ts.dt.month.astype("Int64")
    return y + (m >= 10).astype("Int64")


def write_json(path, obj):
    s14.write_json(path, obj)


def write_parquet(df, path):
    df.to_parquet(path + ".tmp", engine="pyarrow", compression="zstd", compression_level=9, index=False)
    os.replace(path + ".tmp", path)


def share(mask):
    n = len(mask)
    return round(float(mask.sum()) / n, 4) if n else None


def iso(x):
    return None if x is None or pd.isna(x) else pd.Timestamp(x).isoformat()


# ------------------------------------------------------------------------------------------------------------------
# listing and download
# ------------------------------------------------------------------------------------------------------------------
def head(url):
    """(size in bytes, Last-Modified text) of one S3 object, or (None, None) if it is missing."""
    req = urllib.request.Request(url, headers={"User-Agent": UA_SAM}, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return int(r.headers.get("Content-Length") or 0), r.headers.get("Last-Modified")
    except urllib.error.HTTPError as e:
        if e.code in (403, 404):
            return None, None
        raise


def list_files():
    d = http_json(LIST_URL, ua=UA_SAM, headers={"Accept": "*/*"})
    if not d:
        sys.exit("STOP: the SAM.gov listing API did not answer")
    rows = []
    for x in d.get("_embedded", {}).get("customS3ObjectSummaryList", []):
        size, lm = head(S3 + urllib.parse.quote(x["key"]))
        rows.append((x["displayKey"], size, lm))
        time.sleep(0.2)                                          # polite pacing
    log(f"{'file':40s} {'MB':>10s}  last modified")
    for name, size, lm in rows:
        log(f"{name:40s} {(size or 0) / 1e6:10,.1f}  {lm}")
    log(f"{len(rows)} files, {sum(r[1] or 0 for r in rows) / 1e9:,.2f} GB in total")
    return rows


_RECORD_START = re.compile(rb'\n"[0-9a-f]{32}","')


def trim_partial(path):
    """After a partial (Range) download, cut the file after the last COMPLETE row.  A row may hold line breaks inside quoted
    text, so we look for the start of a row (newline, quote, 32 hex digits NoticeId, quote, comma) instead of a newline."""
    size = os.path.getsize(path)
    look = 4 << 20
    while True:
        start = max(0, size - look)
        with open(path, "rb") as fh:
            fh.seek(start)
            tail = fh.read()
        hits = list(_RECORD_START.finditer(tail))
        if hits:
            cut = start + hits[-1].start() + 1                      # keep the newline that ends the last full row
            with open(path, "r+b") as fh:
                fh.truncate(cut)
            return size - cut
        if start == 0:
            raise RuntimeError("no complete row in the partial download")
        look *= 4


def stream_download(url, dest, max_bytes=None, cap=MAX_BYTES, retries=4):
    """Stream url to dest in 1 MB pieces.  On a broken connection, resume with a Range request.
    max_bytes (testing): read only the first max_bytes bytes, then drop the last partial row.
    Returns (bytes kept, total size of the remote file, Last-Modified text)."""
    total, last_mod = head(url)
    if total is None:
        sys.exit(f"STOP: no such file on S3: {url}")
    want = min(total, max_bytes) if max_bytes else total
    if want > cap:
        raise RuntimeError(f"file is {total:,} bytes, above the {cap:,} cap")
    got, last = 0, None
    open(dest, "wb").close()
    for attempt in range(retries):
        try:
            hdrs = {"User-Agent": UA_SAM}
            if got or want < total:
                hdrs["Range"] = f"bytes={got}-{want - 1}"
            with urllib.request.urlopen(urllib.request.Request(url, headers=hdrs), timeout=120) as r:
                if "Range" in hdrs and r.status != 206:              # the server ignored the range: start over
                    got = 0
                    open(dest, "wb").close()
                with open(dest, "ab") as fh:
                    while got < want:
                        buf = r.read(min(1 << 20, want - got))
                        if not buf:
                            break
                        fh.write(buf)
                        got += len(buf)
            if got >= want:
                break
            raise RuntimeError(f"connection ended at {got:,} of {want:,} bytes")
        except Exception as e:
            last = e
            log(f"  download attempt {attempt + 1} stopped at {got / 1e6:,.1f} MB: {e!r}"[:200])
            time.sleep(5 * (attempt + 1))
    if got < want:
        raise RuntimeError(f"download failed after {retries} attempts: {last!r}")
    if want < total:
        dropped = trim_partial(dest)
        log(f"  partial read: kept {os.path.getsize(dest) / 1e6:,.1f} MB, dropped a {dropped:,}-byte partial row")
    return os.path.getsize(dest), total, last_mod


# ------------------------------------------------------------------------------------------------------------------
# reading one yearly file
# ------------------------------------------------------------------------------------------------------------------
def clean_archive_chunk(ch, fy):
    """Raw chunk (all strings) -> rows of the notices table.  Uses script 14's clean_chunk (whitelist, keyword flags from
    Title + Description) and finish_types (dates, amounts), then keeps only NOTICE_COLS."""
    ch = ch.copy()
    ch["Award$"] = ch["Award$"].str.replace("$", "", regex=False)       # older years write "$22,612.62"
    title_len = ch["Title"].str.len()
    c = s14.clean_chunk(ch)
    del ch
    c["title_len"] = title_len.astype("Int32")
    c, notes = s14.finish_types(c, date(2000, 1, 1))
    c["file_fy"] = np.int16(fy)
    for col in ("sol_number", "award_number"):
        c[col] = c[col].astype("string").map(s14.scrub).astype("string")
    c["sol_norm"] = norm_sol(c["sol_number"]).values
    for col in CATEGORICAL:
        c[col] = c[col].astype("string")                               # categories are rebuilt once for the whole year
    return c[NOTICE_COLS], notes


def read_archive(path, fy):
    """Read one archive CSV in chunks.  Returns (notices frame, notes dict)."""
    cols, extra = s14.check_header(path)
    parts, n_raw, notes = [], 0, {}
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        stream = s14.MixedTextStream(path)
        reader = pd.read_csv(stream, usecols=s14.USECOLS, dtype=str, na_values=[""], keep_default_na=False,
                             chunksize=CHUNK_ROWS, on_bad_lines="warn", quoting=csv.QUOTE_MINIMAL)
        for i, ch in enumerate(reader):
            n_raw += len(ch)
            c, n = clean_archive_chunk(ch, fy)
            del ch
            for k, v in n.items():
                notes[k] = notes.get(k, 0) + int(v)
            parts.append(c)
            if i % 10 == 9:
                log(f"  read {n_raw:,} rows")
    stream.close()
    n_bad = sum(1 for w in caught if "Skipping line" in str(w.message))
    if not parts:
        sys.exit("STOP: no rows read")
    df = pd.concat(parts, ignore_index=True)
    del parts
    n0 = len(df)
    df = df.drop_duplicates("notice_id", keep="first")
    notes["duplicate_notice_ids_dropped"] = notes.get("duplicate_notice_ids_dropped", 0) + n0 - len(df)
    for col in CATEGORICAL:
        df[col] = df[col].astype("category")
    df = df.sort_values(["sol_norm", "posted_date", "notice_id"], na_position="last").reset_index(drop=True)
    notes.update({
        "columns_in_file": len(cols),
        "same_47_columns_as_active_file": len(cols) == 47 and not extra,
        "extra_columns_ignored": extra,
        "raw_rows_read": int(n_raw),
        "malformed_rows_skipped": int(n_bad),
        "encoding": (f"decoded as UTF-8 with cp1252 fallback: {stream.n_utf8:,} valid UTF-8 multi-byte sequences, "
                     f"{stream.n_stray:,} stray bytes decoded as cp1252"),
    })
    return df, notes


# ------------------------------------------------------------------------------------------------------------------
# solicitation chains
# ------------------------------------------------------------------------------------------------------------------
def build_solicitations(nt):
    """Notices (any years) -> one row per sol_norm among solicitation-type notices.  Columns: SOL_COLS."""
    has_sol = nt["sol_norm"].notna()
    typ = nt["type"].astype("string")
    base = nt["base_type"].astype("string")
    s = nt[has_sol & typ.isin(SOLICITATION_TYPES)]
    s = s.sort_values(["sol_norm", "posted_date", "notice_id"], na_position="last")
    if s.empty:
        return pd.DataFrame({c: pd.Series(dtype="object") for c in SOL_COLS})
    g = s.groupby("sol_norm", sort=True)
    first = g.head(1).set_index("sol_norm")
    out = pd.DataFrame(index=first.index)
    out["sol_number"] = first["sol_number"]
    out["first_posted"] = first["posted_date"]
    out["last_posted"] = g["posted_date"].max()
    out["first_deadline"] = first["response_deadline"]
    out["final_deadline"] = g["response_deadline"].last()             # last non-missing, in posting order
    out["n_versions"] = g.size().astype("int32")
    out["n_deadlines"] = g["response_deadline"].nunique().astype("int32")
    out["type_first"] = first["type"].astype("string")
    out["base_type_first"] = first["base_type"].astype("string")
    for c in FIRST_COLS[3:]:
        out[c] = first[c]
    out["n_offices"] = g["aac_code"].nunique().astype("int16")
    out["n_departments"] = g["department"].nunique().astype("int16")
    presol = set(nt.loc[has_sol & (typ.isin(PRESOL_TYPES) | base.isin(PRESOL_TYPES)), "sol_norm"].dropna())
    out["has_presol"] = out.index.isin(list(presol)).astype("int8")
    aw = nt[has_sol & (typ == AWARD_TYPE)].sort_values(["sol_norm", "posted_date", "notice_id"], na_position="last")
    aw = aw.drop_duplicates("sol_norm").set_index("sol_norm")
    out["has_award_notice"] = out.index.isin(aw.index).astype("int8")
    out["award_number"] = aw["award_number"].reindex(out.index).astype("string")
    out["award_date"] = aw["award_date"].reindex(out.index)
    posted_utc = out["first_posted"].dt.tz_localize("UTC")
    day = pd.Timedelta(days=1)
    out["window_first_days"] = ((out["first_deadline"] - posted_utc) / day).round(3).astype("float32")
    out["window_final_days"] = ((out["final_deadline"] - posted_utc) / day).round(3).astype("float32")
    out["first_file_fy"] = g["file_fy"].min().astype("int16")
    out["last_file_fy"] = g["file_fy"].max().astype("int16")
    out = out.reset_index()
    for c in ("set_aside_code", "naics", "psc", "department", "cgac", "sub_tier", "fpds_code", "aac_code"):
        out[c] = out[c].astype("string")
    out["sol_norm"] = out["sol_norm"].astype("string")
    out["sol_number"] = out["sol_number"].astype("string")
    for c in ("kw_brand", "kw_sole"):
        out[c] = out[c].astype("int8")
    return out[SOL_COLS]


def window_ok(w):
    """A usable response window: present and between 0 and 365 days."""
    return w.notna() & (w > 0) & (w <= 365)


def solicitation_quality(sol):
    n = len(sol)
    if not n:
        return {"n_solicitations": 0}
    wf, wl = sol["window_first_days"], sol["window_final_days"]
    q = {
        "n_solicitations": int(n),
        "share_first_deadline_missing": share(sol["first_deadline"].isna()),
        "share_usable_window_first": share(window_ok(wf)),
        "share_usable_window_final": share(window_ok(wl)),
        "usable_window_definition": "deadline present and 0 < (deadline - first PostedDate) <= 365 days",
        "median_window_first_days": None if wf[window_ok(wf)].empty else round(float(wf[window_ok(wf)].median()), 2),
        "share_window_first_not_positive": share(wf.notna() & (wf <= 0)),
        "share_window_first_over_365": share(wf > 365),
        "share_more_than_one_version": share(sol["n_versions"] > 1),
        "share_deadline_changed": share(sol["n_deadlines"] > 1),
        "share_has_presol": share(sol["has_presol"] == 1),
        "share_has_award_notice": share(sol["has_award_notice"] == 1),
        "share_more_than_one_office": share(sol["n_offices"] > 1),
        "share_spanning_two_or_more_files": share(sol["last_file_fy"] > sol["first_file_fy"]),
    }
    if "type_first" in sol:
        q["type_first"] = {str(k): int(v) for k, v in sol["type_first"].value_counts(dropna=False).items()}
    if "sol_norm" in sol:
        q["largest_chains"] = [{"sol_norm": r.sol_norm, "n_versions": int(r.n_versions), "n_offices": int(r.n_offices)}
                               for r in sol.nlargest(10, "n_versions").itertuples()]
    return q


def notice_quality(nt, fy):
    pfy, afy = fiscal_year(nt["posted_date"]), fiscal_year(nt["archive_date"])
    by_pfy = pfy.value_counts(dropna=False).sort_index()
    return {
        "rows": int(len(nt)),
        "by_type": {str(k): int(v) for k, v in nt["type"].astype("string").fillna("(blank)").value_counts().items()},
        "share_with_sol_number": share(nt["sol_norm"].notna()),
        "posted_date_range": [iso(nt["posted_date"].min()), iso(nt["posted_date"].max())],
        "archive_date_range": [iso(nt["archive_date"].min()), iso(nt["archive_date"].max())],
        "fiscal_year_definition": {
            "share_posted_in_file_fy": share(pfy == fy),
            "share_archived_in_file_fy": share(afy == fy),
            "rows_by_posted_fy": {str(k): int(v) for k, v in by_pfy.items()},
            "conclusion": ("by PostedDate" if share(pfy == fy) and share(pfy == fy) > max(0.95, share(afy == fy) or 0)
                           else "unclear, see shares"),
        },
        "share_with_aac_code": share(nt["aac_code"].notna()),
        "share_with_response_deadline": share(nt["response_deadline"].notna()),
        "kw_brand_rows": int(nt["kw_brand"].sum()),
        "kw_sole_rows": int(nt["kw_sole"].sum()),
    }


def process_file(path, fy):
    """One archive CSV -> (notices, solicitations, quality dict).  Writes nothing."""
    nt, notes = read_archive(path, fy)
    sol = build_solicitations(nt)
    q = {"notices": notice_quality(nt, fy), "solicitations": solicitation_quality(sol), "reading": notes}
    return nt, sol, q


def run_year(fy, from_file=None, max_bytes=None, out_dir=OUT_DIR):
    t0 = time.time()
    os.makedirs(out_dir, exist_ok=True)
    tmpdir, total, last_mod = None, None, None
    try:
        if from_file:
            path = from_file
            log(f"using local file {path} ({os.path.getsize(path) / 1e6:,.1f} MB)")
        else:
            tmpdir = tempfile.mkdtemp(prefix="sam_archive_")            # system temp, outside the repository
            path = os.path.join(tmpdir, f"FY{fy}.csv")
            url = file_url(fy)
            log("downloading", url, f"(first {max_bytes:,} bytes only)" if max_bytes else "")
            n, total, last_mod = stream_download(url, path, max_bytes=max_bytes)
            log(f"downloaded {n / 1e6:,.1f} MB of {total / 1e6:,.1f} MB in {time.time() - t0:,.0f} s")
        t1 = time.time()
        nt, sol, q = process_file(path, fy)
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)
            tmpdir = None
        log(f"{len(nt):,} notices, {len(sol):,} solicitations; processing took {time.time() - t1:,.0f} s")
        p_n = os.path.join(out_dir, f"notices_FY{fy}.parquet")
        p_s = os.path.join(out_dir, f"solicitations_FY{fy}.parquet")
        write_parquet(nt, p_n)
        write_parquet(sol, p_s)
        pm = s14.peak_mb()
        meta = {
            "fiscal_year": fy,
            "built_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": from_file and "local file" or file_url(fy),
            "source_last_modified": last_mod,
            "source_bytes": total,
            "partial": bool(max_bytes and total and max_bytes < total),
            "partial_note": ("TEST RUN: only the first bytes were read; the file is sorted newest first, so this covers the "
                             "end of the fiscal year only") if (max_bytes and total and max_bytes < total) else None,
            "bytes_read": os.path.getsize(path) if from_file else None,
            "seconds": round(time.time() - t0, 1),
            "peak_memory_mb": round(pm) if pm else None,
            **q,
            "files": {os.path.basename(p): os.path.getsize(p) for p in (p_n, p_s)},
        }
        if not from_file:
            meta["bytes_read"] = n
        write_json(os.path.join(out_dir, f"sam_archive_FY{fy}.json"), meta)
        log(f"wrote {p_n} ({os.path.getsize(p_n) / 1e6:,.1f} MB), {p_s} ({os.path.getsize(p_s) / 1e6:,.1f} MB) and the JSON")
        sq = q["solicitations"]
        log(f"usable response window (first deadline): {sq.get('share_usable_window_first')}; "
            f"fiscal year: {q['notices']['fiscal_year_definition']['conclusion']}")
        log(f"done in {time.time() - t0:,.0f} s" + (f"; peak memory {pm:,.0f} MB" if pm else ""))
        return meta
    finally:
        if tmpdir and os.path.isdir(tmpdir):
            shutil.rmtree(tmpdir, ignore_errors=True)


# ------------------------------------------------------------------------------------------------------------------
# merge across years
# ------------------------------------------------------------------------------------------------------------------
def _part_of(sol_norm, n_parts):
    return (pd.util.hash_array(sol_norm.astype(str).to_numpy(), categorize=True) % np.uint64(n_parts)).astype("int16")


def _arrow(df, schema=None):
    t = pa.Table.from_pandas(df, preserve_index=False)
    return t.cast(schema) if schema is not None else t


def merge(in_dir=OUT_DIR, out_path=None, n_parts=N_PARTS):
    """All notices_FY*.parquet in in_dir -> one solicitations table with chains resolved across years.
    Memory-safe: rows are split by a hash of sol_norm into n_parts files in a temp folder, then built one part at a time."""
    t0 = time.time()
    out_path = out_path or os.path.join(in_dir, "solicitations_all.parquet")
    files = sorted(glob.glob(os.path.join(in_dir, "notices_FY*.parquet")))
    if not files:
        sys.exit(f"STOP: no notices_FY*.parquet in {in_dir}")
    keep_types = list(SOLICITATION_TYPES) + list(PRESOL_TYPES) + [AWARD_TYPE]
    tmpdir = tempfile.mkdtemp(prefix="sam_archive_merge_")
    writer, schema, n_out, n_in = None, None, 0, 0
    parts_out = []
    try:
        for f in files:
            pf = pq.ParquetFile(f)
            for rg in range(pf.num_row_groups):                         # one row group at a time
                d = pf.read_row_group(rg, columns=MERGE_COLS).to_pandas()
                n_in += len(d)
                typ, base = d["type"].astype("string"), d["base_type"].astype("string")
                d = d[d["sol_norm"].notna() & (typ.isin(keep_types) | base.isin(PRESOL_TYPES))]
                for c in CATEGORICAL:
                    if c in d:
                        d[c] = d[c].astype("string")
                part = _part_of(d["sol_norm"], n_parts)
                for p in np.unique(part):
                    os.makedirs(os.path.join(tmpdir, str(p)), exist_ok=True)
                    d[part == p].to_parquet(os.path.join(tmpdir, str(p), f"{os.path.basename(f)}.{rg}.parquet"),
                                            index=False)
                del d
            log(f"  split {os.path.basename(f)}")
        tmp_out = out_path + ".tmp"
        for p in range(n_parts):
            pieces = sorted(glob.glob(os.path.join(tmpdir, str(p), "*.parquet")))
            if not pieces:
                continue
            d = pd.concat([pd.read_parquet(x) for x in pieces], ignore_index=True)
            d = d.drop_duplicates("notice_id", keep="first")
            sol = build_solicitations(d)
            del d
            if sol.empty:
                continue
            t = _arrow(sol, schema)
            if writer is None:
                schema = t.schema
                writer = pq.ParquetWriter(tmp_out, schema, compression="zstd", compression_level=9)
            writer.write_table(t)
            n_out += len(sol)
            parts_out.append(sol[["window_first_days", "window_final_days", "first_deadline", "n_versions",
                                  "n_deadlines", "has_presol", "has_award_notice", "n_offices", "first_file_fy",
                                  "last_file_fy"]])
            del sol, t
        if writer is None:
            sys.exit("STOP: no solicitation-type notices with a Sol# in the yearly files")
        writer.close()
        writer = None
        os.replace(tmp_out, out_path)
    finally:
        if writer is not None:
            writer.close()
        shutil.rmtree(tmpdir, ignore_errors=True)
    allq = pd.concat(parts_out, ignore_index=True)
    q = solicitation_quality(allq)
    meta = {"built_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "yearly_files": [os.path.basename(f) for f in files], "notice_rows_read": int(n_in),
            "rows": int(n_out), "solicitations": q, "seconds": round(time.time() - t0, 1)}
    write_json(os.path.join(os.path.dirname(out_path), "sam_archive_all.json"), meta)
    pm = s14.peak_mb()
    log(f"wrote {out_path}: {n_out:,} solicitations from {len(files)} yearly files in {time.time() - t0:,.0f} s"
        + (f"; peak memory {pm:,.0f} MB" if pm else ""))
    return meta


# ------------------------------------------------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Build public tables from SAM.gov's yearly archive files (see the file header).")
    ap.add_argument("fy", nargs="?", type=int, help="fiscal year of the archive file, e.g. 2024")
    ap.add_argument("--list", action="store_true", help="print every archive file with its size")
    ap.add_argument("--merge", action="store_true", help="join all notices_FY*.parquet into solicitations_all.parquet")
    ap.add_argument("--max-bytes", type=int, help="TESTING: read only the first N bytes of the file (HTTP Range request)")
    ap.add_argument("--from-file", help="use this local CSV instead of downloading (not deleted)")
    ap.add_argument("--out-dir", default=OUT_DIR, help=f"output folder (default {OUT_DIR})")
    args = ap.parse_args()
    if args.list:
        list_files()
    elif args.merge:
        merge(args.out_dir)
    elif args.fy:
        run_year(args.fy, from_file=args.from_file, max_bytes=args.max_bytes, out_dir=args.out_dir)
    else:
        ap.error("give a fiscal year, --list or --merge")


if __name__ == "__main__":
    main()
