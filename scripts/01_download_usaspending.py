"""
01_download_usaspending.py - pull contract TRANSACTIONS for a few civilian agencies from USAspending.gov.
It also holds fetch_verified(), the ONE function every script in this project uses to get transactions from USAspending's
download service (01b, 01c, 09, 12, 13, 15, 17, 32 all go through it).

How it works (no key, no account):
  1. POST https://api.usaspending.gov/api/v2/download/transactions/   (the same service the website's
     "Advanced Search > Download" button uses).  We pass:
        filters : awarding agency + award types + a date window (action_date)
        columns : ONLY the ~35 columns we need  -> files are ~100x smaller than the 297-column bulk files
  2. Poll   GET /api/v2/download/status?file_name=...   until it says "finished"
  3. Download the ZIP from files.usaspending.gov/generated_downloads/...
  4. VERIFY: count the CSV rows inside the ZIP and ask USAspending's count service
     (POST /api/v2/search/spending_by_transaction_count/) for the same filters and dates.  The window is accepted only when the two
     agree (see TOLERANCE_ROWS and TOLERANCE_SHARE).  The download service sometimes returns a CUT-OFF file: status "finished",
     a ZIP that opens fine, but far fewer rows than the window holds (November 2026).  It also seems to hand back the same file for
     the same request for hours.  So a window that fails the check is split into smaller date ranges (new requests), and every
     piece is checked the same way.  See docs/download-integrity.md.

Award types:  B = purchase order, D = definitive contract, IDV_* = indefinite-delivery vehicles
(IDV_A GWAC, IDV_B IDC, IDV_B_A/B/C IDC subtypes, IDV_C FSS, IDV_D BOA, IDV_E BPA).
We skip A (BPA calls) and C (delivery orders): orders are mostly not solicited separately on SAM.gov
and number_of_offers_received is blank on ~90% of them (cycle 2 finding).

Every file is a "transaction" (a base award is the row with modification_number 0; later rows are mods).
The script is RESUMABLE: each window has a manifest_<window>.json next to its ZIP; verified windows are skipped.
Safety checks: each ZIP must be <= 25 MB and below the 500,000-row service cap (otherwise the window is split), and its row count
must match USAspending's own count.

Usage:  python -I scripts/01_download_usaspending.py [agency-index ...]     (default: all agencies in common.AGENCIES)
"""
import calendar
import csv
import io
import json
import os
import sys
import time
import zipfile
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import AGENCIES, DATA, FISCAL_YEARS, UA_USASPENDING, download_file, http_json  # noqa: E402

OUT = os.path.join(DATA, "usaspending")
os.makedirs(OUT, exist_ok=True)

API = "https://api.usaspending.gov/api/v2"
AWARD_TYPES = ["B", "D", "IDV_A", "IDV_B", "IDV_B_A", "IDV_B_B", "IDV_B_C", "IDV_C", "IDV_D", "IDV_E"]

COLUMNS = [
    "contract_transaction_unique_key", "contract_award_unique_key", "award_id_piid", "parent_award_id_piid",
    "modification_number", "action_date", "action_type_code", "award_type_code", "award_or_idv_flag",
    "federal_action_obligation", "base_and_all_options_value", "base_and_exercised_options_value",
    "awarding_agency_name", "awarding_sub_agency_name", "awarding_office_code", "awarding_office_name",
    "solicitation_identifier", "solicitation_date",
    "product_or_service_code", "naics_code",
    "extent_competed_code", "solicitation_procedures_code", "type_of_set_aside_code", "number_of_offers_received",
    "fair_opportunity_limited_sources_code", "other_than_full_and_open_competition_code",
    "type_of_contract_pricing_code", "commercial_item_acquisition_procedures_code",
    "multiple_or_single_award_idv_code", "idv_type_code",
    "recipient_uei", "recipient_parent_uei",
    "period_of_performance_start_date", "period_of_performance_potential_end_date",
    "purchase_card_as_payment_method_code", "cost_or_pricing_data_code",
]

