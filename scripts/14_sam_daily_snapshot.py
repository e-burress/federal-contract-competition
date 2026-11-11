"""
14_sam_daily_snapshot.py - every morning: save the SAM.gov notices posted on each new day, from SAM.gov's official API.

This script uses only SAM.gov's documented Get Opportunities Public API (version 2) with a free personal API key.  SAM.gov's
Terms of Use (https://sam.gov/about/terms-of-use) name its APIs and the extracts on its Data Services page as the ways to get
its data with software.  Until November 10, 2026 this step downloaded SAM.gov's nightly "Contract Opportunities Full CSV" file
instead; see docs/sam-api-key.md.

Source
  GET https://api.sam.gov/opportunities/v2/search      documented at https://open.gsa.gov/api/get-opportunities-public-api/
  One request returns up to 1,000 notices posted in a date range (here: one day), all notice types, in the latest active
  version of each notice.

The key
  Read from the environment variable SAM_API_KEY (on GitHub: the repository secret SAM_API_KEY).  The API takes the key in
  the query string, the only way GSA documents, so this script never prints, logs or saves a request URL, and it removes the
  key from any error text before printing it.  SAM.gov's terms: do not share the key, and renew it every 90 days.  A refused
  key stops the script with exit code 2 and says how to renew it.
  Without a key the script says so and does nothing (exit 0), so `python run.py daily` still runs the other steps.  With
  --require-key (the robot uses it) a missing key is an error.

Daily budget
  A personal key of a non-federal user with no role in SAM.gov allows 10 requests a day (GSA's table on open.gsa.gov).  This
  script makes at most --max-requests (default 6) per run and leaves the rest for checks by hand.  SAM.gov posted 12 to 1,678
  notices a day (all types) between October 20 and November 8, 2026, so one day costs 1 or 2 requests.  Run the step in ONE
  place, the robot or your computer: both spend the same 10 requests.

What it does
  1. With --sync, merges in the store of notices pulled so far from the DRAFT (not public) GitHub release sam-api-store.
  2. Days to pull: every day not yet finished, from the first day in results/sam_api_pulls.csv (or --start, or yesterday when
     the log is empty) through yesterday, oldest first, at most --max-days, never more than 60 days back.  "Yesterday" is
     SAM.gov's calendar day, taken as UTC-5.  Today is never pulled: it is not over.
  3. Each day: postedFrom = postedTo = the day, 1,000 notices per page.  A day is complete when the distinct notice ids
     received reach the API's totalRecords.  A day cut short by the request budget is pulled again in full next time.  A day
     that falls short for another reason twice (or that would need more requests than one run may make) is kept as "short",
     with the shortfall recorded.
     Paging: GSA documents offset as a page index (0, 1, 2 ...).  On the first day that needs a second page the script checks
     this (a second page that mostly repeats the first means the offset counts records) and records the mode it found.
  4. Keeps only the fields in FIELDS and the department, sub-tier and office names and codes.  Never kept: contacts, the
     awardee, street addresses, the description, links.  E-mail addresses and phone numbers typed into titles are masked.  The
     two keyword flags come from the title alone (search results do not include the description; fetching it would cost one
     request per notice).  The forecast model was trained with the title and the first 300 characters of the description.
  5. Writes, each to a temporary name first and then moved into place:
       <DATA>/sam_api/notices.parquet                private: every notice pulled so far, one row per notice id (newest pull)
       <DATA>/sam_snapshots/sam_view_<date>.parquet  private: the forecast step's input, the notices posted in the 400 days
                                                     before <date>, where <date> is the day after the newest finished day
                                                     (the two newest views are kept)
       results/sam_api_pulls.csv                     public: one row per pull of a day: requests used, totalRecords, notices
                                                     received, status
       results/sam_new_notices.csv                   public: notices posted per day, department and notice type
       results/sam_daily_latest.json                 public: summary for the dashboard (schema 2)
  6. With --upload, replaces the store in the draft release sam-api-store (needs gh and GH_TOKEN).

  --check makes ONE request (yesterday, first page), prints what came back (counts, how full each field is, the shape of the
  dates and codes; no names) and writes nothing.  Use it once after setting the key.

Exit codes: 0 done (also when there is nothing to do, when the budget ran out part-way, or with no key and no --require-key);
1 the API or the network failed before any day was finished; 2 the key is missing (with --require-key or --check) or refused.
"""
import argparse
import glob
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA, RESULTS, UA_SAM, kw_flags  # noqa: E402

