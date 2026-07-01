"""
Navbatchi — Omborxona navbatchilik Telegram boti.

Ombor tozaligi bo'yicha navbatchi xodimlarni boshqarish,
foto-hisobot yig'ish, rahbar tasdiqlashi va oylik reyting.
"""

import asyncio
import logging
import os
import sys
from datetime import date, datetime, timedelta, timezone

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    ChatMemberUpdated,
    ErrorEvent,
    InputMediaPhoto,
    Message,
)

import config
import database as db
import keyboards as kb
from access_middleware import TeamAccessMiddleware
from hub_summary import compact_hub_summary
from yordamchi_push import hub_status_line, push_to_yordamchi_hub, push_to_yordamchi_hub_background

# ─── Logging ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("navbatchi")

# Toshkent vaqti (UTC+5)
TZ_TASHKENT = timezone(timedelta(hours=5))

# ─── FSM holatlari ───────────────────────────────────────────────────────────
class ReportStates(StatesGroup):
    waiting_before = State()   # OLDIN rasmlar kutilmoqda
    waiting_after = State()    # KEYIN rasmlar kutilmoqda
    collecting_after = State() # KEYIN rasmlar yig'ilmoqda (ixtiyoriy)


class ComplaintStates(StatesGroup):
    collecting_photos = State()


# ─── Router ──────────────────────────────────────────────────────────────────
router = Router()


@router.errors()
async def global_error_handler(event: ErrorEvent) -> bool:
    """Kutilmagan xatolarni ushlash — bot crash bo'lmasin."""
    logger.exception("Handler xatosi: %s", event.exception)
    update = event.update
    try:
        if update.message:
            await update.message.answer("❌ Xatolik yuz berdi. Qayta urinib ko'ring.")
        elif update.callback_query:
            await update.callback_query.answer("❌ Xatolik!", show_alert=True)
    except Exception:
        pass
    return True


def is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_IDS


async def register_group_chat(bot: Bot, chat_id: int, chat_title: str | None = None) -> None:
    """Guruh chat ID ni bazaga saqlash va adminlarga xabar."""
    db.set_setting("group_chat_id", str(chat_id))
    title = chat_title or str(chat_id)
    logger.info("Guruh ulandi: %s (%s)", title, chat_id)
    for admin_id in config.ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"✅ Guruh ulandi!\n📢 <b>{title}</b>\n🆔 <code>{chat_id}</code>",
                parse_mode="HTML",
            )
        except Exception as e:
            logger.error("Admin %s ga guruh xabari xatosi: %s", admin_id, e)


def now_tashkent() -> datetime:
    return datetime.now(TZ_TASHKENT)


def today_str() -> str:
    return now_tashkent().date().isoformat()


def format_time(iso_str: str | None) -> str:
    """ISO vaqtni o'qilishi oson formatga."""
    if not iso_str:
        return "—"
    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.strftime("%H:%M")
    except ValueError:
        return iso_str


def status_emoji(status: str) -> str:
    mapping = {
        "started": "🔄",
        "submitted": "📨",
        "accepted": "✅",
        "rejected": "❌",
        "need_redo": "⚠️",
        "no_report": "🚫",
    }
    return mapping.get(status, "⏳")


def status_text(status: str) -> str:
    mapping = {
        "started": "boshlangan",
        "submitted": "yuborildi",
        "accepted": "qabul qilindi",
        "rejected": "rad etildi",
        "need_redo": "qayta tozalash",
        "no_report": "hisobot yo'q",
    }
    return mapping.get(status, "kutilmoqda")


def is_employee_on_duty(employee: dict) -> bool:
    """Xodim bugun reja bo'yicha navbatchilikda bormi?"""
    group, duty_employees = db.get_today_duty_employees()
    if not group:
        return False
    return any(e["id"] == employee["id"] for e in duty_employees)


def can_employee_work(employee: dict | None, *, submitted: bool) -> bool:
    """Jamoa xodimi bugun ish boshlash/yuborish huquqiga ega (ixtiyoriy ham)."""
    if not employee:
        return False
    return not submitted


def calculate_submit_score(before_count: int, after_count: int) -> int:
    """Hisobot yuborilganda ball hisoblash."""
    score = 0
    if before_count > 0:
        score += config.SCORE_BEFORE_PHOTO
    if after_count > 0:
        score += config.SCORE_AFTER_PHOTO
    # Vaqtida yuborilgan (kechki hisobotdan oldin)
    score += config.SCORE_ON_TIME
    return score


async def push_report_hub(report: dict, *, day: str | None = None) -> tuple[bool, str]:
    """Kunlik hisobot ballini yordamchi hub ga yuborish."""
    employee = db.get_employee_by_id(report["employee_id"])
    if not employee or not employee.get("telegram_user_id"):
        return False, "tg_id yo'q"
    summary = compact_hub_summary(
        score=int(report.get("score") or 0),
        status=str(report.get("status") or "unknown"),
        before=int(report.get("before_count") or 0),
        after=int(report.get("after_count") or 0),
    )
    return await push_to_yordamchi_hub(
        tg_id=int(employee["telegram_user_id"]),
        bot_key="navbatchi",
        summary=summary,
        day_iso=day or report.get("date") or today_str(),
    )


async def replay_today_hub_reports() -> int:
    """Bugungi hisobotlarni hub ga qayta yuborish (restartdan keyin)."""
    today = today_str()
    pushed = 0
    for report in db.get_today_reports(today):
        if report.get("status") not in ("accepted", "no_report"):
            continue
        try:
            ok, _ = await push_report_hub(report, day=today)
            if ok:
                pushed += 1
        except Exception as e:
            logger.warning("Hub replay xato report=%s: %s", report.get("id"), e)
    return pushed


async def replay_today_work_reports_to_groups(bot: Bot) -> int:
    """Bugungi qabul qilingan ish hisobotlarini guruh(lar)ga yuborish."""
    today = today_str()
    sent = 0
    for report in db.get_today_reports(today):
        if report.get("status") not in ("accepted", "submitted"):
            continue
        employee = db.get_employee_by_id(report["employee_id"])
        if not employee:
            continue
        try:
            ok, err = await send_work_report_to_groups(bot, employee, report)
            if ok:
                sent += 1
                logger.info("Guruh replay OK: %s", employee.get("full_name"))
            else:
                logger.warning("Guruh replay FAIL %s: %s", employee.get("full_name"), err)
            await asyncio.sleep(0.4)
        except Exception as e:
            logger.warning("Guruh replay xato report=%s: %s", report.get("id"), e)
    return sent


# ─── Yordamchi funksiyalar ───────────────────────────────────────────────────

async def get_employee_context(user_id: int) -> tuple[dict | None, dict | None, list[dict], dict | None]:
    """Xodim, guruh, bugungi navbatchilar va hisobot."""
    employee = db.get_employee_by_telegram_id(user_id)
    group, duty_employees = db.get_today_duty_employees()
    report = None
    if employee:
        report = db.get_report(employee["id"], today_str())
    return employee, group, duty_employees, report


