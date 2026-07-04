"""
email-open-tracker v3 (FastAPI) — open tracking + link-click tracking, with a
per-recipient summary dashboard that de-duplicates provider proxy prefetches
(Gmail's GoogleImageProxy, Apple Mail Privacy Protection, etc.).

Legitimate use only: your own outreach, newsletters, or transactional email where
recipients would reasonably expect engagement tracking. Include an unsubscribe link
and disclose tracking in your privacy policy (CAN-SPAM / GDPR / etc.). Do not use this
to covertly track a specific individual who has not consented.

Routes:
  GET /pixel.gif?id=<id>            -> logs an open, returns a 1x1 transparent gif
  GET /r?id=<id>&u=<url>            -> logs a click, then redirects to <url>
  GET /                            -> per-recipient summary dashboard (auto-refresh)
  GET /raw                         -> raw event log
  GET /api/summary                 -> JSON summary (dashboard polls this)
  GET /health                      -> health check
"""

import datetime
import os
import sqlite3
import httpx
from contextlib import asynccontextmanager
from urllib.parse import unquote

from fastapi import FastAPI, Request
from fastapi.responses import Response, JSONResponse, HTMLResponse, RedirectResponse

DB = os.environ.get("TRACKER_DB", "opens.db")

PIXEL_BYTES = bytes.fromhex(
    "47494638396101000100800000ffffff00000021f90401000000002c00000000010001000002024401003b"
)

# IP prefixes and user-agent markers used by mail providers to PREFETCH/scan images.
# Hits matching these are logged but flagged as "proxy" so they don't inflate real opens.
PROXY_IP_PREFIXES = ("66.249.", "74.125.", "64.233.", "72.14.", "209.85.", "216.239.", "17.")
PROXY_UA_MARKERS = ("GoogleImageProxy", "YahooMailProxy", "Google-Read-Aloud")


def is_proxy(ip: str, ua: str) -> bool:
    ip = ip or ""
    ua = ua or ""
    if any(ip.startswith(p) for p in PROXY_IP_PREFIXES):
        return True
    if any(m.lower() in ua.lower() for m in PROXY_UA_MARKERS):
        return True
    return False


def init_db():
    con = sqlite3.connect(DB)
    con.execute(
        """CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tracking_id TEXT, event_type TEXT, target_url TEXT,
            at TEXT, ip TEXT, city TEXT, region TEXT, country TEXT,
            user_agent TEXT, is_proxy INTEGER DEFAULT 0
        )"""
    )
    con.commit()
    con.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="email-open-tracker", lifespan=lifespan)


async def geo_lookup(ip: str):
    if not ip or ip.startswith(("10.", "192.168.", "127.")):
        return ("", "", "")
    try:
        token = os.environ.get("IPINFO_TOKEN", "")
        url = f"https://ipinfo.io/{ip}/json" + (f"?token={token}" if token else "")
        async with httpx.AsyncClient(timeout=3) as client:
            d = (await client.get(url)).json()
        return (d.get("city", ""), d.get("region", ""), d.get("country", ""))
    except Exception:
        return ("", "", "")


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else ""


async def log_event(request: Request, tracking_id: str, event_type: str, target_url: str = ""):
    ip = client_ip(request)
    ua = request.headers.get("user-agent", "")
    city, region, country = await geo_lookup(ip)
    con = sqlite3.connect(DB)
    con.execute(
        "INSERT INTO events (tracking_id, event_type, target_url, at, ip, city, region, country, user_agent, is_proxy) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            tracking_id, event_type, target_url,
            datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
            ip, city, region, country, ua, 1 if is_proxy(ip, ua) else 0,
        ),
    )
    con.commit()
    con.close()


