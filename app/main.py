"""FastAPI app: serves the Mini App, the JSON API, and the Telegram webhook."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import config, sheets, telegram
from .auth import AuthError, authorize

logging.basicConfig(level=logging.INFO)
# httpx logs full request URLs, which for Telegram include the bot token. Mute it.
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("inventorybot")


async def _keepalive():
    """Ping our own public URL periodically so Render's free dyno never idles out."""
    url = f"{config.WEBAPP_URL}/healthz"
    while True:
        await asyncio.sleep(config.KEEPALIVE_INTERVAL)
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.get(url)
            log.info("keepalive ping ok")
        except Exception as exc:  # noqa: BLE001
            log.warning("keepalive ping failed: %s", exc)


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    if config.WEBAPP_URL and config.BOT_TOKEN:
        try:
            await telegram.set_webhook()
            await telegram.set_menu_button()
            log.info("Webhook + menu button configured at %s", config.WEBAPP_URL)
        except Exception as exc:  # noqa: BLE001
            log.warning("Telegram setup skipped: %s", exc)
    else:
        log.warning("WEBAPP_URL or BOT_TOKEN missing; webhook not set (dev mode).")

    keepalive_task = None
    if config.WEBAPP_URL and config.KEEPALIVE_INTERVAL > 0:
        keepalive_task = asyncio.create_task(_keepalive())
        log.info("keepalive every %ss -> %s/healthz", config.KEEPALIVE_INTERVAL, config.WEBAPP_URL)

    yield

    if keepalive_task:
        keepalive_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await keepalive_task


app = FastAPI(title="InventoryBot", lifespan=lifespan)

app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")


# --- auth dependency --------------------------------------------------------
def current_user(x_telegram_init_data: str = Header(default="")):
    try:
        return authorize(x_telegram_init_data)
    except AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc))


# --- pages ------------------------------------------------------------------
@app.get("/")
async def root():
    return RedirectResponse(url="/app")


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.get("/app")
async def miniapp():
    return FileResponse(os.path.join(config.STATIC_DIR, "index.html"))


# --- api --------------------------------------------------------------------
@app.get("/api/grid")
async def api_grid(user=Depends(current_user), force: bool = False):
    try:
        return sheets.get_grid(force=force)
    except sheets.SheetError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.post("/api/cell")
async def api_cell(request: Request, user=Depends(current_user)):
    body = await request.json()
    try:
        row = int(body["row"])
        col = str(body["col"])
        value = body["value"]
    except (KeyError, ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Expected {row:int, col:str, value}")
    force = bool(body.get("force", False))
    try:
        result = sheets.set_cell(row, col, value, force=force)
    except sheets.FormulaCellError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except sheets.SheetError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    log.info("user %s set %s%s = %r", user.get("id"), col, row, value)
    return result


@app.post("/api/planning")
async def api_planning(request: Request, user=Depends(current_user)):
    body = await request.json()
    try:
        days = int(body["days"])
    except (KeyError, ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Expected {days:int}")
    try:
        saved = sheets.set_planning_days(days)
    except sheets.SheetError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    log.info("user %s set planning days = %s", user.get("id"), saved)
    return {"ok": True, "planningDays": saved}


# --- telegram webhook -------------------------------------------------------
@app.post("/telegram/webhook")
async def webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str = Header(default=""),
):
    if x_telegram_bot_api_secret_token != config.WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="bad secret")
    update = await request.json()
    msg = update.get("message")
    if msg and (msg.get("text") or "").startswith("/start"):
        chat_id = msg["chat"]["id"]
        try:
            await telegram.send_start(chat_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("send_start failed: %s", exc)
    return JSONResponse({"ok": True})
