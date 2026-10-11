import asyncio
import html
import logging
from urllib.parse import quote
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse

from . import demo, people_api, scheduler, memo
from .api import public, router
from .config import settings
from .db import SessionLocal
from .notify import acknowledge, escalation
from .seed import ensure_seeded

logging.basicConfig(level=logging.INFO)


log = logging.getLogger(__name__)


async def _escalation_loop():
    """Ack reminders and escalation, once a minute (every 15 s in DEMO_MODE)."""
    while True:
        await asyncio.sleep(escalation.loop_interval_seconds())
        try:
            await asyncio.to_thread(escalation.tick)
        except Exception:  # noqa: BLE001 - keep the loop alive; the next pass retries
            log.exception("escalation tick failed")


async def _scheduler_loop():
    """Scheduled re-evaluations (app/scheduler.py), checked once a minute."""
    while True:
        await asyncio.sleep(60)
        try:
            await asyncio.to_thread(scheduler.tick)
        except Exception:  # noqa: BLE001 - keep the loop alive
            log.exception("scheduler tick failed")


async def _roads_watch_loop():
    """Road sources refresh every 5 min; on a change, run the roads agent then (max 1 per 10 min)."""
    from .agents.local import roads_cache

    while True:
        await asyncio.sleep(300)
        try:
            await asyncio.to_thread(roads_cache.watch_tick)
        except Exception:  # noqa: BLE001 - keep the loop alive
            log.exception("roads watch failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_seeded()  # creates tables, inserts demo data if missing; tolerates an unreachable DB
    from .investigations import recover_interrupted
    try:
        recover_interrupted()
    except Exception:
        log.exception("Could not settle interrupted investigations")
    tasks = []
    if settings.escalation_loop:
        tasks.append(asyncio.create_task(_escalation_loop()))
    if settings.scheduler_loop:
        tasks.append(asyncio.create_task(_scheduler_loop()))
    if settings.roads_watch and settings.local_signals:
        tasks.append(asyncio.create_task(_roads_watch_loop()))
    yield
    for task in tasks:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="Closure Call", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(public)
app.include_router(router)
app.include_router(people_api.router)
app.include_router(demo.router)
app.include_router(memo.router)


@app.get("/ack/{notification_id}", include_in_schema=False, response_class=HTMLResponse)
def ack_page(notification_id: str):
    """The link in messages. Records acknowledged_at once and shows a confirmation page."""
    with SessionLocal() as db:
        n = acknowledge(db, notification_id)
        if n is None:
            body, status = "<h1>Link not recognized</h1><p>This acknowledgment link is not valid.</p>", 404
        else:
            when = n.acknowledged_at.strftime("%Y-%m-%d %H:%M UTC")
            demo_label = next((line for line in (n.message or "").splitlines() if line.startswith("Demo:")), "")
            body = (f"<h1>Thank you</h1><p>Your acknowledgment was recorded at {html.escape(when)}.</p>"
                    f"<p>Instruction: <strong>{html.escape(n.duty_instruction)}</strong></p>"
                    "<p>Gracias. Su confirmación quedó registrada.</p>"
                    # Opens the app as this person (demo role switcher, no real auth).
                    f'<p><a href="/me?as={quote(n.employee_id)}">View my status</a> · '
                    f'<a href="/me?as={quote(n.employee_id)}">Ver mi estado</a></p>')
            if demo_label:
                body += f"<p>{html.escape(demo_label)}</p>"
            status = 200
    page = ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" '
            'content="width=device-width,initial-scale=1"><title>Closure Call</title></head>'
            '<body style="font-family:Arial,sans-serif;font-size:20px;max-width:36rem;margin:2rem auto;padding:0 1rem">'
            f"{body}</body></html>")
    return HTMLResponse(page, status_code=status)


# Serve the built frontend (production). In dev, Vite serves it and proxies /api here.
dist = settings.frontend_dist.resolve()


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    if path == "api" or path.startswith(("api/", "ack/")):
        raise HTTPException(404, "Not found")
    index = dist / "index.html"
    if not index.exists():
        return {"message": "Closure Call API. Frontend not built; run `npm run dev` in /frontend or build it."}
    file = (dist / path).resolve()
    if path and file.is_file() and file.is_relative_to(dist):
        return FileResponse(file)
    return FileResponse(index)
