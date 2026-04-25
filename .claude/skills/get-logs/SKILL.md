---
name: get-logs
description: Download crypto-bot quote logs from the GCP VM via gcloud scp. Invoke when the user asks to fetch / get / download bot logs. Argument is either a date (YYYY-MM-DD, "today", "yesterday") for a single day, or "all" to fetch every completed day. Today's log is in-progress and unzipped, and is skipped in "all" mode.
---

# Get crypto-bot quote logs

Pulls quote logs from the GCP `crypto-bot` instance into the local `quote_log/` directory.

## Setup (fixed for this project)

| | |
|---|---|
| Remote host | `veryshj123@crypto-bot` |
| Remote path | `/home/veryshj123/crypto_bot/quote_log/` |
| Local destination | `./quote_log/` (create if missing) |

**File state on the remote:**
- Completed days: `order_quote_log_YYYY-MM-DD.log.zip` (zipped, immutable)
- Today (in progress): `order_quote_log_YYYY-MM-DD.log` (unzipped, still being written) — **skip in batch mode**

## Modes

### Single date — `/get-logs YYYY-MM-DD` (or `today` / `yesterday`)

1. Resolve arg to `YYYY-MM-DD`:
   - `today` → today's date in user's local timezone
   - `yesterday` → today minus one day
   - Already-formatted date → use as-is
   - No arg → ask the user; don't guess
2. `mkdir -p ./quote_log`
3. If the requested date is **today**, fetch the **unzipped** `.log` file:
   ```
   gcloud compute scp veryshj123@crypto-bot:/home/veryshj123/crypto_bot/quote_log/order_quote_log_<DATE>.log ./quote_log/
   ```
   Otherwise fetch the `.log.zip`:
   ```
   gcloud compute scp veryshj123@crypto-bot:/home/veryshj123/crypto_bot/quote_log/order_quote_log_<DATE>.log.zip ./quote_log/
   ```
4. `ls -lh ./quote_log/order_quote_log_<DATE>.*` and report size.

### All completed days — `/get-logs all`

Downloads every `.log.zip` on the remote. The glob `*.log.zip` naturally **excludes today's in-progress `.log`** because it lacks the `.zip` extension.

1. `mkdir -p ./quote_log`
2. List remote zips first so we can compare with what's local and skip already-downloaded files:
   ```
   gcloud compute ssh veryshj123@crypto-bot --command 'ls -1 /home/veryshj123/crypto_bot/quote_log/*.log.zip'
   ```
3. For each remote filename not already present in `./quote_log/`, run:
   ```
   gcloud compute scp veryshj123@crypto-bot:/home/veryshj123/crypto_bot/quote_log/<FILENAME> ./quote_log/
   ```
   (Sequential, not parallel — each is a real network transfer; parallel scps risk hitting per-IP throttling on the GCP side.)
4. Report: how many were already local (skipped), how many newly downloaded, total size of new downloads.

**Do not** use `scp '...:.../*.log.zip' ./quote_log/` to grab everything in one call — it re-downloads files you already have, wasting bandwidth.

## Common failures

- `No such file or directory` for a date in single-day mode:
  - Date is today and file hasn't been created yet (bot just started)
  - Bot wasn't running that day
  - Timezone off-by-one (bot may rotate on UTC, not local time) — try ±1 day
- `Permission denied (publickey)`: gcloud's SSH key isn't on the instance. Fix: `gcloud compute config-ssh` or add the key in the GCP console.
- `ERROR: (gcloud.compute.scp) Could not fetch resource: ... not found`: project/zone mismatch. Verify with `gcloud config get-value project` and `gcloud compute instances list`.

## What NOT to do

- **Don't fetch today's `.log.zip`** — it doesn't exist yet. If the user asks for today specifically, fetch the unzipped `.log` (single-date rule above).
- **Don't unzip automatically.** Leave `.zip` files in `quote_log/`; downstream tooling can handle that.
- **Don't parallelize** the per-file scp calls in `all` mode — sequential is intentional.
- **Don't re-download** files already in `./quote_log/`. Completed-day zips are immutable; if it's local, it's correct.
