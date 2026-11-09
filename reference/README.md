# Threshold adoption dates, 2018-2020 (reference file)

`threshold_adoption_2018.csv` lists, for each federal agency, the date it put the higher
micro-purchase threshold (MPT, $3,500 to $10,000) and simplified acquisition threshold
(SAT, $150,000 to $250,000) into effect. The FY2018 NDAA (P.L. 115-91, signed 2017-12-12)
raised both by statute. Agencies could not use the new numbers in the FAR until they issued a
"class deviation" (after CAAC Letter 2018-02 of 2018-02-16), and the FAR itself was only amended
effective **2020-08-31** (FAR Case 2018-004, 85 FR 40064, published 2020-07-02). Agencies adopted
on very different dates (2018-02-27 to 2020-05-15), so later adopters can serve as a comparison
group for earlier ones.

## How it was compiled

- Started from the earlier notes (`research notes/cycle2/c2_federal_newdata.md`, section A3).
- Re-downloaded the agency deviation PDFs linked from acquisition.gov under CAAC Letter 2018-02,
  plus the DoD class deviations (acq.osd.mil), and read each one again. Where the PDF had a text
  layer I used it; scanned PDFs were read with Windows OCR and, for dates, by looking at the page image.
- Looked outside the CAAC list (EPA, DOE, VA, USDA, HUD, etc.) using agency pages and web search.
- Checked the FAR effective date in the Federal Register text and on acquisition.gov's
  "Table of effective dates for MPT and SAT."
- Evidence column quotes the exact words (or place) where the date appears.

## What "effective date" means here

The date the deviation took effect, not the date it was signed. Usually they are the same
("effective upon signature / issuance / immediately"), in which case the signature or memo date is used.
Where they differ the effective date is used and the notes column says so:
HHS (signed 2018-05-07, effective 2018-06-01), DHS (signed 05-30, effective 06-01), DOT (signed 08-02,
effective 08-06), USAID (approved 06-08, effective 06-18), NRC (signed 2019-09-27, effective 2019-10-01).
Some agencies changed the two thresholds on different dates (OPM: MPT 2018-11-30, SAT 2019-01-15;
Commerce: SAT 2019-06-13, MPT 2020-05-15; DoD: MPT $5,000 on 2018-04-13 then $10,000 on 2018-08-31,
SAT $250,000 on 2018-04-13). The `confidence` column is the lower of the two dates' confidence.

Confidence: high = date read from the primary document; medium = secondary source, handwritten or OCR-uncertain
date, or an inferred effective date; low = date could not be found (cells left blank).

## Counts (33 rows)

High 18, medium 7 (VA, USDA, Commerce, DOE, DOL, NSF, MCC), low 8, all with blank dates
(DOJ, State, HUD, GSA, NASA, SBA, SSA, FEC).

## Known gaps

- No document found for DOJ, State, NASA, SBA, SSA. They are not on the CAAC list. Either they never
  issued a deviation (then the FAR date applies) or the record is not online. Unknown which.
- HUD: the document exists but the link was dead; GSA and FEC memos are undated.
- USDA: PDF blocked automated access (HTTP 403); date comes from a search excerpt of the document.
- VA: date is from VA's own deviations list (03/22/2018), not from the memo, which could not be opened.
- Commerce SAT date comes from a file name on Commerce's site, not from a signed memo.
- DoD: earlier deviations 2017-O0007 and 2018-O0001 (before the NDAA) were not read.
- This is the department/agency level. Components with their own deviations or delays (for example
  NNSA, bureaus of Commerce, Army/Navy) were not checked. Purchase-card limits were often not raised
  at the same time as the thresholds.
- Agency names follow USAspending top-tier names as best I could; check the exact spelling before joining
  (CFPB, Pretrial Services, AID in particular).

## Caution

Double-check these dates before making any causal claim, especially the medium and low rows. A wrong
treatment date by a few months can matter in an event study, and an agency's contracting offices may
have started using the new thresholds earlier or later than the memo date. Also test whether the
FPDS/USAspending data show bunching at the new thresholds starting near each date.
