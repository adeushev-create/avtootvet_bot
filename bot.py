import asyncio
import logging

import uvicorn
from aiogram import Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    BusinessConnection,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)

import db
from api import app as api_app
from config import settings
from humanizer import humanize
from llm_provider import extract_task, generate_reply
from prompts import build_system_prompt
from scheduler import reminder_loop
from style_profile import load_style_description, load_style_examples
from telegram_bot import bot

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("secretary-bot")

dp = Dispatcher()


@dp.message(Command("myid"))
async def show_my_id(message: Message) -> None:
    if message.from_user is None:
        return
    await message.answer(
        f"Твой Telegram user_id: {message.from_user.id}\n\n"
        f"Впиши его в переменную OWNER_USER_ID на Railway."
    )


@dp.message(Command("crm"))
async def open_crm(message: Message) -> None:
    if message.from_user is None or message.from_user.id != settings.owner_user_id:
        return
    if not settings.webapp_url:
        await message.answer(
            "WEBAPP_URL не задан в .env — сначала задеплой мини-апп / подними туннель, "
            "и укажи публичный HTTPS-адрес."
        )
        return
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Открыть CRM", web_app=WebAppInfo(url=settings.webapp_url))]]
    )
    await message.answer("Твоя мини-CRM:", reply_markup=keyboard)


@dp.message()
async def capture_note(message: Message) -> None:
    """Перехватывает обычные сообщения владельца, написанные боту напрямую.
    Если сообщение начинается с триггер-слова (по умолчанию "запомни") — сохраняет как задачу."""
    if message.from_user is None or message.from_user.id != settings.owner_user_id:
        return
    text = message.text or ""
    trigger = settings.trigger_word.lower()
    if not text.lower().startswith(trigger):
        return

    note_text = text[len(trigger):].strip(" :,-—")
    if not note_text:
        await message.answer(f'После слова "{settings.trigger_word}" напиши, что запомнить.')
        return

    extracted = extract_task(note_text)
    title = extracted["title"] if extracted else note_text
    due_at = extracted["due_at"] if extracted else None

    db.create_task(title, due_at, chat_id=None, source="capture")

    confirmation = f"Записал: {title}"
    if due_at:
        confirmation += f"\nНапомню: {due_at}"
    await message.answer(confirmation)


@dp.business_connection()
async def on_business_connection(connection: BusinessConnection) -> None:
    logger.info("Business connection %s: enabled=%s", connection.id, connection.is_enabled)


def _contains_capture_phrase(text: str) -> bool:
    lowered = text.lower()
    return any(phrase in lowered for phrase in settings.capture_phrases)


async def _capture_task_from_chat(chat_id: int, text: str) -> None:
    extracted = extract_task(text)
    title = extracted["title"] if extracted else text.strip()
    due_at = extracted["due_at"] if extracted else None
    db.create_task(title, due_at, chat_id=chat_id, source="capture")

    if settings.owner_user_id:
        when = f"\nСрок: {due_at}" if due_at else ""
        try:
            await bot.send_message(
                settings.owner_user_id,
                f"📌 Записал в задачи (контакт {chat_id}): {title}{when}",
            )
        except Exception:
            logger.exception("Не удалось отправить подтверждение о захваченной задаче")


@dp.business_message()
async def on_business_message(message: Message) -> None:
    chat_id = message.chat.id
    text = message.text or message.caption or ""

    # chat в business-сообщениях всегда представляет собеседника, а не тебя —
    # поэтому имя/юзернейм контакта берём из chat, а не из from_user (там может быть и твой аккаунт)
    db.upsert_contact(
        chat_id=chat_id,
        username=message.chat.username,
        first_name=message.chat.first_name,
        last_name=message.chat.last_name,
    )

    is_owner_message = message.from_user is not None and message.from_user.id == settings.owner_user_id

    if is_owner_message:
        # это ты сам вручную ответил человеку в обычном Telegram — секретарский режим
        # присылает боту и такие сообщения тоже
        if text:
            db.add_message(chat_id, "assistant", text, mode="manual")
            if _contains_capture_phrase(text):
                await _capture_task_from_chat(chat_id, text)
        return

    if chat_id in settings.excluded_chat_ids:
        logger.info("Чат %s в списке исключений — бот не отвечает", chat_id)
        if text:
            db.add_message(chat_id, "user", text, mode="excluded")
        return

    if not text:
        return

    history = db.get_history(chat_id, limit=10)
    style_description = load_style_description()
    examples = load_style_examples()
    system_prompt = build_system_prompt(settings.your_name, style_description, examples)

    try:
        reply_text = generate_reply(system_prompt, history, text)
    except Exception:
        logger.exception("Ошибка при обращении к LLM (%s)", settings.llm_provider)
        return

    reply_text = humanize(
        reply_text,
        typo_probability=settings.typo_probability,
        casual_probability=settings.casual_probability,
    )

    db.add_message(chat_id, "user", text)

    if settings.auto_extract_tasks:
        try:
            extracted = extract_task(text)
            if extracted:
                db.create_task(extracted["title"], extracted["due_at"], chat_id=chat_id, source="auto")
        except Exception:
            logger.exception("Ошибка при автоопределении задачи")

    if settings.mode == "draft" and settings.owner_user_id:
        draft_id = db.add_message(
            chat_id, "assistant", reply_text, mode="draft", business_connection_id=message.business_connection_id
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Отправить", callback_data=f"send:{draft_id}")]]
        )
        await bot.send_message(
            chat_id=settings.owner_user_id,
            text=f"Черновик ответа для чата {chat_id}:\n\n{reply_text}",
            reply_markup=keyboard,
        )
    else:
        db.add_message(chat_id, "assistant", reply_text, mode="auto")
        await bot.send_message(
            chat_id=chat_id,
            text=reply_text,
            business_connection_id=message.business_connection_id,
        )


@dp.callback_query(F.data.startswith("send:"))
async def on_send_draft(callback: CallbackQuery) -> None:
    if callback.from_user.id != settings.owner_user_id:
        await callback.answer("Недоступно", show_alert=True)
        return

    try:
        draft_id = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("Что-то не так с этой кнопкой", show_alert=True)
        return

    draft = db.get_message(draft_id)
    if not draft or draft["mode"] != "draft":
        await callback.answer("Этот черновик уже неактуален", show_alert=True)
        return

    try:
        await bot.send_message(
            chat_id=draft["chat_id"],
            text=draft["content"],
            business_connection_id=draft["business_connection_id"],
        )
    except Exception:
        logger.exception("Не удалось отправить черновик по кнопке")
        await callback.answer("Не получилось отправить — глянь логи", show_alert=True)
        return

    db.update_message_mode(draft_id, "auto")
    if callback.message:
        try:
            await callback.message.edit_text(f"{draft['content']}\n\n✅ Отправлено")
        except Exception:
            pass
    await callback.answer("Отправлено")


async def run_api() -> None:
    config = uvicorn.Config(api_app, host="0.0.0.0", port=settings.port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


async def main() -> None:
    db.init_db()
    await bot.delete_webhook(drop_pending_updates=True)
    await asyncio.gather(
        dp.start_polling(
            bot,
            allowed_updates=[
                "message",
                "business_connection",
                "business_message",
                "edited_business_message",
            ],
        ),
        run_api(),
        reminder_loop(bot, settings.owner_user_id),
    )


if __name__ == "__main__":
    asyncio.run(main())
