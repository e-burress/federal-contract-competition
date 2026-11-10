"""
25_market_depth.py - how many companies are registered to do business with the federal government, in each industry (NAICS code).

Why: a "thin" market (very few registered companies in an industry) may explain why some competed solicitations get only one offer.
This script measures the depth of each market with a simple count of registrations.  A registration is NOT a capable bidder.

Source (public, no key, no account): SAM.gov's Entity Registration "Public V2" monthly extract.
    listing:   https://sam.gov/api/prod/fileextractservices/v1/api/listfiles?domain=Entity%20Registration/Public%20V2&privacy=Public
    download:  https://sam.gov/api/prod/fileextractservices/v1/api/download/<key>?privacy=Public   (answers with a short-lived public link)
The script picks the newest file named SAM_PUBLIC_UTF-8_MONTHLY_V2_<YYYYMMDD>.ZIP (about 150 MB).  It is a ZIP that holds another ZIP
that holds one pipe-delimited .dat file (about 580 MB, 142 fields per line, about 910,000 lines).  The layout is the GSA document
"SAM Master Extract Mapping v6.0 Public File V2 Layout" (https://open.gsa.gov/api/sam-entity-extracts-api/); the field positions
used below were checked against the real file.  The first line is "BOF PUBLIC V2 00000000 <extract date> ..." and the last is "EOF ...".

Fields used (1-based position in the layout)
   1 unique entity id (UEI; used only to drop duplicate lines, never written)    6 SAM extract code (A active, E expired)
   7 purpose of registration (Z2 all awards, Z1 federal assistance only)           9 registration expiration date (YYYYMMDD)
  19 physical address state           22 physical address country code            33 primary NAICS
  35 NAICS code string (codes separated by ~, each 6 digits plus a size letter Y small, N not small, E see exception string)
 114 NAICS exception string (codes with a size exception; the 1st letter after the code is the general size answer)
 116 exclusion status flag (D = the entity has an active exclusion)
Names, street addresses, e-mails, phone numbers and contact persons are in the file but are never read into any variable that
is kept and never written.  See "Privacy" below.

What counts as an ACTIVE entity (all must hold)
  - SAM extract code is "A" (the monthly file also lists registrations that expired in the last six months, code "E": dropped),
  - the registration expiration date is on or after the extract date,
  - the line is not a repeat of a UEI already seen (a UEI with two CAGE codes has two lines; it is one company),
  - the exclusion flag is not "D" (an excluded company cannot receive awards).
The industry tables then keep only entities whose purpose of registration is Z2 ("all awards", contracts included).  Entities
registered for federal assistance (grants) only, code Z1, almost never list a NAICS code and are not contract bidders.

Counts (per NAICS code)
  n_primary        active Z2 entities whose PRIMARY NAICS is this code
  n_any            active Z2 entities that list this code anywhere in their NAICS string (counted once per entity)
  n_small_any      of those, entities flagged small business for this code (letter Y; for the 13 codes with a size exception,
                   letter E, the general size answer from the exception string is used)
  n_size_unknown   entities where the file gives no size answer (blank letter, or E without an exception entry)

What it does
  1. Reads the listing, picks the newest monthly file, streams the download to a temporary folder OUTSIDE the repository
     (size cap 1 GB, 3 attempts) and deletes the folder at the end, also when something fails.
  2. Reads the .dat file line by line (bytes, nothing is kept except counters; memory stays well under 400 MB).
  3. Writes, each to a temp name first and then moved into place:
       reference/market_depth_naics.csv        one row per 6-digit NAICS code (extract_date is a column)
       reference/market_depth_naics_state.csv  n_any by NAICS x state of the physical address (US states, DC and territories);
                                               if that file would be over 3 MB it is written for 3-digit NAICS instead
       reference/market_depth.md               what the files are, definitions, totals and limits (numbers written by this script)

Privacy (hard rules)
  Only counts are written.  Entity rows, UEIs, CAGE codes, names and addresses never leave the line parser.  The only text values
  that reach an output are NAICS codes, two-letter state codes and numbers.

Usage (from the repository root):
  python -I scripts/25_market_depth.py                        # download the newest file, write the three outputs
  python -I scripts/25_market_depth.py --from-file PATH.zip   # local file (ZIP, ZIP in ZIP, or plain .dat); it is NOT deleted
Set PIO_CONTACT (a web address, never an e-mail) so the User-Agent says who is downloading.
"""
import argparse
import csv
import os
import re
import shutil
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile
from collections import Counter
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import PROJECT, UA_SAM, http_json  # noqa: E402

