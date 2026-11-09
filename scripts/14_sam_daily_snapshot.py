"""
14_sam_daily_snapshot.py - save a DAILY HISTORY of the open federal solicitations on SAM.gov.

Why: SAM.gov only shows what is active TODAY.  Once a notice is archived or amended, the earlier picture is gone.  By
saving one snapshot per day we build the history that SAM.gov does not keep (how many solicitations were open on a given
day, how long they stayed open, which ones disappeared without an award notice, ...).

Source (public, no key, no account): the "Contract Opportunities Full CSV" that SAM.gov publishes on Amazon S3
    https://s3.amazonaws.com/falextracts/Contract%20Opportunities/datagov/ContractOpportunitiesFullCSV.csv
It holds the ACTIVE notices only, about 216 MB, and is rebuilt every night around 04:30 UTC.  It has 47 columns; the
script checks the header and stops with a clear message if a column it needs has been renamed or removed.
(The CSV carries contact names, e-mails and phone numbers, a street address of the place of performance, the awardee name
and the full notice text.  None of those are ever written by this script - see "Privacy" below.)

What it does
  1. Reads the S3 object's Last-Modified header (UTC).  That date is the SNAPSHOT DATE (fallback: today, UTC).
  2. Streams the file to a temporary folder OUTSIDE the repository (size cap 600 MB, 3 attempts) and deletes it at the end,
     also when something fails.
  3. Reads it in chunks of 20,000 rows with pandas (never the whole raw file at once, because the long Description column makes
     the raw file much bigger than the cleaned table).  Only whitelisted columns are loaded.  The Description is used in
     memory for two keyword flags, kw_brand and kw_sole (common.kw_flags on Title + first 300 characters of Description),
     and then thrown away.
  4. Writes three outputs (all written to a temp name first, then moved into place, so a crash never leaves half a file):
       data/sam_snapshots/sam_active_<YYYY-MM-DD>.parquet   cleaned snapshot, one row per notice (git-ignored, zstd)
       results/sam_daily_counts.csv       one row per snapshot_date x department x notice type
       results/sam_daily_latest.json      small summary for the dashboard
  5. With --upload only: uploads the parquet to a DRAFT GitHub release tagged sam-snapshots-<YYYY-MM> (see below).

The counts file (results/sam_daily_counts.csv)
  snapshot_date        the date above
  department           the "Department/Ind.Agency" text as SAM writes it ("(blank)" if empty)
  type                 the notice Type ("Solicitation", "Award Notice", ...)
  n_active             notice rows in the file for that department and type
  n_sol_numbers        distinct Sol# values among them (an amended solicitation keeps its older notice ids, see below)
  n_posted_last_7d     rows whose PostedDate is on or after (snapshot_date - 7 days) at 00:00
  n_open               solicitation types only (blank for other types): rows whose ResponseDeadLine is on or after 00:00 UTC of
                       the snapshot date.  The file keeps solicitations after their deadline until they are archived, so
                       n_active is about 4 times n_open for solicitation types.
  n_deadline_next_7d   solicitation types only: rows with ResponseDeadLine in [snapshot date, +7 days) / [snapshot date, +30 days),
  n_deadline_next_30d  compared in UTC at 00:00.  Rows with no deadline are never counted in n_open or the deadline columns.
  Re-running for the same snapshot date REPLACES that date's rows; other dates are untouched.  Sorted by date, department, type.
  "Solicitation types" are exactly the Type strings "Solicitation" and "Combined Synopsis/Solicitation".

What PostedDate means (checked against the 2026-11-09 file: 70,329 active notices)
  PostedDate is the posting time of THAT NOTICE ID, i.e. of that version of the notice.  It is neither the Sol#'s original
  posting date nor a "last modified" stamp of the whole solicitation.  An amendment is published as a NEW notice id that
  carries the same Sol#, and the older versions stay in the file until their own ArchiveDate.  Evidence: 10,159 Sol# values
  (46,326 of 69,666 rows that have a Sol#) appear on several rows with different NoticeIds and different PostedDates;
  e.g. one Sol# has seven rows posted 2026-07-30 ... 2026-10-27 whose deadlines move 08-24 -> 10-12 -> 10-20 -> 11-02 and whose
  ArchiveDate moves with them.  So: the FIRST posting of a solicitation = the minimum PostedDate over its Sol# (only if the
  original version is still active), the CURRENT version = the maximum.  The column latest_version marks the newest row per
  Sol#.  n_posted_last_7d therefore counts "notices (new solicitations AND amendments) posted in the last 7 days".
  Confidence: high that each row is one version with its own PostedDate; moderate that the minimum PostedDate is the true
  first posting (original versions may already have been archived, which can bias it later).  A second daily snapshot will
  confirm: a NoticeId should keep the same PostedDate from day to day.
  PostedDate has no time zone in the file (it is stored as written); ResponseDeadLine usually carries a UTC offset and is
  stored as a UTC timestamp (a date-only deadline counts as midnight UTC).

Privacy (hard rules)
  Never written anywhere: all PrimaryContact* / SecondaryContact* columns (names, e-mails, phones, faxes), Awardee, PopStreetAddress,
  Description, AdditionalInfoLink, Link.  E-mail addresses and phone numbers that officers typed into a notice Title are
  masked as [email] / [phone].  Because columns are chosen from a whitelist, a new column added by SAM later is
  dropped automatically.

Robustness
  The real file mixes Windows cp1252 text (curly quotes, dashes) with some genuine UTF-8, so it is decoded as UTF-8 and every
  byte that is not valid UTF-8 falls back to cp1252 (MixedTextStream below); the counts are logged.  Rows with the wrong
  number of fields are skipped and counted, not fatal.

Usage (from the repository root):
  python -I scripts/14_sam_daily_snapshot.py                          # download, process, write outputs
  python -I scripts/14_sam_daily_snapshot.py --upload                 # also upload the parquet to a draft release (needs `gh`)
  python -I scripts/14_sam_daily_snapshot.py --from-file PATH.csv [--date YYYY-MM-DD]
                                   # local testing: use an already downloaded CSV (it is NOT deleted); the date defaults
                                   # to today (UTC) because a local file has no S3 Last-Modified header
Set PIO_CONTACT (e.g. the repository URL, never an e-mail) so the User-Agent says who is downloading.
"""
import argparse
import codecs
import csv
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import warnings
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA, RESULTS, UA_SAM, kw_flags  # noqa: E402