# Quarter windows covering the analysis years in config.toml (US fiscal year starts 1 October)
WINDOWS = []
for fy in FISCAL_YEARS:
    WINDOWS += [
        (f"FY{fy}Q1", f"{fy-1}-10-01", f"{fy-1}-12-31"),
        (f"FY{fy}Q2", f"{fy}-01-01", f"{fy}-03-31"),
        (f"FY{fy}Q3", f"{fy}-04-01", f"{fy}-06-30"),
        (f"FY{fy}Q4", f"{fy}-07-01", f"{fy}-09-30"),
    ]


# =====================================================================================================================
# Verified downloads (shared by every script that uses the download service)
# =====================================================================================================================
COUNT_URL = f"{API}/search/spending_by_transaction_count/"
ROW_CAP = 499_000            # the service stops a file at 500,000 rows: at or above this the window is split
SIZE_CAP_KB = 25_000         # our 25 MB rule per file
# How far the rows in a file may differ from USAspending's count and still be accepted. USAspending loads new and corrected
# records every day, so a file made a few minutes (or, when the service hands back a stored file, a few hours) before the count
# can differ by a handful of rows. Good windows matched to the exact row in November 2026; cut-off files were short by 1.2 to 53
# percent. So: at most 5 rows, or 0.05 percent of the count, whichever is larger (65 rows on a 130,000-row quarter).
TOLERANCE_ROWS = 5
TOLERANCE_SHARE = 0.0005
MISMATCH_SPLITS = 2          # a window that fails the check is split; a piece that fails again is split once more; then we stop
WAIT_SECONDS = 1800          # how long to wait for the service to make one file
POLL_SECONDS = 10
PAUSE_SECONDS = 3            # pause between requests (be polite)


def tx_filters(start, end, agencies, award_types=None):
    """the filters of a transaction download and of the matching count request"""
    return {"award_type_codes": list(award_types or AWARD_TYPES),
            "agencies": [{"type": "awarding", "tier": "toptier", "name": n} for n in agencies],
            "time_period": [{"start_date": start, "end_date": end, "date_type": "action_date"}]}


def months(start, end):
    """the calendar-month windows inside start..end (YYYY-MM-DD), the last one cut at `end`"""
    out, d, last = [], date.fromisoformat(start), date.fromisoformat(end)
    while d <= last:
        month_end = date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])
        out.append((d.isoformat(), min(month_end, last).isoformat()))
        d = month_end + timedelta(days=1)
    return out


def halves(start, end):
    """split start..end into two halves; None if it is a single day"""
    a, b = date.fromisoformat(start), date.fromisoformat(end)
    if a >= b:
        return None
    mid = a + (b - a) // 2
    return (a.isoformat(), mid.isoformat()), ((mid + timedelta(days=1)).isoformat(), b.isoformat())


def pieces(label, start, end):
    """the smaller windows a window is split into: its calendar months (labels <label>_m1, _m2, ...) when it spans more than one
    month, otherwise two halves (<label>a, <label>b). None for a single day. The pieces always cover the window exactly once."""
    m = months(start, end)
    if len(m) > 1:
        return [[f"{label}_m{k}", s, e] for k, (s, e) in enumerate(m, 1)]
    h = halves(start, end)
    return None if h is None else [[label + "a", *h[0]], [label + "b", *h[1]]]


def partitions(start, end, parts):
    """True when the pieces [label, start, end] cover start..end exactly once, in order (no gap, no overlap)"""
    if not parts:
        return False
    d = date.fromisoformat(start)
    for _, s, e in parts:
        if date.fromisoformat(s) != d or date.fromisoformat(e) < date.fromisoformat(s):
            return False
        d = date.fromisoformat(e) + timedelta(days=1)
    return d == date.fromisoformat(end) + timedelta(days=1)


def allowed_difference(expected):
    return max(TOLERANCE_ROWS, TOLERANCE_SHARE * expected)


def counts_agree(expected, received):
    """the verification rule: both counts known and at most max(5 rows, 0.05 percent) apart"""
    return expected is not None and received is not None and abs(received - expected) <= allowed_difference(expected)


def expected_rows(filters):
    """USAspending's own number of transactions for these filters (all award categories added up), or None if it did not answer"""
    c = http_json(COUNT_URL, method="POST", body={"filters": filters}, ua=UA_USASPENDING, timeout=120)
    r = (c or {}).get("results")
    if not isinstance(r, dict):
        return None
    try:
        return int(sum(int(v or 0) for v in r.values()))
    except (TypeError, ValueError):
        return None