API_URL = "https://api.sam.gov/opportunities/v2/search"
KEY_ENV = "SAM_API_KEY"
DAILY_LIMIT = 10                   # requests a day for a personal key of a non-federal user with no role (GSA)
DEFAULT_BUDGET = 6                 # requests one run may make
PAGE = 1000                        # the API's largest page
MAX_ATTEMPTS = 2                   # pulls of a day that fall short (not for lack of budget) before it is kept as "short"
MAX_BACK_DAYS = 60                 # never pull a day older than this
VIEW_DAYS = 400                    # the forecast view holds notices posted in this many days
KEEP_VIEWS = 2
SAM_DAY_OFFSET = timedelta(hours=-5)    # SAM.gov's calendar day, taken as US Eastern standard time
TIMEOUT = 120
PAUSE = 1.0                        # seconds between requests

STORE_DIR = os.path.join(DATA, "sam_api")
STORE = os.path.join(STORE_DIR, "notices.parquet")
SNAP_DIR = os.path.join(DATA, "sam_snapshots")
PULLS_CSV = os.path.join(RESULTS, "sam_api_pulls.csv")
NEW_CSV = os.path.join(RESULTS, "sam_new_notices.csv")
LATEST_JSON = os.path.join(RESULTS, "sam_daily_latest.json")
STORE_TAG, STORE_ASSET = "sam-api-store", "notices.parquet"

SOLICITATION_TYPES = ("Solicitation", "Combined Synopsis/Solicitation")
DOD = "DEPT OF DEFENSE"            # the department name SAM.gov uses for the Department of Defense
PULL_COLS = ["posted_day", "pulled_utc", "attempt", "requests", "total_records", "notices_received", "status", "offset_mode"]
NEW_COLS = ["posted_day", "department", "type", "n_notices", "n_sol_numbers"]
FINISHED = ("complete", "short")
EXIT_API, EXIT_KEY = 1, 2

# output column <- path in one API record.  ONLY these (and the hierarchy fields below) are ever kept: a whitelist.
FIELDS = [
    ("notice_id", ("noticeId",)), ("title", ("title",)), ("sol_number", ("solicitationNumber",)),
    ("posted_date", ("postedDate",)), ("type", ("type",)), ("base_type", ("baseType",)),
    ("archive_type", ("archiveType",)), ("archive_date", ("archiveDate",)),
    ("set_aside_code", ("typeOfSetAside",)), ("set_aside", ("typeOfSetAsideDescription",)),
    ("response_deadline", ("responseDeadLine",)), ("naics", ("naicsCode",)), ("psc", ("classificationCode",)),
    ("active", ("active",)),
    ("pop_city", ("placeOfPerformance", "city", "name")), ("pop_state", ("placeOfPerformance", "state", "code")),
    ("pop_zip", ("placeOfPerformance", "zip")), ("pop_country", ("placeOfPerformance", "country", "code")),
    ("award_number", ("award", "number")), ("award_date", ("award", "date")), ("award_amount", ("award", "amount")),
    ("org_type", ("organizationType",)), ("office_state", ("officeAddress", "state")),
    ("office_city", ("officeAddress", "city")), ("office_zip", ("officeAddress", "zipcode")),
    ("office_country", ("officeAddress", "countryCode")),
]
HIERARCHY = ["department", "cgac", "sub_tier", "fpds_code", "office", "aac_code"]
COLUMNS = ([n for n, _ in FIELDS[:3]] + HIERARCHY + [n for n, _ in FIELDS[3:]]
           + ["kw_brand", "kw_sole", "posted_day", "pulled_utc"])
DATE_COLS = ["posted_date", "archive_date", "award_date"]
STRING_COLS = [c for c in COLUMNS if c not in DATE_COLS + ["response_deadline", "award_amount", "kw_brand", "kw_sole",
                                                          "posted_day"]]


class KeyRefused(Exception):
    pass


class RateLimited(Exception):
    pass


class ApiError(Exception):
    pass


def say(*a):
    print(*a, flush=True)


# ------------------------------------------------------------------------------------------------------------------
# text helpers
# ------------------------------------------------------------------------------------------------------------------
_EMAIL = re.compile(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"(?<!\d)(?:\+?1[-. ]?)?\(?\d{3}\)?[-. ]\d{3}[-. ]\d{4}(?!\d)")


def scrub(s):
    return _PHONE.sub("[phone]", _EMAIL.sub("[email]", s)) if isinstance(s, str) else s


def text(v):
    """a clean string, or None ("", "null" and nested objects count as missing)"""
    if v is None or isinstance(v, (dict, list)):
        return None
    s = str(v).strip()
    return None if s == "" or s.lower() in ("null", "none") else s


def get_path(rec, path):
    cur = rec
    for k in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return text(cur)


def redact(s, key):
    return s.replace(key, "***") if key and isinstance(s, str) else s