URL = "https://s3.amazonaws.com/falextracts/Contract%20Opportunities/datagov/ContractOpportunitiesFullCSV.csv"
MAX_BYTES = 600_000_000
CHUNK_ROWS = 20_000
SNAP_DIR = os.path.join(DATA, "sam_snapshots")
COUNTS_CSV = os.path.join(RESULTS, "sam_daily_counts.csv")
LATEST_JSON = os.path.join(RESULTS, "sam_daily_latest.json")

COUNT_COLS = ["snapshot_date", "department", "type", "n_active", "n_sol_numbers", "n_posted_last_7d", "n_open",
              "n_deadline_next_7d", "n_deadline_next_30d"]
SOLICITATION_TYPES = ("Solicitation", "Combined Synopsis/Solicitation")
DOD = "DEPT OF DEFENSE"          # the exact Department/Ind.Agency text SAM uses for the Department of Defense

# raw CSV column -> output column.  ONLY these are ever kept (a whitelist).
KEEP = {
    "NoticeId": "notice_id", "Title": "title", "Sol#": "sol_number",
    "Department/Ind.Agency": "department", "CGAC": "cgac", "Sub-Tier": "sub_tier", "FPDS Code": "fpds_code",
    "Office": "office", "AAC Code": "aac_code",
    "PostedDate": "posted_date", "Type": "type", "BaseType": "base_type",
    "ArchiveType": "archive_type", "ArchiveDate": "archive_date",
    "SetASideCode": "set_aside_code", "SetASide": "set_aside",
    "ResponseDeadLine": "response_deadline",
    "NaicsCode": "naics", "ClassificationCode": "psc",
    "PopCity": "pop_city", "PopState": "pop_state", "PopZip": "pop_zip", "PopCountry": "pop_country",
    "AwardNumber": "award_number", "AwardDate": "award_date", "Award$": "award_amount",
    "OrganizationType": "org_type", "State": "office_state", "City": "office_city",
    "ZipCode": "office_zip", "CountryCode": "office_country",
}
CATEGORICAL = ["department", "cgac", "sub_tier", "fpds_code", "office", "aac_code", "type", "base_type", "archive_type",
               "set_aside_code", "set_aside", "naics", "psc", "pop_state", "pop_country", "org_type", "office_state",
               "office_country"]
