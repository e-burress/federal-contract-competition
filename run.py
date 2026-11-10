"""
run.py - run the whole project with one command. Plain Python; no AI is involved anywhere.

  python run.py check     check Python, the packages and the folders (add --online to test the data sources too)
  python run.py quick     download the published data snapshot, then rebuild every result, chart and the dashboard (minutes)
  python run.py all       rebuild everything from the government sources (about an hour, mostly waiting on downloads)
  python run.py daily     the morning refresh: SAM.gov snapshot, this fiscal year so far, forecast tracking, dashboard
  python run.py history   download the yearly tables of the long history (FY2008 onward) and rebuild its results
  python run.py verify    compare your results with the published ones (after quick or all)
  python run.py test      run the automatic tests (offline, seconds)
  python run.py steps     list every step
  python run.py STEP      run one step, for example: python run.py figures

Maintainers only:
  python run.py export-data     write the public snapshot files to dist/ from your own download
  python run.py publish-data    upload dist/ as a public GitHub release (needs the GitHub CLI, gh)

Never part of any command: the scripts that create the sealed forecasts (10 and 10b) and the forecast model (05-08, 11),
whose October 2026 inputs were not kept.  See docs/run-it-yourself.md.

This file uses only the standard library, so `check` works before anything is installed.
"""
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
PROCESSED = os.path.join(DATA, "processed")
RESULTS = os.path.join(ROOT, "results")
PUBLISHED = os.path.join(DATA, "published_results")       # the published results, set aside before a rebuild, for `verify`
DIST = os.path.join(ROOT, "dist")

if sys.version_info < (3, 11):
    sys.exit(f"This project needs Python 3.11 or newer; this is Python {sys.version.split()[0]}.")
import tomllib  # noqa: E402  (Python 3.11+)

with open(os.path.join(ROOT, "config.toml"), "rb") as _f:
    CONFIG = tomllib.load(_f)
# optional local data folder, as in scripts/common.py (FCC_DATA_DIR, or [paths] data in config.local.toml)
if os.environ.get("FCC_DATA_DIR"):
    DATA = os.environ["FCC_DATA_DIR"]
elif os.path.exists(os.path.join(ROOT, "config.local.toml")):
    with open(os.path.join(ROOT, "config.local.toml"), "rb") as _f:
        DATA = tomllib.load(_f).get("paths", {}).get("data", DATA)
PROCESSED = os.path.join(DATA, "processed")
PUBLISHED = os.path.join(DATA, "published_results")
SNAPSHOT = CONFIG["data"]["snapshot"]
DATA_REPO = CONFIG["project"]["repository"]                # the published snapshot always comes from the original project
CONTACT = os.environ.get("PIO_CONTACT") or CONFIG["contact"]["default"]

