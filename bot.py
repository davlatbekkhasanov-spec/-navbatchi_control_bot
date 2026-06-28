"""
Navbatchi — Omborxona navbatchilik Telegram boti.

Ombor tozaligi bo'yicha navbatchi xodimlarni boshqarish,
foto-hisobot yig'ish, rahbar tasdiqlashi va oylik reyting.
"""

import asyncio
import logging
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


class AdminStates(StatesGroup):
    waiting_comment = State()  # Admin izoh kiritmoqda


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
    """Xodim bugun navbatchilikda bormi?"""
    group, duty_employees = db.get_today_duty_employees()
    if not group:
        return False
    return any(e["id"] == employee["id"] for e in duty_employees)


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
        report and report["status"] in ("submitted", "accepted", "need_redo", "rejected")
    )
    return {
        "employee": employee,
        "on_duty": on_duty,
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
    elif state["on_duty"] and not state["submitted"]:
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
    elif not state["on_duty"]:
        text += "\n\nℹ️ Bugun sizning navbatchilik kuningiz <b>emas</b>."
    await _send_menu(
        message,
        text,
        kb.employee_main_inline(
            on_duty=state["on_duty"],
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

    if not is_employee_on_duty(employee) and not admin_user:
        await message.answer("ℹ️ Bugun sizning navbatchilik kuningiz emas.")
        return

    today = today_str()
    existing = db.get_report(employee["id"], today)
    if existing:
        if existing["status"] in ("submitted", "accepted"):
            await message.answer("✅ Siz bugun allaqachon hisobot yuborgansiz.")
            return
        if existing["status"] in ("need_redo", "rejected") and admin_user:
            db.update_report(existing["id"], status="started", submit_time=None, score=0)
            await state.set_state(ReportStates.waiting_before)
            await state.update_data(report_id=existing["id"])
            markup = kb.employee_duty_reply_keyboard(has_started=True)
            await message.answer(
                "🔄 Qayta ishlash boshlandi. 📸 OLDIN rasmlarni yuboring.",
                parse_mode="HTML",
                reply_markup=markup,
            )
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

    group, _ = db.get_today_duty_employees()
    if not group:
        if admin_user:
            group = db.get_group_by_id(employee["group_id"])
        if not group:
            await message.answer("❌ Bugun navbatchilik guruh topilmadi.")
            return

    report = db.create_report(employee["id"], group["id"], today)
    await state.set_state(ReportStates.waiting_before)
    await state.update_data(report_id=report["id"])

    markup = kb.employee_duty_reply_keyboard(has_started=True)
    await message.answer(
        f"▶️ Ish boshlandi! ⏰ {format_time(report['start_time'])}\n\n"
        "📸 Avval <b>OLDIN</b> rasmlarni yuboring.",
        parse_mode="HTML",
        reply_markup=markup,
    )
    if not admin_user:
        await _send_menu(message, kb.EMP_WORK_TEXT, kb.employee_work_inline())


async def send_admin_review(bot: Bot, report: dict, employee: dict) -> None:
    """Adminlarga hisobotni tasdiqlash uchun yuborish."""
    photos = db.get_report_photos(report["id"])
    group = db.get_all_groups()
    group_name = next((g["name"] for g in group if g["id"] == report["group_id"]), "?")

    text = (
        f"📋 <b>Yangi hisobot</b>\n\n"
        f"👤 Xodim: <b>{employee['full_name']}</b>\n"
        f"👥 Guruh: <b>{group_name}</b>\n"
        f"📅 Sana: <b>{report['date']}</b>\n"
        f"⏰ Boshlash: <b>{format_time(report['start_time'])}</b>\n"
        f"⏰ Tugatish: <b>{format_time(report['submit_time'])}</b>\n"
        f"📷 OLDIN rasmlar: <b>{report['before_count']}</b>\n"
        f"📷 KEYIN rasmlar: <b>{report['after_count']}</b>\n"
        f"⭐ Ball: <b>{report['score']}</b>"
    )

    markup = kb.admin_review_keyboard(report["id"])

    for admin_id in config.ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text, parse_mode="HTML", reply_markup=markup)
            if photos:
                # Media group (max 10 ta)
                for i in range(0, len(photos), 10):
                    chunk = photos[i : i + 10]
                    media = []
                    for j, photo in enumerate(chunk):
                        label = "📷 OLDIN" if photo["photo_type"] == "before" else "📷 KEYIN"
                        cap = f"{label} ({i + j + 1}/{len(photos)})" if j == 0 else None
                        media.append(
                            InputMediaPhoto(media=photo["photo_file_id"], caption=cap)
                            if cap
                            else InputMediaPhoto(media=photo["photo_file_id"])
                        )
                    await bot.send_media_group(admin_id, media=media)
        except Exception as e:
            logger.error("Admin %s ga yuborishda xato: %s", admin_id, e)


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
            if duty_employees:
                await message.answer(
                    "👋 Salom!\n\n"
                    "🧹 <b>Navbatchi</b> botiga xush kelibsiz.\n"
                    "Iltimos, o'zingizni tanlang:",
                    parse_mode="HTML",
                    reply_markup=kb.employee_select_keyboard(duty_employees),
                )
            else:
                await message.answer(
                    "👋 Salom!\n\n"
                    "🧹 Bugun navbatchilik yo'q.\n"
                    "Navbatchilik kuningizda qayta kiring.",
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
        "<b>Ball tizimi:</b>\n"
        f"• Vaqtida: +{config.SCORE_ON_TIME} | OLDIN: +{config.SCORE_BEFORE_PHOTO}\n"
        f"• KEYIN: +{config.SCORE_AFTER_PHOTO} | Qabul: +{config.SCORE_ACCEPTED}\n"
        f"• Qayta tozalash: {config.SCORE_REDO} | Yo'q: {config.SCORE_NO_REPORT}"
    )
    if is_admin_user:
        text += (
            "\n\n<b>Admin bo'limlari:</b>\n"
            "📋 Navbatchilik — ro'yxat va guruhga yuborish\n"
            "📊 Hisobotlar — kunlik va oylik\n"
            "👥 Ma'lumotnoma — xodimlar va guruhlar\n"
            "🧹 Navbatchi ishi — xodimlar kabi sinov qilish"
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
        elif not st["on_duty"]:
            text += "\n\nℹ️ Bugun navbatchilik kuningiz <b>emas</b>."
        await _edit_or_send_menu(
            callback,
            text,
            kb.employee_main_inline(
                on_duty=st["on_duty"],
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
    data = await state.get_data()
    report_id = data.get("report_id")
    if not report_id:
        return
    file_id = message.photo[-1].file_id
    db.add_photo(report_id, file_id, "before")
    employee = db.get_employee_by_telegram_id(message.from_user.id)
    report = db.get_report(employee["id"], today_str())
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
    data = await state.get_data()
    report_id = data.get("report_id")
    if not report_id:
        return
    file_id = message.photo[-1].file_id
    db.add_photo(report_id, file_id, "after")
    employee = db.get_employee_by_telegram_id(message.from_user.id)
    report = db.get_report(employee["id"], today_str())
    await message.answer(
        f"✅ KEYIN rasm qabul qilindi! (jami: {report['after_count']})\n"
        "Yana rasm yuborishingiz yoki 📤 Hisobotni yuborish tugmasini bosishingiz mumkin."
    )


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
    await state.clear()
    admin_user = is_admin(message.from_user.id)
    home_markup = (
        kb.admin_reply_keyboard()
        if admin_user
        else kb.reply_base_keyboard()
    )
    await message.answer(
        f"📤 Hisobot yuborildi!\n\n"
        f"📷 OLDIN: {updated['before_count']} ta\n"
        f"📷 KEYIN: {updated['after_count']} ta\n"
        f"⭐ Ball: {score}\n\n"
        "⏳ Rahbar tasdiqlashini kuting...",
        reply_markup=home_markup,
    )

    # Adminlarga yuborish
    await send_admin_review(bot, updated, employee)


# ─── Admin tasdiqlash ────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("review:accept:"))
async def review_accept(callback: CallbackQuery, bot: Bot) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q!", show_alert=True)
        return
    report_id = int(callback.data.split(":")[2])
    await _process_review(callback, bot, report_id, "accepted", config.SCORE_ACCEPTED)


@router.callback_query(F.data.startswith("review:redo:"))
async def review_redo(callback: CallbackQuery, bot: Bot) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q!", show_alert=True)
        return
    report_id = int(callback.data.split(":")[2])
    await _process_review(callback, bot, report_id, "need_redo", config.SCORE_REDO)


@router.callback_query(F.data.startswith("review:comment:"))
async def review_comment_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q!", show_alert=True)
        return
    report_id = int(callback.data.split(":")[2])
    report = db.get_report_by_id(report_id)
    if not report:
        await callback.answer("Hisobot topilmadi!", show_alert=True)
        return
    if report["status"] != "submitted":
        await callback.answer("⚠️ Bu hisobot allaqachon ko'rib chiqilgan!", show_alert=True)
        await _disable_review_keyboard(callback)
        return
    await state.set_state(AdminStates.waiting_comment)
    await state.update_data(report_id=report_id)
    await callback.message.answer(
        "⚠️ Izohni yozing:",
        reply_markup=kb.cancel_comment_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "review:cancel_comment")
async def review_cancel_comment(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("❌ Izoh bekor qilindi.")
    await callback.answer()


@router.message(AdminStates.waiting_comment)
async def review_comment_submit(message: Message, state: FSMContext, bot: Bot) -> None:
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    report_id = data.get("report_id")
    if not report_id:
        return
    comment = message.text
    await state.clear()
    await _process_review_message(
        message, bot, report_id, "need_redo", config.SCORE_REDO, comment
    )


async def _disable_review_keyboard(callback: CallbackQuery) -> None:
    """Tasdiqlash tugmalarini o'chirish."""
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass


async def _mark_review_message_done(callback: CallbackQuery, status_label: str) -> None:
    """Hisobot xabariga natija qo'shish va tugmalarni olib tashlash."""
    try:
        text = callback.message.html_text or callback.message.text or ""
        if status_label not in text:
            text += f"\n\n———\n✅ <b>{status_label.upper()}</b>"
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=None)
    except Exception:
        await _disable_review_keyboard(callback)


async def _process_review(
    callback: CallbackQuery, bot: Bot, report_id: int, status: str, score_delta: int
) -> None:
    updated = db.try_finalize_report_review(
        report_id, status=status, score_delta=score_delta
    )
    if not updated:
        await callback.answer("⚠️ Bu hisobot allaqachon ko'rib chiqilgan!", show_alert=True)
        await _disable_review_keyboard(callback)
        return

    employee = db.get_employee_by_id(updated["employee_id"])
    status_label = status_text(status)
    await _mark_review_message_done(callback, status_label)

    if employee and employee.get("telegram_user_id"):
        try:
            await bot.send_message(
                employee["telegram_user_id"],
                f"📋 Hisobotingiz: <b>{status_label}</b>\n⭐ Ball: {updated['score']}",
                parse_mode="HTML",
            )
        except Exception as e:
            logger.error("Xodimga xabar yuborishda xato: %s", e)

    await callback.answer("✅ Bajarildi!")


async def _process_review_message(
    message: Message,
    bot: Bot,
    report_id: int,
    status: str,
    score_delta: int,
    comment: str | None = None,
) -> None:
    """Izoh bilan qaytarish."""
    updated = db.try_finalize_report_review(
        report_id, status=status, score_delta=score_delta, comment=comment
    )
    if not updated:
        await message.answer("⚠️ Bu hisobot allaqachon ko'rib chiqilgan yoki topilmadi.")
        return

    employee = db.get_employee_by_id(updated["employee_id"])
    status_label = status_text(status)

    if employee and employee.get("telegram_user_id"):
        try:
            text = f"📋 Hisobotingiz: <b>{status_label}</b>\n⭐ Ball: {updated['score']}"
            if comment:
                text += f"\n💬 Izoh: {comment}"
            await bot.send_message(employee["telegram_user_id"], text, parse_mode="HTML")
        except Exception as e:
            logger.error("Xodimga xabar yuborishda xato: %s", e)

    await message.answer(
        f"✅ <b>{employee['full_name'] if employee else '?'}</b> — {status_label}\n"
        f"⭐ Yangi ball: {updated['score']}",
        parse_mode="HTML",
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
        lines.append(f"  • {e['full_name']}{rest_note}")

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
            lines.append(f"  • {e['full_name']} — {emoji} {label} — {score} ball")
        else:
            lines.append(f"  • {e['full_name']} — ⏳ hali yubormadi")

    return "\n".join(lines)


async def send_duty_list_to_group(bot: Bot, *, manual: bool = False) -> bool:
    """Navbatchilar ro'yxatini guruhga yuborish."""
    group_chat_id = db.get_group_chat_id()
    if not group_chat_id:
        return False
    today = today_str()
    if not manual and db.was_task_run("morning", today):
        return False
    try:
        text = build_morning_message()
        await bot.send_message(group_chat_id, text, parse_mode="HTML")
        if not manual:
            db.mark_task_run("morning", today)
        logger.info("Navbatchilar ro'yxati yuborildi (manual=%s)", manual)
        return True
    except Exception as e:
        logger.error("Navbatchilar yuborish xatosi: %s", e)
        return False


async def send_morning_report(bot: Bot) -> None:
    """Ertalab 07:30 da avtomatik yuborish."""
    await send_duty_list_to_group(bot, manual=False)


async def send_evening_report(bot: Bot) -> None:
    """Kechqurun guruhga yakuniy hisobot."""
    group_chat_id = db.get_group_chat_id()
    if not group_chat_id:
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

    try:
        text = build_evening_message()
        await bot.send_message(group_chat_id, text, parse_mode="HTML")
        db.mark_task_run("evening", today)
        logger.info("Kechki xabar yuborildi")
    except Exception as e:
        logger.error("Kechki xabar xatosi: %s", e)


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

    bot = Bot(token=config.BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

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