LIST_URL = "https://sam.gov/api/prod/fileextractservices/v1/api/listfiles?domain=Entity%20Registration/Public%20V2&privacy=Public"
DOWNLOAD_URL = "https://sam.gov/api/prod/fileextractservices/v1/api/download/{key}?privacy=Public"
MAX_BYTES = 1_000_000_000            # download size cap
MAX_UNPACKED = 3_000_000_000         # refuse to unpack more than this
STATE_FILE_MAX = 3_000_000           # bytes; above this the state file switches to 3-digit NAICS
REF_DIR = os.path.join(PROJECT, "reference")
OUT_NAICS = "market_depth_naics.csv"
OUT_STATE = "market_depth_naics_state.csv"
OUT_DOC = "market_depth.md"

# 0-based positions of the fields we use (layout position minus 1)
F_UEI, F_CODE, F_PURPOSE, F_EXPIRY, F_STATE, F_COUNTRY, F_PRIMARY, F_NAICS, F_EXC, F_EXCL = 0, 5, 6, 8, 18, 21, 32, 34, 113, 115
N_PIPES = 141                        # 142 fields per line, so 141 separators
ALL_AWARDS = b"Z2"
STATES = set("AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC "
             "SD TN TX UT VT VA WA WV WI WY DC PR VI GU MP AS".split())
TOKEN = re.compile(r"(\d{6})([YNE ]?)")
CODE6 = re.compile(r"\d{6}")
FILE_RE = re.compile(r"SAM_PUBLIC_(UTF-8_)?MONTHLY_V2_(\d{8})\.ZIP$", re.I)


def log(*a):
    print(*a, flush=True)