# name: (script, arguments, needs the internet, what it does)
STEPS = {
    "download":            ("scripts/01c_download_civilian.py", [], True, "contract records for every civilian agency, FY2024-FY2026 (resumable)"),
    "build-five":          ("scripts/02_build_awards.py", [], False, "one row per award: the five departments"),
    "build-civilian":      ("scripts/02_build_awards.py", ["--civilian"], False, "one row per award: every civilian agency"),
    "build-threshold":     ("scripts/bunching_lib.py", ["--rebuild"], False, "base actions of civilian purchase orders and contracts (threshold test)"),
    "atlas":               ("scripts/03_atlas.py", [], False, "single-offer shares, five departments"),
    "atlas-civilian":      ("scripts/03b_atlas_civilian.py", [], False, "single-offer shares, every civilian agency"),
    "quality":             ("scripts/16_quality_monitor.py", [], False, "flags sudden changes in how agencies record their data"),
    "threshold":           ("scripts/04_threshold.py", [], False, "the $250K/$350K pile-up test"),
    "threshold-quarterly": ("scripts/04b_threshold_quarterly.py", [], False, "pile-ups quarter by quarter"),
    "sql-check":           ("scripts/check_counts_sql.py", [], False, "independent SQL recount of the headline counts (needs the raw download)"),
    "figures":             ("scripts/make_figures.py", [], False, "the charts"),
    "report":              ("scripts/build_report.py", [], False, "the numbers in README.md and docs/methodology.md"),
    "dashboard":           ("scripts/build_dashboard.py", [], False, "the dashboard page (docs/index.html)"),
    "explorer":            ("scripts/build_explorer.py", [], False, "the data behind the interactive explorer (docs/explorer.html)"),
    "market-depth":        ("scripts/25_market_depth.py", [], True, "registered companies per industry code, from SAM.gov's public entity extract"),
    "sam-snapshot":        ("scripts/14_sam_daily_snapshot.py", [], True, "today's SAM.gov list of open notices and the daily counts"),
    "live":                ("scripts/15_civilian_fytd.py", [], True, "single-offer shares this fiscal year so far, every civilian agency"),
    "track-f2":            ("scripts/13_track_threshold.py", [], True, "how the sealed threshold forecast (F2) is tracking"),
    "history-atlas":       ("scripts/19_history_atlas.py", [], False, "single-offer shares FY2008 onward, civilian and Defense"),
    "threshold-history":   ("scripts/20_threshold_history.py", [], False, "pile-ups under every simplified-acquisition limit since FY2008"),
    "threshold-staggered": ("scripts/21_threshold_staggered.py", [], False, "the 2018 change, agency by agency (staggered adoption dates)"),
    "drivers":             ("scripts/23_drivers.py", [], False, "why some competed awards get one offer: shares, model, blocks"),
    "split-screen":        ("scripts/26_split_screen.py", [], False, "split-like purchase clusters: excess at each threshold over placebo lines"),
    "outcomes":            ("scripts/24_outcomes.py", [], False, "after award: single-offer versus multi-offer awards, from the history tables"),
    "threshold-scan":      ("scripts/27_threshold_scan.py", [], False, "pile-ups under every FAR dollar threshold since FY2008, with placebo values and before/after tests"),
    "model-build":         ("scripts/30_model_v2.py", ["--build-training"], False, "forecast model v2: the labeled training table (needs the history and SAM archive tables)"),
    "model-evaluate":      ("scripts/30_model_v2.py", ["--evaluate"], False, "forecast model v2: out-of-time evaluation against baselines"),
    "model-train":         ("scripts/30_model_v2.py", ["--train"], False, "forecast model v2: final fit, private model file and public model card"),
    "score-f1":            ("scripts/09_resolve_forecast.py", [], True, "score the sealed single-offer forecast F1 (optional: path to the private file)"),
}
PIPELINES = {
    "quick": ["get-snapshot", "atlas", "atlas-civilian", "quality", "threshold", "threshold-quarterly", "figures", "report", "dashboard", "explorer"],
    "all":   ["download", "build-five", "build-civilian", "build-threshold", "atlas", "atlas-civilian", "quality", "threshold",
              "threshold-quarterly", "sql-check", "figures", "report", "dashboard", "explorer"],
    "daily": ["sam-snapshot", "live", "track-f2", "dashboard", "explorer"],
    "model": ["model-build", "model-evaluate", "model-train"],
    "history": ["get-history", "history-atlas", "threshold-history", "threshold-staggered", "threshold-scan", "drivers", "split-screen", "outcomes", "report", "dashboard", "explorer"],
}
# results that change every day, are not rebuilt by quick/all, or are never published (office level); `verify` skips them
LIVE_OR_FROZEN = ("tracking_", "live_fy", "sam_daily_", "model_", "resolution_", "threshold_resolution_", "atlas_offices")


def say(msg=""):
    print(msg, flush=True)


def run_step(name, extra=()):
    if name == "get-snapshot":
        return get_snapshot()
    if name == "get-history":
        return get_history()
    script, args, online, what = STEPS[name]
    if not os.path.exists(os.path.join(ROOT, script)):
        say(f"--- {name}: skipped ({script} is not in this copy of the project)")
        return
    say(f"\n=== {name}: {what}")
    env = dict(os.environ, PIO_CONTACT=CONTACT, PYTHONIOENCODING="utf-8")
    t0 = time.time()
    r = subprocess.run([sys.executable, "-I", os.path.join(ROOT, script), *args, *extra], cwd=ROOT, env=env)
    if r.returncode != 0:
        hint = " It needs the internet: check your connection and run the same command again (finished parts are kept)." if online else ""
        sys.exit(f"\nStep '{name}' failed (exit code {r.returncode}).{hint}")
    say(f"=== {name}: done in {time.time() - t0:.0f} s")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def fetch(url, dest):
    req = urllib.request.Request(url, headers={"User-Agent": f"federal-contract-competition/1.1 ({CONTACT})"})
    with urllib.request.urlopen(req, timeout=120) as r, open(dest + ".part", "wb") as f:
        shutil.copyfileobj(r, f, 1 << 20)
    os.replace(dest + ".part", dest)


