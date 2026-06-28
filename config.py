"""Bot sozlamalari — .env faylidan o'qiladi."""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _env_int(name: str, default: int | None = None) -> int | None:
    """Butun son o'qish — noto'g'ri qiymatda default qaytaradi."""
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(str(raw).strip())
    except ValueError:
        print(f"[config] Ogohlantirish: {name} noto'g'ri — default ishlatiladi", file=sys.stderr)
        return default


def _env_str(name: str, default: str = "") -> str:
    raw = os.getenv(name)
    return raw.strip() if raw and raw.strip() else default


BOT_VERSION = "2.3.1"

# Telegram
BOT_TOKEN: str = _env_str("BOT_TOKEN")
ADMIN_IDS: list[int] = [
    int(x.strip())
    for x in _env_str("ADMIN_IDS").split(",")
    if x.strip().isdigit()
]
GROUP_CHAT_ID: int | None = _env_int("GROUP_CHAT_ID")

# SQLite
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "navbatchi.db"

# Jadval vaqtlari (Toshkent vaqti, UTC+5)
MORNING_HOUR = _env_int("MORNING_HOUR", 7) or 7
MORNING_MINUTE = _env_int("MORNING_MINUTE", 30) or 30
EVENING_HOUR = _env_int("EVENING_HOUR", 20) or 20
EVENING_MINUTE = _env_int("EVENING_MINUTE", 0) or 0

# Ball tizimi
SCORE_ON_TIME = 10
SCORE_BEFORE_PHOTO = 10
SCORE_AFTER_PHOTO = 10
SCORE_ACCEPTED = 20
SCORE_REDO = -15
SCORE_NO_REPORT = -30

# Hafta kunlari (0 = dushanba)
DAY_NAMES_UZ = {
    0: "dushanba",
    1: "seshanba",
    2: "chorshanba",
    3: "payshanba",
    4: "juma",
    5: "shanba",
    6: "yakshanba",
}

DAY_NAMES_UZ_CAP = {
    0: "Dushanba",
    1: "Seshanba",
    2: "Chorshanba",
    3: "Payshanba",
    4: "Juma",
    5: "Shanba",
    6: "Yakshanba",
}