def get_employee_state(user_id: int) -> dict:
    """Xodim holati — menyu logikasi uchun."""
    employee = db.get_employee_by_telegram_id(user_id)
    if not employee:
        return {
            "employee": None,
            "on_duty": False,
            "can_work": False,
            "has_started": False,
            "submitted": False,
            "report": None,
        }
    on_duty = is_employee_on_duty(employee)
    if is_admin(user_id):
        on_duty = True
    report = db.get_report(employee["id"], today_str())
    has_started = bool(report and report["status"] == "started")
    submitted = bool(
        report and report["status"] in ("submitted", "accepted")
    )
    can_work = can_employee_work(employee, submitted=submitted)
    return {
        "employee": employee,
        "on_duty": on_duty,
        "can_work": can_work,
        "has_started": has_started,
        "submitted": submitted,
        "report": report,
    }


async def _send_menu(message: Message, text: str, markup) -> None:
    """Inline menyu yuborish."""
    await message.answer(text, parse_mode="HTML", reply_markup=markup)


async def _edit_or_send_menu(callback: CallbackQuery, text: str, markup) -> None:
    """Callback da menyu yangilash."""
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=markup)


async def open_admin_home(message: Message) -> None:
    await message.answer(
        kb.ADMIN_MAIN_TEXT + f"\n\n🔖 Versiya: <b>{config.BOT_VERSION}</b>",
        parse_mode="HTML",
        reply_markup=kb.admin_reply_keyboard(),
    )


async def open_admin_duty_work(message: Message) -> None:
    """Admin — navbatchi ish jarayonini sinash."""
    db.ensure_admin_demo_employee(message.from_user.id)
    state = get_employee_state(message.from_user.id)
    emp = state["employee"]
    text = (
        "🧹 <b>Navbatchi ishi</b> (sinov rejimi)\n\n"
        f"👤 Profil: <b>{emp['full_name']}</b>\n"
        "Quyidagi tugmalar xodimlar bilan bir xil ishlaydi."
    )
    if state["submitted"]:
        text += "\n\n✅ Bugungi hisobot allaqachon yuborilgan."
    elif state["has_started"]:
        text += "\n\n🔄 Ish jarayoni davom etmoqda."
    else:
        text += "\n\n▶️ Ishni boshlash tugmasini bosing."
    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=kb.employee_duty_reply_keyboard(
            has_started=state["has_started"],
            submitted=state["submitted"],
        ),
    )


async def send_my_status(message: Message) -> None:
    """Xodim yoki admin — bugungi holat."""
    user_id = message.from_user.id
    if is_admin(user_id):
        db.ensure_admin_demo_employee(user_id)
    st = get_employee_state(user_id)
    if not st["employee"]:
        await message.answer("❌ Profil topilmadi.")
        return
    emp = st["employee"]
    r = st["report"]
    if not r:
        txt = f"📋 <b>{emp['full_name']}</b>\n\n⏳ Bugun hali ish boshlanmagan."
    else:
        txt = (
            f"📋 <b>{emp['full_name']}</b>\n\n"
            f"Holat: {status_emoji(r['status'])} <b>{status_text(r['status'])}</b>\n"
            f"📷 OLDIN: {r['before_count']} | KEYIN: {r['after_count']}\n"
            f"⭐ Ball: {r['score']}"
        )
    markup = (
        kb.admin_reply_keyboard()
        if is_admin(user_id)
        else kb.employee_duty_reply_keyboard(
            has_started=st["has_started"],
            submitted=st["submitted"],
        )
    )
    await message.answer(txt, parse_mode="HTML", reply_markup=markup)


async def open_employee_home(message: Message, state: dict) -> None:
    emp = state["employee"]
    if state["has_started"]:
        reply_markup = kb.work_reply_keyboard()
    elif state["can_work"]:
        reply_markup = kb.reply_base_keyboard([kb.BTN_WORK_START])
    else:
        reply_markup = kb.reply_base_keyboard()

    await message.answer(
        f"👋 <b>{emp['full_name']}</b>",
        parse_mode="HTML",
        reply_markup=reply_markup,
    )
    text = kb.EMP_MAIN_TEXT
    if state["on_duty"] and not state["submitted"]:
        text += "\n\n✅ Bugun siz <b>navbatchilikdasiz</b>."
    elif state["can_work"] and not state["on_duty"]:
        text += (
            "\n\nℹ️ Bugun reja bo'yicha navbatchilik kuningiz <b>emas</b>.\n"
            "🙋 Lekin <b>ixtiyoriy</b> tozalash qilishingiz mumkin."
        )
    elif not state["can_work"]:
        text += "\n\n✅ Bugungi hisobot allaqachon yuborilgan."
    await _send_menu(
        message,
        text,
        kb.employee_main_inline(
            can_work=state["can_work"],
            has_started=state["has_started"],
            submitted=state["submitted"],
        ),
    )


async def _do_start_work(message: Message, state: FSMContext) -> None:
    """Ishni boshlash — umumiy logika."""
    user_id = message.from_user.id
    admin_user = is_admin(user_id)
    if admin_user:
        db.ensure_admin_demo_employee(user_id)
    employee = db.get_employee_by_telegram_id(user_id)
    if not employee:
        await message.answer("❌ Avval o'zingizni tanlang (/start).")
        return

    today = today_str()
    existing = db.get_report(employee["id"], today)
    if existing:
        if existing["status"] in ("submitted", "accepted"):
            await message.answer("✅ Siz bugun allaqachon hisobot yuborgansiz.")
            return
        if existing["status"] == "started":
            markup = kb.employee_duty_reply_keyboard(has_started=True)
            await message.answer(
                "🔄 Ish allaqachon boshlangan. Rasmlarni yuboring.",
                reply_markup=markup,
            )
            if not admin_user:
                await _send_menu(message, kb.EMP_WORK_TEXT, kb.employee_work_inline())
            return

    scheduled = is_employee_on_duty(employee)
    if scheduled:
        group, _ = db.get_today_duty_employees()
    else:
        group = db.get_group_by_id(employee["group_id"])
    if not group:
        if admin_user:
            group = db.get_group_by_id(employee["group_id"])
        if not group:
            await message.answer("❌ Navbatchilik guruhi topilmadi.")
            return

    report = db.create_report(employee["id"], group["id"], today)
    await state.set_state(ReportStates.waiting_before)
    await state.update_data(report_id=report["id"])

    markup = kb.employee_duty_reply_keyboard(has_started=True)
    start_label = "▶️ Ish boshlandi!" if scheduled else "▶️ Ixtiyoriy ish boshlandi!"
    await message.answer(
        f"{start_label} ⏰ {format_time(report['start_time'])}\n\n"
        "📸 Avval <b>OLDIN</b> rasmlarni yuboring.",
        parse_mode="HTML",
        reply_markup=markup,
    )
    if not admin_user:
        await _send_menu(message, kb.EMP_WORK_TEXT, kb.employee_work_inline())


