# Deploy InventoryBot to Render (free tier)

The same service hosts the Telegram webhook **and** the Mini App.

## 0. Prereqs (already done)
- BotFather token in `token.txt` (local) — on Render it goes in an env var.
- `service_account.json` (or `driveload-*.json`) and the sheet **shared with its
  `client_email` as Editor**.
- Your Telegram ID `563381712` is whitelisted in `app/config.py:WHITELISTED_IDS`.

## 1. Put the code in a Git repo
Render deploys from GitHub/GitLab. Secrets are git-ignored (`token.txt`,
`*service_account*.json`, `driveload-*.json`) — they are supplied as env vars instead.

```powershell
git init
git add .
git commit -m "InventoryBot: Telegram Mini App + API"
git branch -M main
git remote add origin https://github.com/<you>/InventoryBot.git
git push -u origin main
```

## 2. Create the Render service
Option A — **Blueprint** (uses `render.yaml`):
1. Render Dashboard → **New** → **Blueprint** → pick your repo.
2. It reads `render.yaml` (web service, free plan, build + start commands, health check).

Option B — **Manual Web Service**:
- Environment: **Python**
- Build: `pip install -r requirements.txt`
- Start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- Health check path: `/healthz`

## 3. Set environment variables (Render → service → Environment)
| Key | Value |
|---|---|
| `BOT_TOKEN` | your BotFather token (the contents of `token.txt`) |
| `SPREADSHEET_ID` | your Google Sheet ID (the contents of `sheet_id.txt`) |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | the **entire** contents of your service-account JSON (paste as one value) |
| `WEBHOOK_SECRET` | leave to auto-generate (Blueprint) or set any random string |

`WEBAPP_URL` is **not** needed — the app reads Render's `RENDER_EXTERNAL_URL`
automatically and, on startup, registers the webhook and sets the bot's menu button.

## 4. Deploy & connect
1. Deploy. Watch logs for `Webhook + menu button configured at https://...onrender.com`.
2. In Telegram open your bot → `/start` → tap **📦 Open Inventory**, or use the
   blue menu button next to the message box.
3. Only whitelisted IDs get in; everyone else sees "Not authorized".

## 5. Free-tier: kept awake automatically
Render free web services **sleep after ~15 min idle**. This app prevents that:
on startup it launches a background task that pings its own
`RENDER_EXTERNAL_URL/healthz` every **10 min** (`KEEPALIVE_INTERVAL`, seconds).
That self-request is inbound HTTP traffic, so Render keeps the instance running.

- One always-on service uses ~730 hr/month, under the free **750 hr/month** cap.
- Tune or disable via env `KEEPALIVE_INTERVAL` (e.g. `300`; `0` = off).
- **Backstop (recommended):** also add an external pinger (UptimeRobot /
  cron-job.org) hitting `/healthz` every ~10 min. If the instance ever does sleep
  (deploy gap, crash), the internal loop is asleep too — an external ping is what
  wakes it back up.

## Local dev
```powershell
pip install -r requirements.txt
# token.txt + a service-account json in the project root are auto-detected.
uvicorn app.main:app --reload --port 8000
```
Without a public HTTPS URL the webhook isn't set (dev mode logs a warning); use a
tunnel (cloudflared / ngrok) and set `WEBAPP_URL` to test inside Telegram.

## Updating the whitelist later
Edit `WHITELISTED_IDS` in `app/config.py` and redeploy. (Users get their ID from
`@userinfobot`.)
