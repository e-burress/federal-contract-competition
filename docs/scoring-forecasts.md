# How the sealed forecasts are scored

This guide is for a person, not a programmer. You run a few commands. No AI is needed or used.

## 1. The two sealed forecasts

Both were made on **24 October 2026**, before their answers existed. Each file was fingerprinted (SHA-256) the same day. The fingerprints are in `forecasts/SHA256SUMS.txt` and in the README.

| | What it forecasts | File | Scored |
|---|---|---|---|
| **F1** (`PIO-F1-open-solicitations-single-offer-20261024`) | For 150 open solicitations in five departments (VA, HHS, DHS, Interior, USDA): the chance that the award gets only one offer. | **Private** until scored. It names buying offices. Only the fingerprint is public. | **By you**, on **31 Jan 2027**, **28 Feb 2027** and (final) **31 Mar 2027** |
| **F2** (`PIO-F2-threshold-fy2027q1-20261024`) | How much purchases pile up just under the $250K and $350K limits in FY2027 Q1 (Oct to Dec 2026). | Public, in `forecasts/` | **Automatically**, by the daily job, from 15 Feb 2027. Final on **28 Feb 2027** |

You do nothing for F2. The daily job checks its fingerprint every day and posts the score.

## 2. Scoring F1 (three times)

You need the private F1 file. Keep it under its original name:
`PIO-F1-open-solicitations-single-offer-20261024.json`.

1. Put the file in the `forecasts/` folder. (Git ignores it there, so it cannot be pushed by accident.) Or keep it anywhere and give its path in step 3.
2. Open a terminal in the project folder. Check that things work: `python run.py check`
3. Score it:

```
python run.py score-f1
```

or, with the path to the file:

```
python run.py score-f1 "C:\path\to\PIO-F1-open-solicitations-single-offer-20261024.json"
```

Run it on 31 Jan 2027, on 28 Feb 2027 and on 31 Mar 2027 (the last one is the final score). It downloads recent contract awards from USAspending. That takes a few minutes. Run it once per date.

To try the code without the internet, on old data (the numbers mean nothing):

```
python run.py score-f1 --from-processed
```

### What the script does

1. **It checks the fingerprint first.** If the file is not exactly the sealed file, it says `REFUSED` and scores nothing.
2. For each solicitation it looks for the award on USAspending. The rule was written into the forecast file in advance:
   - Match on the solicitation number (capital letters and digits only).
   - Only the first ("base") action of the award counts, dated on or after 24 Oct 2026.
   - If there are several, the earliest wins.
   - It counts only if the award was competed **and** the number of offers is 1 to 998. One offer means outcome 1. Anything else means outcome 0.
   - Otherwise the forecast stays **unresolved** and is not scored. The reason is counted: no award yet, not competed, or offers blank or 999.
3. It scores the resolved ones: Brier score, log loss and AUC for the model, and the same for the baseline (the department-wide rate). It also reports a calibration table by forecast band and counts by department.
4. It prints a short summary on the screen.

### What it tells you when it stops

| Exit code | Meaning |
|---|---|
| 0 | Done |
| 2 | The file was not found, or the command was wrong. The message says where to put the file. |
| 3 | **Refused.** The file does not match the public fingerprint. |
| 4 | The USAspending download failed. Nothing was written. Run the same command again. |

## 3. What is published and what stays private

| | Where | Public? |
|---|---|---|
| Totals only: how many forecasts, how many resolved, reasons for the rest, the scores, the calibration table, counts by department | `results/resolution_PIO-F1-open-solicitations-single-offer-20261024_<date>.json` | **Yes.** This is the file to commit. |
| One row per solicitation (numbers, award, offers, outcome) | `<data folder>/private/resolution_..._<date>.csv`. The data folder is `data/` unless you set another in `config.local.toml`. | **No.** Never commit or share it before the final score. |
| The F1 forecast file itself | `forecasts/` (ignored by Git) | **No**, until the final score. |

**Minimum for publication.** The script writes to `results/` only once at least 20 forecasts have resolved. With fewer, a total would give individual forecasts away (one resolved forecast makes the Brier score the square of its probability), so the aggregate JSON goes to `<data folder>/private/` instead and the script says how many more are needed. From 20 on, the public file hides every calibration band with fewer than 5 forecasts and merges every department with fewer than 5 resolved forecasts into one "(other departments)" row.

Before the final date, the JSON says `"provisional": true` and `"stage"` is `provisional` or `interim`. The final run has `"stage": "final"`.

## 4. After the final score (31 Mar 2027): publish the F1 file

The point of the fingerprint is that nobody can quietly change the forecast. To prove that, post the file exactly as it was.

1. Run the final score and commit its JSON from `results/`.
2. Copy the F1 file, **unchanged**, into `forecasts/`. Do not open and re-save it. Even one changed character breaks the fingerprint.
3. Remove the line `forecasts/PIO-F1-*.json` from `.gitignore` (or add the file with `git add -f`), then commit and push.
4. Update the README line that says only the fingerprint is public.

## 5. How anyone can check the fingerprint

The public fingerprint of F1 is:

```
a8d1bb3319bf2fc1439550ee99c971742408a99a4772db426b26dec15f0657d2
```

Compute the fingerprint of the posted file:

- Mac or Linux: `sha256sum forecasts/PIO-F1-open-solicitations-single-offer-20261024.json`
- Windows (PowerShell): `Get-FileHash forecasts\PIO-F1-open-solicitations-single-offer-20261024.json -Algorithm SHA256`

If the long string matches, the file is the one sealed on 24 Oct 2026. The fingerprint was already public in the README and in `forecasts/SHA256SUMS.txt` before the scoring, so a matching file cannot have been edited since.

Anyone can also re-run the score on the posted file with `python run.py score-f1` and compare with the published JSON.
