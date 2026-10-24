"""
common.py - small helpers shared by all scripts in this project.

Everything here is deliberately plain Python (standard library only) so you can read it top to bottom.

How to run any script in this folder (from the repository root):
    python -I scripts/<script>.py
The "-I" flag means "isolated mode": Python will not import code from the current folder or
from environment variables.  That is a safety habit when you are reading files you downloaded.
Because of "-I", each script adds the scripts folder to sys.path itself (see the first lines of each script).
"""
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

# ---- folders -----------------------------------------------------------------------------------
# PROJECT = the procurement_observatory folder (parent of scripts/)
PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(PROJECT, "data")
RESULTS = os.path.join(PROJECT, "results")
FORECASTS = os.path.join(PROJECT, "forecasts")
for _p in (DATA, RESULTS, FORECASTS):
    os.makedirs(_p, exist_ok=True)

# ---- how we introduce ourselves to servers ------------------------------------------------------
# A descriptive User-Agent is polite and some government sites reject the default Python one.
# Put your own contact address in the environment before running the download scripts, e.g.
#   Git Bash:    export PIO_CONTACT="you@example.com"
#   PowerShell:  $env:PIO_CONTACT = "you@example.com"
CONTACT = os.environ.get("PIO_CONTACT", "contact-not-set")
UA_USASPENDING = f"federal-procurement-observatory/1.0 (research; {CONTACT})"
UA_SAM = f"federal-procurement-observatory/1.0 (research; {CONTACT})"

# ---- the agencies in the slice (USAspending top-tier names) ------------------------------------
# Filled in after looking at where open SAM solicitations are (see docs/methodology.md).
AGENCIES = [
    "Department of Veterans Affairs",
    "Department of Health and Human Services",
    "Department of Homeland Security",
    "Department of the Interior",
    "Department of Agriculture",
]


def http_json(url, method="GET", body=None, headers=None, timeout=60, retries=4, pause=2.0, ua=UA_USASPENDING):
    """GET or POST a URL and return parsed JSON (or None after `retries` failures).
    Retries on network errors and on HTTP 429/5xx with a growing pause (be polite to servers)."""
    hdrs = {"User-Agent": ua, "Accept": "*/*"}
    if headers:
        hdrs.update(headers)
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            last_err = f"HTTP {e.code}"
            if e.code in (400, 404, 406):      # our fault, retrying will not help
                return None
            time.sleep(pause * (attempt + 1) * (3 if e.code == 429 else 1))
        except Exception as e:                  # timeouts, resets, bad JSON ...
            last_err = repr(e)
            time.sleep(pause * (attempt + 1))
    print("  giving up on", url[:120], last_err, flush=True)
    return None


def download_file(url, dest, ua=UA_USASPENDING, timeout=120, max_bytes=25_000_000, retries=3):
    """Download one file to `dest`, refusing anything bigger than max_bytes (the 25 MB rule)."""
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": ua})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                size = int(r.headers.get("Content-Length") or 0)
                if size > max_bytes:
                    raise RuntimeError(f"file is {size} bytes, above the {max_bytes} cap")
                buf = r.read(max_bytes + 1)
                if len(buf) > max_bytes:
                    raise RuntimeError("download exceeded cap")
            with open(dest, "wb") as fh:
                fh.write(buf)
            return len(buf)
        except Exception as e:
            print("  download retry", attempt + 1, repr(e)[:100], flush=True)
            time.sleep(3 * (attempt + 1))
    return None


def flatten(r):
    """Reduce one search result to the non-personal fields we need."""
    org = r.get("organizationHierarchy") or []
    by_level = {o.get("level"): o for o in org}
    dept = (by_level.get(1) or {}).get("name")
    agency = (by_level.get(2) or {}).get("name")
    deeper = [o for o in org if (o.get("level") or 0) >= 3]
    office_chain = " > ".join(o.get("name") or "" for o in deeper)
    # the deepest organisation level that carries a code is the contracting office code (AAC-like)
    office_code = None
    for o in sorted(org, key=lambda o: o.get("level") or 0, reverse=True):
        if o.get("code"):
            office_code = o["code"]
            break
    desc = r.get("descriptions") or []
    snippet = (desc[0].get("content") if desc and isinstance(desc[0], dict) else "") or ""
    kw_brand, kw_sole = kw_flags(r.get("title"), snippet)
    return {
        "kw_brand": kw_brand,
        "kw_sole": kw_sole,
        "notice_id": r.get("_id"),
        "sol": (r.get("solicitationNumber") or "").strip(),
        "type_code": (r.get("type") or {}).get("code"),
        "type": (r.get("type") or {}).get("value"),
        "publish": r.get("publishDate"),
        "response": r.get("responseDate"),
        "response_tz": r.get("responseTimeZone"),
        "modified": r.get("modifiedDate"),
        "title": r.get("title"),
        "canceled": r.get("isCanceled"),
        "active": r.get("isActive"),
        "n_mods": (r.get("modifications") or {}).get("count"),
        "parent_notice": r.get("parentNoticeId"),
        "dept": dept,
        "agency": agency,
        "office_chain": office_chain,
        "office_code": office_code,
    }


# ---- text flags -------------------------------------------------------------------------------------
# Two transparent keyword flags computed from the notice TITLE and the first ~300 characters of its description (as returned by the
# search endpoint).  Only the 0/1 flags are stored - never the text, which can contain names and e-mail addresses.
#   kw_brand : "brand name" (brand-name-or-equal requirements often have only one qualifying source)
#   kw_sole  : sole source / single source / only one responsible source / limited source / proprietary
_BRAND = re.compile(r"brand[\s-]*name", re.I)
_SOLE = re.compile(r"sole[\s-]*source|single[\s-]*source|only one (responsible )?source|one responsible source|limited[\s-]*source|proprietary", re.I)


def kw_flags(title, snippet):
    text = f"{title or ''} {snippet or ''}"
    return int(bool(_BRAND.search(text))), int(bool(_SOLE.search(text)))