def get_snapshot():
    """Download the published data snapshot (a public GitHub release) into data/processed/ and check every file's SHA-256."""
    say(f"\n=== get-snapshot: published data snapshot {SNAPSHOT}")
    base = f"https://github.com/{DATA_REPO}/releases/download/{SNAPSHOT}/"
    os.makedirs(PROCESSED, exist_ok=True)
    sums_path = os.path.join(PROCESSED, "SHA256SUMS")
    try:
        fetch(base + "SHA256SUMS", sums_path)
    except Exception as e:
        sys.exit(f"Could not download the snapshot list from {base} ({e}). Check your internet connection and try again.")
    sums = dict(line.split()[::-1] for line in open(sums_path, encoding="utf-8") if line.strip())
    rename = {"base_awards_five.parquet": "base_awards.parquet"}           # the name the analysis scripts read
    for name, digest in sums.items():
        dest = os.path.join(PROCESSED, rename.get(name, name))
        if os.path.exists(dest) and sha256(dest) == digest:
            say(f"  {name}: already here")
            continue
        say(f"  {name}: downloading")
        fetch(base + name, dest)
        if sha256(dest) != digest:
            os.remove(dest)
            sys.exit(f"{name} did not match its published SHA-256 fingerprint; the download was damaged. Run the command again.")
    say("=== get-snapshot: done, every file matches its fingerprint")


def get_history():
    """Download the yearly history tables (public release history-v1) into data/history/processed/."""
    say("\n=== get-history: yearly tables of the long history (release history-v1)")
    api = f"https://api.github.com/repos/{DATA_REPO}/releases/tags/history-v1"
    try:
        req = urllib.request.Request(api, headers={"User-Agent": f"federal-contract-competition/1.1 ({CONTACT})",
                                                   "Accept": "application/vnd.github+json"})
        assets = json.load(urllib.request.urlopen(req, timeout=60))["assets"]
    except Exception as e:
        sys.exit(f"Could not read the history release ({e}). Check your internet connection and try again.")
    out = os.path.join(DATA, "history", "processed")
    os.makedirs(out, exist_ok=True)
    for a in sorted(assets, key=lambda a: a["name"]):
        dest = os.path.join(out, a["name"])
        if os.path.exists(dest) and os.path.getsize(dest) == a["size"]:
            continue
        say(f"  {a['name']}: downloading ({a['size'] / 1e6:.0f} MB)")
        fetch(a["browser_download_url"], dest)
    say(f"=== get-history: done, {len(assets)} files in data/history/processed")


def find_git():
    """The git program: on PATH, in GIT_BIN, or in its usual Windows folder. None when there is none."""
    for g in (shutil.which("git"), os.environ.get("GIT_BIN"), r"C:\Program Files\Git\cmd\git.exe"):
        if g and os.path.isfile(g):
            return g
    return None


def git_published():
    """The published result files, {name: bytes}, from the last commit (git show HEAD:results/<file>).
    None when this folder is not a git checkout or git is not available."""
    git = find_git()
    if not git or not os.path.exists(os.path.join(ROOT, ".git")):
        return None
    r = subprocess.run([git, "-C", ROOT, "ls-tree", "-z", "HEAD", "results/"], capture_output=True)
    if r.returncode != 0:
        return None
    wanted = []                                     # (object id, file name) of each published .csv and .json
    for entry in r.stdout.decode("utf-8").split("\0"):
        if "\t" not in entry:
            continue
        info, path = entry.split("\t", 1)
        kind, obj = info.split()[1:3]
        name = path[len("results/"):]
        if kind == "blob" and path.startswith("results/") and "/" not in name and name.endswith((".csv", ".json")):
            wanted.append((obj, name))
    if not wanted:
        return None
    # one git process for every file (the same bytes as git show HEAD:results/<file>)
    r = subprocess.run([git, "-C", ROOT, "cat-file", "--batch"], input="".join(o + "\n" for o, _ in wanted).encode(), capture_output=True)
    if r.returncode != 0:
        return None
    out, pos = {}, 0
    for obj, name in wanted:
        eol = r.stdout.index(b"\n", pos)
        header = r.stdout[pos:eol].split()
        if len(header) != 3 or header[1] != b"blob":
            return None
        size = int(header[2])
        out[name] = r.stdout[eol + 1:eol + 1 + size]
        pos = eol + 1 + size + 1                    # the content is followed by one newline
    return out


