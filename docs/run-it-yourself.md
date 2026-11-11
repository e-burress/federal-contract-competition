# Run it yourself

You do not need to know how to code. Pick the way that fits you. They go from easiest to most hands-on.

## 1. Just look

The dashboard is here: https://e-burress.github.io/federal-contract-competition/

It is updated every morning. Nothing to install.

## 2. One click in your browser (GitHub Codespaces)

This gives you a free computer in the cloud with everything already set up.

1. Open https://codespaces.new/e-burress/federal-contract-competition
2. Sign in to GitHub (a free account is fine) and press **Create new codespace**.
3. Wait a minute or two. A window opens with a terminal at the bottom.
4. Type this and press Enter:

```
python run.py quick
```

GitHub gives personal accounts some free Codespaces hours each month. A quick run uses only a small part of that. Stop or delete the codespace when you are done so it does not keep using hours.

## 3. Your own automatic copy (Fork)

You get your own copy of the dashboard that refreshes itself every morning.

1. Sign in to GitHub and press **Fork** at the top of the repository page.
2. In your fork, open **Settings**, then **Actions**, and turn Actions on if GitHub asks.
3. In **Settings**, open **Pages**. Under **Source**, choose **Deploy from a branch**. Set the branch to `main` and the folder to `/docs`. Save.
4. Optional: to include SAM.gov's daily notices, get your own free SAM.gov API key and add it as the repository secret `SAM_API_KEY` ([`sam-api-key.md`](sam-api-key.md)). Without it that one step is skipped.
5. Open the **Actions** tab. Choose **Daily dashboard refresh** in the list on the left. Press **Run workflow**.
6. When it finishes, your dashboard is at `https://<your-username>.github.io/<repo>/`. Replace `<your-username>` with your GitHub name and `<repo>` with the name of your fork (usually `federal-contract-competition`).

## 4. On your own computer

Pick one route. You only need one.

### Route A: uv (easiest)

[uv](https://docs.astral.sh/uv/) is a free tool that installs the right Python and packages for you. Install it by typing one line yourself.

Windows (PowerShell):

```
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

macOS or Linux (Terminal):

```
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Close the window and open a new one. Then go to the project folder and type:

```
uv run run.py quick
```

To get the project folder, press the green **Code** button on GitHub and choose **Download ZIP**. Unzip it. Or use `git clone https://github.com/e-burress/federal-contract-competition`.

### Route B: plain Python and pip

You need Python 3.11 or newer from https://www.python.org/downloads/ (on Windows, tick "Add python.exe to PATH").

Windows (PowerShell), inside the project folder:

```
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python run.py quick
```

If PowerShell says scripts are disabled, skip the second line and use `.venv\Scripts\python.exe` instead of `python` in the last two lines.

macOS or Linux, inside the project folder:

```
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python run.py quick
```

Next time, only activate the environment again and run `python run.py quick`.

### Route C: double-click a file

In the project folder, double-click the file for your system. It runs `python run.py quick` for you and keeps the window open.

- Windows: `run-windows.bat`. If Windows warns that it protected your PC, choose **More info**, then **Run anyway**.
- macOS: `run-mac.command`. The first time, macOS may refuse to open it. Right-click the file, choose **Open**, then **Open** again. If it still will not run, open Terminal, type `chmod +x ` (with a space after it), drag the file into the window and press Enter.
- Linux: `run-linux.sh`. Your file manager may need to be set to run scripts, or type `./run-linux.sh` in a terminal.

These files use uv if you have it. If not, they use Python 3.11 or newer and set up a `.venv` folder the first time. If you have neither, they tell you what to type to install one. They never install anything on their own.

## 5. What each command does

Type these after `python run.py` (or after `uv run run.py`).

| Command | What it does | How long |
|---|---|---|
| `check` | Checks your Python, packages and folders. | Seconds |
| `quick` | Downloads the published data snapshot from a public GitHub release and rebuilds every result, chart and the dashboard. | Minutes |
| `all` | Full rebuild from the government sources. Mostly waiting on downloads. | About an hour |
| `daily` | The morning refresh that the automatic copy runs. | Minutes |
| `verify` | Compares your results with the published ones. | Seconds |
| `test` | Offline tests. | Seconds |
| `steps` | Lists all the steps. | Instant |
| a step name | Runs just that one step. Anything typed after the name goes to its script, for example `python run.py daily-forecast --dry-run`. | Varies |

Some steps worth knowing by name:

| Step | What it does |
|---|---|
| `brief` | Rebuilds the one-page brief, `docs/brief.html`. Part of `quick`, `all`, `daily` and `history`. |
| `reconcile` | Compares our yearly transaction counts and dollars with USAspending's own totals. Needs the history tables and the internet. Part of `history`. Writes `results/reconciliation.csv` and `results/reconciliation_summary.json`. |
| `daily-forecast` | Forecasts the day's new open solicitations and seals the batch (the batch stays private, its fingerprint goes to `forecasts/daily/`). Needs the private model file, so it is in no pipeline. Use `--dry-run` to write nothing public. See [`daily-forecasts.md`](daily-forecasts.md). |
| `score-daily` | Scores the sealed daily forecasts once the awards appear and writes the totals to `results/daily_forecast_scores.json`. Needs the private batch files, so it is in no pipeline. |

Start with `check`, then `quick`, then `verify`.

## 6. What cannot be re-run, and why

- **The sealed forecasts in `forecasts/`.** They were written once, before the answers were known, and fingerprinted so nobody can change them later. They are never re-created. Script 10b refuses to overwrite PIO-F2.
- **The daily forecast batches.** They name solicitations and buying offices, so they stay private; only their fingerprints and counts are public in `forecasts/daily/`. Without the private model file, `daily-forecast` cannot run, and without the batch files, `score-daily` cannot score.

Everything else can be rebuilt.

## 7. Troubleshooting

- **A download is slow or fails.** Run the same command again. Parts that already finished are skipped.
- **A download says NOT VERIFIED or REJECTED.** The file did not match USAspending's own row count, or the count could not be checked. Nothing unchecked is used. Run the same command again later. See [`download-integrity.md`](download-integrity.md).
- **Out of memory.** Close other programs. The full rebuild (`all`) needs about 3 GB of free memory. `quick` needs less.
- **Spaces in the folder path (Windows).** These are fine.
- **Downloads are blocked.** Work or school networks sometimes block them. Try a home network, or use Codespaces (route 2).
- **Something else.** Run `python run.py check` first. It says what is missing. You can also open an issue on GitHub.
