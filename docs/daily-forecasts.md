# Daily sealed forecasts: a short guide

This guide is for a person, not a programmer. No AI is used anywhere in these steps.

## What happens each morning

1. The robot saves SAM.gov's list of active notices (`scripts/14_sam_daily_snapshot.py`).
2. It picks the **new** open solicitations (`scripts/31_daily_forecast.py`). A solicitation is picked when:
   - its newest notice is a "Solicitation" or a "Combined Synopsis/Solicitation", and no award notice for it is active;
   - its response deadline is at least one full day away;
   - its number has at least 8 letters and digits (shorter numbers cannot be matched to awards);
   - it has never been forecast before. Each solicitation gets one forecast, the first time it is seen.
3. The forecast model (version 2, `scripts/forecast_lib.py`) gives each one the chance that its competed award gets only one offer. Three simple baselines are stored next to it: the sector's past rate, the agency's past rate, and the buying office's past rate.
4. The day's forecasts are written to one private batch file, `PIO-D-<YYYYMMDD>.json`. Its SHA-256 fingerprint, a row of counts (including how many forecasts are "fresh", see below) and short hashes of the solicitation numbers go to the public folder [`forecasts/daily/`](../forecasts/daily/). The robot commits them the same morning. That is the seal.
5. The scoring step (`scripts/32_score_daily.py`) checks which forecasts have an award yet and updates `results/daily_forecast_scores.json`.

The model refuses to forecast if its award history is more than 9 months old. Retrain it first (`python run.py model`).

## How to run it yourself

You need the private model file in `<data folder>/models/model_v2.json` and a SAM.gov snapshot from today or yesterday.

```
python run.py sam-snapshot
python -I scripts/31_daily_forecast.py --dry-run
```

`--dry-run` prints how many solicitations would be forecast, by sector, and the spread of the forecasts. It writes nothing public. Without `--dry-run` it seals a batch and adds lines to `forecasts/daily/`. Run it at most once a day; a second run the same day changes nothing.

A real run prints only what the ledger publishes: the counts, and the mean forecast when there are at least 5 forecasts. The log of the GitHub robot is public, so do not use `--dry-run` there. With fewer than 5 forecasts, neither kind of run prints anything about them.

If a run fails halfway, run the same command again the same day. It never makes a second batch for a day:

- With `--upload`, the batch is uploaded to the draft release before any public line is written. A rerun on a fresh computer fetches it and seals that same file.
- The public lines are written in this order: `seen.txt`, `ledger.csv`, then `SHA256SUMS.txt` (the seal) last.
- If only some of them name today's batch and the batch file cannot be found, the script stops (exit code 3) and changes nothing. Put the file back, or rerun with `--upload`.
- On GitHub Actions a real run without `--upload` is refused, because the runner's disk is deleted after the run and a lost batch could never be scored.

To score:

```
python run.py live
python -I scripts/32_score_daily.py
```

Add `--defense` to also download Department of Defense awards. That is slower, and Defense data are published 90 days late, so Defense forecasts resolve later than civilian ones.

## What a batch file holds

A batch can never be changed, so each record keeps what is needed to check it later. The file starts with `schema_version` (now 1), the rules, the fingerprints of the model file and of the code, and the selection counts. Then comes one line per forecast with:

- the model's inputs exactly as they were used: the first-posted date, the days from posting to the first deadline, notice type, set-aside, NAICS and product or service codes, department, sub-tier, office code, the two keyword flags, and earlier presolicitation or sources-sought dates;
- what the model worked out from them: agency, sub-agency, sector and the office it matched;
- the newest notice type, the response deadline as posted (in UTC) and its date, the days from first posting to the forecast, and whether the forecast is fresh;
- the forecast and the three baselines.

From the batch file and the model file alone, anyone with both can recompute every forecast (`recompute` in `scripts/31_daily_forecast.py`; a test checks it). The first batch holds about 5,600 forecasts and is about 2.4 MB.

## Fresh and backlog