STAMP = "rebuild_started.txt"      # in PUBLISHED: when the last `quick` or `all` started, so `verify` knows what it rebuilt


def keep_published_copy(cmd=""):
    """Set the published results aside before a rebuild, so `verify` compares against them.
    In a git checkout they come from the last commit, so a second run still compares with the published files and not with
    the previous rebuild.  Without git, results/ is copied only when there is no copy yet."""
    started = time.time()
    published = git_published()
    if published is not None:
        tmp = PUBLISHED + ".part"
        if os.path.isdir(tmp):
            shutil.rmtree(tmp)
        os.makedirs(tmp)
        for name, blob in published.items():
            with open(os.path.join(tmp, name), "wb") as f:
                f.write(blob)
        if os.path.isdir(PUBLISHED):
            shutil.rmtree(PUBLISHED)
        os.replace(tmp, PUBLISHED)
        say(f"verify will compare with the published results ({len(published)} files from the last git commit)")
    elif os.path.isdir(PUBLISHED):
        say(f"verify will compare with the copy of results/ already in {PUBLISHED} (git is not available, so it was not replaced)")
    else:
        shutil.copytree(RESULTS, PUBLISHED)
        say(f"verify will compare with a copy of results/ taken now, in {PUBLISHED} (git is not available; the copy is kept for later runs)")
    with open(os.path.join(PUBLISHED, STAMP), "w", encoding="utf-8") as f:
        f.write(f"{started}\n{cmd}\n")


def verify():
    """Compare rebuilt results with the published ones: numbers must agree to 9 significant digits.
    Files the last `quick` or `all` did not write are listed apart, never counted as a match."""
    try:
        import pandas as pd
    except ImportError:
        sys.exit("verify needs the packages in requirements.txt (pandas).")
    if not os.path.isdir(PUBLISHED):
        sys.exit("Nothing to compare with yet: run `python run.py quick` (or `all`) first.")
    started, cmd = None, "the last rebuild"
    stamp = os.path.join(PUBLISHED, STAMP)
    if os.path.exists(stamp):
        with open(stamp, encoding="utf-8") as f:
            lines = f.read().splitlines()
        started = float(lines[0])
        cmd = f"`{lines[1]}`" if len(lines) > 1 and lines[1] else cmd
    same, differ, missing, stale = 0, [], [], []
    for name in sorted(os.listdir(PUBLISHED)):
        if name.startswith(LIVE_OR_FROZEN) or not name.endswith((".csv", ".json")):
            continue
        a, b = os.path.join(PUBLISHED, name), os.path.join(RESULTS, name)
        if not os.path.exists(b):
            missing.append(name)
            continue
        if started is not None and os.path.getmtime(b) < started - 2:     # 2 s for coarse file-system clocks
            stale.append(name)
            continue
        if name.endswith(".json"):
            ok = close_json(json.load(open(a, encoding="utf-8")), json.load(open(b, encoding="utf-8")))
            problem = "" if ok else "values differ"
        else:
            problem = compare_csv(pd, a, b)
            ok = not problem
        if ok:
            same += 1
        else:
            differ.append(f"{name}: {problem}")
    say(f"\nverify: {same} result files match the published ones" + (f"; {len(differ)} differ" if differ else "")
        + (f"; {len(missing)} are missing" if missing else "")
        + (f"; {len(stale)} were not rebuilt by {cmd}, so they were not compared" if stale else ""))
    for d in differ:
        say("  differs  " + d)
    for m in missing:
        say("  missing  " + m)
    for s in stale:
        say("  not rebuilt by this command  " + s)
    if differ:
        sys.exit("verify: differences found. After `quick` this should not happen; after `all` small differences are"
                 " normal, because agencies keep correcting their reports.")


