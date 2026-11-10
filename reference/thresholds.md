# FAR dollar thresholds, fiscal years 2006 to 2027 (reference file)

`thresholds.csv` lists, one row per value period, the FAR dollar thresholds where awards could pile up just under a line. It covers the micro-purchase threshold (MPT), the simplified acquisition threshold (SAT), the $25,000 public posting line, the small business reservation band, the cost or pricing data (TINA) threshold, the simplified procedures for commercial items, the subcontracting plan threshold, the justification approval levels, and the 8(a) limits. The project can use it to scan every threshold that changed, not only the October 2025 SAT change.

The 2018 to 2020 change of the MPT and SAT happened agency by agency. Those dates are in `threshold_adoption_2018.csv`. This file does not repeat them. For civilian agencies it gives the date the FAR text changed and points to that file.

## How it was compiled

- The five periodic inflation rules were read in full text from federalregister.gov: 71 FR 57363 (2006-09-28), 75 FR 53129 (2010-10-01), 80 FR 38293 (2015-10-01), 85 FR 62485 (2020-10-01) and 90 FR 41872 (2025-10-01, FAC 2025-06). Each value is the number printed in the amendment text of that rule. The "removed" number in one rule gives the value before it.
- Statutes were read on govinfo.gov: P.L. 114-328 (FY2017 NDAA), P.L. 115-91 (FY2018 NDAA, sections 805, 806, 811) and P.L. 119-60 (FY2026 NDAA, section 1804).
- DoD class deviations 2017-O0006, 2018-O0012, 2018-O0013, 2018-O0015 and 2018-O0018 were downloaded from acq.osd.mil. They are scans, so they were read by eye. DoD deviation 2026-O0048 and a DHS Coast Guard deviation (26-15) were read as text.
- Current values were checked against the eCFR text of Title 48 (current to 2026-11-06).
- Evidence quotes are under 25 words. Where the text says "removing X and adding Y", the quote shows both numbers.

## How to read the file

- One row per value period. `effective_date` is blank when the value was already in force before FY2006 and its start date was not researched. `end_date` is blank when the value is still in force.
- `end_date` is the day before the next value starts.
- `applies_to` is `all`, `civilian` or `DoD`. Where DoD differed, there are separate rows. "DoD" rows follow DoD's own class deviations.
- For the 2018 MPT and SAT change, the civilian rows use the date the FAR text changed (2020-08-31). By law the new numbers applied from 2017-12-12, but a civilian agency could use them only after its own class deviation. The DoD rows use DoD's real dates.
- The `measure` column says what dollar amount is compared with the line. Several lines compare with the anticipated value, not the obligation.
- Rows with a blank `value_usd` describe a band or a placeholder (small business reservation band, FPDS reporting floor, Cost Accounting Standards).
- Confidence: high means read in the primary document. Medium means a secondary source or an inference. Low means not read.
- Values that FAR rewrote as text ("micro-purchase threshold", "simplified acquisition threshold") move automatically with those two rows. Many other thresholds (past performance, bonds, and so on) are tied to the SAT.

## What changed and when

All five inflation dates are 1 October (28 September in 2006). That is the first day of the fiscal year, which has its own spending pattern. Several thresholds move on the same day, so a bunching test on one of them will be confounded by the others.

| Date | MPT | SAT | TINA | Commercial (13.5) | Subcontracting plan |
|---|---|---|---|---|---|
| 2006-09-28 | 2,500 to 3,000 | 100,000 (no change) | 550,000 to 650,000 | 5.5M | 500,000 to 550,000 |
| 2010-10-01 | 3,000 (no change) | 100,000 to 150,000 | 650,000 to 700,000 | 6.5M | 550,000 to 650,000 |
| 2015-10-01 | 3,000 to 3,500 | 150,000 (no change) | 700,000 to 750,000 | 7M | 650,000 to 700,000 |
| 2017-07-13 (DoD only) | 3,500 to 5,000 | | | | |
| 2018-04-13 (DoD only) | | 150,000 to 250,000 | | | |
| 2018-07-01 | | | 750,000 to 2M (statute) | | |
| 2018-08-31 (DoD only) | 5,000 to 10,000 | | | | |
| 2018-02-27 to 2020-08-31 (civilian, by agency) | 3,500 to 10,000 | 150,000 to 250,000 | | | |
| 2020-10-01 | 10,000 (no change) | 250,000 (no change) | 2M (no change) | 7.5M | 700,000 to 750,000 |
| 2025-10-01 | 10,000 to 15,000 | 250,000 to 350,000 | 2M to 2.5M | 9M | 750,000 to 900,000 |
| 2026-07-01 (DoD, Coast Guard) | | | 2.5M to 10M | | |

Other lines that moved with the inflation rules: justification approval levels (FAR 6.304), the 8(a) competitive limits, the 8(a) sole-source justification line ($20M from 2011-03-16, $22M in 2015, $25M in 2020, $30M in 2025) and the display-notice floor in FAR 5.101 ($10,000, $15,000 in 2010, $20,000 in 2025).

