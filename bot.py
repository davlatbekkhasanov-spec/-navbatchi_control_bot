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
            await message.answer(
                "👋 Salom, <b>Admin</b>!\n\n"
                "🧹 <b>Navbatchi</b> botiga xush kelibsiz.\n\n"
                "⏰ Har kuni <b>07:30</b> da navbatchilar ro'yxati "
                "guruhga avtomatik yuboriladi.\n\n"
                "👇 Quyidagi tugmalardan foydalaning:",
                parse_mode="HTML",
                reply_markup=kb.admin_menu_keyboard(),
            )
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
                )
            return

        on_duty = is_employee_on_duty(employee)
        has_started = report is not None and report["status"] in ("started",)

        await message.answer(
            f"👋 Salom, <b>{employee['full_name']}</b>!\n\n"
            + (
                "📋 Bugun siz navbatchilikdasiz. Ishni boshlang!"
                if on_duty
                else "ℹ️ Bugun sizning navbatchilik kuningiz emas."
            ),
            parse_mode="HTML",
            reply_markup=kb.main_menu_keyboard(on_duty, has_started),
        )
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

    on_duty = is_employee_on_duty(employee)
    report = db.get_report(employee_id, today_str())
    has_started = report is not None and report["status"] == "started"

    await callback.message.answer(
        "📋 Menyudan foydalaning:",
        reply_markup=kb.main_menu_keyboard(on_duty, has_started),
    )


@router.message(Command("help"))
@router.message(F.text == kb.BTN_HELP)
async def cmd_help(message: Message) -> None:
    text = (
        "ℹ️ <b>Yordam</b>\n\n"
        "🧹 <b>Navbatchi</b> — ombor tozaligi boti.\n\n"
        "<b>Xodimlar uchun:</b>\n"
        "1️⃣ ▶️ Ishni boshlash\n"
        "2️⃣ 📸 OLDIN rasm yuborish (majburiy)\n"
        "3️⃣ ✅ Tozalash tugadi / KEYIN rasmlar\n"
        "4️⃣ KEYIN rasmlar yuborish (ixtiyoriy)\n"
        "5️⃣ 📤 Hisobotni yuborish\n\n"
        "<b>Ball tizimi:</b>\n"
        f"• Vaqtida yuborish: +{config.SCORE_ON_TIME}\n"
        f"• OLDIN rasm: +{config.SCORE_BEFORE_PHOTO}\n"
        f"• KEYIN rasm: +{config.SCORE_AFTER_PHOTO}\n"
        f"• Qabul qilindi: +{config.SCORE_ACCEPTED}\n"
        f"• Qayta tozalash: {config.SCORE_REDO}\n"
        f"• Hisobot yo'q: {config.SCORE_NO_REPORT}"
    )
    if is_admin(message.from_user.id):
        text += (
            "\n\n<b>Admin tugmalari:</b>\n"
            f"{kb.BTN_TODAY} — ro'yxatni ko'rish\n"
            f"{kb.BTN_SEND_GROUP} — guruhga yuborish\n"
            f"{kb.BTN_REPORT} — bugungi hisobot\n"
            f"{kb.BTN_RATING} — oylik reyting\n"
            f"{kb.BTN_EMPLOYEES} — xodimlar\n"
            f"{kb.BTN_GROUPS} — navbatchi guruhlari\n"
            "/setgroup — guruhni ulash (guruh ichida)"
        )
        await message.answer(text, parse_mode="HTML", reply_markup=kb.admin_menu_keyboard())
        return
    await message.answer(text, parse_mode="HTML")


# ─── Admin buyruqlari ────────────────────────────────────────────────────────

