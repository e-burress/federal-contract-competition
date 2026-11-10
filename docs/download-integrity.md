# Download integrity: cut-off files from USAspending

## What went wrong

On November 10, 2026, USAspending's download service (`POST /api/v2/download/transactions/`) returned cut-off files.
The status said "finished". The ZIP opened fine. But it held far fewer rows than the window has.

One example, checked by hand the same day: civilian agencies, October 1 to December 31, 2007.

- USAspending's own count for that request (`POST /api/v2/search/spending_by_transaction_count/`, same filters) is 276,880 transactions.
- The download service answered at once with a file made earlier that day. Its status said `total_rows: 130000`.
- The CSV inside held exactly 130,000 rows. So the status agreed with the file, and both were wrong.
- The rows that were there covered all 92 days of the quarter, so the dates did not give it away.
- Nothing in the status response marked the file as cut off. `status` was "finished" and `message` was empty.
- The same request on two computers, hours apart, got the same file back. The service seems to keep a file for an identical request.
- A different request worked. October 2007 alone was made fresh in about three minutes. It held 111,883 rows, the exact count.
- Two other windows were complete: April to June 2008 (328,683 rows) and October to December 2013 (112,916 rows) matched their counts exactly.
  April to June 2014 was cut off: 110,000 rows in the file, 149,021 in the count.
  July to September 2014 was cut off too: 190,000 rows against 192,333. That is only 1.2 percent short.

Round numbers (110,000, 120,000, 130,000) were a hint. They are not a test: a cut-off file need not have a round number.

The yearly tables built from these files were 3.6 to 31 percent short (fiscal years 2008 to 2016 on one computer, and
2008, 2009, 2014, 2015 and 2016 in the public release `history-v1`). The comparison with USAspending's yearly totals
(`scripts/28_reconcile.py`) caught it afterwards. Nothing caught it at download time.

## How the project protects itself now

Every script that uses the download service goes through one function, `fetch_verified` in
`scripts/01_download_usaspending.py`. After a file arrives it:

1. counts the CSV data rows inside the ZIP;
2. asks USAspending's count service for the same filters and dates;
3. accepts the file only if the two agree within a small tolerance: at most 5 rows, or 0.05 percent of the count,
   whichever is larger. USAspending adds and corrects records every day, so a few rows of difference are normal.
   Complete files matched to the exact row. Cut-off files were short by 1.2 to 53 percent (2,333 to 146,880 rows).

If the numbers do not match, the file is deleted and never used. The window is asked for again as smaller windows
(a quarter becomes its months, a month becomes two halves). These are new requests, so the service makes new files.
Each piece is checked the same way. After two rounds of splitting the window is reported as failed.

The pieces of a window never overlap, so no row is counted twice. Each window keeps a record, `manifest_<window>.json`,
with the expected rows, the received rows and `verified: true` or `false`.

If the count service cannot be reached, the file is not verified. What happens next depends on the script:

| Script | If a file is cut off | If the count service is down |
|---|---|---|
| `17_history_download.py` (long history) | split and retry; fail if still wrong | fail; run again later |
| `12_resolve_threshold.py` (the sealed F2 score) | split and retry; stop and score nothing if still wrong | stop and score nothing |
| `13_track_threshold.py` (F2 tracking) | as 12 | as 12 |
| `09_resolve_forecast.py` (the sealed F1 score) | split and retry; stop and score nothing if still wrong | stop and score nothing |
| `32_score_daily.py --defense` | split and retry; stop and write nothing if still wrong | stop and write nothing |
| `15_civilian_fytd.py` (daily live table) | split and retry; stop and write nothing if still wrong | write the table, marked `"verified": false` |
| `01`, `01b`, `01c` (snapshot downloads) | split and retry; report failed windows | report failed windows; the file is kept and checked on the next run |

The live table is the one exception because it is refreshed every day and is clearly marked: the dashboard shows a
"Not verified" note. `15_civilian_fytd.py` also leaves `download_check.json` next to the live files once every window is
downloaded. `32_score_daily.py` scores the daily forecasts from those files only when that record exists and says
`verified: true`; otherwise it stops and writes nothing. A cut-off that was detected is never tolerated anywhere.

Two gates sit on top:

- `18_history_build.py` follows the records from each quarter and month down to the files it uses. It writes
  `verified: true` into `history_FY<year>.json` only when every window of both sectors is there and every file matched.
  Files that no record points to are ignored. The robot that publishes the yearly tables
  (`.github/workflows/history.yml`) refuses to upload a year that is not verified.
- The weekly analysis robot (`.github/workflows/history-analysis.yml`) runs two checks first and commits nothing if
  either fails:
  - `python run.py get-history --require-verified` fails, listing the years, when any yearly table is marked not
    verified or carries no verification record. (Run by hand without the switch, `get-history` only prints messages.)
  - `28_reconcile.py --strict` compares each year with USAspending's like-for-like total today. A settled year (one
    that ended more than 15 months before the check, `SETTLED_MONTHS`) fails if the two differ by more than 0.5 percent.
    A more recent year keeps growing at USAspending after our download by itself (Defense data come 90 days late,
    and agencies report late and correct for months), so it fails only if our count is above USAspending's by more
    than 0.5 percent. A lower count is reported as expected growth. Recent years are protected by the verification
    made at download time instead. If USAspending cannot be reached, the check fails and nothing is committed that
    week.
  - Until the yearly tables built before November 2026 (no record) and the cut-off years in the public release are
    rebuilt by `.github/workflows/history.yml`, the weekly robot fails on purpose.

## What a user should do

- `python run.py get-history` now says which yearly tables are verified. Tables built before November 2026 carry no
  verification. Some of them are too low. Run `python run.py reconcile` to compare each year with USAspending's totals.
- To rebuild a year from scratch on your own computer:

  ```
  python -I scripts/17_history_download.py 2014 all --force
  python -I scripts/18_history_build.py 2014 --require-verified
  python -I scripts/28_reconcile.py 2014
  ```

  Without `--force`, files you already have are checked against the count and only the bad ones are downloaded again.
  Fiscal year 2014 took 21 minutes to download this way (two of its quarters came back cut off and were fetched again
  month by month) and half a minute to build. Afterwards it matched USAspending's total to the transaction.
- If a script says NOT VERIFIED or REJECTED, nothing unchecked was used. Run the same command again later.
- Never publish a year whose `history_FY<year>.json` does not say `"verified": true`.