The public posting line did not move. It is $25,000 through the whole period, because trade agreements fix it (2020 and 2025 rules say so). It is a level to study, not a change to test.

## The $10 million TINA claim: verified, with corrections

An earlier project note said the threshold rose from $2 million to $10 million on July 1, 2026. This is **mostly true but needs three corrections**.

- True: section 1804(c) of the FY2026 NDAA (P.L. 119-60, approved 2025-12-18) changed 10 U.S.C. 3702(a) from $2,000,000 to $10,000,000 for prime contracts entered into after June 30, 2026. DoD's DFARS text (class deviation 2026-O0048) says to obtain certified data "when a prime contract is expected to exceed $10 million and is awarded after June 30, 2026".
- Correction 1: the figure before July 1, 2026 was $2.5 million, not $2 million. The $2 million is the statute's base. The FAR rule of 2025-10-01 had already raised the working figure to $2.5 million.
- Correction 2: it applies to agencies under Title 10 (DoD and the Coast Guard; NASA was not checked), not to civilian agencies. The civilian statute, 41 U.S.C. 3502, was not amended. Civilian agencies stay at $2.5 million. The FAR text (15.403-4, eCFR 2026-11-06) still says $2.5 million.
- Correction 3: the test is the date the prime contract was entered into, not the date of the negotiation or modification.

## Where this file disagrees with other project text

- `docs/methodology.md` and `README.md`: the 2025-10-01 change (SAT $250,000 to $350,000, MPT $10,000 to $15,000) is confirmed by the final rule (90 FR 41872) and by the eCFR text. No disagreement.
- `threshold_adoption_2018.csv` (DoD row, notes): it says DoD raised the MPT to $5,000 on 2018-04-13. DoD class deviation 2017-O0006 (2017-07-13) had already set $5,000, under FY2017 NDAA sections 217(a) and 821. The 2018-04-13 deviation kept it. The note about unread deviations 2017-O0007 and 2018-O0001 is a different matter: 2017-O0007 is about special emergency procurement authorities. The MPT deviation is 2017-O0006.
- The request mentioned an 8(a) line of $100 million. No such 8(a) figure exists in the FAR. The $100 million in FAR 6.304 is the DoD, NASA and Coast Guard head of procuring activity ceiling (2020-10-01 to 2025-09-30). It became $150 million on 2025-10-01.

## Other changes found

- The FY2026 NDAA (section 1804(b)) also changed the DoD justification approval levels in 10 U.S.C. 3204(e): competition advocate above $10 million (was $500,000) and up to $100 million (was $10 million); head of the procuring activity up to $500 million (was $75 million). The Act has no delayed date, so it took effect 2025-12-18. FAR 6.304 text still shows the old levels. No implementing guidance was found. These are shown as statute-only rows.
- The FY2026 NDAA also changed major-system, MDAP and Cost Accounting Standards thresholds. Those are not tabulated.
- No change to the MPT or SAT was found in 2026 as of November 2026. Proposals to raise them have been made but were not found enacted.

## Known gaps

- Start dates before 2006-09-28 are blank. The first-period values are exact (they are the "removed" numbers in the 2006 rule), but how long they were in force was not researched.
- The DoD $3,500 period (2015-10-01 to 2017-07-12) is inferred, not read in a DoD document.
- The statutory source of DoD's $10,000 MPT in 2018 (FY2019 NDAA) was not read. The deviation itself was.
- The 8(a) $20 million line (2011-03-16) is from the Federal Register summary and the 2015 "removed" number. The interim rule text was not read in full.
- DoD's 2025 MPT and SAT were read through the FAR rule and the DFARS inflation rule. No separate DoD memo was read.
- Civilian use of the $2 million TINA threshold between 2018-07-01 and the FAR text on 2020-08-03 was not checked. DoD used 2018-07-01 (deviations 2018-O0012 and 2018-O0015).
- The FAR Overhaul (2025 to 2026) renumbers parts through deviations. Values were checked in the eCFR text and in the DoD Part 215 deviation only. Overhaul texts for other parts were not read.
- Contingency, humanitarian and peacekeeping variants of the SAT (for example $1 million inside the US from 2025-10-01) are not tabulated. Variants of the MPT are.
- Cost Accounting Standards, DFARS-only lines (for example earned value management) and trade-agreement lines are not covered.
- Statutory dates come from the Act. Agencies applied some of them later, and some contracting offices earlier or later than their memo.

## Caution

Check the values and dates against the primary document before any causal claim. A bunching test needs the right date for each agency and each line. Several lines moved on the same day, the effective date is the date of entering the contract and not the award or obligation in some rules, and the measure is often the anticipated value including options, not the recorded obligation. The FPDS reporting floor also moves with the MPT, which changes what the data can show below that line. Use the confidence column, and treat medium and low rows as leads.
