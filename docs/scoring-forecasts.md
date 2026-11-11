# How the sealed forecasts are scored

This guide is for a person, not a programmer. No AI is needed or used.

## 1. The sealed forecasts

Two forecasts were made on **24 October 2026**, before their answers existed. Each file was fingerprinted (SHA-256) the same day. The fingerprints are in `forecasts/SHA256SUMS.txt` and in the README.

| | What it forecasts | File | Scored |
|---|---|---|---|
| **F2** (`PIO-F2-threshold-fy2027q1-20261024`) | How much purchases pile up just under the $250K and $350K limits in FY2027 Q1 (Oct to Dec 2026). | Public, in `forecasts/` | **Automatically**, by the daily job, from 15 Feb 2027. Final on **28 Feb 2027** |
| **F1** (`PIO-F1-open-solicitations-single-offer-20261024`) | The chance of a single offer for 150 open solicitations. | Fingerprint only | **Withdrawn**: not scored |

You do nothing for F2. The daily job checks its fingerprint every day, tracks the quarter as the data comes in (`13_track_threshold.py`), and posts the score (`12_resolve_threshold.py`) with exactly the estimator of the methodology's section 4.

The daily sealed forecasts have their own guide: [`daily-forecasts.md`](daily-forecasts.md).

## 2. How anyone can check the fingerprint

The public fingerprint of F2 is:

```
5a7009ba0f17e953bd1fea32d2b7fe9241a849b85222266506c5312e4790c43b
```

Compute the fingerprint of the file:

- Mac or Linux: `sha256sum forecasts/PIO-F2-threshold-fy2027q1-20261024.json`
- Windows (PowerShell): `Get-FileHash forecasts\PIO-F2-threshold-fy2027q1-20261024.json -Algorithm SHA256`

If the long string matches, the file is the one sealed on 24 Oct 2026. The fingerprint has been public since that day, so a matching file cannot have been edited since.