DESCRIPTION = "Description"
USECOLS = list(KEEP) + [DESCRIPTION]
# never to be kept, listed so the header check can say "yes, we know about these"
KNOWN_DROPPED = {"PopStreetAddress", "Active", "Awardee", "AdditionalInfoLink", "Link", DESCRIPTION}


# A few notice TITLES contain an e-mail address or phone number typed in by the contracting officer; mask them.
_EMAIL = re.compile(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"(?<!\d)(?:\+?1[-. ]?)?\(?\d{3}\)?[-. ]\d{3}[-. ]\d{4}(?!\d)")


def scrub(text):
    return _PHONE.sub("[phone]", _EMAIL.sub("[email]", text)) if isinstance(text, str) else text


def log(*a):
    print(*a, flush=True)


# ------------------------------------------------------------------------------------------------------------------
# download
# ------------------------------------------------------------------------------------------------------------------
def snapshot_date_from_headers(headers):
    lm = headers.get("Last-Modified")
    if lm:
        try:
            return parsedate_to_datetime(lm).astimezone(timezone.utc).date()
        except Exception:
            pass
    return datetime.now(timezone.utc).date()


def head_date():
    """Snapshot date from a HEAD request (cheap), or today UTC."""
    try:
        req = urllib.request.Request(URL, headers={"User-Agent": UA_SAM}, method="HEAD")
        with urllib.request.urlopen(req, timeout=60) as r:
            return snapshot_date_from_headers(r.headers)
    except Exception as e:
        log("  HEAD failed, using today UTC as snapshot date:", repr(e)[:100])
        return datetime.now(timezone.utc).date()


def stream_download(dest, retries=3):
    """Stream URL to dest in 1 MB pieces; stop if it gets past MAX_BYTES.  Returns (bytes, snapshot date)."""
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(URL, headers={"User-Agent": UA_SAM})
            with urllib.request.urlopen(req, timeout=120) as r:
                snap = snapshot_date_from_headers(r.headers)
                size = int(r.headers.get("Content-Length") or 0)
                if size > MAX_BYTES:
                    raise RuntimeError(f"server says {size} bytes, above the {MAX_BYTES} cap")
                got = 0
                with open(dest, "wb") as fh:
                    while True:
                        buf = r.read(1 << 20)
                        if not buf:
                            break
                        got += len(buf)
                        if got > MAX_BYTES:
                            raise RuntimeError("download exceeded the size cap")
                        fh.write(buf)
                if size and got != size:
                    raise RuntimeError(f"incomplete download: {got} of {size} bytes")
                return got, snap
        except Exception as e:
            last = e
            log(f"  download attempt {attempt + 1} failed: {e!r}"[:200])
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"download failed after {retries} attempts: {last!r}")


# ------------------------------------------------------------------------------------------------------------------
# encoding
# ------------------------------------------------------------------------------------------------------------------
# The real file is a MIX: mostly Windows cp1252 text (curly quotes 0x92/0x93/0x94, dashes 0x96/0x97, accents) with a few
# thousand genuine UTF-8 sequences from other systems.  Neither pure "utf-8" nor pure "cp1252" is right, so we decode as
# UTF-8 and let every byte that is not valid UTF-8 fall back to cp1252 (bytes cp1252 leaves undefined become U+FFFD).
def _cp1252_fallback(err):
    bad = err.object[err.start:err.end]
    return bad.decode("cp1252", errors="replace"), err.end


codecs.register_error("cp1252fallback", _cp1252_fallback)
_UTF8_SEQ = re.compile(rb"[\xc2-\xdf][\x80-\xbf]|[\xe0-\xef][\x80-\xbf]{2}|[\xf0-\xf4][\x80-\xbf]{3}")
_HIGH = re.compile(rb"[\x80-\xff]")