@app.get("/pixel.gif")
async def pixel(request: Request, id: str = "unknown"):
    await log_event(request, id, "open")
    return Response(
        content=PIXEL_BYTES,
        media_type="image/gif",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.get("/r")
async def click(request: Request, id: str = "unknown", u: str = ""):
    """Log a link click, then redirect to the real destination `u`."""
    target = unquote(u)
    await log_event(request, id, "click", target)
    if not target.startswith(("http://", "https://")):
        target = "https://" + target if target else "https://example.com"
    return RedirectResponse(target, status_code=302)


@app.get("/api/summary")
async def api_summary():
    con = sqlite3.connect(DB)
    rows = con.execute(
        "SELECT tracking_id, event_type, at, city, region, country, is_proxy FROM events ORDER BY id ASC"
    ).fetchall()
    con.close()

    people = {}
    for tid, etype, at, city, region, country, proxy in rows:
        p = people.setdefault(tid, {
            "id": tid, "opened": False, "real_opens": 0, "proxy_opens": 0,
            "clicks": 0, "first_open": None, "last_open": None,
            "last_location": "", "first_click": None,
        })
        loc = " ".join(x for x in (city, region, country) if x)
        if etype == "open":
            p["opened"] = True
            if proxy:
                p["proxy_opens"] += 1
            else:
                p["real_opens"] += 1
                if not p["first_open"]:
                    p["first_open"] = at
                p["last_open"] = at
                if loc:
                    p["last_location"] = loc
            if not p["first_open"]:  # fall back to proxy time if only proxy hits
                p["first_open"] = at
                p["last_open"] = at
                if loc and not p["last_location"]:
                    p["last_location"] = loc
        elif etype == "click":
            p["clicks"] += 1
            if not p["first_click"]:
                p["first_click"] = at
    return JSONResponse(sorted(people.values(), key=lambda x: x["last_open"] or "", reverse=True))


DASH = """
<!doctype html><meta charset="utf-8"><title>Open Tracker</title>
<style>
 body{font-family:-apple-system,system-ui,sans-serif;margin:2rem;color:#1a1a1a}
 h1{font-size:1.3rem;margin-bottom:.2rem}
 table{border-collapse:collapse;width:100%;font-size:.9rem;margin-top:1rem}
 th,td{border-bottom:1px solid #eee;padding:.55rem;text-align:left}
 th{background:#fafafa} .muted{color:#888;font-size:.85rem}
 .dot{color:#16a34a} .yes{color:#16a34a;font-weight:600} .no{color:#bbb}
 a{color:#2563eb;text-decoration:none} .pill{background:#eef;border-radius:10px;padding:1px 7px;font-size:.8rem}
</style>
<h1>Recipients <span class="dot">&#9679;</span> <span id="count" class="muted"></span></h1>
<p class="muted">Live &mdash; refreshes every 5s. One row per recipient (the <code>id</code> in your pixel/link).
Provider prefetches (Gmail/Apple/Yahoo scanning images) are counted separately so they don't inflate real opens.
<a href="/raw">View raw log &rarr;</a></p>
<table><thead><tr>
 <th>Recipient (id)</th><th>Opened?</th><th>Real opens</th><th>Clicks</th>
 <th>First open</th><th>Last open</th><th>Location</th><th>Provider prefetches</th>
</tr></thead><tbody id="rows"></tbody></table>
<script>
async function load(){
  const d = await (await fetch('/api/summary')).json();
  document.getElementById('count').textContent = '(' + d.length + ')';
  document.getElementById('rows').innerHTML = d.map(p => `
    <tr>
      <td>${p.id}</td>
      <td>${p.opened ? '<span class="yes">Yes</span>' : '<span class="no">No</span>'}</td>
      <td>${p.real_opens}</td>
      <td>${p.clicks ? '<span class="pill">'+p.clicks+'</span>' : 0}</td>
      <td class="muted">${p.first_open || '—'}</td>
      <td class="muted">${p.last_open || '—'}</td>
      <td>${p.last_location || '—'}</td>
      <td class="muted">${p.proxy_opens}</td>
    </tr>`).join('');
}
load(); setInterval(load, 5000);
</script>
"""

RAW = """
<!doctype html><meta charset="utf-8"><title>Raw log</title>
<style>
 body{font-family:-apple-system,system-ui,sans-serif;margin:2rem;color:#1a1a1a}
 table{border-collapse:collapse;width:100%;font-size:.85rem}
 th,td{border-bottom:1px solid #eee;padding:.45rem;text-align:left}
 th{background:#fafafa} .muted{color:#888} .p{color:#c026d3}
 a{color:#2563eb;text-decoration:none}
</style>
<h1>Raw event log</h1><p><a href="/">&larr; Back to summary</a></p>
<table><thead><tr><th>When (UTC)</th><th>ID</th><th>Type</th><th>Target</th>
<th>Location</th><th>IP</th><th>Proxy?</th></tr></thead><tbody id="rows"></tbody></table>
<script>
async function load(){
  const d = await (await fetch('/api/raw')).json();
  document.getElementById('rows').innerHTML = d.map(e => `
    <tr><td>${e.at}</td><td>${e.tracking_id}</td><td>${e.event_type}</td>
    <td class="muted">${(e.target_url||'').slice(0,40)}</td>
    <td>${[e.city,e.region,e.country].filter(Boolean).join(' ')}</td>
    <td>${e.ip}</td><td class="${e.is_proxy?'p':''}">${e.is_proxy?'proxy':''}</td></tr>`).join('');
}
load(); setInterval(load, 5000);
</script>
"""


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return DASH


@app.get("/raw", response_class=HTMLResponse)
async def raw_page():
    return RAW


@app.get("/api/raw")
async def api_raw():
    con = sqlite3.connect(DB)
    rows = con.execute(
        "SELECT tracking_id, event_type, target_url, at, ip, city, region, country, is_proxy "
        "FROM events ORDER BY id DESC LIMIT 1000"
    ).fetchall()
    con.close()
    keys = ["tracking_id", "event_type", "target_url", "at", "ip", "city", "region", "country", "is_proxy"]
    return JSONResponse([dict(zip(keys, r)) for r in rows])


@app.get("/health")
async def health():
    return {"status": "ok"}