# ------------------------------------------------------------------------------------------------------------------
# counting (the only place where lines are looked at)
# ------------------------------------------------------------------------------------------------------------------
class Tally:
    """Counters only.  add(line) takes one raw line (bytes) and updates the counters; it keeps no entity data except one
    integer per active UEI (to drop repeated lines), which is never written."""

    def __init__(self, extract_date):
        self.extract = extract_date.encode() if isinstance(extract_date, str) else extract_date    # b"YYYYMMDD"
        self.seen = set()
        self.lines = 0
        self.skipped = Counter()          # why a line was dropped
        self.active = 0                   # active entities (all purposes)
        self.active_purpose = Counter()   # by purpose of registration
        self.no_naics = 0                 # active Z2 entities without any valid NAICS code
        self.counted = 0                  # active Z2 entities with at least one NAICS code
        self.code_slots = 0               # sum over counted entities of the number of codes they list
        self.primary = Counter()
        self.any = Counter()
        self.small = Counter()
        self.unknown = Counter()
        self.state = Counter()            # (6-digit naics, state)
        self.state3 = Counter()           # (3-digit naics, state), each entity once per 3-digit group
        self.no_us_state = 0              # counted entities whose physical address is not in a US state/territory
        self.bad_tokens = 0               # NAICS tokens that are not 6 digits (ignored)
        self.bad_primary = 0              # primary NAICS values that are not 6 digits (ignored)
        self.sizes_from_exception = 0

    def add(self, raw):
        if raw.startswith((b"BOF", b"EOF")):
            return
        self.lines += 1
        if raw.count(b"|") != N_PIPES:
            self.skipped["malformed line (a '|' inside a text field)"] += 1
            return
        p = raw.rstrip(b"\r\n").split(b"|")
        if p[F_CODE] != b"A":
            self.skipped["expired registration (extract code E)"] += 1
            return
        if p[F_EXPIRY] < self.extract:
            self.skipped["active code but expiration date before the extract date"] += 1
            return
        uei = int.from_bytes(p[F_UEI], "big")
        if uei in self.seen:
            self.skipped["repeated UEI (second CAGE code)"] += 1
            return
        self.seen.add(uei)
        if p[F_EXCL].strip() == b"D":
            self.skipped["active exclusion (flag D)"] += 1
            return
        self.active += 1
        purpose = p[F_PURPOSE].decode("latin-1")
        self.active_purpose[purpose] += 1
        if p[F_PURPOSE] != ALL_AWARDS:
            return
        codes = self._codes(p[F_NAICS].decode("latin-1"), p[F_EXC].decode("latin-1"))
        if not codes:
            self.no_naics += 1
            return
        self.counted += 1
        self.code_slots += len(codes)
        primary = p[F_PRIMARY].decode("latin-1").strip()
        if CODE6.fullmatch(primary):
            self.primary[primary] += 1
        elif primary:
            self.bad_primary += 1
        st = p[F_STATE].decode("latin-1").strip().upper() if p[F_COUNTRY] == b"USA" else ""
        if st not in STATES:
            st = ""
            self.no_us_state += 1
        for code, flag in codes.items():
            self.any[code] += 1
            if flag == "Y":
                self.small[code] += 1
            elif flag != "N":
                self.unknown[code] += 1
            if st:
                self.state[(code, st)] += 1
        if st:
            for g in {c[:3] for c in codes}:
                self.state3[(g, st)] += 1

    def _codes(self, naics_string, exception_string):
        """{code: Y/N/? } for one entity.  Each code once.  '?' = no size answer."""
        exc = None
        out = {}
        for tok in naics_string.split("~"):
            if not tok.strip():
                continue
            m = TOKEN.fullmatch(tok)
            if not m:
                self.bad_tokens += 1
                continue
            code, flag = m.group(1), m.group(2)
            if code in out:
                continue
            if flag == "E":
                if exc is None:
                    exc = {t[:6]: t[6:7] for t in exception_string.split("~") if len(t) >= 7}
                flag = exc.get(code, "?")
                if flag in ("Y", "N"):
                    self.sizes_from_exception += 1
            out[code] = flag if flag in ("Y", "N") else "?"
        return out


# ------------------------------------------------------------------------------------------------------------------
# download and unpack
# ------------------------------------------------------------------------------------------------------------------
def newest_file(listing):
    """Pick the newest monthly file from the listing.  UTF-8 version preferred.  Returns (key, YYYYMMDD) or None."""
    best = None
    for item in ((listing or {}).get("_embedded") or {}).get("customS3ObjectSummaryList") or []:
        key = item.get("key") or ""
        m = FILE_RE.search(key)
        if m:
            rank = (m.group(2), 1 if m.group(1) else 0)
            if best is None or rank > best[0]:
                best = (rank, key)
    return (best[1], best[0][0]) if best else None


def stream_download(url, dest, retries=3):
    """Stream url to dest in 1 MB pieces; stop at MAX_BYTES.  Returns the number of bytes."""
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA_SAM})
            with urllib.request.urlopen(req, timeout=120) as r:
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
                return got
        except Exception as e:
            last = e
            log(f"  download attempt {attempt + 1} failed: {e!r}"[:200])
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"download failed after {retries} attempts: {last!r}")


def iter_lines(path, workdir, depth=0):
    """Yield the raw lines (bytes) of the .dat file.  `path` may be a ZIP, a ZIP inside a ZIP (the real file), or a plain file."""
    if not zipfile.is_zipfile(path):
        with open(path, "rb") as fh:
            yield from fh
        return
    if depth > 2:
        raise RuntimeError("ZIP files nested too deeply")
    with zipfile.ZipFile(path) as z:
        infos = [i for i in z.infolist() if not i.is_dir()]
        if len(infos) != 1:
            raise RuntimeError(f"expected one file inside the ZIP, found {len(infos)}")
        info = infos[0]
        if info.file_size > MAX_UNPACKED:
            raise RuntimeError(f"unpacked size {info.file_size} is above the {MAX_UNPACKED} cap")
        if info.filename.lower().endswith(".zip"):
            inner = os.path.join(workdir, f"inner{depth}.zip")
            with z.open(info) as src, open(inner, "wb") as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
            try:
                yield from iter_lines(inner, workdir, depth + 1)
            finally:
                os.remove(inner)
        else:
            with z.open(info) as src:
                yield from src