class MixedTextStream(io.TextIOBase):
    """Read-only text view of a binary file that tolerates the mixed encoding above.  Blocks are cut at a newline so a
    multi-byte sequence is never split.  pandas reads from this like from a normal text file.
    After reading: n_utf8 = valid multi-byte UTF-8 sequences, n_stray = bytes that were not valid UTF-8 (decoded as cp1252)."""

    def __init__(self, path, block=8 << 20):
        self._fh = open(path, "rb")
        self._block = block
        self._buf = ""
        self._eof = False
        self._first = True
        self.n_utf8 = 0
        self.n_stray = 0

    def readable(self):
        return True

    def _fill(self):
        raw = self._fh.read(self._block)
        if not raw:
            self._eof = True
            return
        if not raw.endswith(b"\n"):                       # extend the block to the next newline (or end of file)
            tail = self._fh.readline()
            raw += tail
        if self._first:
            self._first = False
            if raw.startswith(codecs.BOM_UTF8):
                raw = raw[3:]
            if raw[:2] in (codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE) or b"\x00" in raw[:200]:
                raise RuntimeError("the file looks like UTF-16, which this script does not handle")
        text = raw.decode("utf-8", errors="cp1252fallback")
        if not raw.isascii():
            seqs = _UTF8_SEQ.findall(raw)
            self.n_utf8 += len(seqs)
            self.n_stray += len(_HIGH.findall(raw)) - sum(len(s) for s in seqs)
        self._buf += text

    def read(self, n=-1):
        while not self._eof and (n < 0 or len(self._buf) < n):
            self._fill()
        if n < 0:
            out, self._buf = self._buf, ""
        else:
            out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def close(self):
        self._fh.close()
        super().close()


# ------------------------------------------------------------------------------------------------------------------
# read + clean
# ------------------------------------------------------------------------------------------------------------------
def check_header(path):
    st = MixedTextStream(path, block=1 << 20)
    head = pd.read_csv(st, nrows=0, dtype=str)
    st.close()
    cols = list(head.columns)
    missing = [c for c in USECOLS if c not in cols]
    if missing:
        sys.exit(f"STOP: the CSV header changed; missing columns: {missing}\nheader is: {cols}")
    extra = [c for c in cols if c not in KEEP and c not in KNOWN_DROPPED
             and not c.startswith(("PrimaryContact", "SecondaryContact"))]
    return cols, extra


def clean_chunk(ch):
    """Raw chunk (all strings) -> cleaned small frame with the two keyword flags.  The Description never leaves this function."""
    flags = [kw_flags(t if isinstance(t, str) else "", d[:300] if isinstance(d, str) else "")
             for t, d in zip(ch["Title"].tolist(), ch[DESCRIPTION].tolist())]
    out = ch[list(KEEP)].rename(columns=KEEP)
    out["title"] = out["title"].map(scrub)
    out["kw_brand"] = pd.array([f[0] for f in flags], dtype="int8")
    out["kw_sole"] = pd.array([f[1] for f in flags], dtype="int8")
    return out


def read_snapshot(path):
    parts, n_raw = [], 0
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        stream = MixedTextStream(path)
        reader = pd.read_csv(stream, usecols=USECOLS, dtype=str, na_values=[""], keep_default_na=False,
                             chunksize=CHUNK_ROWS, on_bad_lines="warn", quoting=csv.QUOTE_MINIMAL)
        for ch in reader:
            n_raw += len(ch)
            parts.append(clean_chunk(ch))
            del ch
    stream.close()
    n_bad = sum(1 for w in caught if "Skipping line" in str(w.message))
    df = pd.concat(parts, ignore_index=True)
    enc_note = (f"decoded as UTF-8 with cp1252 fallback: {stream.n_utf8:,} valid UTF-8 multi-byte sequences, "
                f"{stream.n_stray:,} stray bytes decoded as cp1252")
    return df, n_raw, n_bad, enc_note