@router.message(Command("today"))
@router.message(F.text == kb.BTN_TODAY)
async def cmd_today(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return
    text = build_morning_message()
    await message.answer(text, parse_mode="HTML", reply_markup=kb.admin_menu_keyboard())


@router.message(F.text == kb.BTN_SEND_GROUP)
async def btn_send_duty_to_group(message: Message, bot: Bot) -> None:
    """Admin istalgan vaqtda navbatchilar ro'yxatini guruhga yuboradi."""
    if not is_admin(message.from_user.id):
        return
    ok = await send_duty_list_to_group(bot, manual=True)
    if ok:
        await message.answer(
            "✅ <b>Navbatchilar ro'yxati guruhga yuborildi!</b>",
            parse_mode="HTML",
            reply_markup=kb.admin_menu_keyboard(),
        )
    else:
        await message.answer(
            "❌ Guruh ulanmagan.\n\n"
            "Botni guruhga qo'shing va guruhda /setgroup yuboring.",
            parse_mode="HTML",
            reply_markup=kb.admin_menu_keyboard(),
        )


@router.message(Command("report_today"))
@router.message(F.text == kb.BTN_REPORT)
async def cmd_report_today(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return
    text = build_evening_message()
    await message.answer(text, parse_mode="HTML", reply_markup=kb.admin_menu_keyboard())


@router.message(Command("rating"))
@router.message(F.text == kb.BTN_RATING)
async def cmd_rating(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return
    now = now_tashkent()
    ratings = db.get_monthly_rating(now.year, now.month)
    lines = [f"🏆 <b>Oylik reyting — {now.strftime('%B %Y')}</b>\n"]
    for i, r in enumerate(ratings, 1):
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
        lines.append(
            f"{medal} <b>{r['full_name']}</b> ({r['group_name']}) — {r['total_score']} ball"
        )
    await message.answer("\n".join(lines), parse_mode="HTML", reply_markup=kb.admin_menu_keyboard())


@router.message(Command("employees"))
@router.message(F.text == kb.BTN_EMPLOYEES)
async def cmd_employees(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return
    employees = db.get_all_employees()
    lines = ["👥 <b>Xodimlar ro'yxati</b>\n"]
    for e in employees:
        rest = config.DAY_NAMES_UZ_CAP[e["rest_day"]]
        lines.append(
            f"• <b>{e['full_name']}</b> — {e['group_name']}, dam: {rest}"
        )
    await message.answer("\n".join(lines), parse_mode="HTML", reply_markup=kb.admin_menu_keyboard())


@router.message(Command("groups"))
@router.message(F.text == kb.BTN_GROUPS)
async def cmd_groups(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return
    groups = db.get_all_groups()
    lines = ["👥 <b>Navbatchilik guruhlari</b>\n"]
    for g in groups:
        days = ", ".join(config.DAY_NAMES_UZ_CAP[d] for d in g["duty_days_list"])
        emps = db.get_employees_by_group(g["id"])
        names = ", ".join(e["full_name"] for e in emps)
        lines.append(f"\n<b>{g['name']}</b>\n📅 Kunlar: {days}\n👤 {names}")
    await message.answer("\n".join(lines), parse_mode="HTML", reply_markup=kb.admin_menu_keyboard())


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

@router.message(F.text == "▶️ Ishni boshlash")
async def start_work(message: Message, state: FSMContext) -> None:
    employee = db.get_employee_by_telegram_id(message.from_user.id)
    if not employee:
        await message.answer("❌ Avval o'zingizni tanlang (/start).")
        return

    if not is_employee_on_duty(employee):
        await message.answer("ℹ️ Bugun sizning navbatchilik kuningiz emas.")
        return

    today = today_str()
    existing = db.get_report(employee["id"], today)
    if existing:
        if existing["status"] in ("submitted", "accepted"):
            await message.answer("✅ Siz bugun allaqachon hisobot yuborgansiz.")
            return
        if existing["status"] == "started":
            await message.answer(
                "🔄 Ish allaqachon boshlangan. Rasmlarni yuboring.",
                reply_markup=kb.main_menu_keyboard(True, True),
            )
            return

    group, _ = db.get_today_duty_employees()
    if not group:
        await message.answer("❌ Bugun navbatchilik guruh topilmadi.")
        return
    report = db.create_report(employee["id"], group["id"], today)
    await state.set_state(ReportStates.waiting_before)
    await state.update_data(report_id=report["id"])

    await message.answer(
        f"▶️ Ish boshlandi! ⏰ {format_time(report['start_time'])}\n\n"
        "📸 Avval <b>OLDIN</b> rasmlarni yuboring.\n"
        "Tugmani bosing va rasmlarni yuboring:",
        parse_mode="HTML",
        reply_markup=kb.main_menu_keyboard(True, True),
    )


# ─── Foto-hisobot jarayoni ───────────────────────────────────────────────────

@router.message(F.text == "📸 OLDIN rasm yuborish")
async def btn_before_photos(message: Message, state: FSMContext) -> None:
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


@router.message(F.text == "✅ Tozalash tugadi / KEYIN rasmlar")
async def btn_after_photos(message: Message, state: FSMContext) -> None:
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


@router.message(F.text == "📤 Hisobotni yuborish")
async def submit_report(message: Message, state: FSMContext, bot: Bot) -> None:
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
    db.update_report(
        report["id"],
        status="submitted",
        submit_time=now,
        score=score,
    )
    await state.clear()

    updated = db.get_report(employee["id"], today_str())
    await message.answer(
        f"📤 Hisobot yuborildi!\n\n"
        f"📷 OLDIN: {updated['before_count']} ta\n"
        f"📷 KEYIN: {updated['after_count']} ta\n"
        f"⭐ Ball: {score}\n\n"
        "⏳ Rahbar tasdiqlashini kuting...",
        reply_markup=kb.main_menu_keyboard(False, False),
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


async def _process_review(
    callback: CallbackQuery, bot: Bot, report_id: int, status: str, score_delta: int
) -> None:
    await _process_review_message(callback.message, bot, report_id, status, score_delta)
    await callback.answer("✅ Bajarildi!")


async def _process_review_message(
    message: Message,
    bot: Bot,
    report_id: int,
    status: str,
    score_delta: int,
    comment: str | None = None,
) -> None:
    """Hisobot holatini yangilash va xodimga xabar."""
    with db.get_connection() as conn:
        row = conn.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
    if not row:
        await message.answer("❌ Hisobot topilmadi.")
        return
    report = dict(row)
    new_score = report["score"] + score_delta
    fields = {"status": status, "score": new_score}
    if comment:
        fields["admin_comment"] = comment
    db.update_report(report_id, **fields)

    employee = db.get_employee_by_id(report["employee_id"])
    status_label = status_text(status)

    # Xodimga xabar
    if employee and employee.get("telegram_user_id"):
        try:
            text = f"📋 Hisobotingiz: <b>{status_label}</b>\n⭐ Ball: {new_score}"
            if comment:
                text += f"\n💬 Izoh: {comment}"
            await bot.send_message(employee["telegram_user_id"], text, parse_mode="HTML")
        except Exception as e:
            logger.error("Xodimga xabar yuborishda xato: %s", e)

    await message.answer(
        f"✅ <b>{employee['full_name'] if employee else '?'}</b> — {status_label}\n"
        f"⭐ Yangi ball: {new_score}",
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
