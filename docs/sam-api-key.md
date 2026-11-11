# The SAM.gov API key

One step of this project needs a free personal API key from SAM.gov: the daily step that saves the notices SAM.gov posted the
day before (`scripts/14_sam_daily_snapshot.py`, `python run.py sam-snapshot`). Nothing else needs it. Without a key that step
says so and skips itself, and every other step runs as before.

**Why a key.** SAM.gov's [Terms of Use](https://sam.gov/about/terms-of-use) name its APIs and the files on its
[Data Services](https://sam.gov/data-services) page as the ways to get its data with software. The daily step
therefore uses only SAM.gov's documented
[Get Opportunities Public API](https://open.gsa.gov/api/get-opportunities-public-api/), which needs a key.

## 1. Get a key

1. Sign in at [sam.gov](https://sam.gov). SAM.gov asks you to agree to its Terms of Use, then sends you to Login.gov to sign in
   or create an account.
2. Open **Account Details** (your name at the top right, then Account Details), find **Public API Key**, click the eye icon and
   enter the one-time password that SAM.gov emails you. The key appears.
3. Copy it into your password manager. It is shown only until you leave the page.

Keep the key to yourself (SAM.gov's terms forbid sharing it) and never paste it into a file in this folder, an issue or a chat.

## 2. Put it on your computer

The scripts read the key from an environment variable named `SAM_API_KEY`.

Windows (PowerShell). Paste these two lines, press Enter, then paste the key when asked. The key is not shown while you paste:

```
$k = Read-Host "Paste your SAM.gov API key" -AsSecureString
[Environment]::SetEnvironmentVariable("SAM_API_KEY", [System.Net.NetworkCredential]::new("", $k).Password, "User")
```

Close the window and open a new one: only new windows see the variable. It is stored in your Windows account, readable by the
programs you run.

macOS or Linux: add `export SAM_API_KEY='your key'` to your shell profile (for example `~/.zshrc`), then open a new terminal.

## 3. Put it on GitHub (for the daily robot)

1. On GitHub, open the repository, then **Settings**, **Secrets and variables**, **Actions**.
2. Press **New repository secret**. Name: `SAM_API_KEY`. Secret: paste the key. Press **Add secret**.

GitHub stores secrets encrypted, passes them only to this repository's workflows, and hides them in the logs. Workflows
started from someone else's fork never receive them. The daily workflow gives the key to the SAM.gov step only.

## 4. Check it

```
python -I scripts/14_sam_daily_snapshot.py --check
```

This makes one request (the first page of yesterday's notices), prints how many notices came back and how full each kept field
is, and writes nothing. It prints no names and never prints the key.

## How many requests

A personal key of a person outside the government who has no role in a SAM.gov entity allows **10 requests a day**. One request
returns up to 1,000 notices, and SAM.gov posts up to about 1,700 notices on a busy day, so a day costs 1 or 2 requests. The
script makes at most 6 requests a run (`--max-requests` changes that) and leaves the rest for checks by hand. After a pause it
catches up a few days per run, oldest first, at most 60 days back.

Run the daily step in **one place**, either the robot or your computer. Both spend the same 10 requests, and each keeps its own
copy of the notices.

## Every 90 days

SAM.gov's terms ask you to renew the key every 90 days. Generate a new one on Account Details, then repeat steps 2 and 3 with the
new key. If SAM.gov refuses a key, the daily step stops with exit code 2 and says so; the rest of the daily run goes on.

## What is kept, and where

| What | Where | Public? |
|---|---|---|
| Every notice pulled, whitelisted fields only (no contacts, awardee, street address or description) | `<data folder>/sam_api/notices.parquet`; on GitHub, the draft release `sam-api-store` | No |
| The forecast step's input | `<data folder>/sam_snapshots/sam_view_<date>.parquet` | No |
| One row per day pulled: requests used, notices expected and received, status | `results/sam_api_pulls.csv` | Yes |
| Notices posted per day, department and notice type | `results/sam_new_notices.csv` | Yes |
| The dashboard summary | `results/sam_daily_latest.json` | Yes |

Until November 10, 2026 the daily step downloaded SAM.gov's nightly file of active notices instead. `results/sam_daily_counts.csv`
holds the counts from that file for November 9 and 10, 2026 and is no longer updated. The two series count different things:
open solicitations then, solicitations posted or updated per day now.