def finish_types(df, snap):
    """Convert strings to real types; unparseable values become missing (counted)."""
    notes = {}
    n0 = len(df)
    df = df[df["notice_id"].notna()].copy()
    notes["rows_without_notice_id_dropped"] = n0 - len(df)
    n1 = len(df)
    df = df.drop_duplicates("notice_id", keep="last")
    notes["duplicate_notice_ids_dropped"] = n1 - len(df)

    def ts(col, utc):
        s = df[col]
        out = pd.to_datetime(s, format="ISO8601", errors="coerce", utc=utc)
        notes[f"{col}_unparsed"] = int((out.isna() & s.notna()).sum())
        return out

    df["posted_date"] = ts("posted_date", False).astype("datetime64[s]")
    df["response_deadline"] = ts("response_deadline", True).dt.tz_convert("UTC").astype("datetime64[s, UTC]")
    for c in ("archive_date", "award_date"):
        df[c] = ts(c, False).dt.normalize().astype("datetime64[s]")
    amt = pd.to_numeric(df["award_amount"].str.replace(",", "", regex=False), errors="coerce")
    notes["award_amount_unparsed"] = int((amt.isna() & df["award_amount"].notna()).sum())
    df["award_amount"] = amt.astype("float64")
    for c in CATEGORICAL:
        df[c] = df[c].astype("category")
    for c in df.columns:
        if str(df[c].dtype) in ("str", "string", "object"):
            df[c] = df[c].astype("string")
    # One Sol# can have several active notice ids (original + amendments).  latest_version = 1 for the row with the newest
    # PostedDate within its Sol# (rows without a Sol# count as their own latest).
    newest = df.groupby("sol_number")["posted_date"].transform("max")
    df["latest_version"] = ((df["posted_date"] == newest) | df["sol_number"].isna()).astype("int8")
    df["snapshot_date"] = pd.Timestamp(snap).as_unit("s")
    cols = ["snapshot_date"] + [c for c in df.columns if c != "snapshot_date"]
    return df[cols].sort_values("notice_id").reset_index(drop=True), notes


# ------------------------------------------------------------------------------------------------------------------
# summaries
# ------------------------------------------------------------------------------------------------------------------
def build_counts(df, snap):
    """One row per department x type for this snapshot date."""
    start = pd.Timestamp(snap, tz="UTC")
    d7, d30 = start + pd.Timedelta(days=7), start + pd.Timedelta(days=30)
    t = pd.DataFrame({
        "department": df["department"].astype("string").fillna("(blank)"),
        "type": df["type"].astype("string").fillna("(blank)"),
        "sol": df["sol_number"].astype("string"),
        "posted7": df["posted_date"] >= pd.Timestamp(snap) - pd.Timedelta(days=7),
        "is_sol": df["type"].isin(SOLICITATION_TYPES),
    })
    dl = df["response_deadline"]
    t["dl7"] = t["is_sol"] & (dl >= start) & (dl < d7)
    t["dl30"] = t["is_sol"] & (dl >= start) & (dl < d30)
    t["open"] = t["is_sol"] & (dl >= start)
    g = t.groupby(["department", "type"], observed=True, sort=True)
    out = g.agg(n_active=("type", "size"), n_sol_numbers=("sol", "nunique"), n_posted_last_7d=("posted7", "sum"),
                n_open=("open", "sum"), n_deadline_next_7d=("dl7", "sum"), n_deadline_next_30d=("dl30", "sum"),
                is_sol=("is_sol", "max")).reset_index()
    for c in ("n_open", "n_deadline_next_7d", "n_deadline_next_30d"):
        out[c] = out[c].astype("Int64").where(out["is_sol"])      # blank for non-solicitation types
    out = out.drop(columns="is_sol")
    for c in ("n_active", "n_sol_numbers", "n_posted_last_7d"):
        out[c] = out[c].astype("int64")
    out.insert(0, "snapshot_date", snap.isoformat())
    out = out[COUNT_COLS]
    return out.sort_values(["snapshot_date", "department", "type"]).reset_index(drop=True)


def update_counts_csv(new, snap):
    """Replace this date's rows, keep the rest, write sorted, atomically."""
    if os.path.exists(COUNTS_CSV):
        old = pd.read_csv(COUNTS_CSV, dtype={"snapshot_date": str, "department": str, "type": str}, keep_default_na=False,
                          na_values=[""])
        old = old[old["snapshot_date"] != snap.isoformat()]
        allr = pd.concat([old, new], ignore_index=True)
    else:
        allr = new
    for c in ("n_active", "n_sol_numbers", "n_posted_last_7d", "n_open", "n_deadline_next_7d", "n_deadline_next_30d"):
        allr[c] = allr[c].astype("Int64")
    allr = allr.sort_values(["snapshot_date", "department", "type"]).reset_index(drop=True)
    tmp = COUNTS_CSV + ".tmp"
    allr.to_csv(tmp, index=False, encoding="utf-8", lineterminator="\n")
    os.replace(tmp, COUNTS_CSV)
    return len(allr)


