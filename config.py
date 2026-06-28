"""Bot sozlamalari — .env faylidan o'qiladi."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Telegram
BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
ADMIN_IDS: list[int] = [
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
]
GROUP_CHAT_ID: int | None = (
    int(os.getenv("GROUP_CHAT_ID")) if os.getenv("GROUP_CHAT_ID") else None
)

# SQLite
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "navbatchi.db"

# Jadval vaqtlari (Toshkent vaqti, UTC+5)
MORNING_HOUR = int(os.getenv("MORNING_HOUR", "8"))
MORNING_MINUTE = int(os.getenv("MORNING_MINUTE", "0"))
EVENING_HOUR = int(os.getenv("EVENING_HOUR", "20"))
EVENING_MINUTE = int(os.getenv("EVENING_MINUTE", "0"))

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