def header_date(first_line):
    """Extract date (YYYYMMDD) from the first line 'BOF PUBLIC V2 00000000 20261101 0911820 0008387', or None."""
    parts = first_line.decode("latin-1").split()
    if parts[:1] == ["BOF"]:
        for tok in parts[1:]:
            if re.fullmatch(r"20\d{6}", tok):
                return tok
    return None


def read_file(path, workdir, fallback_date):
    """Read the whole extract line by line and return the Tally.  The extract date comes from the first line."""
    tally = None
    ext = fallback_date
    for raw in iter_lines(path, workdir):
        if tally is None:
            ext = header_date(raw) or fallback_date
            if not ext:
                raise RuntimeError("no extract date in the first line and none from the file name")
            tally = Tally(ext)
        tally.add(raw)
    if tally is None:
        raise RuntimeError("the file has no lines")
    return tally, ext


# ------------------------------------------------------------------------------------------------------------------
# outputs
# ------------------------------------------------------------------------------------------------------------------
def write_csv(path, header, rows):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)
    return tmp


def iso(d):
    return f"{d[:4]}-{d[4:6]}-{d[6:]}"


def write_outputs(tally, ext, out_dir):
    """Write the two CSVs.  Returns a dict about what was written."""
    os.makedirs(out_dir, exist_ok=True)
    codes = sorted(set(tally.any) | set(tally.primary))
    rows = [(c, tally.primary[c], tally.any[c], tally.small[c], tally.unknown[c], iso(ext)) for c in codes]
    p1 = os.path.join(out_dir, OUT_NAICS)
    t1 = write_csv(p1, ["naics", "n_primary", "n_any", "n_small_any", "n_size_unknown", "extract_date"], rows)

    p2 = os.path.join(out_dir, OUT_STATE)
    t2 = write_csv(p2, ["naics", "state", "n_any"], [(c, s, n) for (c, s), n in sorted(tally.state.items())])
    level = 6
    if os.path.getsize(t2) > STATE_FILE_MAX:
        os.remove(t2)
        level = 3
        t2 = write_csv(p2, ["naics3", "state", "n_any"], [(c, s, n) for (c, s), n in sorted(tally.state3.items())])
    os.replace(t1, p1)
    os.replace(t2, p2)
    return {"codes": len(rows), "state_level": level, "state_bytes": os.path.getsize(p2),
            "state_rows": len(tally.state) if level == 6 else len(tally.state3)}