def rows_in_zip(path):
    """the number of data rows in the transaction CSV files of a download ZIP (headers and blank lines not counted). The csv
    module is used, so a line break inside a quoted field does not count as a row. Reads the file as a stream (little memory)."""
    n = 0
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            if "Transactions" in name and name.endswith(".csv"):
                with z.open(name) as fh:
                    r = csv.reader(io.TextIOWrapper(fh, encoding="utf-8", newline=""))
                    if next(r, None) is None:
                        continue
                    n += sum(1 for row in r if row)
    return n


def request_file(body):
    """submit a download request and wait until the service has made the file: (response, status) """
    resp = http_json(f"{API}/download/transactions/", method="POST", body=body, ua=UA_USASPENDING)
    if not resp or "file_name" not in resp:
        return None, {"status": "submit failed"}
    t0 = time.monotonic()
    while True:
        st = http_json(f"{API}/download/status?file_name={resp['file_name']}", ua=UA_USASPENDING) or {}
        if st.get("status") in ("finished", "failed"):
            return resp, st
        if time.monotonic() - t0 > WAIT_SECONDS:
            return resp, {"status": "timeout"}
        time.sleep(POLL_SECONDS)


def manifest_file(out, label):
    return os.path.join(out, f"manifest_{label}.json")


def read_manifest(out, label):
    p = manifest_file(out, label)
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def write_manifest(out, label, rec):
    p = manifest_file(out, label)
    with open(p + ".tmp", "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=1)
    os.replace(p + ".tmp", p)


def old_pieces(out, label):
    """the pieces of a window split by an older version of the scripts (no 'pieces' list in its manifest): <label>a/<label>b
    (halves) or <label>_m1, _m2, ... (months), read from their manifests. None when there are none."""
    for names in ([label + "a", label + "b"], [f"{label}_m{k}" for k in range(1, 13)]):
        found = []
        for n in names:
            m = read_manifest(out, n)
            if not m or not m.get("start") or not m.get("end"):
                break
            found.append([n, m["start"], m["end"]])
        if len(found) >= 2:
            return found
    return None


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def fetch_verified(label, start, end, agencies, out, prefix="CIVILIAN", columns=None, award_types=None, force=False,
                   allow_unverified=False, _splits_left=MISMATCH_SPLITS):
    """Download the transactions of `agencies` dated start..end into out/<prefix>__<label>.zip and VERIFY the file: its CSV
    rows must match USAspending's own count for the same filters (counts_agree). Returns the labels that failed ([] = done).

    - A window that is too big (500,000-row cap, 25 MB) is split into smaller windows (`pieces`), each downloaded the same way.
    - A file whose row count does not match is deleted and the window is split into smaller windows, which are new requests
      (asking again for the same window can return the same stored, cut-off file). Pieces are checked the same way; after
      MISMATCH_SPLITS rounds of splitting the window is reported as failed.
    - When the count service does not answer, the window is NOT verified: it counts as failed, unless allow_unverified=True,
      in which case it is kept and its manifest says verified: false.
    - Resumable: a verified window is skipped; a ZIP already on disk without a verification is checked (not downloaded again);
      force=True downloads everything again.
    Each window (and piece) gets manifest_<label>.json with the service's answer and expected_rows / received_rows / verified.
    The pieces of a split window are listed in its manifest ("pieces"); they never overlap, so no row is counted twice."""
    kw = dict(prefix=prefix, columns=columns, award_types=award_types, force=force, allow_unverified=allow_unverified)
    old = read_manifest(out, label) or {}
    dest = os.path.join(out, f"{prefix}__{label}.zip" if prefix else f"{label}.zip")
    filters = tx_filters(start, end, agencies, award_types)
    if old.get("start") and (old.get("start"), old.get("end")) != (start, end):
        print(f"  {label}: the record on disk is for {old.get('start')}..{old.get('end')}: not used, asked again", flush=True)
        old = {}                     # a record of this label for other dates (an earlier, different split): never trust it or its file
        _remove(dest)

    def split(rec, splits_left):
        parts = rec.get("pieces")
        if not parts or not partitions(start, end, parts):
            parts = None if force else old_pieces(out, label)
            if not parts or not partitions(start, end, parts):      # pieces left by an older version must cover the window
                parts = pieces(label, start, end)
        if parts is None:
            rec.update(done=False, split=False)
            write_manifest(out, label, rec)
            print(f"  {label}: cannot be split further (a single day); FAILED", flush=True)
            return [label]
        rec.update(done=False, split=True, pieces=parts)
        write_manifest(out, label, rec)
        failed = []
        for p, s, e in parts:
            failed += fetch_verified(p, s, e, agencies, out, **dict(kw, _splits_left=splits_left))
        return failed

    if not force:
        if old.get("done") and old.get("verified"):
            return []
        if old.get("split"):
            return split(dict(old), old.get("splits_left", _splits_left))

    rec = {"window": label, "start": start, "end": end, "prefix": prefix}
    if not force and os.path.exists(dest):
        # downloaded earlier (older scripts, or the count service did not answer): check the file we have
        rec.update({k: old[k] for k in ("file_name", "status", "total_rows", "total_size_kb", "bytes", "downloaded_utc") if k in old})
        rec["path"] = os.path.basename(dest)
        print(f"[{time.strftime('%H:%M:%S')}] {label} {start}..{end}: checking the file already downloaded", flush=True)
    else:
        print(f"[{time.strftime('%H:%M:%S')}] {label} {start}..{end}", flush=True)
        resp, st = request_file({"filters": filters, "columns": columns or COLUMNS, "file_format": "csv"})
        rec.update(file_name=(resp or {}).get("file_name"), status=st.get("status"), total_rows=st.get("total_rows"),
                   total_size_kb=st.get("total_size"), service_seconds=st.get("seconds_elapsed"))
        time.sleep(PAUSE_SECONDS)
        if st.get("status") != "finished":
            rec.update(done=False, verified=False, note="the download service did not deliver a file")
            write_manifest(out, label, rec)
            print("  ", {k: rec.get(k) for k in ("status", "note")}, flush=True)
            return [label]
        if float(st.get("total_size") or 0) > SIZE_CAP_KB or int(st.get("total_rows") or 0) >= ROW_CAP:
            rec["note"] = "above the 25 MB / 500,000-row limits: split into smaller windows"
            print(f"   {st.get('total_rows')} rows, {st.get('total_size')} KB: too big, split", flush=True)
            return split(rec, _splits_left)
        _remove(dest)
        n = download_file(resp["file_url"], dest, ua=UA_USASPENDING)
        if not n:
            _remove(dest)
            rec.update(done=False, verified=False, note="the file could not be downloaded")
            write_manifest(out, label, rec)
            print("   the file could not be downloaded", flush=True)
            return [label]
        rec.update(bytes=n, path=os.path.basename(dest), downloaded_utc=_now())

    try:
        received = rows_in_zip(dest)
    except (zipfile.BadZipFile, OSError, csv.Error, UnicodeDecodeError, EOFError) as e:
        _remove(dest)
        rec.update(done=False, verified=False, path=None, note=f"the file is damaged ({e!r}); deleted")
        write_manifest(out, label, rec)
        print("  ", rec["note"], flush=True)
        return [label]
    expected = expected_rows(filters)
    time.sleep(PAUSE_SECONDS)
    rec.update(received_rows=received, expected_rows=expected, checked_utc=_now(),
               allowed_difference=None if expected is None else round(allowed_difference(expected), 1))
    if expected is None:
        rec.update(verified=False, done=bool(allow_unverified),
                   note="USAspending's count service did not answer: this file is NOT verified")
        write_manifest(out, label, rec)
        print(f"   {received:,} rows; NOT VERIFIED (the count service did not answer)", flush=True)
        return [] if allow_unverified else [label]
    if counts_agree(expected, received):
        rec.update(verified=True, done=True)
        rec.pop("note", None)
        write_manifest(out, label, rec)
        print(f"   verified: {received:,} rows (USAspending counts {expected:,})", flush=True)
        return []
    # the file is cut off (or out of date): never use it; ask again with smaller windows, which are different requests
    _remove(dest)
    rec.update(verified=False, path=None, rejected_utc=_now(),
               note=f"REJECTED: the file holds {received:,} rows but USAspending counts {expected:,} for the same filters")
    print(f"   {rec['note']}", flush=True)
    if _splits_left <= 0:
        rec.update(done=False, split=False)
        write_manifest(out, label, rec)
        print(f"  {label}: still not matching after {MISMATCH_SPLITS} rounds of splitting; FAILED", flush=True)
        return [label]
    rec["splits_left"] = _splits_left - 1
    return split(rec, _splits_left - 1)


def collect(out, label):
    """Follow a window's manifest (and the manifests of its pieces, if it was split) down to the files that hold its rows.
    Returns (leaves, problems): leaves = the manifests of the files to use, each with 'zip' = the file's full path;
    problems = plain-English reasons why the window is not complete and verified (empty when it is)."""
    m = read_manifest(out, label)
    if m is None:
        return [], [f"{label}: no download record (manifest_{label}.json)"]
    if m.get("split"):
        parts = m.get("pieces") or old_pieces(out, label)
        if not parts:
            return [], [f"{label}: split, but its pieces are unknown"]
        if m.get("start") and m.get("end") and not partitions(m["start"], m["end"], parts):
            return [], [f"{label}: its pieces do not cover the window exactly once"]
        leaves, problems = [], []
        for p, s, e in parts:
            pm = read_manifest(out, p) or {}
            if pm.get("start") and (pm.get("start"), pm.get("end")) != (s, e):     # the piece's own record must be for these dates
                problems.append(f"{p}: its record is for {pm.get('start')}..{pm.get('end')}, not {s}..{e}")
                continue
            a, b = collect(out, p)
            leaves += a
            problems += b
        return leaves, problems
    if not m.get("done"):
        return [], [f"{label}: not downloaded ({m.get('note') or m.get('status')})"]
    z = os.path.join(out, m.get("path") or "")
    if not m.get("path") or not os.path.exists(z):
        return [], [f"{label}: the file {m.get('path')} is missing"]
    leaf = dict(m, zip=z)
    if "verified" not in m:
        return [leaf], [f"{label}: no verification record (downloaded before November 2026)"]
    if not m["verified"]:
        return [leaf], [f"{label}: NOT verified ({m.get('note')})"]
    return [leaf], []


def verification(out, labels):
    """The verification of a set of top-level windows: (summary dict, leaves). The summary says verified true only when every
    window was downloaded completely and every file matched USAspending's count."""
    leaves, problems = [], []
    for label in labels:
        a, b = collect(out, label)
        leaves += a
        problems += b
    exp = [lf.get("expected_rows") for lf in leaves]
    rec = [lf.get("received_rows") for lf in leaves]
    summary = {
        "verified": bool(leaves) and not problems,
        "rule": f"each file's CSV rows must be within max({TOLERANCE_ROWS} rows, {TOLERANCE_SHARE * 100:.2f} percent) of "
                "USAspending's count (search/spending_by_transaction_count) for the same filters and dates",
        "expected_rows": sum(e for e in exp if e is not None) if leaves and None not in exp else None,
        "received_rows": sum(r for r in rec if r is not None) if leaves and None not in rec else None,
        "windows": [{"window": lf.get("window"), "start": lf.get("start"), "end": lf.get("end"), "file": lf.get("path"),
                     "expected_rows": lf.get("expected_rows"), "received_rows": lf.get("received_rows"),
                     "verified": lf.get("verified")} for lf in leaves],
        "problems": problems}
    return summary, leaves


# =====================================================================================================================
# This script's own download: the five departments, one agency at a time
# =====================================================================================================================
def main():
    idxs = [int(a) for a in sys.argv[1:] if a.isdigit()] or list(range(len(AGENCIES)))
    failed = []
    for ai in idxs:
        agency = AGENCIES[ai]
        safe = agency.replace(" ", "_")
        for label, start, end in WINDOWS:
            # files are named <agency>__<window>.zip, as before; each window has its own manifest_<agency>__<window>.json
            failed += fetch_verified(f"{safe}__{label}", start, end, [agency], OUT, prefix="")
    print("FAILED (rerun to retry):", failed if failed else "none", flush=True)
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
