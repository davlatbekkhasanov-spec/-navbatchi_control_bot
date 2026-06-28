from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

import config
from employee_registry import is_team_member


class TeamAccessMiddleware(BaseMiddleware):
    """Faqat jamoa a'zolari botdan foydalanishi mumkin."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = None
        reply_target = None
        if isinstance(event, Message) and event.from_user:
            user = event.from_user
            reply_target = event
        elif isinstance(event, CallbackQuery) and event.from_user:
            user = event.from_user
            reply_target = event.message

        if not user:
            return await handler(event, data)

        extra = frozenset(config.ADMIN_IDS)
        if is_team_member(user.id, extra_admin_ids=extra):
            return await handler(event, data)

        text = (
            "⛔ <b>Bu bot faqat jamoa a'zolari uchun.</b>\n\n"
            "Agar siz jamoada bo'lsangiz, administratorga Telegram IDingizni yuboring."
        )
        if reply_target:
            await reply_target.answer(text, parse_mode="HTML")
        elif isinstance(event, CallbackQuery):
            await event.answer("⛔ Faqat jamoa a'zolari uchun", show_alert=True)
        return None