def build_latest(df, snap, meta):
    """Small summary for the dashboard.  "Open" = a solicitation type whose response deadline is on or after 00:00 UTC of the
    snapshot date.  The file keeps notices past their deadline until they are archived (about 3 in 4 solicitations in it
    are past their deadline), so "active" and "open" are very different numbers; both are given.  Because an amended
    solicitation keeps its older notice ids, each count is given as notice rows and as distinct Sol# values."""
    start = pd.Timestamp(snap, tz="UTC")
    sol = df[df["type"].isin(SOLICITATION_TYPES)]
    dl = sol["response_deadline"]
    dep = sol["department"].astype("string").fillna("(blank)")
    is_dod = dep == DOD
    opn = dl >= start
    in7 = opn & (dl < start + pd.Timedelta(days=7))
    in30 = opn & (dl < start + pd.Timedelta(days=30))

    def both(mask):
        sub = sol[mask]
        return {"notice_rows": int(len(sub)), "distinct_sol_numbers": int(sub["sol_number"].nunique())}

    top_rows = dep[opn & ~is_dod].value_counts().head(10)
    top = []
    for name, n in top_rows.items():
        sub = sol[opn & (dep == name)]
        top.append({"department": name, "open_notice_rows": int(n), "open_distinct_sol_numbers": int(sub["sol_number"].nunique())})
    by_type = df["type"].astype("string").fillna("(blank)").value_counts()
    return {
        "snapshot_date": snap.isoformat(),
        "built_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "SAM.gov Contract Opportunities Full CSV (active notices only)",
        "total_active_notices": int(len(df)),
        "by_type": {k: int(v) for k, v in by_type.items()},
        "solicitation_types": list(SOLICITATION_TYPES),
        "active_solicitations_in_file": both(slice(None)),
        "active_solicitations_deadline_passed": both(dl < start),
        "active_solicitations_no_deadline": both(dl.isna()),
        "open_solicitations": {
            "definition": "solicitation type and response deadline on or after 00:00 UTC of the snapshot date",
            "all": both(opn),
            "dod": both(opn & is_dod),
            "civilian": both(opn & ~is_dod),
            "dod_department_label": DOD,
            "deadline_next_7d": both(in7),
            "deadline_next_30d": both(in30),
            "deadline_next_7d_civilian": both(in7 & ~is_dod),
            "deadline_next_30d_civilian": both(in30 & ~is_dod),
        },
        "top10_civilian_departments_by_open_solicitations": top,
        "windows": "next_7d = deadline in [start, start+7d), next_30d = [start, start+30d), start = 00:00 UTC of the snapshot date",
        "kw_flag_counts_among_open_solicitations": {"kw_brand": int(sol.loc[opn, "kw_brand"].sum()),
                                                    "kw_sole": int(sol.loc[opn, "kw_sole"].sum())},
        "quality": meta,
    }


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


# ------------------------------------------------------------------------------------------------------------------
# optional upload to a DRAFT release
# ------------------------------------------------------------------------------------------------------------------
def upload_draft(parquet_path, snap):
    """Upload to a draft release `sam-snapshots-YYYY-MM` with the gh CLI.  Never public: always --draft.

    About finding drafts by tag: a draft release has no real git tag yet, so the plain "releases/tags/<tag>" API lookup
    returns 404 for it.  Recent gh versions work around this by listing releases and matching the tag name, but listing is
    paginated, so we list explicitly (`gh release list --json tagName,isDraft`) and decide ourselves.  GitHub also allows
    SEVERAL drafts with the same tag name, so we must not blindly `create` twice.  Needs GH_TOKEN with contents: write."""
    tag = f"sam-snapshots-{snap.strftime('%Y-%m')}"
    if shutil.which("gh") is None:
        sys.exit("--upload needs the GitHub CLI (gh) on PATH")
    lst = subprocess.run(["gh", "release", "list", "--limit", "200", "--json", "tagName,isDraft"],
                         capture_output=True, text=True)
    if lst.returncode != 0:
        sys.exit("gh release list failed: " + lst.stderr.strip())
    exists = any(r["tagName"] == tag and r["isDraft"] for r in json.loads(lst.stdout or "[]"))
    if not exists:
        log(f"creating DRAFT release {tag}")
        c = subprocess.run(["gh", "release", "create", tag, "--draft", "--title", f"SAM daily snapshots {snap.strftime('%Y-%m')}",
                            "--notes", "Daily cleaned snapshots of active SAM.gov notices (no contact, awardee, street address or "
                                       "description text). Draft: not public."], capture_output=True, text=True)
        if c.returncode != 0:
            sys.exit("gh release create failed: " + c.stderr.strip())
    log(f"uploading {os.path.basename(parquet_path)} to {tag} (--clobber)")
    u = subprocess.run(["gh", "release", "upload", tag, parquet_path, "--clobber"], capture_output=True, text=True)
    if u.returncode != 0:
        sys.exit("gh release upload failed: " + u.stderr.strip())
    log("  upload done")


