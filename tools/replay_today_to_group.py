"""Bugungi qabul qilingan navbatchi hisobotlarini guruh(lar)ga qayta yuborish.

Ishlatish (Railway yoki lokal, BOT_TOKEN + DB bilan):
    python tools/replay_today_to_group.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aiogram import Bot

import config
import database as db
from bot import send_work_report_to_groups, today_str


async def main() -> None:
    if not config.BOT_TOKEN:
        print("BOT_TOKEN yo'q")
        sys.exit(1)

    db.init_db()
    today = today_str()
    group_ids = db.all_group_chat_ids()
    print(f"Kun: {today} | Guruhlar: {group_ids} | DB: {config.DB_PATH}")

    reports = db.get_today_reports(today)
    if not reports:
        print("Bugun hisobot yo'q")
        return

    bot = Bot(token=config.BOT_TOKEN)
    try:
        me = await bot.get_me()
        print(f"Bot: @{me.username}")

        sent = 0
        for report in reports:
            if report.get("status") not in ("accepted", "submitted"):
                print(f"  skip {report.get('full_name')}: {report.get('status')}")
                continue
            employee = db.get_employee_by_id(report["employee_id"])
            if not employee:
                print(f"  skip report {report['id']}: xodim yo'q")
                continue
            ok, err = await send_work_report_to_groups(bot, employee, report)
            name = employee.get("full_name") or report.get("full_name")
            if ok:
                sent += 1
                print(f"  OK: {name}")
            else:
                print(f"  FAIL: {name} — {err}")
            await asyncio.sleep(0.5)

        print(f"Jami yuborildi: {sent}/{len(reports)}")
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
