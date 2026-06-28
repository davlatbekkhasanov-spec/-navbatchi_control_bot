"""Bot klaviaturalari."""

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

# ─── Admin tugmalari ─────────────────────────────────────────────────────────
BTN_TODAY = "📋 Bugungi navbatchilar"
BTN_SEND_GROUP = "📤 Guruhga yuborish"
BTN_REPORT = "📊 Bugungi hisobot"
BTN_RATING = "🏆 Oylik reyting"
BTN_EMPLOYEES = "👥 Xodimlar"
BTN_GROUPS = "🗂️ Navbatchi guruhlari"
BTN_HELP = "ℹ️ Yordam"


def main_menu_keyboard(is_on_duty: bool = False, has_started: bool = False) -> ReplyKeyboardMarkup:
    """Asosiy menyu."""
    builder = ReplyKeyboardBuilder()
    if is_on_duty and not has_started:
        builder.row(KeyboardButton(text="▶️ Ishni boshlash"))
    elif is_on_duty and has_started:
        builder.row(KeyboardButton(text="📸 OLDIN rasm yuborish"))
        builder.row(KeyboardButton(text="✅ Tozalash tugadi / KEYIN rasmlar"))
        builder.row(KeyboardButton(text="📤 Hisobotni yuborish"))
    builder.row(KeyboardButton(text=BTN_HELP))
    return builder.as_markup(resize_keyboard=True)


def admin_menu_keyboard() -> ReplyKeyboardMarkup:
    """Admin asosiy menyu — chiroyli tugmalar."""
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text=BTN_TODAY),
        KeyboardButton(text=BTN_SEND_GROUP),
    )
    builder.row(
        KeyboardButton(text=BTN_REPORT),
        KeyboardButton(text=BTN_RATING),
    )
    builder.row(
        KeyboardButton(text=BTN_EMPLOYEES),
        KeyboardButton(text=BTN_GROUPS),
    )
    builder.row(KeyboardButton(text=BTN_HELP))
    return builder.as_markup(resize_keyboard=True, input_field_placeholder="Menyudan tanlang...")


def employee_select_keyboard(employees: list[dict]) -> InlineKeyboardMarkup:
    """Xodimni tanlash (birinchi marta bog'lash)."""
    builder = InlineKeyboardBuilder()
    for emp in employees:
        builder.row(
            InlineKeyboardButton(
                text=emp["full_name"],
                callback_data=f"link:{emp['id']}",
            )
        )
    return builder.as_markup()


def admin_review_keyboard(report_id: int) -> InlineKeyboardMarkup:
    """Admin tasdiqlash tugmalari."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="✅ Qabul qilindi",
            callback_data=f"review:accept:{report_id}",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="❌ Qayta tozalash",
            callback_data=f"review:redo:{report_id}",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="⚠️ Izoh bilan qaytarish",
            callback_data=f"review:comment:{report_id}",
        )
    )
    return builder.as_markup()


def cancel_comment_keyboard() -> InlineKeyboardMarkup:
    """Izoh kiritishni bekor qilish."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="❌ Bekor qilish", callback_data="review:cancel_comment")
    )
    return builder.as_markup()
