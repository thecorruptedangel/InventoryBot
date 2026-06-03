"""Thin Telegram Bot API client + webhook/menu setup."""

from __future__ import annotations

import httpx

from . import config

API = "https://api.telegram.org/bot{token}/{method}"


async def call(method: str, payload: dict | None = None) -> dict:
    if not config.BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN not configured")
    url = API.format(token=config.BOT_TOKEN, method=method)
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, json=payload or {})
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram {method} failed: {data}")
    return data["result"]


async def set_webhook() -> None:
    """Point Telegram at our webhook, guarded by the secret token header."""
    if not config.WEBAPP_URL:
        return
    await call(
        "setWebhook",
        {
            "url": f"{config.WEBAPP_URL}/telegram/webhook",
            "secret_token": config.WEBHOOK_SECRET,
            "allowed_updates": ["message"],
            "drop_pending_updates": True,
        },
    )


async def set_menu_button() -> None:
    """Make the bot's menu button open the Mini App for everyone."""
    if not config.WEBAPP_URL:
        return
    await call(
        "setChatMenuButton",
        {
            "menu_button": {
                "type": "web_app",
                "text": "Open Inventory",
                "web_app": {"url": f"{config.WEBAPP_URL}/app"},
            }
        },
    )


async def send_start(chat_id: int) -> None:
    """Reply to /start with a button that launches the Mini App."""
    await call(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": "📦 *InventoryBot*\nTap below to open the inventory app.",
            "parse_mode": "Markdown",
            "reply_markup": {
                "inline_keyboard": [
                    [{"text": "📦 Open Inventory", "web_app": {"url": f"{config.WEBAPP_URL}/app"}}]
                ]
            },
        },
    )
