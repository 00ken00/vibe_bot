---
name: get-logs
description: Download a day's quote log from the GCP crypto-bot VM via gcloud scp. Invoke when the user asks to fetch / get / download bot logs for a specific date. Argument is a date — YYYY-MM-DD, or the literal "today" / "yesterday". Single-day fetch only; for ranges, run the skill once per day.
---

# Get crypto-bot quote logs

Pulls one day's `order_quote_log_<date>.log.zip` from the GCP `crypto-bot` instance into the local `quote_log/` directory.

## Setup (fixed for this project)

| | |
|---|---|
| Remote host | `veryshj123@crypto-bot` |
| Remote path | `/home/veryshj123/crypto_bot/quote_log/` |
| Filename pattern | `order_quote_log_YYYY-MM-DD.log.zip` |
| Local destination | `./quote_log/` (create if missing) |

## How to run

1. **Resolve the date arg** to `YYYY-MM-DD`:
   - `today` → today's date in the user's local timezone
   - `yesterday` → today minus one day
   - Already-formatted `YYYY-MM-DD` → use as-is
   - If no arg given, ask the user (don't guess — picking the wrong day produces a confusing "file not found")

2. **Ensure local dir exists**: `mkdir -p ./quote_log`

3. **Run the download**:
   ```
   gcloud compute scp veryshj123@crypto-bot:/home/veryshj123/crypto_bot/quote_log/order_quote_log_<DATE>.log.zip ./quote_log/
   ```

4. **Verify and report**: list the file (`ls -lh quote_log/order_quote_log_<DATE>.log.zip`) and report its size.

## Common failures

- `No such file or directory` on the remote: that day's log doesn't exist yet. Likely causes:
  - The date is today and the file hasn't been rotated yet (bot rotates at end of day)
  - The bot wasn't running that day
  - Off-by-one timezone (the bot may rotate on UTC, not local)
- `Permission denied (publickey)`: gcloud's SSH key isn't on the instance. The user usually fixes this with `gcloud compute config-ssh` or by adding their key in the GCP console.
- `ERROR: (gcloud.compute.scp) Could not fetch resource: ... not found`: zone/project mismatch. Suggest `gcloud config get-value project` and `gcloud compute instances list` to verify.

## What NOT to do

- Don't loop the command over a date range without asking — the user invokes per-day. If they want a range, confirm first and space the calls (each is a real network transfer).
- Don't unzip automatically. The `.zip` lives in `quote_log/` — let the user (or a follow-up skill) decide what to do with it.
