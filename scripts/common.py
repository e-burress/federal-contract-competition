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
import tomllib
import urllib.error
import urllib.parse
import urllib.request

# ---- folders -----------------------------------------------------------------------------------
# PROJECT = the repository folder (parent of scripts/)
PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(PROJECT, "data")
# Optional: keep the big data folder somewhere else (for example outside a cloud-synced folder).  Set the environment variable
# FCC_DATA_DIR, or put   [paths]  data = "D:/somewhere"   in a file named config.local.toml next to config.toml (git-ignored).
_local = os.path.join(PROJECT, "config.local.toml")
if os.environ.get("FCC_DATA_DIR"):
    DATA = os.environ["FCC_DATA_DIR"]
elif os.path.exists(_local):
    with open(_local, "rb") as _f:
        DATA = tomllib.load(_f).get("paths", {}).get("data", DATA)
RESULTS = os.path.join(PROJECT, "results")
FORECASTS = os.path.join(PROJECT, "forecasts")
for _p in (DATA, RESULTS, FORECASTS):
    os.makedirs(_p, exist_ok=True)

# ---- settings (config.toml in the repository root) ----------------------------------------------
with open(os.path.join(PROJECT, "config.toml"), "rb") as _f:
    CONFIG = tomllib.load(_f)
# in a GitHub fork, GITHUB_REPOSITORY names the fork, so its robot identifies itself correctly
REPOSITORY = os.environ.get("GITHUB_REPOSITORY") or CONFIG["project"]["repository"]
FISCAL_YEARS = CONFIG["analysis"]["fiscal_years"]
WINDOW_END = CONFIG["analysis"]["window_end"]

# ---- how we introduce ourselves to servers ------------------------------------------------------
# A descriptive User-Agent is polite and some government sites reject the default Python one.
# It says who is downloading: the PIO_CONTACT environment variable if set, otherwise the repository's web address
# (in a fork: the fork's address). Use a web address, not a personal e-mail.
CONTACT = os.environ.get("PIO_CONTACT") or (
    f"https://github.com/{os.environ['GITHUB_REPOSITORY']}" if os.environ.get("GITHUB_REPOSITORY") else CONFIG["contact"]["default"])
UA_USASPENDING = f"federal-contract-competition/1.0 (research; {CONTACT})"
UA_SAM = f"federal-contract-competition/1.0 (research; {CONTACT})"

# ---- the five departments of the in-depth analysis (config.toml) ------------------------------
AGENCIES = CONFIG["analysis"]["five_departments"]


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


# ---- text flags -------------------------------------------------------------------------------------
# Two transparent keyword flags computed from a notice's TITLE and, where available, the first ~300 characters of its description.
# Only the 0/1 flags are stored - never the text, which can contain names and e-mail addresses.
#   kw_brand : "brand name" (brand-name-or-equal requirements often have only one qualifying source)
#   kw_sole  : sole source / single source / only one responsible source / limited source / proprietary
_BRAND = re.compile(r"brand[\s-]*name", re.I)
_SOLE = re.compile(r"sole[\s-]*source|single[\s-]*source|only one (responsible )?source|one responsible source|limited[\s-]*source|proprietary", re.I)


def kw_flags(title, snippet):
    text = f"{title or ''} {snippet or ''}"
    return int(bool(_BRAND.search(text))), int(bool(_SOLE.search(text)))
