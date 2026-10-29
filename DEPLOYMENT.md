# Deployment guide — Нячанг Лайф | Новости

This repository uses GitHub Actions for scheduled execution and Supabase for persistent private bot state.

## Before merging the bot update

### 1. Create the private Supabase table

1. Open your own Supabase project.
2. Open **SQL Editor**.
3. Paste and run all of `supabase_schema.sql`.
4. Confirm the table `public.bot_state` exists.

The application stores the current queue, moderation state, ad drafts, delivery offsets, and deduplication state in this table. The public repository seed contains only deduplication history and the last Telegram update offset; it does not include pending draft/ad content.

### 2. Set GitHub Actions secrets

Open the repository **Settings → Secrets and variables → Actions → New repository secret**. Add:

| Secret | Required | Value |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Yes | Token for the bot that is admin in the channel |
| `TELEGRAM_CHAT_ID` | Recommended | `@NhaTrangLifeNews` or the channel's numeric `-100…` ID |
| `ADMIN_USER_ID` | Yes | Your numeric Telegram user ID (not the @username) |
| `SUPABASE_URL` | Yes | Project URL from Supabase |
| `SUPABASE_SECRET_KEY` | Yes | Server-side Supabase secret key; keep it in Secrets only |
| `WEATHERAPI_KEY` | Yes for production weather and ads | API key from WeatherAPI.com |
| `GEMINI_API_KEY` | Optional | Enables the constrained Gemini editorial pass |

For more than one editor, use `ADMIN_USER_IDS` as a comma-separated list of numeric Telegram IDs. The bot also accepts the legacy `SUPABASE_SERVICE_ROLE_KEY` secret instead of `SUPABASE_SECRET_KEY`.

**Do not paste these values into source code, issues, pull requests, logs, or this chat.**

### 3. Merge and enable the scheduled run

The PR's test job compiles Python and runs offline unit tests; it does not publish to Telegram. The scheduled production job runs only from `main`, only after tests pass, and uses the Secrets above.

After the Supabase table and secrets are ready, merge the pull request. GitHub Actions then checks the bot every five minutes. The five-minute job is only a scheduler tick; editorial timers still enforce the 15-minute preview window and scheduled posting windows.

For a manual production run, open **Actions → Nha Trang News Bot → Run workflow** and set `run_bot` to `true`. Leave it `false` for a tests-only workflow run.

## Safety gates

- If Supabase state storage or an editor ID is missing, the production run stops before processing updates.
- If no commercial-weather key is configured, the production code does not fall back to the non-commercial Open-Meteo endpoint. Weather publication and ad publishing are blocked rather than emitting unlicensed forecast content.
- If a news item has no usable photo, no successful private preview, or a caption that would need a text-only continuation, it is kept on hold. It is not released on the timer.
- Regular news remains subject to freshness checks, duplicate filtering, editorial scoring and daily limits. Only narrowly verified urgent reports from trusted sources can bypass the review timer, and the existing editor draft remains intact.