# ------------------------------------------------------------------------------------------------------------------
# one API request
# ------------------------------------------------------------------------------------------------------------------
def api_get(key, day, offset, timeout=TIMEOUT):
    """Notices posted on `day` (one page).  Returns the parsed answer; 404 "No Data found" counts as an empty day.
    The URL holds the key: it is built here and never printed or kept."""
    q = urllib.parse.urlencode({"api_key": key, "postedFrom": f"{day:%m/%d/%Y}", "postedTo": f"{day:%m/%d/%Y}",
                                "limit": PAGE, "offset": offset}, safe="/")
    req = urllib.request.Request(API_URL + "?" + q, headers={"User-Agent": UA_SAM, "Accept": "application/json"})
    try:
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read()
        finally:
            time.sleep(PAUSE)                    # polite pacing between requests
    except urllib.error.HTTPError as e:
        try:
            msg = e.read().decode("utf-8", "replace")[:400]
        except Exception:
            msg = ""
        msg = redact(msg, key)
        low = msg.lower()
        if e.code == 404 and "no data" in low:
            return {"totalRecords": 0, "opportunitiesData": []}
        if e.code == 429 or "over_rate_limit" in low or "rate limit" in low:
            raise RateLimited(f"HTTP {e.code}: {msg}") from None
        if e.code in (401, 403) or "api_key" in low or "api key" in low:
            raise KeyRefused(f"HTTP {e.code}: {msg}") from None
        raise ApiError(f"HTTP {e.code}: {msg}") from None
    except urllib.error.URLError as e:
        raise ApiError(redact(f"network error: {e.reason}", key)) from None
    except (TimeoutError, OSError) as e:
        raise ApiError(redact(f"network error: {e!r}", key)) from None
    try:
        d = json.loads(body)
    except ValueError:
        raise ApiError("the answer was not JSON") from None
    if not isinstance(d, dict) or "totalRecords" not in d:
        msg = redact(json.dumps(d)[:300], key)
        low = msg.lower()
        if "over_rate_limit" in low:
            raise RateLimited(msg)
        if "api_key" in low or "api key" in low:
            raise KeyRefused(msg)
        raise ApiError("unexpected answer: " + msg)
    return d


class Budget:
    def __init__(self, n):
        self.cap, self.left, self.used = n, n, 0

    def take(self):
        self.left -= 1
        self.used += 1


def pull_day(key, day, budget, mode, get=None):
    """Ask for every notice posted on `day`.  Returns (records by notice id, totalRecords or None, offset mode found or None,
    reason the day is not complete: None, "budget", "too-big", "empty-page" or "pages-exhausted").
    Raises KeyRefused, RateLimited or ApiError; every request is taken from `budget` before it is sent."""
    get = get or api_get
    recs, page, total = {}, 0, None
    confirmed = mode if mode in ("page", "record") else None
    use = confirmed or "page"
    while True:
        if budget.left <= 0:
            return recs, total, confirmed, "budget"
        budget.take()
        d = get(key, day, page if use == "page" else page * PAGE)
        total = int(d.get("totalRecords") or 0)
        rows = [r for r in (d.get("opportunitiesData") or []) if isinstance(r, dict) and text(r.get("noticeId"))]
        if page == 0 and math.ceil(total / PAGE) > budget.cap:
            for r in rows:
                recs[text(r["noticeId"])] = r
            return recs, total, confirmed, "too-big"
        if page > 0 and confirmed is None and rows:
            fresh = sum(1 for r in rows if text(r["noticeId"]) not in recs)
            if fresh * 2 < len(rows):            # mostly a repeat of what came before: the offset counts records
                confirmed = use = "record"
                continue                         # ask again for this page, with a record offset
            confirmed = use
        for r in rows:
            recs[text(r["noticeId"])] = r
        if len(recs) >= total:
            return recs, total, confirmed, None
        if not rows:
            return recs, total, confirmed, "empty-page"
        page += 1
        if page > total // PAGE + 1:
            return recs, total, confirmed, "pages-exhausted"