# ------------------------------------------------------------------------------------------------------------------
def peak_mb():
    """Peak memory of this process in MB (Windows or Linux), or None."""
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            class PMC(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
            k = ctypes.WinDLL("kernel32", use_last_error=True)
            p = ctypes.WinDLL("psapi", use_last_error=True)
            k.GetCurrentProcess.restype = wintypes.HANDLE
            p.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
            c = PMC()
            c.cb = ctypes.sizeof(PMC)
            p.GetProcessMemoryInfo(k.GetCurrentProcess(), ctypes.byref(c), c.cb)
            return c.PeakWorkingSetSize / 1e6
        import resource
        r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss      # KB on Linux
        return r / 1e3
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser(description="Save today's SAM.gov active-notice snapshot (see the file header).")
    ap.add_argument("--upload", action="store_true", help="also upload the parquet to a DRAFT GitHub release (needs gh)")
    ap.add_argument("--from-file", help="use this local CSV instead of downloading (not deleted; for testing)")
    ap.add_argument("--date", help="snapshot date YYYY-MM-DD (only with --from-file; default today UTC)")
    args = ap.parse_args()

    t0 = time.time()
    os.makedirs(SNAP_DIR, exist_ok=True)
    tmpdir = None
    try:
        if args.from_file:
            path = args.from_file
            snap = date.fromisoformat(args.date) if args.date else datetime.now(timezone.utc).date()
            log(f"using local file {path} ({os.path.getsize(path) / 1e6:,.1f} MB), snapshot date {snap}")
        else:
            tmpdir = tempfile.mkdtemp(prefix="sam_snapshot_")           # system temp, outside the repository
            path = os.path.join(tmpdir, "ContractOpportunitiesFullCSV.csv")
            log("downloading", URL)
            n, snap = stream_download(path)
            log(f"downloaded {n / 1e6:,.1f} MB in {time.time() - t0:,.0f} s; snapshot date (Last-Modified, UTC) = {snap}")

        cols, extra = check_header(path)
        log(f"header ok: {len(cols)} columns; new unknown columns (ignored): {extra}")

        df, n_raw, n_bad, enc_note = read_snapshot(path)
        log(f"read {n_raw:,} raw rows; {n_bad:,} malformed rows skipped; encoding: {enc_note}")
        if n_raw == 0:
            sys.exit("STOP: no rows read")
        if tmpdir:                                                       # free the disk as early as possible
            shutil.rmtree(tmpdir, ignore_errors=True)
            tmpdir = None
        df, notes = finish_types(df, snap)
        log(f"{len(df):,} notices after cleaning;", notes)

        parquet = os.path.join(SNAP_DIR, f"sam_active_{snap.isoformat()}.parquet")
        df.to_parquet(parquet + ".tmp", engine="pyarrow", compression="zstd", compression_level=9, index=False)
        os.replace(parquet + ".tmp", parquet)
        log(f"wrote {parquet} ({os.path.getsize(parquet) / 1e6:,.1f} MB)")

        counts = build_counts(df, snap)
        total_rows = update_counts_csv(counts, snap)
        log(f"wrote {COUNTS_CSV}: {len(counts):,} rows for {snap}, {total_rows:,} rows in total")

        meta = {"encoding": enc_note, "raw_rows_read": int(n_raw), "malformed_rows_skipped": int(n_bad),
                "rows_kept": int(len(df)), "extra_columns_ignored": extra, **notes}
        latest = build_latest(df, snap, meta)
        write_json(LATEST_JSON, latest)
        log(f"wrote {LATEST_JSON}")

        if args.upload:
            upload_draft(parquet, snap)
        else:
            log("(no --upload: nothing was sent anywhere)")
        pm = peak_mb()
        log(f"done in {time.time() - t0:,.0f} s" + (f"; peak memory {pm:,.0f} MB" if pm else ""))
    finally:
        if tmpdir and os.path.isdir(tmpdir):
            shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
