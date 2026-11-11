"""
sam_csv.py - read SAM.gov's contract-opportunity CSV files (the nightly active-notices file and the yearly archive files).

Moved here unchanged from scripts/14_sam_daily_snapshot.py when that script switched to SAM.gov's API (November 2026).
22_sam_archive.py uses it to read the archive files: same 47 columns, same mixed encoding.

What is here
  KEEP / USECOLS     the whitelist of columns that are ever kept (contacts, awardee, street address, links and the description
                     are never kept; the description is read only to set two keyword flags and then dropped)
  MixedTextStream    a text view of a file that is mostly Windows cp1252 with some genuine UTF-8 (see the comment below)
  check_header, clean_chunk, read_snapshot, finish_types, scrub, write_json, peak_mb, log
"""
import codecs
import csv
import io
import json
import os
import re
import sys
import warnings

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import kw_flags  # noqa: E402

CHUNK_ROWS = 20_000
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



def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)



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
