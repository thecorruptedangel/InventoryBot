"""Telegram Mini App initData validation (HMAC-SHA256) + whitelist check."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.parse

from . import config


class AuthError(Exception):
    """Raised when initData is missing, forged, expired, or not whitelisted."""

    def __init__(self, message: str, status: int = 401):
        super().__init__(message)
        self.status = status


def _data_check_string(pairs: dict[str, str]) -> str:
    return "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))


def validate_init_data(init_data: str) -> dict:
    """
    Verify Telegram WebApp initData per the documented algorithm and return the
    decoded `user` object. Raises AuthError on any failure.
    """
    if not init_data:
        raise AuthError("Missing initData", 401)
    if not config.BOT_TOKEN:
        raise AuthError("Server missing BOT_TOKEN", 500)

    parsed = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
    received_hash = parsed.pop("hash", None)
    if not received_hash:
        raise AuthError("initData has no hash", 401)

    secret_key = hmac.new(b"WebAppData", config.BOT_TOKEN.encode(), hashlib.sha256).digest()
    calc_hash = hmac.new(
        secret_key, _data_check_string(parsed).encode(), hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(calc_hash, received_hash):
        raise AuthError("initData signature mismatch", 401)

    auth_date = int(parsed.get("auth_date", "0") or "0")
    if config.INITDATA_MAX_AGE and (time.time() - auth_date) > config.INITDATA_MAX_AGE:
        raise AuthError("initData expired", 401)

    try:
        user = json.loads(parsed.get("user", "{}"))
    except ValueError:
        raise AuthError("initData user unparseable", 401)

    if not user.get("id"):
        raise AuthError("initData has no user", 401)
    return user


def authorize(init_data: str) -> dict:
    """Validate signature AND whitelist membership. Returns the user dict."""
    user = validate_init_data(init_data)
    if int(user["id"]) not in config.WHITELISTED_IDS:
        raise AuthError("Not authorized for this bot", 403)
    return user