# ------------------------------------------------------------------------------------------------------------------
# records -> rows
# ------------------------------------------------------------------------------------------------------------------
def hierarchy(rec):
    """Department, sub-tier and office names and codes from fullParentPathName and fullParentPathCode, e.g.
    "GENERAL SERVICES ADMINISTRATION.FEDERAL ACQUISITION SERVICE.GSA/FAS CENTER ..." and "047.4732.47QTCA".  The codes are
    exact: CGAC (department), FPDS code (sub-tier), AAC (office, the last level).  The names are split on "." as well; when a
    name holds a period (e.g. "U.S. SPECIAL OPERATIONS COMMAND"), the extra pieces are given to the sub-tier name."""
    names, codes = text(rec.get("fullParentPathName")), text(rec.get("fullParentPathCode"))
    cs = [c.strip() for c in codes.split(".")] if codes else []
    ns = names.split(".") if names else []
    levels = len(cs) or len(ns)
    out = dict.fromkeys(HIERARCHY)
    if cs:
        out["cgac"] = cs[0] or None
        out["fpds_code"] = (cs[1] or None) if len(cs) >= 2 else None
        out["aac_code"] = (cs[-1] or None) if len(cs) >= 3 else None
    if ns:
        if levels <= 1:
            out["department"] = names
        else:
            out["department"] = ns[0].strip() or None
            if levels >= 3 and len(ns) >= 3:
                out["office"] = ns[-1].strip() or None
                middle = ns[1:-1]
                extra = max(len(middle) - (levels - 2), 0)
                out["sub_tier"] = ".".join(middle[:extra + 1]).strip() or None
            elif len(ns) >= 2:
                out["sub_tier"] = ".".join(ns[1:]).strip() or None
    for k, f in (("department", "department"), ("sub_tier", "subTier"), ("office", "office")):   # older answers
        v = text(rec.get(f))
        if v:
            out[k] = v
    return out


def wall_time(v):
    """a timestamp as written (an offset, if any, is dropped), or NaT (also for a year outside 1900-2200: a typo)"""
    if v is None:
        return pd.NaT
    try:
        t = pd.Timestamp(v)
    except (ValueError, TypeError, OverflowError):
        return pd.NaT
    if t is pd.NaT or not 1900 <= t.year <= 2200:
        return pd.NaT
    return t.tz_localize(None) if t.tzinfo is not None else t


def to_frame(records, day, pulled_utc):
    """API records -> the store's columns (a whitelist; nothing else from a record is kept)."""
    rows = []
    for r in records:
        row = {name: get_path(r, path) for name, path in FIELDS}
        if row["response_deadline"] is None:
            row["response_deadline"] = get_path(r, ("reponseDeadLine",))     # the spelling in GSA's table
        if row["naics"] is None and isinstance(r.get("naicsCodes"), list) and r["naicsCodes"]:
            row["naics"] = text(r["naicsCodes"][0])
        row.update(hierarchy(r))
        row["kw_brand"], row["kw_sole"] = kw_flags(row["title"] or "", "")
        row["title"] = scrub(row["title"])
        rows.append(row)
    df = pd.DataFrame(rows, columns=[c for c in COLUMNS if c not in ("posted_day", "pulled_utc")])
    df["posted_day"] = pd.Timestamp(day)
    df["pulled_utc"] = pulled_utc
    return typed(df)


def typed(df):
    df = df.copy()
    for c in DATE_COLS:
        if pd.api.types.is_datetime64_any_dtype(df[c]):                 # read back from the store
            s = df[c].dt.tz_localize(None) if getattr(df[c].dt, "tz", None) is not None else df[c]
        else:
            s = pd.Series([wall_time(v) for v in df[c]], index=df.index, dtype="datetime64[ns]")
        df[c] = (s.dt.normalize() if c != "posted_date" else s).astype("datetime64[s]")
    if pd.api.types.is_datetime64_any_dtype(df["response_deadline"]):
        dl = df["response_deadline"]
        dl = dl.dt.tz_convert("UTC") if getattr(dl.dt, "tz", None) is not None else dl.dt.tz_localize("UTC")
    else:
        raw = df["response_deadline"].astype(object).where(df["response_deadline"].notna(), None)
        dl = pd.to_datetime(raw, format="ISO8601", errors="coerce", utc=True)
        dl = dl.where((dl.dt.year >= 1900) & (dl.dt.year <= 2200))
    df["response_deadline"] = dl.astype("datetime64[s, UTC]")
    amt = df["award_amount"].astype("string").str.replace(",", "", regex=False)
    df["award_amount"] = pd.to_numeric(amt, errors="coerce").astype("float64")
    for c in ("kw_brand", "kw_sole"):
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype("int8")
    df["posted_day"] = pd.to_datetime(df["posted_day"]).astype("datetime64[s]")
    for c in STRING_COLS:
        df[c] = df[c].astype("string")
    return df[COLUMNS]


def empty_store():
    return typed(pd.DataFrame({c: pd.Series(dtype=object) for c in COLUMNS}))


