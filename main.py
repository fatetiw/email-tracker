"""
email-open-tracker (FastAPI) — simple open tracking with a live "Email opens" list.

Legitimate use only: your own outreach, newsletters, or transactional email where
recipients would reasonably expect engagement tracking. Include an unsubscribe link
and disclose tracking. Do not use this to covertly track a specific individual who
has not consented.

Routes:
  GET /pixel.gif?id=<id>   -> logs an open, returns a 1x1 transparent gif
  GET /                    -> live dashboard: total opens + list
  GET /api/opens           -> JSON of recent opens
  GET /health              -> health check
"""

import datetime
import os
import sqlite3
import httpx
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import Response, JSONResponse, HTMLResponse

DB = os.environ.get("TRACKER_DB", "opens.db")

PIXEL_BYTES = bytes.fromhex(
    "47494638396101000100800000ffffff00000021f90401000000002c00000000010001000002024401003b"
)


def init_db():
    con = sqlite3.connect(DB)
    con.execute(
        """CREATE TABLE IF NOT EXISTS opens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tracking_id TEXT, opened_at TEXT, ip TEXT,
            city TEXT, region TEXT, country TEXT, user_agent TEXT
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


@app.get("/pixel.gif")
async def pixel(request: Request, id: str = "unknown"):
    ip = client_ip(request)
    city, region, country = await geo_lookup(ip)
    con = sqlite3.connect(DB)
    con.execute(
        "INSERT INTO opens (tracking_id, opened_at, ip, city, region, country, user_agent) "
        "VALUES (?,?,?,?,?,?,?)",
        (
            id,
            datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
            ip, city, region, country,
            request.headers.get("user-agent", ""),
        ),
    )
    con.commit()
    con.close()
    return Response(
        content=PIXEL_BYTES,
        media_type="image/gif",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.get("/api/opens")
async def api_opens():
    con = sqlite3.connect(DB)
    rows = con.execute(
        "SELECT tracking_id, opened_at, ip, city, region, country, user_agent "
        "FROM opens ORDER BY id DESC LIMIT 500"
    ).fetchall()
    con.close()
    keys = ["tracking_id", "opened_at", "ip", "city", "region", "country", "user_agent"]
    return JSONResponse([dict(zip(keys, r)) for r in rows])


DASH = """
<!doctype html><meta charset="utf-8"><title>Open Tracker</title>
<style>
 body{font-family:-apple-system,system-ui,sans-serif;margin:2rem;color:#1a1a1a}
 h1{font-size:1.3rem;margin-bottom:.2rem}
 table{border-collapse:collapse;width:100%;font-size:.9rem;margin-top:1rem}
 th,td{border-bottom:1px solid #eee;padding:.5rem;text-align:left}
 th{background:#fafafa} .muted{color:#888;font-size:.85rem} .dot{color:#16a34a}
</style>
<h1>Email opens <span class="dot">&#9679;</span> <span id="count" class="muted"></span></h1>
<p class="muted">Live &mdash; refreshes every 5s. Total counts every image load, including
Gmail/Apple auto-fetching images on delivery, so it can be higher than real human opens.</p>
<table><thead><tr><th>When (UTC)</th><th>ID</th><th>Location</th><th>IP</th><th>Client</th></tr></thead>
<tbody id="rows"></tbody></table>
<script>
async function load(){
  const d = await (await fetch('/api/opens')).json();
  document.getElementById('count').textContent = '(' + d.length + ' total)';
  document.getElementById('rows').innerHTML = d.map(o =>
    `<tr><td>${o.opened_at}</td><td>${o.tracking_id}</td>
     <td>${[o.city,o.region,o.country].filter(Boolean).join(' ')}</td>
     <td>${o.ip}</td><td class="muted">${(o.user_agent||'').slice(0,60)}</td></tr>`).join('');
}
load(); setInterval(load, 5000);
</script>
"""


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return DASH


@app.get("/health")
async def health():
    return {"status": "ok"}