def depth_summary(tally):
    """Numbers for the .md: distribution of registrants per code, top ten codes."""
    counts = sorted(tally.any.values())
    n = len(counts)
    summary = {"n_codes": n}
    for k in (2, 5, 10, 20, 50, 100):
        summary[f"lt{k}"] = sum(1 for c in counts if c < k)
    summary["median"] = counts[n // 2] if n else 0
    summary["top10"] = tally.any.most_common(10)
    return summary


def write_doc(tally, ext, info, source_name, out_dir):
    s = depth_summary(tally)
    small_total = sum(tally.small.values())
    unk_total = sum(tally.unknown.values())
    skipped = "\n".join(f"  - {v:,}: {k}" for k, v in tally.skipped.most_common())
    top = "\n".join(f"| {i + 1} | {c} | {n:,} | {tally.primary[c]:,} | {tally.small[c]:,} |" for i, (c, n) in enumerate(s["top10"]))
    purposes = ", ".join(f"{k or '(blank)'}: {v:,}" for k, v in tally.active_purpose.most_common())
    if info["state_level"] == 6:
        state_txt = (f"`{OUT_STATE}` has {info['state_rows']:,} rows (6-digit NAICS x state; columns naics, state, n_any; "
                     f"{info['state_bytes'] / 1e6:.1f} MB).")
    else:
        state_txt = (f"`{OUT_STATE}` is at the 3-digit NAICS level (columns naics3, state, n_any; {info['state_rows']:,} rows), "
                     f"because the 6-digit version would be over 3 MB.  An entity is counted once per 3-digit group.")
    lines_per = tally.code_slots / tally.counted if tally.counted else 0
    text = f"""# Market depth: registered companies per industry (NAICS)

Generated by `scripts/25_market_depth.py` on {date.today().isoformat()}.  Do not edit by hand; run the script again.

## What these files are

For each industry code (6-digit NAICS), how many companies were registered in SAM.gov to do business with the federal government on the
extract date.  The idea: if few companies are registered in an industry, a solicitation in that industry may get few offers.

- `{OUT_NAICS}`: one row per 6-digit NAICS code that at least one active entity lists.  Columns: `naics`, `n_primary`, `n_any`,
  `n_small_any`, `n_size_unknown`, `extract_date`.  {info['codes']:,} rows.
- {state_txt}
- This page: `{OUT_DOC}`.

## Source

SAM.gov Entity Registration "Public V2" monthly extract, file `{source_name}`, extract date **{iso(ext)}**.  Public, no key and no account:
file listing at `https://sam.gov/api/prod/fileextractservices/v1/api/listfiles?domain=Entity%20Registration/Public%20V2&privacy=Public`,
download through `https://sam.gov/api/prod/fileextractservices/v1/api/download/<key>?privacy=Public`.  The layout is GSA's
"SAM Master Extract Mapping v6.0 Public File V2 Layout" (https://open.gsa.gov/api/sam-entity-extracts-api/).  The file lists
{tally.lines:,} registration lines.  The raw file is downloaded to a temporary folder outside the repository, read once, and deleted.
It contains business names and addresses (including sole proprietors).  Only counts are published here.

## Definitions

- **Active entity**: SAM extract code `A`, registration expiration date on or after the extract date, not a repeated UEI (a company with
  two CAGE codes has two lines), and no active exclusion (flag `D`).  Active entities in this extract: **{tally.active:,}**
  (by purpose of registration: {purposes}).
- **Contract-eligible**: purpose of registration `Z2` ("all awards").  Entities registered for grants only (`Z1`) are left out of the industry
  counts; they almost never list a NAICS code anyway.  Of the {tally.active_purpose['Z2']:,} active `Z2` entities, **{tally.counted:,}** list at least one
  valid NAICS code and {tally.no_naics:,} list none.
- `n_primary`: counted entities whose primary NAICS is this code.
- `n_any`: counted entities that list this code anywhere in their NAICS list.  Each entity is counted once per code.  Entities list
  {lines_per:.1f} codes on average, so the column sums to far more than the number of entities ({sum(tally.any.values()):,} code listings).
- `n_small_any`: of the `n_any` entities, those flagged small for this code.  The flag is the company's own answer in SAM for that code.
  For the 13 codes that have size exceptions the file shows the letter `E`; then the general size answer from the exception string is used
  ({tally.sizes_from_exception:,} listings were resolved this way).  Total small listings: {small_total:,}.
- `n_size_unknown`: the file gives no size answer (blank letter, because SBA sets no size standard for the code, or `E` with no exception entry).
  Total: {unk_total:,}.  So `n_any - n_small_any - n_size_unknown` is the number flagged "not small".
- State: physical address state, only when the physical address country is USA and the code is one of the 50 states, DC, PR, VI, GU, MP, AS.
  {tally.no_us_state:,} counted entities have no such state (foreign address, military mail code or a misspelled state) and are in the NAICS file but not in the state file.
- Codes that no active entity lists have no row.  Treat a missing code as zero registrants.

## Quick sanity numbers

- Codes with at least one registrant: {s['n_codes']:,}.  Median registrants per code: {s['median']:,}.
- Codes with fewer than 5 / 10 / 20 / 50 / 100 registrants (`n_any`): {s['lt5']:,} / {s['lt10']:,} / {s['lt20']:,} / {s['lt50']:,} / {s['lt100']:,}.  Codes with exactly one: {s['lt2']:,}.

Ten codes with the most registrants:

| rank | NAICS | n_any | n_primary | n_small_any |
|---|---|---|---|---|
{top}

Lines dropped, and why:
{skipped}
  - Ignored inside kept lines: {tally.bad_tokens:,} NAICS entries and {tally.bad_primary:,} primary NAICS values that are not 6 digits.

## Limits (read before using)

- **A registration is not a capable bidder.**  Registering is cheap and free.  Many companies register and never bid, and a company can list
  codes it rarely works in.  Counts overstate real competition and overstate it most in broad "catch-all" codes.
- **Many firms list many codes** (see the average above).  The columns are not additive across codes; do not sum `n_any` to count companies.
- **Only today's registrations.**  The monthly file is one snapshot ({iso(ext)}), not history.  Registrations expire after one year, and
  companies that left the market before the extract are not here.  Using it for older awards assumes the market looked the same.
- **Self-reported.**  NAICS lists, small-business answers and addresses are entered by the company.  The primary NAICS may be missing or malformed.
- **Place is the company's address,** not where it works.  Many firms bid far from their address; do not read the state file as local competition.
- **Size answers** follow the company's size at registration time, and for some codes SBA publishes no size standard.
- **Very small counts are often not real markets.**  The files list every 6-digit code any company typed in, including retired NAICS
  versions and typing mistakes.  Before calling a code "thin", check that it is a current NAICS code (the list is not part of this extract).
- **A few lines are lost:** lines with a stray `|` inside a text field cannot be read reliably and are skipped (see the list above; the
  other drops are intended: expired, repeated or excluded registrations).
- The extract has no parent company field, so a firm and its subsidiaries count separately.
"""
    path = os.path.join(out_dir, OUT_DOC)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    os.replace(tmp, path)


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
            ps = ctypes.WinDLL("psapi", use_last_error=True)
            k.GetCurrentProcess.restype = wintypes.HANDLE
            ps.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
            c = PMC()
            c.cb = ctypes.sizeof(PMC)
            ps.GetProcessMemoryInfo(k.GetCurrentProcess(), ctypes.byref(c), c.cb)
            return c.PeakWorkingSetSize / 1e6
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e3
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser(description="Count registered companies per NAICS code (see the file header).")
    ap.add_argument("--from-file", help="use this local file (ZIP, ZIP in ZIP, or .dat) instead of downloading; not deleted")
    args = ap.parse_args()

    t0 = time.time()
    tmpdir = tempfile.mkdtemp(prefix="sam_entity_")          # system temp, outside the repository
    try:
        if args.from_file:
            path, source_name, fallback = args.from_file, os.path.basename(args.from_file), None
            m = FILE_RE.search(source_name)
            fallback = m.group(2) if m else None
            log(f"using local file {path} ({os.path.getsize(path) / 1e6:,.1f} MB)")
        else:
            found = newest_file(http_json(LIST_URL, ua=UA_SAM))
            if not found:
                sys.exit("STOP: no SAM_PUBLIC_*_MONTHLY_V2_<date>.ZIP file in the listing (it may have moved or may now need a key)")
            key, fallback = found
            source_name = key.rsplit("/", 1)[-1]
            url = DOWNLOAD_URL.format(key=urllib.parse.quote(key))
            path = os.path.join(tmpdir, "download.zip")
            log("downloading", source_name)
            n = stream_download(url, path)
            log(f"downloaded {n / 1e6:,.1f} MB in {time.time() - t0:,.0f} s")

        tally, ext = read_file(path, tmpdir, fallback)
        log(f"read {tally.lines:,} lines, extract date {ext}; active entities {tally.active:,}; counted (Z2 with NAICS) {tally.counted:,}")
        if tally.counted == 0:
            sys.exit("STOP: no entities counted; the layout may have changed")
        info = write_outputs(tally, ext, REF_DIR)
        write_doc(tally, ext, info, source_name, REF_DIR)
        log(f"wrote {OUT_NAICS} ({info['codes']:,} codes), {OUT_STATE} ({info['state_bytes'] / 1e6:.2f} MB, {info['state_level']}-digit), {OUT_DOC}")
        pm = peak_mb()
        log(f"done in {time.time() - t0:,.0f} s" + (f"; peak memory {pm:,.0f} MB" if pm else ""))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