async def build_complaint_message() -> tuple[str, list[dict], dict | None]:
    """Shikoyat matni va bugungi navbatchilar."""
    today = now_tashkent()
    group, employees = db.get_today_duty_employees()
    day_name = config.DAY_NAMES_UZ_CAP[today.weekday()]
    if employees:
        names_block = "\n".join(f"  • <b>{e['full_name']}</b>" for e in employees)
    else:
        names_block = "  • <i>Bugun navbatchilik yo'q</i>"
    text = (
        "⚠️ <b>SHIKOYAT</b>\n\n"
        f"📅 {day_name}, {today.strftime('%d.%m.%Y')}\n"
        f"👥 Guruh: <b>{group['name'] if group else '—'}</b>\n\n"
        f"👤 <b>Bugungi navbatchilar:</b>\n{names_block}\n\n"
        "📸 Tozalash sifati qoniqarli emas — rasvo joylar aniqlandi."
    )
    return text, employees, group


async def send_complaint(bot: Bot, photo_ids: list[str]) -> tuple[bool, str]:
    """Shikoyatni guruhga va navbatchilarga yuborish."""
    text, employees, _group = await build_complaint_message()
    group_ids = db.all_group_chat_ids()
    sent_any = False

    for group_chat_id in group_ids:
        try:
            await bot.send_message(group_chat_id, text, parse_mode="HTML")
            sent_any = True
            for i in range(0, len(photo_ids), 10):
                chunk = photo_ids[i : i + 10]
                media = []
                for j, fid in enumerate(chunk):
                    cap = "📷 Shikoyat rasmi" if j == 0 and i == 0 else None
                    media.append(
                        InputMediaPhoto(media=fid, caption=cap)
                        if cap
                        else InputMediaPhoto(media=fid)
                    )
                await bot.send_media_group(group_chat_id, media=media)
        except Exception as e:
            logger.error("Guruhga shikoyat yuborishda xato (%s): %s", group_chat_id, e)

    notify_text = text + "\n\n⚠️ Iltimos, tezda bartaraf eting!"
    for emp in employees:
        tg_id = emp.get("telegram_user_id")
        if not tg_id:
            continue
        try:
            await bot.send_message(tg_id, notify_text, parse_mode="HTML")
            sent_any = True
            if photo_ids:
                for i in range(0, len(photo_ids), 10):
                    chunk = photo_ids[i : i + 10]
                    media = [
                        InputMediaPhoto(media=fid) for fid in chunk
                    ]
                    await bot.send_media_group(tg_id, media=media)
        except Exception as e:
            logger.error("Xodim %s ga shikoyat xatosi: %s", emp["full_name"], e)

    if sent_any:
        return True, "✅ Shikoyat yuborildi!"
    if not group_ids and not any(e.get("telegram_user_id") for e in employees):
        return False, "❌ Guruh ulanmagan va navbatchilar botda yo'q."
    return False, "❌ Shikoyat yuborilmadi. Guruh yoki xodimlarni tekshiring."


def build_work_report_message(employee: dict, report: dict) -> str:
    """Navbatchi kunlik ish hisoboti — guruh matni."""
    today = now_tashkent()
    day_name = config.DAY_NAMES_UZ_CAP[today.weekday()]
    submit_t = format_time(report.get("submit_time") or report.get("start_time"))
    report_group = db.get_group_by_id(report.get("group_id") or employee["group_id"])
    gname = report_group["name"] if report_group else "—"
    voluntary = not is_employee_on_duty(employee)
    voluntary_line = "\n🙋 <i>Ixtiyoriy navbatchilik</i>" if voluntary else ""
    return (
        "🧹 <b>NAVBATCHI HISOBOT</b>\n\n"
        f"👤 <b>{employee['full_name']}</b>{voluntary_line}\n"
        f"📅 {day_name}, {today.strftime('%d.%m.%Y')}  ⏰ {submit_t}\n"
        f"👥 Guruh: <b>{gname}</b>\n\n"
        f"📷 OLDIN: <b>{report.get('before_count') or 0}</b> ta\n"
        f"📷 KEYIN: <b>{report.get('after_count') or 0}</b> ta\n"
        f"⭐ Ball: <b>{report.get('score') or 0}</b>"
    )


async def send_work_report_to_groups(
    bot: Bot, employee: dict, report: dict
) -> tuple[bool, str]:
    """Xodim hisobotini barcha ulangan guruhlarga yuborish."""
    group_ids = db.all_group_chat_ids()
    if not group_ids:
        return False, "Guruh ulanmagan (/setgroup yoki GROUP_CHAT_ID)"

    text = build_work_report_message(employee, report)
    photos = db.get_report_photos(report["id"])
    before_ids = [p["photo_file_id"] for p in photos if p["photo_type"] == "before"]
    after_ids = [p["photo_file_id"] for p in photos if p["photo_type"] == "after"]

    sent_any = False
    last_err = ""
    for gid in group_ids:
        try:
            await bot.send_message(gid, text, parse_mode="HTML")
            sent_any = True
            for label, ids in (("📷 OLDIN", before_ids), ("📷 KEYIN", after_ids)):
                if not ids:
                    continue
                for i in range(0, len(ids), 10):
                    chunk = ids[i : i + 10]
                    media = []
                    for j, fid in enumerate(chunk):
                        cap = f"{label} ({len(ids)} ta)" if j == 0 and i == 0 else None
                        media.append(
                            InputMediaPhoto(media=fid, caption=cap)
                            if cap
                            else InputMediaPhoto(media=fid)
                        )
                    await bot.send_media_group(gid, media=media)
        except Exception as e:
            last_err = str(e)[:120]
            logger.error("Guruhga hisobot yuborish xatosi (%s): %s", gid, e)

    if sent_any:
        return True, ""
    return False, last_err or "yuborib bo'lmadi"


# ─── /start va yordam ────────────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    try:
        await state.clear()
        user_id = message.from_user.id

        if is_admin(user_id):
            await open_admin_home(message)
            return

        employee, group, duty_employees, report = await get_employee_context(user_id)

        if not employee:
            roster = db.get_all_employees()
            if roster:
                await message.answer(
                    "👋 Salom!\n\n"
                    "🧹 <b>Navbatchi</b> botiga xush kelibsiz.\n"
                    "Iltimos, o'zingizni tanlang:",
                    parse_mode="HTML",
                    reply_markup=kb.employee_select_keyboard(roster),
                )
            else:
                await message.answer(
                    "👋 Salom!\n\n"
                    "🧹 Xodimlar ro'yxati topilmadi.\n"
                    "Administrator bilan bog'laning.",
                    reply_markup=kb.reply_base_keyboard(),
                )
            return

        await open_employee_home(message, get_employee_state(user_id))
    except Exception as e:
        logger.exception("/start xatosi: %s", e)
        await message.answer("❌ Xatolik yuz berdi. /start ni qayta yuboring.")


@router.callback_query(F.data.startswith("link:"))
async def link_employee(callback: CallbackQuery) -> None:
    """Xodimni Telegram akkauntiga bog'lash."""
    employee_id = int(callback.data.split(":")[1])
    employee = db.get_employee_by_id(employee_id)
    if not employee:
        await callback.answer("Xodim topilmadi!", show_alert=True)
        return

    db.link_employee_telegram(employee_id, callback.from_user.id)
    await callback.answer(f"✅ {employee['full_name']} sifatida bog'landi!")
    await callback.message.edit_text(
        f"✅ Siz <b>{employee['full_name']}</b> sifatida ro'yxatdan o'tdingiz.",
        parse_mode="HTML",
    )

    await callback.message.answer("📋 Menyudan foydalaning:", reply_markup=kb.reply_base_keyboard())
    await open_employee_home(callback.message, get_employee_state(callback.from_user.id))


