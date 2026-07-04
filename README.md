# email-open-tracker v2 (FastAPI)

Async open-tracking pixel server with a **live dashboard** that auto-refreshes every 5
seconds, plus one-click deploy configs for Render and Railway.

**Legitimate use only** — your own outreach, newsletters, or transactional mail where
recipients would reasonably expect engagement tracking. Include an unsubscribe link and
disclose tracking in your privacy policy (CAN-SPAM, GDPR, etc.). Don't use it to covertly
track a specific individual who hasn't consented.

## Files

- `main.py` — FastAPI app: pixel route, live dashboard, JSON API, health check
- `requirements.txt` — dependencies
- `render.yaml` — one-click Render deploy (Blueprint)
- `Procfile` — for Railway / Heroku-style platforms
- `README.md` — this file

## Run locally

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Dashboard: http://127.0.0.1:8000/ · Test a hit: http://127.0.0.1:8000/pixel.gif?id=test1
(the dashboard updates on its own within 5s).

## Deploy

**Render (one click):** push this folder to a GitHub repo → render.com → New → Blueprint →
pick the repo. `render.yaml` configures everything. You get a URL like
`https://your-app.onrender.com`.

**Railway:** railway.app → New Project → Deploy from GitHub. The `Procfile` handles the
start command.

(Free tiers sleep when idle and SQLite may reset on redeploy — attach a persistent disk or
hosted DB for anything long-lived.)

## Optional: better geolocation

Get a free token at ipinfo.io and set `IPINFO_TOKEN` in your host's environment variables
for higher lookup limits.

## Use in an email

Add a unique pixel per recipient so you know *who* opened:

```html
<img src="https://YOUR-DEPLOYED-URL/pixel.gif?id=jane@example.com" width="1" height="1" alt="" style="display:none;">
```

## Honest limitations

Apple Mail Privacy Protection preloads images (fake instant "open" from Apple's servers),
Gmail proxies images through Google (hides the real IP), and many clients block images by
default. Treat opens as a rough signal, not precise tracking — and no framework choice
changes this; it's enforced on the recipient's side.

## Rebranding

Nothing depends on the folder name. Change the `<title>` and `<h1>` in the `DASH` template
inside `main.py`, rename the folder, done.