def compare_csv(pd, a, b):
    x, y = pd.read_csv(a), pd.read_csv(b)
    if list(x.columns) != list(y.columns) or len(x) != len(y):
        return f"shape {x.shape} vs {y.shape}"
    for c in x.columns:
        numeric = all(pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s) for s in (x[c], y[c]))
        if numeric:
            bad = ~((x[c] - y[c]).abs() <= 1e-9 * y[c].abs().clip(lower=1) + 1e-12) & ~(x[c].isna() & y[c].isna())
        else:
            bad = x[c].astype(str) != y[c].astype(str)
        if bad.any():
            i = bad.idxmax()
            return f"column {c}, row {i}: {x[c][i]} vs {y[c][i]}"
    return ""


def close_json(x, y):
    if isinstance(x, dict) and isinstance(y, dict):
        return x.keys() == y.keys() and all(close_json(x[k], y[k]) for k in x if k not in ("built_utc", "as_of"))
    if isinstance(x, list) and isinstance(y, list):
        return len(x) == len(y) and all(close_json(p, q) for p, q in zip(x, y))
    if isinstance(x, (int, float)) and isinstance(y, (int, float)) and not isinstance(x, bool):
        if math.isnan(x) or math.isnan(y):
            return math.isnan(x) and math.isnan(y)          # NaN matches NaN only
        return abs(x - y) <= 1e-9 * max(1.0, abs(y))
    return x == y


def check(online=False):
    say(f"Python {sys.version.split()[0]}: OK (3.11 or newer needed)")
    pins = {}
    for line in open(os.path.join(ROOT, "requirements.txt"), encoding="utf-8"):
        line = line.split("#")[0].strip()
        if "==" in line:
            k, v = line.split("==")
            pins[k.strip()] = v.strip()
    import importlib.metadata as md
    problems = 0
    for pkg, want in pins.items():
        try:
            have = md.version(pkg)
            note = "OK" if have == want else f"installed {have}, tested with {want} (usually fine)"
        except md.PackageNotFoundError:
            note, problems = "MISSING: run  pip install -r requirements.txt  (or use uv, see docs/run-it-yourself.md)", problems + 1
        say(f"{pkg:13s} {note}")
    for folder in (DATA, RESULTS, os.path.join(ROOT, "docs")):
        os.makedirs(folder, exist_ok=True)
        probe = os.path.join(folder, ".write_test")
        open(probe, "w").close()
        os.remove(probe)
    say("folders: OK (data/, results/, docs/ are writable)")
    inputs = {"raw download (for `all`)": os.path.join(DATA, "usaspending_civilian"),
              "processed tables (from `quick` or `all`)": os.path.join(PROCESSED, "base_awards.parquet")}
    for label, path in inputs.items():
        say(f"{label}: {'present' if os.path.exists(path) and (os.path.isfile(path) or os.listdir(path)) else 'not yet'}")
    if online:
        for url in ("https://api.usaspending.gov/api/v2/awards/last_updated/", f"https://github.com/{DATA_REPO}"):
            try:
                urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "federal-contract-competition/1.1"}), timeout=30)
                say(f"reachable: {url}")
            except Exception as e:
                say(f"NOT reachable: {url} ({e})")
                problems += 1
    if problems:
        sys.exit(f"\ncheck: {problems} problem(s) above.")
    say("\ncheck: everything needed is in place.")


