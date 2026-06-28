"""Bot klaviaturalari — ichki (nested) menyu tizimi."""

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

# ─── Doimiy reply tugma ──────────────────────────────────────────────────────
BTN_HOME = "🏠 Bosh menyu"

# Admin reply bo'limlari (pastda doim ko'rinadi)
BTN_ADMIN_DUTY = "📋 Navbatchilik"
BTN_ADMIN_REPORTS = "📊 Hisobotlar"
BTN_ADMIN_INFO = "👥 Ma'lumotnoma"
BTN_ADMIN_HELP = "ℹ️ Yordam"
BTN_TODAY_VIEW = "📋 Bugungi navbatchilar"
BTN_TODAY_SEND = "📤 Guruhga yuborish"

# Eski reply tugmalar (ish jarayoni uchun)
BTN_WORK_START = "▶️ Ishni boshlash"
BTN_WORK_BEFORE = "📸 OLDIN rasm yuborish"
BTN_WORK_AFTER = "✅ Tozalash tugadi / KEYIN rasmlar"
BTN_WORK_SUBMIT = "📤 Hisobotni yuborish"

# ─── Inline callback kalitlari ───────────────────────────────────────────────
# Menyu navigatsiya: m:*
MENU_ADMIN_MAIN = "m:adm:main"
MENU_ADMIN_DUTY = "m:adm:duty"
MENU_ADMIN_REPORTS = "m:adm:reports"
MENU_ADMIN_INFO = "m:adm:info"

MENU_EMP_MAIN = "m:emp:main"
MENU_EMP_WORK = "m:emp:work"

# Amallar: a:*
ACT_TODAY_VIEW = "a:today:view"
ACT_TODAY_SEND = "a:today:send"
ACT_REPORT = "a:report"
ACT_RATING = "a:rating"
ACT_EMPLOYEES = "a:employees"
ACT_GROUPS = "a:groups"
ACT_HELP = "a:help"
ACT_STATUS = "a:status"
ACT_WORK_START = "a:work:start"


def admin_reply_keyboard() -> ReplyKeyboardMarkup:
    """Admin — pastda doim ko'rinadigan tugmalar."""
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text=BTN_ADMIN_DUTY),
        KeyboardButton(text=BTN_ADMIN_REPORTS),
    )
    builder.row(
        KeyboardButton(text=BTN_ADMIN_INFO),
        KeyboardButton(text=BTN_ADMIN_HELP),
    )
    builder.row(KeyboardButton(text=BTN_HOME))
    return builder.as_markup(resize_keyboard=True, is_persistent=True)


def admin_duty_reply_keyboard() -> ReplyKeyboardMarkup:
    """Navbatchilik bo'limi tugmalari."""
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text=BTN_TODAY_VIEW),
        KeyboardButton(text=BTN_TODAY_SEND),
    )
    builder.row(KeyboardButton(text=BTN_HOME))
    return builder.as_markup(resize_keyboard=True, is_persistent=True)


def reply_base_keyboard(*extra_rows: list[str]) -> ReplyKeyboardMarkup:
    """Asosiy reply klaviatura + ixtiyoriy qo'shimcha qatorlar."""
    builder = ReplyKeyboardBuilder()
    for row in extra_rows:
        builder.row(*[KeyboardButton(text=t) for t in row])
    builder.row(KeyboardButton(text=BTN_HOME))
    return builder.as_markup(resize_keyboard=True, input_field_placeholder="🏠 Bosh menyu...")


def work_reply_keyboard() -> ReplyKeyboardMarkup:
    """Navbatchilik ish jarayoni reply tugmalari."""
    return reply_base_keyboard(
        [BTN_WORK_BEFORE],
        [BTN_WORK_AFTER],
        [BTN_WORK_SUBMIT],
    )


def employee_select_keyboard(employees: list[dict]) -> InlineKeyboardMarkup:
    """Xodimni tanlash (birinchi marta bog'lash)."""
    builder = InlineKeyboardBuilder()
    for emp in employees:
        builder.row(
            InlineKeyboardButton(
                text=f"👤 {emp['full_name']}",
                callback_data=f"link:{emp['id']}",
            )
        )
    return builder.as_markup()


def _back_row(target: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text="◀️ Orqaga", callback_data=target)


# ─── Admin ichki menyu ───────────────────────────────────────────────────────

def admin_main_inline() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📋 Navbatchilik", callback_data=MENU_ADMIN_DUTY),
        InlineKeyboardButton(text="📊 Hisobotlar", callback_data=MENU_ADMIN_REPORTS),
    )
    builder.row(
        InlineKeyboardButton(text="👥 Ma'lumotnoma", callback_data=MENU_ADMIN_INFO),
        InlineKeyboardButton(text="ℹ️ Yordam", callback_data=ACT_HELP),
    )
    return builder.as_markup()