@router.message(F.text == kb.BTN_HOME)
async def btn_home_menu(message: Message, state: FSMContext) -> None:
    """Bosh menyu — admin yoki xodim ichki menyusi."""
    await state.clear()
    user_id = message.from_user.id
    if is_admin(user_id):
        await open_admin_home(message)
        return
    emp_state = get_employee_state(user_id)
    if not emp_state["employee"]:
        await message.answer("❌ Avval /start bilan ro'yxatdan o'ting.")
        return
    await open_employee_home(message, emp_state)


@router.message(F.text == kb.BTN_ADMIN_DUTY)
async def reply_admin_duty(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        kb.ADMIN_DUTY_TEXT,
        parse_mode="HTML",
        reply_markup=kb.admin_duty_reply_keyboard(),
    )


@router.message(F.text == kb.BTN_ADMIN_REPORTS)
async def reply_admin_reports(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        kb.ADMIN_REPORTS_TEXT,
        parse_mode="HTML",
        reply_markup=kb.admin_reports_reply_keyboard(),
    )


@router.message(F.text == kb.BTN_ADMIN_INFO)
async def reply_admin_info(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        kb.ADMIN_INFO_TEXT,
        parse_mode="HTML",
        reply_markup=kb.admin_info_reply_keyboard(),
    )


@router.message(F.text == kb.BTN_ADMIN_HELP)
async def reply_admin_help(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        _help_text(True),
        parse_mode="HTML",
        reply_markup=kb.admin_reply_keyboard(),
    )


