"""Central configuration. Reads from environment first, with local-dev fallbacks."""

from __future__ import annotations

import glob
import hashlib
import json
import os

# --- paths ------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(BASE_DIR, "static")


# --- access control ---------------------------------------------------------
# Hardcoded allowlist of Telegram user IDs. Add more by extending this list.
WHITELISTED_IDS: list[int] = [
    563381712,
    5639164058,
]


# --- spreadsheet ------------------------------------------------------------
def _read_sheet_id() -> str:
    """Secret. From env (Render) or a git-ignored sheet_id.txt (local dev)."""
    v = os.environ.get("SPREADSHEET_ID")
    if v:
        return v.strip()
    path = os.path.join(BASE_DIR, "sheet_id.txt")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()
    return ""


SPREADSHEET_ID = _read_sheet_id()
WORKSHEET_INDEX = int(os.environ.get("WORKSHEET_INDEX", "0"))

HEADER_ROW = 10        # row with column labels
DATA_START_ROW = 12    # first item row
NUM_COLS = 13          # A..M
LAST_COL_LETTER = "M"
SCAN_LAST_ROW = 250    # read generous range; data currently ends ~row 139

# Column roles (1-based indexes) used for list metadata.
COL_NAME = 1           # A  item name
COL_SOURCE = 5         # E  supplier
COL_ORDERABLE = 11     # K  orderable number (>0 => needs ordering)
COL_ORDERTEXT = 12     # L  human order text ("7 Box Bowl Schalen")

# Columns never surfaced in the app (meaning unknown / intentionally ignored).
EXCLUDED_COLS = {"M"}

# Master "plan for N days" cell. Column G is =K1, so every item's Needed/Orderable
# derives from this one cell. Editing it re-drives the whole order list.
PLANNING_CELL = "K1"

GRID_CACHE_TTL = 30    # seconds; also invalidated on every write


# --- telegram ---------------------------------------------------------------
def _read_token() -> str:
    tok = os.environ.get("BOT_TOKEN")
    if tok:
        return tok.strip()
    path = os.path.join(BASE_DIR, "token.txt")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()
    return ""


BOT_TOKEN = _read_token()

# Public base URL of this service, e.g. https://inventorybot.onrender.com
# Render exposes RENDER_EXTERNAL_URL automatically.
WEBAPP_URL = (
    os.environ.get("WEBAPP_URL")
    or os.environ.get("RENDER_EXTERNAL_URL")
    or ""
).rstrip("/")

# Secret token Telegram echoes back on each webhook call (anti-spoof).
# Telegram only allows [A-Za-z0-9_-] in secret_token, but Render's generated
# secret may contain other chars — so hash whatever we get into a valid 64-hex
# string. Same value is used to register the webhook and to verify it.
_raw_webhook_secret = os.environ.get("WEBHOOK_SECRET") or (
    ("whsec:" + BOT_TOKEN) if BOT_TOKEN else "dev-secret"
)
WEBHOOK_SECRET = hashlib.sha256(_raw_webhook_secret.encode()).hexdigest()

INITDATA_MAX_AGE = int(os.environ.get("INITDATA_MAX_AGE", str(24 * 3600)))

# Self-ping interval to keep Render's free instance awake (must be < 15 min idle
# window). Set to 0 to disable.
KEEPALIVE_INTERVAL = int(os.environ.get("KEEPALIVE_INTERVAL", "600"))


# --- google credentials -----------------------------------------------------
def service_account_info() -> dict | None:
    """Return SA dict from env JSON, else None to signal file-based auth."""
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if raw:
        return json.loads(raw)
    return None


def service_account_file() -> str:
    """Discover a service-account json on disk (local dev)."""
    env = os.environ.get("INVENTORY_SA_FILE")
    if env:
        return env
    default = os.path.join(BASE_DIR, "service_account.json")
    if os.path.exists(default):
        return default
    for path in glob.glob(os.path.join(BASE_DIR, "*.json")):
        try:
            with open(path, encoding="utf-8") as fh:
                if json.load(fh).get("type") == "service_account":
                    return path
        except (ValueError, OSError):
            continue
    return default