On the first day, every open solicitation is new to the robot, though most were posted days or weeks earlier. Later days are almost all new postings. A forecast is **fresh** if the solicitation was first posted at most 3 days before the forecast, otherwise it is **backlog**. The ledger counts the fresh forecasts of each batch, and the scores are also shown separately for fresh and backlog forecasts.

## How scoring works

The rule is written into every batch before any outcome exists.

- Look for the first ("base") action of a contract on USAspending with the same solicitation number (capital letters and digits only), dated **on or after the day offers were due**. That day is the response deadline posted when the forecast was made, recorded in the batch.
- Why: we only forecast solicitations whose offers are due at least a day later. An award dated before that day cannot come from those offers. It is an earlier award under the same number, for example under a rolling solicitation.
- This differs from the rule of the first sealed forecast, PIO-F1 ([`scoring-forecasts.md`](scoring-forecasts.md)), which counts awards from its forecast date on.
- If the deadline is later moved earlier and the award is dated before the recorded day, the forecast stays unresolved.
- The snapshot keeps deadlines in UTC, not in the notice's own time zone. So the recorded day is the UTC date. For a US deadline late in the evening it can be one day later than the local date.
- If several match, the earliest wins (ties: the larger value).
- It counts only if the award was competed and the number of offers is 1 to 998. One offer means outcome 1, more than one means 0.
- Otherwise the forecast stays unresolved and is not scored. The reason is counted: no award yet, not competed, or offers blank or 999.

Before scoring, each batch file is fingerprinted. A file that does not match its public fingerprint is refused.

The scores are the Brier score and log loss (lower is better) and the AUC (0.5 is a coin flip), for the model and for each baseline. There is also a calibration table (forecast bands against what happened) and the same numbers by sector, by month of forecast, and for fresh and backlog forecasts.

## The privacy choices

- **Private:** the batch files and the per-solicitation outcomes. They name solicitations and buying offices. A forecast is a risk indicator about a market, not an accusation, and a public list could be misread or could change how people bid.
- **Public:** the fingerprints, the ledger of counts, the short hashes of solicitation numbers already forecast, and aggregate scores.
- The short hashes are **not anonymous**. Anyone can hash a number from SAM.gov and find it in `seen.txt`. That shows only that it was forecast.
- Scores are published only once at least 20 forecasts have resolved. Cells with fewer than 5 resolved forecasts are hidden. Then more cells are hidden, smallest first, until at least two are hidden and they hold at least 5 resolved forecasts together. So no small cell can be worked out by subtracting the shown cells from the total.
- Anyone can tell the sector, month and freshness of each forecast solicitation, and whether it has resolved. So rows of different tables could be combined to single out a few forecasts, even when every row has 5 or more. Before publishing, the scorer checks every such combination. If one singles out fewer than 5 resolved forecasts, it hides the whole fresh and backlog table, then the month table, then the sector table, until none does.
- The scores file is rebuilt every day and its history is public. If it changed each time one more forecast resolved, the difference between two days would give away that one forecast. So the scores are kept as they were until the number of resolved forecasts has changed by at least 5. `scores.as_of` says when they were computed. The counts at the top of the file still change every day.
- The robot keeps the private files in draft (non-public) GitHub releases: the model (`model-v2`), the batches (`forecast-batches-<YYYY-MM>`) and the outcomes (`forecast-outcomes`).

## Known limits

- The snapshot holds only active notices. An earlier version of a notice that SAM.gov has already archived is missed, so "first posted" can be later than the true first posting.
- On the first day, every open solicitation is new, so the first batch is much larger than later ones, and mostly backlog.
- Civilian outcomes come from this fiscal year's live download. Matches found earlier are kept, but an award reported late, after a new fiscal year has started, can be missed.
- Many solicitations never lead to an award with a matching number. They stay unresolved.
- Only resolved forecasts are scored, and they are not a random sample. Quick, simple purchases resolve first, Defense awards resolve about 90 days later than civilian ones, and some solicitations never resolve. Early scores describe the forecasts that resolved so far, not all of them. The public file shows how many have resolved, by sector and by reason.