@router.message(F.text == kb.BTN_MY_DUTY)
async def reply_admin_my_duty(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return
    await open_admin_duty_work(message)


@router.message(F.text == kb.BTN_MY_STATUS)
async def reply_my_status(message: Message) -> None:
    await send_my_status(message)


@router.message(F.text == kb.BTN_TODAY_VIEW)
async def reply_today_view(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        build_morning_message(),
        parse_mode="HTML",
        reply_markup=kb.admin_duty_reply_keyboard(),
    )


@router.message(F.text == kb.BTN_TODAY_SEND)
async def reply_today_send(message: Message, bot: Bot) -> None:
    if not is_admin(message.from_user.id):
        return
    ok = await send_duty_list_to_group(bot, manual=True)
    if ok:
        await message.answer(
            "✅ Navbatchilar ro'yxati guruhga yuborildi!",
            reply_markup=kb.admin_duty_reply_keyboard(),
        )
    else:
        await message.answer(
            "❌ Guruh ulanmagan. Guruhda /setgroup yuboring.",
            reply_markup=kb.admin_duty_reply_keyboard(),
        )


@router.message(F.text == kb.BTN_REPORT_TODAY)
async def reply_report_today(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        build_evening_message(),
        parse_mode="HTML",
        reply_markup=kb.admin_reports_reply_keyboard(),
    )


@router.message(F.text == kb.BTN_RATING)
async def reply_rating(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return
    now = now_tashkent()
    ratings = db.get_monthly_rating(now.year, now.month)
    lines = [f"🏆 <b>Oylik reyting — {now.strftime('%B %Y')}</b>\n"]
    for i, r in enumerate(ratings, 1):
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
        lines.append(f"{medal} <b>{r['full_name']}</b> ({r['group_name']}) — {r['total_score']} ball")
    await message.answer(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=kb.admin_reports_reply_keyboard(),
    )


@router.message(F.text == kb.BTN_EMPLOYEES)
async def reply_employees(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return
    employees = db.get_all_employees()
    lines = ["👥 <b>Xodimlar ro'yxati</b>\n"]
    for e in employees:
        rest = config.DAY_NAMES_UZ_CAP[e["rest_day"]]
        lines.append(f"• <b>{e['full_name']}</b> — {e['group_name']}, dam: {rest}")
    await message.answer(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=kb.admin_info_reply_keyboard(),
    )


@router.message(F.text == kb.BTN_GROUPS)
async def reply_groups(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return
    groups = db.get_all_groups()
    lines = ["🗂️ <b>Navbatchilik guruhlari</b>\n"]
    for g in groups:
        days = ", ".join(config.DAY_NAMES_UZ_CAP[d] for d in g["duty_days_list"])
        emps = db.get_employees_by_group(g["id"])
        names = ", ".join(e["full_name"] for e in emps)
        lines.append(f"\n<b>{g['name']}</b>\n📅 {days}\n👤 {names}")
    await message.answer(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=kb.admin_info_reply_keyboard(),
    )


def _help_text(is_admin_user: bool) -> str:
    text = (
        "ℹ️ <b>Yordam</b>\n\n"
        "🧹 <b>Navbatchi</b> — ombor tozaligi boti.\n\n"
        "<b>Navbatchilik tartibi:</b>\n"
        "1️⃣ Ishni boshlash\n"
        "2️⃣ OLDIN rasm (majburiy)\n"
        "3️⃣ KEYIN rasm (ixtiyoriy)\n"
        "4️⃣ Hisobotni yuborish\n\n"
        "🙋 Reja bo'yicha navbatchi bo'lmagan kunda ham jamoa xodimlari "
        "<b>ixtiyoriy</b> tozalash qilishi mumkin.\n\n"
        "<b>Ball tizimi:</b>\n"
        f"• Vaqtida: +{config.SCORE_ON_TIME} | OLDIN: +{config.SCORE_BEFORE_PHOTO}\n"
        f"• KEYIN: +{config.SCORE_AFTER_PHOTO} | Avto qabul: +{config.SCORE_ACCEPTED}\n"
        f"• Yo'q hisobot: {config.SCORE_NO_REPORT}"
    )
    if is_admin_user:
        text += (
            "\n\n<b>Admin bo'limlari:</b>\n"
            "📋 Navbatchilik — ro'yxat va guruhga yuborish\n"
            "📊 Hisobotlar — kunlik va oylik\n"
            "👥 Ma'lumotnoma — xodimlar va guruhlar\n"
            "🧹 Navbatchi ishi — sinov rejimi\n"
            "⚠️ Shikoyat — rasvo joy rasmlari + bugungi navbatchilar"
        )
    return text


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    admin_user = is_admin(message.from_user.id)
    text = _help_text(admin_user)
    markup = kb.admin_reply_keyboard() if admin_user else kb.admin_result_inline(kb.MENU_EMP_MAIN)
    await message.answer(text, parse_mode="HTML", reply_markup=markup)


# ─── Ichki menyu navigatsiya ─────────────────────────────────────────────────

@router.callback_query(F.data.startswith("m:"))
async def menu_navigate(callback: CallbackQuery) -> None:
    """Ichki menyu — bo'limlar orasida harakat."""
    if not is_admin(callback.from_user.id) and callback.data.startswith("m:adm"):
        await callback.answer("Ruxsat yo'q!", show_alert=True)
        return

    data = callback.data
    if data == kb.MENU_ADMIN_MAIN:
        await callback.answer()
        await callback.message.answer(
            kb.ADMIN_MAIN_TEXT,
            parse_mode="HTML",
            reply_markup=kb.admin_reply_keyboard(),
        )
    elif data == kb.MENU_ADMIN_DUTY:
        await callback.answer()
        await callback.message.answer(
            kb.ADMIN_DUTY_TEXT,
            parse_mode="HTML",
            reply_markup=kb.admin_duty_reply_keyboard(),
        )
    elif data == kb.MENU_ADMIN_REPORTS:
        await callback.answer()
        await callback.message.answer(
            kb.ADMIN_REPORTS_TEXT,
            parse_mode="HTML",
            reply_markup=kb.admin_reports_reply_keyboard(),
        )
    elif data == kb.MENU_ADMIN_INFO:
        await callback.answer()
        await callback.message.answer(
            kb.ADMIN_INFO_TEXT,
            parse_mode="HTML",
            reply_markup=kb.admin_info_reply_keyboard(),
        )
    elif data == kb.MENU_EMP_MAIN:
        st = get_employee_state(callback.from_user.id)
        text = kb.EMP_MAIN_TEXT
        if st["on_duty"] and not st["submitted"]:
            text += "\n\n✅ Bugun siz <b>navbatchilikdasiz</b>."
        elif st["can_work"] and not st["on_duty"]:
            text += (
                "\n\nℹ️ Bugun reja bo'yicha navbatchilik kuningiz <b>emas</b>.\n"
                "🙋 Lekin <b>ixtiyoriy</b> tozalash qilishingiz mumkin."
            )
        elif not st["can_work"]:
            text += "\n\n✅ Bugungi hisobot allaqachon yuborilgan."
        await _edit_or_send_menu(
            callback,
            text,
            kb.employee_main_inline(
                can_work=st["can_work"],
                has_started=st["has_started"],
                submitted=st["submitted"],
            ),
        )
    elif data == kb.MENU_EMP_WORK:
        await _edit_or_send_menu(callback, kb.EMP_WORK_TEXT, kb.employee_work_inline())
    else:
        return
    await callback.answer()


@router.callback_query(F.data.startswith("a:"))
async def menu_action(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    """Menyu amallari."""
    data = callback.data
    user_id = callback.from_user.id

    # Ish jarayoni yo'riqnomasi
    if data.startswith("a:work:hint:"):
        hints = {
            "a:work:hint:before": "📸 Quyidagi reply tugmani bosing:\n<b>📸 OLDIN rasm yuborish</b>",
            "a:work:hint:after": "✅ Quyidagi reply tugmani bosing:\n<b>✅ Tozalash tugadi / KEYIN rasmlar</b>",
            "a:work:hint:submit": "📤 Quyidagi reply tugmani bosing:\n<b>📤 Hisobotni yuborish</b>",
        }
        await callback.answer()
        await callback.message.answer(hints.get(data, ""), parse_mode="HTML")
        return

    if data == kb.ACT_WORK_START:
        await callback.answer()
        await _do_start_work(callback.message, state)
        return

    if data == kb.ACT_HELP:
        admin_user = is_admin(user_id)
        await callback.answer()
        markup = kb.admin_reply_keyboard() if admin_user else kb.admin_result_inline(kb.MENU_EMP_MAIN)
        await callback.message.answer(_help_text(admin_user), parse_mode="HTML", reply_markup=markup)
        return

    if data == kb.ACT_STATUS:
        st = get_employee_state(user_id)
        if not st["employee"]:
            await callback.answer("Ro'yxatdan o'tmagan!", show_alert=True)
            return
        emp = st["employee"]
        r = st["report"]
        if not r:
            txt = f"📋 <b>{emp['full_name']}</b>\n\n⏳ Bugun hali ish boshlanmagan."
        else:
            txt = (
                f"📋 <b>{emp['full_name']}</b>\n\n"
                f"Holat: {status_emoji(r['status'])} <b>{status_text(r['status'])}</b>\n"
                f"📷 OLDIN: {r['before_count']} | KEYIN: {r['after_count']}\n"
                f"⭐ Ball: {r['score']}"
            )
        await callback.answer()
        await callback.message.answer(txt, parse_mode="HTML", reply_markup=kb.admin_result_inline(kb.MENU_EMP_MAIN))
        return

    # Admin amallari
    if not is_admin(user_id):
        await callback.answer("Ruxsat yo'q!", show_alert=True)
        return

    if data == kb.ACT_TODAY_VIEW:
        await callback.answer()
        await callback.message.answer(
            build_morning_message(),
            parse_mode="HTML",
            reply_markup=kb.admin_duty_reply_keyboard(),
        )
    elif data == kb.ACT_TODAY_SEND:
        ok = await send_duty_list_to_group(bot, manual=True)
        await callback.answer("✅ Yuborildi!" if ok else "❌ Guruh ulanmagan!", show_alert=not ok)
        if ok:
            await callback.message.answer(
                "✅ Navbatchilar ro'yxati guruhga yuborildi!",
                reply_markup=kb.admin_duty_reply_keyboard(),
            )
        else:
            await callback.message.answer(
                "❌ Guruh ulanmagan. Guruhda /setgroup yuboring.",
                reply_markup=kb.admin_duty_reply_keyboard(),
            )
    elif data == kb.ACT_REPORT:
        await callback.answer()
        await callback.message.answer(
            build_evening_message(),
            parse_mode="HTML",
            reply_markup=kb.admin_reports_reply_keyboard(),
        )
    elif data == kb.ACT_RATING:
        now = now_tashkent()
        ratings = db.get_monthly_rating(now.year, now.month)
        lines = [f"🏆 <b>Oylik reyting — {now.strftime('%B %Y')}</b>\n"]
        for i, r in enumerate(ratings, 1):
            medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
            lines.append(f"{medal} <b>{r['full_name']}</b> ({r['group_name']}) — {r['total_score']} ball")
        await callback.answer()
        await callback.message.answer(
            "\n".join(lines),
            parse_mode="HTML",
            reply_markup=kb.admin_reports_reply_keyboard(),
        )
    elif data == kb.ACT_EMPLOYEES:
        employees = db.get_all_employees()
        lines = ["👥 <b>Xodimlar ro'yxati</b>\n"]
        for e in employees:
            rest = config.DAY_NAMES_UZ_CAP[e["rest_day"]]
            lines.append(f"• <b>{e['full_name']}</b> — {e['group_name']}, dam: {rest}")
        await callback.answer()
        await callback.message.answer(
            "\n".join(lines),
            parse_mode="HTML",
            reply_markup=kb.admin_info_reply_keyboard(),
        )
    elif data == kb.ACT_GROUPS:
        groups = db.get_all_groups()
        lines = ["🗂️ <b>Navbatchilik guruhlari</b>\n"]
        for g in groups:
            days = ", ".join(config.DAY_NAMES_UZ_CAP[d] for d in g["duty_days_list"])
            emps = db.get_employees_by_group(g["id"])
            names = ", ".join(e["full_name"] for e in emps)
            lines.append(f"\n<b>{g['name']}</b>\n📅 {days}\n👤 {names}")
        await callback.answer()
        await callback.message.answer(
            "\n".join(lines),
            parse_mode="HTML",
            reply_markup=kb.admin_info_reply_keyboard(),
        )


# ─── Admin buyruqlari (slash — qolgan) ───────────────────────────────────────

@router.message(Command("today"))
async def cmd_today(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        build_morning_message(),
        parse_mode="HTML",
        reply_markup=kb.admin_duty_reply_keyboard(),
    )


@router.message(Command("report_today"))
async def cmd_report_today(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        build_evening_message(),
        parse_mode="HTML",
        reply_markup=kb.admin_reports_reply_keyboard(),
    )


@router.message(Command("replay_guruh"))
async def cmd_replay_guruh(message: Message, bot: Bot) -> None:
    """Admin: bugungi ish hisobotlarini guruhga qayta yuborish."""
    if not is_admin(message.from_user.id):
        return
    await message.answer("📤 Bugungi hisobotlar guruhga yuborilmoqda...")
    n = await replay_today_work_reports_to_groups(bot)
    await message.answer(
        f"✅ Tayyor: <b>{n}</b> ta hisobot guruhga yuborildi.",
        parse_mode="HTML",
        reply_markup=kb.admin_duty_reply_keyboard(),
    )


@router.message(Command("rating"))
async def cmd_rating(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return
    now = now_tashkent()
    ratings = db.get_monthly_rating(now.year, now.month)
    lines = [f"🏆 <b>Oylik reyting — {now.strftime('%B %Y')}</b>\n"]
    for i, r in enumerate(ratings, 1):
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
        lines.append(f"{medal} <b>{r['full_name']}</b> ({r['group_name']}) — {r['total_score']} ball")
    await message.answer(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=kb.admin_reports_reply_keyboard(),
    )


@router.message(Command("employees"))
async def cmd_employees(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return
    employees = db.get_all_employees()
    lines = ["👥 <b>Xodimlar ro'yxati</b>\n"]
    for e in employees:
        rest = config.DAY_NAMES_UZ_CAP[e["rest_day"]]
        lines.append(f"• <b>{e['full_name']}</b> — {e['group_name']}, dam: {rest}")
    await message.answer(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=kb.admin_info_reply_keyboard(),
    )


@router.message(Command("groups"))
async def cmd_groups(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return
    groups = db.get_all_groups()
    lines = ["🗂️ <b>Navbatchilik guruhlari</b>\n"]
    for g in groups:
        days = ", ".join(config.DAY_NAMES_UZ_CAP[d] for d in g["duty_days_list"])
        emps = db.get_employees_by_group(g["id"])
        names = ", ".join(e["full_name"] for e in emps)
        lines.append(f"\n<b>{g['name']}</b>\n📅 {days}\n👤 {names}")
    await message.answer(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=kb.admin_info_reply_keyboard(),
    )


@router.message(Command("setgroup"))
async def cmd_setgroup(message: Message, bot: Bot) -> None:
    """Guruh chat ID ni qo'lda ulash (admin guruhda yuboradi)."""
    if not is_admin(message.from_user.id):
        return
    if message.chat.type not in ("group", "supergroup"):
        await message.answer(
            "❌ Bu buyruq faqat <b>guruh ichida</b> ishlaydi.\n"
            "Botni guruhga qo'shing va u yerda /setgroup yuboring.",
            parse_mode="HTML",
        )
        return
    await register_group_chat(bot, message.chat.id, message.chat.title)
    await message.answer(
        f"✅ Guruh ulandi!\n🆔 <code>{message.chat.id}</code>",
        parse_mode="HTML",
    )


@router.my_chat_member()
async def bot_added_to_group(event: ChatMemberUpdated, bot: Bot) -> None:
    """Bot guruhga qo'shilganda avtomatik ulash."""
    try:
        new_status = event.new_chat_member.status
        if new_status not in ("member", "administrator"):
            return
        if event.chat.type not in ("group", "supergroup"):
            return
        await register_group_chat(bot, event.chat.id, event.chat.title)
    except Exception as e:
        logger.error("Guruh ulanish xatosi: %s", e)


# ─── Ishni boshlash ──────────────────────────────────────────────────────────

@router.message(F.text.in_({kb.BTN_WORK_START, "▶️ Ishni boshlash"}))
async def start_work(message: Message, state: FSMContext) -> None:
    if is_admin(message.from_user.id):
        db.ensure_admin_demo_employee(message.from_user.id)
    await _do_start_work(message, state)


# ─── Foto-hisobot jarayoni ───────────────────────────────────────────────────

@router.message(F.text.in_({kb.BTN_WORK_BEFORE, "📸 OLDIN rasm yuborish"}))
async def btn_before_photos(message: Message, state: FSMContext) -> None:
    if is_admin(message.from_user.id):
        db.ensure_admin_demo_employee(message.from_user.id)
    employee = db.get_employee_by_telegram_id(message.from_user.id)
    if not employee:
        return
    report = db.get_report(employee["id"], today_str())
    if not report or report["status"] != "started":
        await message.answer("❌ Avval ▶️ Ishni boshlash tugmasini bosing.")
        return
    await state.set_state(ReportStates.waiting_before)
    await state.update_data(report_id=report["id"])
    await message.answer(
        "📸 <b>OLDIN</b> rasmlarni yuboring.\n"
        "Bir yoki bir nechta rasm yuborishingiz mumkin.\n"
        "Tugagach ✅ Tozalash tugadi tugmasini bosing.",
        parse_mode="HTML",
    )


@router.message(ReportStates.waiting_before, F.photo)
async def receive_before_photo(message: Message, state: FSMContext) -> None:
    if is_admin(message.from_user.id):
        db.ensure_admin_demo_employee(message.from_user.id)
    data = await state.get_data()
    report_id = data.get("report_id")
    if not report_id:
        employee = db.get_employee_by_telegram_id(message.from_user.id)
        if employee:
            report = db.get_report(employee["id"], today_str())
            if report and report["status"] == "started":
                report_id = report["id"]
                await state.update_data(report_id=report_id)
    if not report_id:
        await message.answer("❌ Avval ▶️ Ishni boshlash tugmasini bosing.")
        return
    file_id = message.photo[-1].file_id
    db.add_photo(report_id, file_id, "before")
    employee = db.get_employee_by_telegram_id(message.from_user.id)
    if not employee:
        return
    report = db.get_report(employee["id"], today_str())
    if not report:
        return
    await message.answer(
        f"✅ OLDIN rasm qabul qilindi! (jami: {report['before_count']})\n"
        "Yana rasm yuborishingiz yoki ✅ Tozalash tugadi tugmasini bosishingiz mumkin."
    )


@router.message(F.text.in_({kb.BTN_WORK_AFTER, "✅ Tozalash tugadi / KEYIN rasmlar"}))
async def btn_after_photos(message: Message, state: FSMContext) -> None:
    if is_admin(message.from_user.id):
        db.ensure_admin_demo_employee(message.from_user.id)
    employee = db.get_employee_by_telegram_id(message.from_user.id)
    if not employee:
        return
    report = db.get_report(employee["id"], today_str())
    if not report or report["status"] != "started":
        await message.answer("❌ Avval ishni boshlang.")
        return
    if report["before_count"] == 0:
        await message.answer(
            "❌ Kamida bitta <b>OLDIN</b> rasm yuborishingiz shart!",
            parse_mode="HTML",
        )
        return
    await state.set_state(ReportStates.collecting_after)
    await state.update_data(report_id=report["id"])
    await message.answer(
        "📸 Endi <b>KEYIN</b> rasmlarni yuboring (ixtiyoriy).\n"
        "Tugagach 📤 Hisobotni yuborish tugmasini bosing.",
        parse_mode="HTML",
    )


@router.message(ReportStates.collecting_after, F.photo)
@router.message(ReportStates.waiting_after, F.photo)
async def receive_after_photo(message: Message, state: FSMContext) -> None:
    if is_admin(message.from_user.id):
        db.ensure_admin_demo_employee(message.from_user.id)
    data = await state.get_data()
    report_id = data.get("report_id")
    if not report_id:
        return
    file_id = message.photo[-1].file_id
    db.add_photo(report_id, file_id, "after")
    employee = db.get_employee_by_telegram_id(message.from_user.id)
    if not employee:
        return
    report = db.get_report(employee["id"], today_str())
    if not report:
        return
    await message.answer(
        f"✅ KEYIN rasm qabul qilindi! (jami: {report['after_count']})\n"
        "Yana rasm yuborishingiz yoki 📤 Hisobotni yuborish tugmasini bosishingiz mumkin."
    )


# ─── Admin shikoyat ──────────────────────────────────────────────────────────

@router.message(F.text == kb.BTN_COMPLAINT)
async def start_complaint(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    text, employees, _ = await build_complaint_message()
    await state.set_state(ComplaintStates.collecting_photos)
    await state.update_data(complaint_photos=[])
    names = ", ".join(e["full_name"] for e in employees) if employees else "—"
    await message.answer(
        text
        + "\n\n📸 <b>Rasvo joylarning rasmlarini yuboring.</b>\n"
        f"Tayyor bo'lgach — <b>{kb.BTN_COMPLAINT_SEND}</b> tugmasini bosing.\n\n"
        f"👤 Navbatchilar: {names}",
        parse_mode="HTML",
        reply_markup=kb.admin_complaint_keyboard(),
    )


@router.message(ComplaintStates.collecting_photos, F.photo)
async def complaint_add_photo(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    photos: list[str] = list(data.get("complaint_photos", []))
    photos.append(message.photo[-1].file_id)
    await state.update_data(complaint_photos=photos)
    await message.answer(
        f"✅ Rasm qabul qilindi! (jami: {len(photos)} ta)\n"
        f"Tayyor bo'lgach — <b>{kb.BTN_COMPLAINT_SEND}</b> bosing.",
        parse_mode="HTML",
    )


@router.message(ComplaintStates.collecting_photos, F.text == kb.BTN_COMPLAINT_SEND)
async def complaint_submit(message: Message, state: FSMContext, bot: Bot) -> None:
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    photos: list[str] = list(data.get("complaint_photos", []))
    if not photos:
        await message.answer("❌ Kamida bitta rasm yuboring!")
        return
    ok, result = await send_complaint(bot, photos)
    await state.clear()
    await message.answer(result, reply_markup=kb.admin_reply_keyboard())


@router.message(ComplaintStates.collecting_photos, F.text == kb.BTN_COMPLAINT_CANCEL)
async def complaint_cancel(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    await message.answer(
        "❌ Shikoyat bekor qilindi.",
        reply_markup=kb.admin_reply_keyboard(),
    )


@router.message(F.photo)
async def receive_photo_without_state(message: Message, state: FSMContext) -> None:
    """FSM holati yo'qolsa ham ish jarayonidagi rasmni qabul qilish."""
    if is_admin(message.from_user.id):
        db.ensure_admin_demo_employee(message.from_user.id)
    employee = db.get_employee_by_telegram_id(message.from_user.id)
    if not employee:
        return
    report = db.get_report(employee["id"], today_str())
    if not report or report["status"] != "started":
        return
    current = await state.get_state()
    if current in (
        ReportStates.waiting_before.state,
        ReportStates.collecting_after.state,
        ReportStates.waiting_after.state,
        ComplaintStates.collecting_photos.state,
    ):
        return
    if report["before_count"] == 0:
        await state.set_state(ReportStates.waiting_before)
        await state.update_data(report_id=report["id"])
        await receive_before_photo(message, state)
    else:
        await state.set_state(ReportStates.collecting_after)
        await state.update_data(report_id=report["id"])
        await receive_after_photo(message, state)


@router.message(F.text.in_({kb.BTN_WORK_SUBMIT, "📤 Hisobotni yuborish"}))
async def submit_report(message: Message, state: FSMContext, bot: Bot) -> None:
    if is_admin(message.from_user.id):
        db.ensure_admin_demo_employee(message.from_user.id)
    employee = db.get_employee_by_telegram_id(message.from_user.id)
    if not employee:
        return
    report = db.get_report(employee["id"], today_str())
    if not report or report["status"] != "started":
        await message.answer("❌ Avval ishni boshlang va rasmlarni yuboring.")
        return
    if report["before_count"] == 0:
        await message.answer("❌ Kamida bitta OLDIN rasm kerak!")
        return

    score = calculate_submit_score(report["before_count"], report["after_count"])
    now = datetime.now().isoformat()
    updated = db.try_submit_report(report["id"], score=score, submit_time=now)
    if not updated:
        await message.answer("ℹ️ Bu hisobot allaqachon yuborilgan.")
        return
    push_to_yordamchi_hub_background(
        tg_id=int((db.get_employee_by_id(updated["employee_id"]) or {}).get("telegram_user_id") or 0),
        bot_key="navbatchi",
        summary=compact_hub_summary(
            score=int(updated["score"]),
            status=updated["status"],
            before=int(updated["before_count"] or 0),
            after=int(updated["after_count"] or 0),
        ),
        day_iso=updated.get("date") or today_str(),
    )
    group_ok, group_err = await send_work_report_to_groups(bot, employee, updated)
    await state.clear()
    admin_user = is_admin(message.from_user.id)
    home_markup = (
        kb.admin_reply_keyboard()
        if admin_user
        else kb.reply_base_keyboard()
    )
    await message.answer(
        f"✅ <b>Hisobot qabul qilindi!</b>\n\n"
        f"📷 OLDIN: {updated['before_count']} ta\n"
        f"📷 KEYIN: {updated['after_count']} ta\n"
        f"⭐ Ball: {updated['score']}"
        + (f"\n\n📢 Guruhga yuborildi." if group_ok else f"\n\n⚠️ Guruhga yuborilmadi: {group_err}"),
        reply_markup=home_markup,
    )


# ─── Guruh xabarlari ─────────────────────────────────────────────────────────

def build_morning_message() -> str:
    """Ertalabki navbatchilik xabari."""
    group, employees = db.get_today_duty_employees()
    today = now_tashkent()
    day_name = config.DAY_NAMES_UZ_CAP[today.weekday()]

    if not group:
        return f"📅 <b>{day_name}</b>\n\nℹ️ Bugun navbatchilik yo'q."

    lines = [
        f"🌅 <b>Bugungi navbatchilik</b>",
        f"📅 {day_name}, {today.strftime('%d.%m.%Y')}",
        f"👥 Guruh: <b>{group['name']}</b>",
        "",
        "👤 Navbatchilar:",
    ]
    for e in employees:
        rest_note = ""
        if e["rest_day"] == today.weekday():
            rest_note = " (dam olish)"
        lines.append(f"  • <b>{e['full_name']}</b>{rest_note}")

    lines += [
        "",
        "📌 Eslatma: botga kirib ▶️ Ishni boshlash tugmasini bosing!",
    ]
    return "\n".join(lines)


def build_evening_message() -> str:
    """Kechki yakuniy hisobot."""
    group, employees = db.get_today_duty_employees()
    today = today_str()
    reports = {r["employee_id"]: r for r in db.get_today_reports(today)}

    if not group:
        return "🧹 Bugun navbatchilik yo'q edi."

    lines = [
        "🧹 <b>Bugungi navbatchilik yakuni</b>",
        f"👥 Guruh: <b>{group['name']}</b>",
        "",
        "👤 Xodimlar:",
    ]

    for e in employees:
        r = reports.get(e["id"])
        if r:
            emoji = status_emoji(r["status"])
            label = status_text(r["status"])
            score = r["score"]
            lines.append(f"  • <b>{e['full_name']}</b> — {emoji} {label} — {score} ball")
        else:
            lines.append(f"  • <b>{e['full_name']}</b> — ⏳ hali yubormadi")

    return "\n".join(lines)


async def send_duty_list_to_group(bot: Bot, *, manual: bool = False) -> bool:
    """Navbatchilar ro'yxatini guruhga yuborish."""
    group_ids = db.all_group_chat_ids()
    if not group_ids:
        return False
    today = today_str()
    if not manual and db.was_task_run("morning", today):
        return False
    text = build_morning_message()
    sent = False
    for group_chat_id in group_ids:
        try:
            await bot.send_message(group_chat_id, text, parse_mode="HTML")
            sent = True
        except Exception as e:
            logger.error("Navbatchilar yuborish xatosi (%s): %s", group_chat_id, e)
    if sent:
        if not manual:
            db.mark_task_run("morning", today)
        logger.info("Navbatchilar ro'yxati yuborildi (manual=%s)", manual)
    return sent


async def send_morning_report(bot: Bot) -> None:
    """Ertalab 07:30 da avtomatik yuborish."""
    await send_duty_list_to_group(bot, manual=False)


async def send_evening_report(bot: Bot) -> None:
    """Kechqurun guruhga yakuniy hisobot."""
    group_ids = db.all_group_chat_ids()
    if not group_ids:
        return
    today = today_str()
    if db.was_task_run("evening", today):
        return

    # Hisobot yubormagan xodimlarga jarima
    group, employees = db.get_today_duty_employees()
    if group:
        reports = {r["employee_id"]: r for r in db.get_today_reports(today)}
        for e in employees:
            if e["id"] not in reports:
                db.create_no_report_penalty(e["id"], group["id"], today)
                penalty = db.get_report(e["id"], today)
                if penalty and e.get("telegram_user_id"):
                    push_to_yordamchi_hub_background(
                        tg_id=int(e["telegram_user_id"]),
                        bot_key="navbatchi",
                        summary=compact_hub_summary(
                            score=int(penalty["score"]),
                            status=penalty["status"],
                        ),
                        day_iso=today,
                    )

    text = build_evening_message()
    sent = False
    for group_chat_id in group_ids:
        try:
            await bot.send_message(group_chat_id, text, parse_mode="HTML")
            sent = True
        except Exception as e:
            logger.error("Kechki xabar xatosi (%s): %s", group_chat_id, e)
    if sent:
        db.mark_task_run("evening", today)
        logger.info("Kechki xabar yuborildi")


# ─── Scheduler ───────────────────────────────────────────────────────────────

async def scheduler_loop(bot: Bot) -> None:
    """Har daqiqa vaqtni tekshirib, jadval bo'yicha xabar yuborish."""
    while True:
        try:
            now = now_tashkent()
            today = now.date().isoformat()

            if now.hour == config.MORNING_HOUR and now.minute == config.MORNING_MINUTE:
                if not db.was_task_run("morning", today):
                    await send_morning_report(bot)

            if now.hour == config.EVENING_HOUR and now.minute == config.EVENING_MINUTE:
                if not db.was_task_run("evening", today):
                    await send_evening_report(bot)

        except Exception as e:
            logger.error("Scheduler xatosi: %s", e)

        await asyncio.sleep(30)


# ─── Ishga tushirish ─────────────────────────────────────────────────────────

async def main() -> None:
    if not config.BOT_TOKEN:
        logger.error("❌ BOT_TOKEN topilmadi! Railway Variables ga BOT_TOKEN qo'shing.")
        sys.exit(1)

    try:
        db.init_db()
    except Exception as e:
        logger.exception("❌ Ma'lumotlar bazasi xatosi: %s", e)
        sys.exit(1)

    logger.info("Ma'lumotlar bazasi tayyor: %s", config.DB_PATH)
    logger.info("Admin IDs: %s", config.ADMIN_IDS or "yo'q")
    logger.info("Guruhlar: %s", db.all_group_chat_ids() or "—")
    logger.info("Yordamchi hub: %s", hub_status_line())

    bot = Bot(token=config.BOT_TOKEN)
    me = await bot.get_me()
    logger.info("Bot: @%s (id=%s)", me.username, me.id)

    wh = await bot.get_webhook_info()
    if wh.url:
        logger.warning("Webhook topildi (%s) — o'chirilmoqda (faqat polling)", wh.url)
        await bot.delete_webhook(drop_pending_updates=True)
    else:
        logger.info("Webhook yo'q — polling rejimi")

    dp = Dispatcher(storage=MemoryStorage())
    dp.message.middleware(TeamAccessMiddleware())
    dp.callback_query.middleware(TeamAccessMiddleware())
    dp.include_router(router)

    replayed = await replay_today_hub_reports()
    if replayed:
        logger.info("Hub replay: %s ta hisobot yuborildi", replayed)

    if os.getenv("REPLAY_GROUP_TODAY", "").strip() == "1":
        n = await replay_today_work_reports_to_groups(bot)
        logger.info("REPLAY_GROUP_TODAY: %s ta hisobot guruhga yuborildi", n)

    asyncio.create_task(scheduler_loop(bot))

    logger.info("Navbatchi bot ishga tushmoqda...")
    try:
        await dp.start_polling(
            bot,
            drop_pending_updates=True,
            allowed_updates=[
                "message",
                "callback_query",
                "my_chat_member",
                "chat_member",
            ],
        )
    except Exception as e:
        logger.exception("❌ Polling xatosi: %s", e)
        sys.exit(1)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot to'xtatildi.")
    except Exception as e:
        logger.exception("❌ Kritik xato: %s", e)
        sys.exit(1)