def admin_duty_inline() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📋 Bugungi navbatchilar", callback_data=ACT_TODAY_VIEW)
    )
    builder.row(
        InlineKeyboardButton(text="📤 Guruhga yuborish", callback_data=ACT_TODAY_SEND)
    )
    builder.row(_back_row(MENU_ADMIN_MAIN))
    return builder.as_markup()


def admin_reports_inline() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📊 Bugungi hisobot", callback_data=ACT_REPORT)
    )
    builder.row(
        InlineKeyboardButton(text="🏆 Oylik reyting", callback_data=ACT_RATING)
    )
    builder.row(_back_row(MENU_ADMIN_MAIN))
    return builder.as_markup()


def admin_info_inline() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="👥 Xodimlar ro'yxati", callback_data=ACT_EMPLOYEES)
    )
    builder.row(
        InlineKeyboardButton(text="🗂️ Navbatchi guruhlari", callback_data=ACT_GROUPS)
    )
    builder.row(_back_row(MENU_ADMIN_MAIN))
    return builder.as_markup()


def admin_result_inline(back_to: str = MENU_ADMIN_MAIN) -> InlineKeyboardMarkup:
    """Natija xabaridan keyin qaytish tugmasi."""
    builder = InlineKeyboardBuilder()
    builder.row(_back_row(back_to))
    return builder.as_markup()


# ─── Xodim ichki menyu ───────────────────────────────────────────────────────

def employee_main_inline(
    *,
    on_duty: bool,
    has_started: bool,
    submitted: bool,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if on_duty and not submitted:
        if not has_started:
            builder.row(
                InlineKeyboardButton(text="▶️ Ishni boshlash", callback_data=ACT_WORK_START)
            )
        else:
            builder.row(
                InlineKeyboardButton(text="🧹 Ish jarayoni", callback_data=MENU_EMP_WORK)
            )
    builder.row(
        InlineKeyboardButton(text="📋 Mening holatim", callback_data=ACT_STATUS)
    )
    builder.row(
        InlineKeyboardButton(text="ℹ️ Yordam", callback_data=ACT_HELP)
    )
    return builder.as_markup()


def employee_work_inline() -> InlineKeyboardMarkup:
    """Ish jarayoni bo'limi — reply tugmalar bilan birga ishlaydi."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📸 OLDIN rasm", callback_data="a:work:hint:before"),
        InlineKeyboardButton(text="📤 KEYIN rasm", callback_data="a:work:hint:after"),
    )
    builder.row(
        InlineKeyboardButton(text="✅ Hisobot yuborish", callback_data="a:work:hint:submit"),
    )
    builder.row(_back_row(MENU_EMP_MAIN))
    return builder.as_markup()


# ─── Admin tasdiqlash ────────────────────────────────────────────────────────

def admin_review_keyboard(report_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Qabul qilindi", callback_data=f"review:accept:{report_id}")
    )
    builder.row(
        InlineKeyboardButton(text="❌ Qayta tozalash", callback_data=f"review:redo:{report_id}")
    )
    builder.row(
        InlineKeyboardButton(
            text="⚠️ Izoh bilan qaytarish", callback_data=f"review:comment:{report_id}"
        )
    )
    return builder.as_markup()


def cancel_comment_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="❌ Bekor qilish", callback_data="review:cancel_comment")
    )
    return builder.as_markup()


# ─── Menyu matnlari ──────────────────────────────────────────────────────────

ADMIN_MAIN_TEXT = (
    "🧹 <b>Navbatchi — Admin panel</b>\n\n"
    "⏰ Har kuni <b>07:30</b> da navbatchilar ro'yxati guruhga avtomatik ketadi.\n\n"
    "👇 Bo'limni tanlang:"
)

ADMIN_DUTY_TEXT = "📋 <b>Navbatchilik bo'limi</b>\n\nKerakli amalni tanlang:"
ADMIN_REPORTS_TEXT = "📊 <b>Hisobotlar bo'limi</b>\n\nKerakli amalni tanlang:"
ADMIN_INFO_TEXT = "👥 <b>Ma'lumotnoma</b>\n\nKerakli ro'yxatni tanlang:"

EMP_MAIN_TEXT = (
    "🧹 <b>Navbatchi</b>\n\n"
    "👇 Bo'limni tanlang:"
)

EMP_WORK_TEXT = (
    "🧹 <b>Ish jarayoni</b>\n\n"
    "Quyidagi reply tugmalardan foydalaning:\n"
    "• 📸 OLDIN rasm yuborish\n"
    "• ✅ Tozalash tugadi / KEYIN rasmlar\n"
    "• 📤 Hisobotni yuborish"
)
