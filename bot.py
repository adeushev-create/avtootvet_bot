import asyncio
import logging

import uvicorn
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command
from aiogram.types import (
    BusinessConnection,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)

import db
from api import app as api_app
from config import settings
from humanizer import humanize
from llm_provider import generate_reply
from prompts import build_system_prompt
from scheduler import reminder_loop
from style_profile import load_style_description, load_style_examples

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("secretary-bot")

bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode=None))
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


@dp.business_connection()
async def on_business_connection(connection: BusinessConnection) -> None:
    logger.info("Business connection %s: enabled=%s", connection.id, connection.is_enabled)


@dp.business_message()
async def on_business_message(message: Message) -> None:
    chat_id = message.chat.id
    sender = message.from_user

    db.upsert_contact(
        chat_id=chat_id,
        username=sender.username if sender else None,
        first_name=sender.first_name if sender else None,
        last_name=sender.last_name if sender else None,
    )

    incoming_text = message.text or message.caption or ""

    if chat_id in settings.excluded_chat_ids:
        logger.info("Чат %s в списке исключений — бот не отвечает", chat_id)
        if incoming_text:
            db.add_message(chat_id, "user", incoming_text, mode="excluded")
        return

    if not incoming_text:
        return

    history = db.get_history(chat_id, limit=10)
    style_description = load_style_description()
    examples = load_style_examples()
    system_prompt = build_system_prompt(settings.your_name, style_description, examples)

    try:
        reply_text = generate_reply(system_prompt, history, incoming_text)
    except Exception:
        logger.exception("Ошибка при обращении к LLM (%s)", settings.llm_provider)
        return

    reply_text = humanize(
        reply_text,
        typo_probability=settings.typo_probability,
        casual_probability=settings.casual_probability,
    )

    db.add_message(chat_id, "user", incoming_text)

    if settings.mode == "draft" and settings.owner_user_id:
        db.add_message(chat_id, "assistant", reply_text, mode="draft")
        await bot.send_message(
            chat_id=settings.owner_user_id,
            text=f"Черновик ответа для чата {chat_id}:\n\n{reply_text}",
        )
    else:
        db.add_message(chat_id, "assistant", reply_text, mode="auto")
        await bot.send_message(
            chat_id=chat_id,
            text=reply_text,
            business_connection_id=message.business_connection_id,
        )


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
