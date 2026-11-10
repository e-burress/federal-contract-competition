# Daily sealed forecasts

Every morning the robot forecasts each **new** open federal solicitation on SAM.gov: the chance that its competed award gets **only one offer**. Each day's forecasts form one batch, `PIO-D-<YYYYMMDD>.json`. The batch is sealed the same morning, before any outcome exists. The forecasts are scored in public once the awards appear on USAspending.

A high forecast is a risk indicator about the market for a purchase. It is not an accusation against anyone.

## What is in this folder

| File | What it holds | Public? |
|---|---|---|
| `SHA256SUMS.txt` | One line per batch: its SHA-256 fingerprint, its file name and the time it was made (UTC). This line is the seal. | Yes |
| `ledger.csv` | One row per batch: date, model version, the model file's fingerprint, the number of forecasts by sector, the number of fresh forecasts (first posted at most 3 days before the forecast), the mean forecast (left blank when a batch has fewer than 5 forecasts) and the batch fingerprint. | Yes |
| `seen.txt` | A short hash of every solicitation number already forecast, so the robot never forecasts the same solicitation twice. | Yes |
| `PIO-D-<YYYYMMDD>.json` | The batch itself: one forecast per solicitation, with three simple baselines, the model's inputs (enough to recompute each forecast) and the response deadline. | **No.** Kept private. |

The scripts write these files: `scripts/31_daily_forecast.py` (forecast and seal) and `scripts/32_score_daily.py` (score). The scores are in `results/daily_forecast_scores.json`. The guide is [`docs/daily-forecasts.md`](../../docs/daily-forecasts.md).

## How sealing works

1. The robot writes the day's batch file once. It is never edited, and a rerun on the same day never makes a second batch.
2. In the same run it adds the file's SHA-256 fingerprint to `SHA256SUMS.txt` and commits it to this public repository. The commit time stamps the fingerprint in public.
3. A fingerprint changes completely if even one character of the file changes. So once the line is public, nobody can quietly change that day's forecasts. A file that does not match its line is refused by the scoring script.
4. The batch also records the rule that will decide each outcome, written before any outcome existed. Only an award dated on or after the day offers were due counts. That day is recorded in the batch.

These files only grow. A line, once committed, is never changed or removed.

## How to check a fingerprint

When a batch file is published (for example after its forecasts are scored), anyone can check it:

- Mac or Linux: `sha256sum PIO-D-20261110.json`
- Windows (PowerShell): `Get-FileHash PIO-D-20261110.json -Algorithm SHA256`

Compare the long string with the line for that file in `SHA256SUMS.txt`. If they match, the file is the one sealed that day. To see when the line became public, look at the commit that added it: `git log --format=%cI -S <fingerprint> -- forecasts/daily/SHA256SUMS.txt` (or use "Blame" on GitHub).

## What is private, and why

The batches name solicitations and the buying offices behind them. A list of "likely single-offer" purchases with office names could be misread as an accusation, and it could change how people bid. So the forecasts stay private, in the project's private data folder and in non-public draft releases. Only the fingerprints, the counts in the ledger and aggregate scores are public.

Scores are published only once at least 20 forecasts have resolved. Table cells with fewer than 5 resolved forecasts are hidden, together with enough other cells that they cannot be worked out by subtraction. The scores are recomputed only when at least 5 more forecasts have resolved, so no single forecast can be read from the difference between two days' files either. Rows of different tables (sector, month, fresh or backlog) are also checked together, so that no combination of them singles out fewer than 5 resolved forecasts. The robot's public log shows only what the ledger shows.

## About `seen.txt`

Each line is the first 16 hex characters of the SHA-256 of a normalized solicitation number (capital letters and digits only). **These hashes are not anonymous.** Anyone can take a number from SAM.gov, hash it the same way and look it up here. That tells them only that the solicitation was forecast, not what the forecast was. Lines starting with `#` name the batch that the hashes below them belong to.