def export_data():
    """Write the public snapshot to dist/: the processed tables minus office names and company identifiers (left out to keep
    the tables small, not to hide anything: contract numbers stay, and every award is public on USAspending.gov)."""
    import pandas as pd
    os.makedirs(DIST, exist_ok=True)
    plan = {"base_awards_five.parquet": ("base_awards.parquet", ["awarding_office_name", "recipient_uei", "recipient_parent_uei"]),
            "base_awards_civilian.parquet": ("base_awards_civilian.parquet", []),
            "threshold_base_actions.parquet": ("threshold_base_actions.parquet", [])}
    meta = {"snapshot": SNAPSHOT, "source": "USAspending.gov contract transactions (Advanced Search download), civilian agencies, FY2024-FY2026",
            "left_out": "buying-office names and company identifiers, only to keep the tables small and focused",
            "note": "Not anonymous: contract numbers are kept, and every award is public on USAspending.gov.", "files": {}}
    manifests = [json.load(open(os.path.join(DATA, "usaspending_civilian", m), encoding="utf-8"))
                 for m in os.listdir(os.path.join(DATA, "usaspending_civilian")) if m.startswith("manifest_")]
    stamps = [m.get("downloaded_utc", "") for m in manifests if m.get("downloaded_utc")]
    zips = [os.path.join(DATA, "usaspending_civilian", z) for z in os.listdir(os.path.join(DATA, "usaspending_civilian")) if z.endswith(".zip")]
    meta["downloaded"] = (max(stamps)[:10] if stamps else
                          time.strftime("%Y-%m-%d", time.gmtime(max(os.path.getmtime(z) for z in zips))))
    for out, (src, drop) in plan.items():
        df = pd.read_parquet(os.path.join(PROCESSED, src))
        df = df.drop(columns=[c for c in drop if c in df.columns])
        df.to_parquet(os.path.join(DIST, out), index=False, compression="zstd")
        meta["files"][out] = {"rows": int(len(df)), "columns": list(df.columns)}
        say(f"  {out}: {len(df):,} rows, {len(df.columns)} columns")
    shutil.copy2(os.path.join(DATA, "usaspending_civilian", "agencies.json"), os.path.join(DIST, "agencies.json"))
    json.dump(meta, open(os.path.join(DIST, "snapshot.json"), "w", encoding="utf-8"), indent=1)
    with open(os.path.join(DIST, "SHA256SUMS"), "w", encoding="utf-8") as f:
        for name in sorted(os.listdir(DIST)):
            if name != "SHA256SUMS":
                f.write(f"{sha256(os.path.join(DIST, name))}  {name}\n")
    say(f"export-data: wrote dist/ for snapshot {SNAPSHOT}. Next: python run.py publish-data")


def publish_data():
    gh = shutil.which("gh") or os.environ.get("GH_BIN")
    if not gh or not os.path.isdir(DIST):
        sys.exit("publish-data needs dist/ (run export-data) and the GitHub CLI `gh` on PATH (or GH_BIN set to it).")
    files = [os.path.join(DIST, n) for n in sorted(os.listdir(DIST))]
    notes = (f"Processed tables behind the published results (snapshot {SNAPSHOT}). `python run.py quick` downloads them. "
             "Buying-office names and company identifiers are left out, only to keep the tables small and focused. "
             "The tables are not anonymous: contract numbers are kept, and every award is public on USAspending.gov. "
             "See snapshot.json and docs/data-dictionary.md.")
    subprocess.run([gh, "release", "create", SNAPSHOT, *files, "--title", f"Data snapshot {SNAPSHOT[5:]}", "--notes", notes,
                    "--repo", DATA_REPO], check=True)


def main():
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(errors="replace")
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help", "help"):
        say(__doc__)
        return
    cmd = args[0]
    t0 = time.time()
    if cmd == "check":
        check(online="--online" in args)
    elif cmd == "test":
        r = subprocess.run([sys.executable, "-I", "-m", "unittest", "discover", "-s", "tests", "-v"], cwd=ROOT,
                           env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        sys.exit(r.returncode)
    elif cmd == "steps":
        for name, (script, a, online, what) in STEPS.items():
            say(f"  {name:20s} {what}{'  [internet]' if online else ''}")
        for p, names in PIPELINES.items():
            say(f"\n  {p}: " + " -> ".join(names))
    elif cmd in PIPELINES:
        if cmd in ("quick", "all"):
            keep_published_copy(cmd)
        for name in PIPELINES[cmd]:
            run_step(name)
        if cmd == "quick":
            verify()
        say(f"\n{cmd}: finished in {(time.time() - t0) / 60:.1f} minutes. Open docs/index.html in a browser to see the dashboard.")
    elif cmd == "verify":
        verify()
    elif cmd in STEPS or cmd in ("get-snapshot", "get-history"):
        run_step(cmd, args[1:])
    elif cmd == "export-data":
        export_data()
    elif cmd == "publish-data":
        publish_data()
    else:
        sys.exit(f"Unknown command '{cmd}'. Try: python run.py help")


if __name__ == "__main__":
    main()