# ------------------------------------------------------------------------------------------------------------------
# files
# ------------------------------------------------------------------------------------------------------------------
def atomic_parquet(df, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    df.to_parquet(path + ".tmp", engine="pyarrow", compression="zstd", compression_level=9, index=False)
    os.replace(path + ".tmp", path)


def atomic_csv(df, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    df.to_csv(path + ".tmp", index=False, encoding="utf-8", lineterminator="\n")
    os.replace(path + ".tmp", path)


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    os.replace(path + ".tmp", path)


def read_store(path=None):
    path = path or STORE
    if not os.path.isfile(path):
        return empty_store()
    df = pd.read_parquet(path)
    for c in COLUMNS:
        if c not in df.columns:
            df[c] = None
    return typed(df[COLUMNS])


def merge_store(store, new):
    """add `new` to `store`; a notice id that is in both keeps the copy pulled last"""
    parts = [p for p in (store, new) if p is not None and len(p)]
    if not parts:
        return empty_store()
    allr = pd.concat(parts, ignore_index=True)
    allr = allr.sort_values("pulled_utc", kind="mergesort").drop_duplicates("notice_id", keep="last")
    return allr.sort_values(["posted_day", "notice_id"], kind="mergesort").reset_index(drop=True)


def read_pulls():
    if not os.path.isfile(PULLS_CSV):
        return pd.DataFrame(columns=PULL_COLS)
    return pd.read_csv(PULLS_CSV, dtype=str, keep_default_na=False)


def day_status(pulls):
    """{day: (status of its latest pull, number of pulls)} (the log is in time order)"""
    out = {}
    for d, s in zip(pulls["posted_day"], pulls["status"]):
        day = date.fromisoformat(d)
        out[day] = (s, out.get(day, (None, 0))[1] + 1)
    return out


def known_mode(pulls):
    modes = [m for m in pulls["offset_mode"] if m in ("page", "record")] if len(pulls) else []
    return modes[-1] if modes else None


def days_to_pull(pulls, yesterday, start=None, max_days=30):
    """(days to pull, oldest first; True if unfinished days older than MAX_BACK_DAYS are left out)"""
    st = day_status(pulls)
    floor = yesterday - timedelta(days=MAX_BACK_DAYS)
    begin = start or (min(st) if st else yesterday)
    gap = begin < floor and any(st.get(begin + timedelta(days=i), ("", 0))[0] not in FINISHED
                                for i in range((floor - begin).days))
    begin = max(begin, floor)
    out = []
    d = begin
    while d <= yesterday:
        if st.get(d, ("", 0))[0] not in FINISHED:
            out.append(d)
        d += timedelta(days=1)
    return out[:max_days], gap


def update_new_csv(store, days):
    """replace the rows of `days` in results/sam_new_notices.csv"""
    keep = set(pd.Timestamp(d) for d in days)
    sub = store[store["posted_day"].isin(keep)]
    t = pd.DataFrame({"posted_day": sub["posted_day"].dt.strftime("%Y-%m-%d"),
                      "department": sub["department"].fillna("(blank)").astype(str),
                      "type": sub["type"].fillna("(blank)").astype(str),
                      "sol": sub["sol_number"].astype(object)})
    g = (t.groupby(["posted_day", "department", "type"], sort=True)
         .agg(n_notices=("type", "size"), n_sol_numbers=("sol", "nunique")).reset_index())
    names = {d.isoformat() for d in days}
    if os.path.isfile(NEW_CSV):
        old = pd.read_csv(NEW_CSV, dtype=str, keep_default_na=False)
        old = old[~old["posted_day"].isin(names)]
        g = pd.concat([old, g.astype(str)], ignore_index=True)
    g = g.astype(str).sort_values(["posted_day", "department", "type"]).reset_index(drop=True)
    atomic_csv(g[NEW_COLS], NEW_CSV)


def write_view(store, through, first_day):
    """the forecast step's input (31_daily_forecast.py): notices posted in the VIEW_DAYS days before as_of"""
    as_of = through + timedelta(days=1)
    v = store[store["posted_day"] >= pd.Timestamp(as_of - timedelta(days=VIEW_DAYS))].copy()
    newest = v.groupby("sol_number")["posted_date"].transform("max")
    v["latest_version"] = ((v["posted_date"] == newest).fillna(False) | v["sol_number"].isna()).astype("int8")
    v.insert(0, "snapshot_date", pd.Series(pd.Timestamp(as_of), index=v.index).astype("datetime64[s]"))
    v["store_first_day"] = pd.Series(pd.Timestamp(first_day), index=v.index).astype("datetime64[s]")
    path = os.path.join(SNAP_DIR, f"sam_view_{as_of.isoformat()}.parquet")
    atomic_parquet(v.reset_index(drop=True), path)
    for old in sorted(glob.glob(os.path.join(SNAP_DIR, "sam_view_*.parquet")))[:-KEEP_VIEWS]:
        os.remove(old)
    return path


def build_latest(store, pulls, through, warnings, used, cap):
    st = day_status(pulls)
    finished = sorted(d for d, (s, _) in st.items() if s in FINISHED)
    win_from = through - timedelta(days=6)
    w = store[(store["posted_day"] >= pd.Timestamp(win_from)) & (store["posted_day"] <= pd.Timestamp(through))]
    sol = w[w["type"].isin(SOLICITATION_TYPES).fillna(False).to_numpy(bool)]
    dep = sol["department"].fillna("(blank)").astype(str)
    is_dod = (dep == DOD).to_numpy(bool)

    def both(mask):
        sub = sol[mask]
        return {"notices": int(len(sub)), "distinct_sol_numbers": int(sub["sol_number"].nunique())}

    civ = sol[~is_dod]
    top = []
    for name, n in civ.groupby(dep[~is_dod])["sol_number"].nunique().sort_values(ascending=False, kind="mergesort").head(10).items():
        top.append({"department": name, "distinct_sol_numbers": int(n), "notices": int((dep[~is_dod] == name).sum())})
    nd = store[store["posted_day"] == pd.Timestamp(through)]
    return {
        "schema": 2,
        "source": "SAM.gov Get Opportunities Public API v2 (api.sam.gov), with a personal API key",
        "built_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "through": through.isoformat(),
        "window": {"from": win_from.isoformat(), "to": through.isoformat(), "days": 7,
                   "days_pulled": sum(1 for d in finished if win_from <= d <= through)},
        "solicitation_types": list(SOLICITATION_TYPES),
        "solicitation_notices_posted": {
            "definition": ("notices of a solicitation type posted on the days of the window (new solicitations and amendments); "
                           "distinct_sol_numbers counts each solicitation number once"),
            "all": both(slice(None)), "civilian": both(~is_dod), "dod": both(is_dod), "dod_department_label": DOD},
        "top10_civilian_departments": top,
        "newest_day": {"date": through.isoformat(), "notices_all_types": int(len(nd)),
                       "by_type": {k: int(v) for k, v in nd["type"].fillna("(blank)").astype(str).value_counts().items()}},
        "store": {"first_day": finished[0].isoformat() if finished else None, "days_finished": len(finished),
                  "days_short": sorted(d.isoformat() for d, (s, _) in st.items() if s == "short"),
                  "notices": int(len(store))},
        "requests": {"this_run": used, "per_run_cap": cap, "daily_limit": DAILY_LIMIT},
        "keyword_flags": "from the title only",
        "warnings": warnings,
    }


# ------------------------------------------------------------------------------------------------------------------
# the draft release that keeps the store between robot runs
# ------------------------------------------------------------------------------------------------------------------
def _gh(args):
    return subprocess.run(["gh", *args], capture_output=True, text=True)


def need_gh(what):
    if shutil.which("gh") is None:
        raise SystemExit(f"{what} needs the GitHub CLI (gh) on PATH and GH_TOKEN")


def draft_exists(tag):
    """True if a DRAFT release with this tag exists.  Listed explicitly: a draft has no git tag yet, so a lookup by tag fails."""
    lst = _gh(["release", "list", "--limit", "200", "--json", "tagName,isDraft"])
    if lst.returncode != 0:
        raise RuntimeError("gh release list failed: " + lst.stderr.strip())
    return any(r["tagName"] == tag and r["isDraft"] for r in json.loads(lst.stdout or "[]"))


def sync_store():
    """merge the copy kept in the draft release into the local store (nothing to do if there is none yet)"""
    need_gh("--sync")
    if not draft_exists(STORE_TAG):
        say(f"no draft release {STORE_TAG} yet: starting from the local store")
        return
    tmp = tempfile.mkdtemp(prefix="sam_store_")
    try:
        d = _gh(["release", "download", STORE_TAG, "--pattern", STORE_ASSET, "--dir", tmp])
        got = os.path.join(tmp, STORE_ASSET)
        if d.returncode != 0 or not os.path.isfile(got):
            if "no assets" in (d.stderr or "").lower():
                say(f"the draft release {STORE_TAG} has no store yet")
                return
            raise RuntimeError("gh release download failed: " + (d.stderr or "").strip())
        merged = merge_store(read_store(), read_store(got))
        atomic_parquet(merged, STORE)
        say(f"merged the store from the draft release {STORE_TAG}: {len(merged):,} notices")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def upload_store():
    need_gh("--upload")
    if not draft_exists(STORE_TAG):
        say(f"creating DRAFT release {STORE_TAG}")
        c = _gh(["release", "create", STORE_TAG, "--draft", "--title", "SAM.gov notices pulled from the API (private store)",
                 "--notes", "Every SAM.gov notice pulled day by day from the Get Opportunities API, whitelisted fields only (no "
                            "contacts, awardee, street address or description). Draft: not public."])
        if c.returncode != 0:
            raise RuntimeError("gh release create failed: " + c.stderr.strip())
    u = _gh(["release", "upload", STORE_TAG, STORE, "--clobber"])
    if u.returncode != 0:
        raise RuntimeError("gh release upload failed: " + u.stderr.strip())
    say(f"  uploaded the store to the draft release {STORE_TAG}")


# ------------------------------------------------------------------------------------------------------------------
# --check
# ------------------------------------------------------------------------------------------------------------------
def shapes(values, n=3):
    """the most common shapes of some strings, with every digit shown as 9 and every letter as A (no names are printed)"""
    s = pd.Series([re.sub(r"[A-Za-z]", "A", re.sub(r"\d", "9", v)) for v in values if isinstance(v, str)], dtype=object)
    return ", ".join(f"{k} ({c})" for k, c in s.value_counts().head(n).items()) or "none"


def run_check(key, day, get):
    say(f"Checking the SAM.gov API with your key: notices posted on {day} (one request, first page only).")
    d = get(key, day, 0)
    recs = [r for r in (d.get("opportunitiesData") or []) if isinstance(r, dict)]
    say(f"OK: totalRecords {int(d.get('totalRecords') or 0):,}; {len(recs):,} notices in this page.")
    if not recs:
        return 0
    df = to_frame(recs, day, "check")
    say("How full each kept field is (share of notices with a value):")
    for c in COLUMNS:
        if c not in ("posted_day", "pulled_utc", "kw_brand", "kw_sole"):
            say(f"  {c:18s} {df[c].notna().mean():6.1%}")
    say("Shapes (9 = digit, A = letter):")
    say("  postedDate        " + shapes([text(r.get("postedDate")) for r in recs]))
    say("  responseDeadLine  " + shapes([text(r.get("responseDeadLine")) for r in recs]))
    say("  fullParentPathCode levels: " + ", ".join(f"{k} levels: {v}" for k, v in sorted(
        pd.Series([len((text(r.get("fullParentPathCode")) or "").split(".")) if text(r.get("fullParentPathCode")) else 0
                   for r in recs]).value_counts().items())))
    dropped = sorted({k for r in recs for k in r} - {p[0] for _, p in FIELDS} - {"fullParentPathName", "fullParentPathCode",
                                                                                 "department", "subTier", "office",
                                                                                 "naicsCodes", "reponseDeadLine"})
    say("  fields in the answer that are never kept: " + ", ".join(dropped))
    say(f"Nothing was written. This used 1 of your {DAILY_LIMIT} requests for today.")
    return 0


# ------------------------------------------------------------------------------------------------------------------
def parse_args(argv):
    ap = argparse.ArgumentParser(prog="python -I scripts/14_sam_daily_snapshot.py",
                                 description="Save the SAM.gov notices posted on each new day, from SAM.gov's official API.")
    ap.add_argument("--max-requests", type=int, default=DEFAULT_BUDGET,
                    help=f"requests this run may make (default {DEFAULT_BUDGET}; the key allows {DAILY_LIMIT} a day)")
    ap.add_argument("--max-days", type=int, default=30, help="days this run may pull (default 30; the budget usually stops first)")
    ap.add_argument("--start", help="first day to pull when nothing has been pulled yet (YYYY-MM-DD; default yesterday)")
    ap.add_argument("--sync", action="store_true", help="first merge in the store kept in the draft release sam-api-store (gh)")
    ap.add_argument("--upload", action="store_true", help="afterwards replace the store in the draft release sam-api-store (gh)")
    ap.add_argument("--require-key", action="store_true", help="a missing SAM_API_KEY is an error (exit 2), not a skip")
    ap.add_argument("--check", action="store_true", help="one request to check the key and the answer's shape; writes nothing")
    a = ap.parse_args(argv)
    if a.max_requests < 1 or a.max_requests > DAILY_LIMIT:
        ap.error(f"--max-requests must be between 1 and {DAILY_LIMIT}")
    return a


KEY_HELP = ("A new key: sign in at sam.gov, open Account Details, and generate the Public API Key again (SAM.gov's terms say to "
            "renew it every 90 days). Then update SAM_API_KEY on your computer and the SAM_API_KEY secret on GitHub. "
            "See docs/sam-api-key.md.")


def run(argv=None, now=None, get=None):
    a = parse_args(sys.argv[1:] if argv is None else argv)
    get = get or api_get
    key = (os.environ.get(KEY_ENV) or "").strip()
    if not key:
        msg = f"{KEY_ENV} is not set. This step needs a free personal SAM.gov API key: see docs/sam-api-key.md."
        if a.require_key or a.check:
            say("ERROR: " + msg + " Nothing was downloaded.")
            return EXIT_KEY
        say(msg + " Step skipped; nothing was downloaded.")
        return 0
    now = pd.Timestamp(now or datetime.now(timezone.utc))
    now = now.tz_localize("UTC") if now.tzinfo is None else now.tz_convert("UTC")
    yesterday = (now.tz_localize(None) + SAM_DAY_OFFSET).date() - timedelta(days=1)

    if a.check:
        try:
            return run_check(key, yesterday, get)
        except KeyRefused as e:
            say(f"SAM.gov refused the key ({redact(str(e), key)}).\n{KEY_HELP}")
            return EXIT_KEY
        except (RateLimited, ApiError) as e:
            say(f"ERROR: {redact(str(e), key)}")
            return EXIT_API

    if a.sync:
        try:
            sync_store()
        except RuntimeError as e:
            say(f"ERROR: {e}")
            return EXIT_API
    store = read_store()
    pulls = read_pulls()
    start = date.fromisoformat(a.start) if a.start else None
    days, gap = days_to_pull(pulls, yesterday, start, a.max_days)
    warnings = []
    if gap:
        warnings.append(f"days more than {MAX_BACK_DAYS} days before {yesterday} were never finished and are not pulled any more")
    if not days:
        say(f"Nothing to pull: every day through {yesterday} is done.")

    budget, mode, st = Budget(a.max_requests), known_mode(pulls), day_status(pulls)
    frames, log_rows, finished, stop = [], [], [], None
    for day in days:
        attempt = st.get(day, (None, 0))[1] + 1
        before = budget.used
        pulled_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        recs, total, reason, err = {}, None, None, None
        try:
            recs, total, found, reason = pull_day(key, day, budget, mode, get)
            mode = found or mode
        except KeyRefused as e:
            stop, err = "key", redact(str(e), key)
        except RateLimited as e:
            stop, err = "rate", redact(str(e), key)
        except ApiError as e:
            stop, err = "api", redact(str(e), key)
        used = budget.used - before
        if err is not None and used == 0:
            break
        if reason == "budget" and total is None:          # the budget ran out before this day started
            stop = "budget"
            break
        if err is None and reason is None:
            status = "complete"
        elif err is None and reason == "too-big":
            status = "short"
        elif err is None and reason in ("empty-page", "pages-exhausted") and attempt >= MAX_ATTEMPTS:
            status = "short"
        else:
            status = "incomplete"
        if recs:
            frames.append(to_frame(recs.values(), day, pulled_utc))
        log_rows.append({"posted_day": day.isoformat(), "pulled_utc": pulled_utc, "attempt": str(attempt), "requests": str(used),
                         "total_records": "" if total is None else str(total), "notices_received": str(len(recs)),
                         "status": status, "offset_mode": mode or ""})
        shortfall = "" if total is None or len(recs) >= total else f" ({total - len(recs):,} short)"
        say(f"{day}: {len(recs):,} notices of {'?' if total is None else f'{total:,}'} in {used} request(s): {status}{shortfall}"
            + (f" - {err}" if err else ""))
        if status in FINISHED:
            finished.append(day)
        if err is not None or reason == "budget":
            stop = stop or "budget"
            break

    if frames:
        store = merge_store(store, pd.concat(frames, ignore_index=True))
        atomic_parquet(store, STORE)
        say(f"store: {len(store):,} notices ({STORE})")
    if log_rows:
        pulls = pd.concat([pulls, pd.DataFrame(log_rows, columns=PULL_COLS)], ignore_index=True)
        atomic_csv(pulls[PULL_COLS], PULLS_CSV)
    if finished:
        update_new_csv(store, finished)
    st = day_status(pulls)
    done = sorted(d for d, (s, _) in st.items() if s in FINISHED)
    if stop == "budget":
        warnings.append("the request budget ran out before every day was pulled; the rest follows in the next runs")
    if stop == "rate":
        warnings.append("SAM.gov said today's request limit is used up; the rest follows tomorrow")
    if done and (log_rows or not glob.glob(os.path.join(SNAP_DIR, f"sam_view_{(done[-1] + timedelta(days=1)).isoformat()}.parquet"))):
        through = done[-1]
        if log_rows:
            write_json(LATEST_JSON, build_latest(store, pulls, through, warnings, budget.used, budget.cap))
        path = write_view(store, through, done[0])
        say(f"view for the forecast step: {path} (notices through {through})")
    if a.upload and frames:
        try:
            upload_store()
        except RuntimeError as e:
            say(f"ERROR: {e}")
            return EXIT_API
    say(f"requests used: {budget.used} of {budget.cap} allowed this run ({DAILY_LIMIT} a day for the key)")
    for w in warnings:
        say("WARNING: " + w)
    if stop == "key":
        say(f"SAM.gov refused the key.\n{KEY_HELP}")
        return EXIT_KEY
    if stop == "api" and not finished:
        say("The SAM.gov API did not answer properly; nothing new was finished. Run the step again later.")
        return EXIT_API
    return 0


def main(argv=None):
    return run(argv)


if __name__ == "__main__":
    sys.exit(main())
