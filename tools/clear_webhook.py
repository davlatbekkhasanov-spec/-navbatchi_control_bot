"""Telegram webhook ni o'chirish — BOT-MARKET/constructor pollingni bloklaganda.

Ishlatish (BOT_TOKEN env bilan):
    python tools/clear_webhook.py
"""

from __future__ import annotations

import asyncio
import os
import sys

from aiogram import Bot
from dotenv import load_dotenv

load_dotenv()


async def main() -> None:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        print("BOT_TOKEN yo'q")
        sys.exit(1)
    bot = Bot(token=token)
    try:
        me = await bot.get_me()
        wh = await bot.get_webhook_info()
        print(f"Bot: @{me.username} (id={me.id})")
        print(f"Webhook: {wh.url or '(yoq)'}")
        if wh.url:
            await bot.delete_webhook(drop_pending_updates=True)
            print("Webhook o'chirildi — endi Railway polling ishlaydi.")
        else:
            print("Webhook allaqachon yo'q.")
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
