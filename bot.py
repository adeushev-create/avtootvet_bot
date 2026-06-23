import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone

import uvicorn
from aiogram import Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    Audio,
    BusinessConnection,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MenuButtonWebApp,
    Message,
    Voice,
    WebAppInfo,
)

import db
from api import app as api_app
from config import settings
from humanizer import humanize
from llm_provider import extract_task, generate_reply, generate_reply_pair, transcribe_voice, analyze_tone, generate_contact_summary
from prompts import build_system_prompt
from scheduler import reminder_loop
from style_profile import load_style_description, load_style_examples
from telegram_bot import bot

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("secretary-bot")

dp = Dispatcher()

# --- Busy mode ---
# Хранится в памяти + резервно в db (на случай рестарта бот всё равно потеряет состояние,
# но при следующем incoming-сообщении проверит актуальность)
_busy_until: datetime | None = None
_busy_reason: str = ""


def _is_busy() -> bool:
    global _busy_until
    if _busy_until is None:
        return False
    if datetime.now(timezone.utc) > _busy_until:
        _busy_until = None
        _busy_reason = ""
        return False
    return True


def _parse_busy_duration(text: str) -> timedelta | None:
    """Разбирает строку типа '2h', '30m', '1d', '90m'."""
    m = re.match(r"(\d+)\s*([hHчmMмdDд])", text.strip())
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2).lower()
    if unit in ("h", "ч"):
        return timedelta(hours=n)
    if unit in ("m", "м"):
        return timedelta(minutes=n)
    if unit in ("d", "д"):
        return timedelta(days=n)
    return None


@dp.message(Command("myid"))
async def show_my_id(message: Message) -> None:
    if message.from_user is None:
        return
    await message.answer(
        f"Твой Telegram user_id: {message.from_user.id}\n\n"
        f"Впиши его в переменную OWNER_USER_ID на Railway."
    )


@dp.message(Command("abon"))
async def cmd_abon(message: Message) -> None:
    if message.from_user is None or message.from_user.id != settings.owner_user_id:
        return
    settings.ab_test_mode = True
    await message.answer(
        "🅰🅱 A/B тест включён.\n\n"
        "Теперь на каждое входящее сообщение буду присылать два варианта ответа с кнопками «Отправить А» и «Отправить Б». "
        "Тот, что выберешь — уйдёт собеседнику и запомнится как образец для следующих ответов.\n\n"
        "Выключить: /aboff"
    )


@dp.message(Command("aboff"))
async def cmd_aboff(message: Message) -> None:
    if message.from_user is None or message.from_user.id != settings.owner_user_id:
        return
    settings.ab_test_mode = False
    await message.answer("A/B тест выключен, вернулся в обычный режим с одним черновиком.")


@dp.message(Command("busy"))
async def cmd_busy(message: Message) -> None:
    global _busy_until, _busy_reason
    if message.from_user is None or message.from_user.id != settings.owner_user_id:
        return
    text = (message.text or "").replace("/busy", "").strip()

    # парсим время и причину: "/busy 2h на встрече"
    duration = None
    reason = ""
    parts = text.split(None, 1) if text else []
    if parts:
        duration = _parse_busy_duration(parts[0])
        reason = parts[1] if len(parts) > 1 else parts[0] if not duration else ""
    if not duration:
        duration = timedelta(hours=1)

    _busy_until = datetime.now(timezone.utc) + duration
    _busy_reason = reason.strip()

    until_str = _busy_until.strftime("%H:%M")
    reason_str = f" ({_busy_reason})" if _busy_reason else ""
    await message.answer(
        f"🔴 Режим «занят» до {until_str}{reason_str}\n"
        f"Входящим буду отвечать автоматически с пометкой что занят.\n"
        f"Напиши /free чтобы выключить раньше."
    )


@dp.message(Command("free"))
async def cmd_free(message: Message) -> None:
    global _busy_until, _busy_reason
    if message.from_user is None or message.from_user.id != settings.owner_user_id:
        return
    _busy_until = None
    _busy_reason = ""
    await message.answer("🟢 Режим «занят» выключен, вернулся в обычный режим.")


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


_SWEAR_WORDS = ("бля", "хуй", "хуе", "пизд", "ебат", "ебал", "ебан", "сука", "нахуй", "ахуе", "охуен", "мудак", "пидор")
_CJK_PATTERN = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]")


def _contains_swearing(text: str) -> bool:
    lowered = text.lower()
    return any(w in lowered for w in _SWEAR_WORDS)


def _strip_unexpected_scripts(text: str) -> str:
    """Иногда LLM выдаёт случайные иероглифы (известный глитч). Подчищаем на всякий случай,
    даже если в промпте уже есть инструкция писать только кириллицей."""
    cleaned = _CJK_PATTERN.sub("", text)
    return re.sub(r"\s{2,}", " ", cleaned).strip()


def _apply_swearing_rule(system_prompt: str, incoming_text: str) -> str:
    """Мат — только взаимно: если собеседник сам матерился в своём сообщении, можно ответить
    в тон (если профиль это вообще допускает). Если не матерился — в этом ответе без мата,
    даже если обычно с этим человеком можно."""
    if _contains_swearing(incoming_text):
        addendum = "\n\nСобеседник в этом сообщении выругался — можешь ответить в тон, как обычно с этим человеком, если профиль это допускает."
    else:
        addendum = "\n\nСобеседник в этом сообщении не матерился. В ЭТОМ ответе обойдись без мата, даже если обычно с этим человеком ты можешь материться. Мат — только в ответ на мат."
    return system_prompt + addendum


def _resolve_style_profile(contact: dict | None) -> str:
    """Смотрит ВСЕ теги контакта в CRM, сопоставляет с картой TAG_PROFILE_MAP.
    Если совпало несколько разных профилей сразу — побеждает тот, что выше в PROFILE_PRIORITY."""
    if not contact:
        return "default"

    matched_profiles = set()
    for tag in contact.get("tags", []):
        tag_name = tag["name"].strip().lower()
        if tag_name in settings.tag_profile_map:
            matched_profiles.add(settings.tag_profile_map[tag_name])

    if not matched_profiles:
        return "default"

    for profile in settings.profile_priority:
        if profile in matched_profiles:
            return profile

    return next(iter(matched_profiles))


def _build_contact_context(chat_id: int, contact: dict | None) -> str:
    """Персональный контекст для конкретного собеседника.
    НЕ дублирует историю чата — та уже передаётся отдельно через messages.
    Здесь: устойчивые паттерны твоего письма именно этому человеку + правило про имя."""
    past_replies = db.get_assistant_messages(chat_id, limit=30)
    if not past_replies:
        return ""

    first_name = ((contact or {}).get("first_name") or "").strip()
    name_used_before = bool(first_name) and any(first_name.lower() in r.lower() for r in past_replies)

    # берём только последние 15 для примеров — не переполняем промпт
    sample = past_replies[-15:]
    examples_block = "\n".join(f"- {r}" for r in sample)

    name_note = (
        f'Ты уже обращался к этому человеку по имени "{first_name}" раньше — можешь и сейчас.'
        if name_used_before
        else "Ты раньше не обращался к этому человеку по имени — не начинай без явного повода."
    )

    return (
        f"\n\n---\nКАК ТЫ ОБЫЧНО ПИШЕШЬ ИМЕННО ЭТОМУ ЧЕЛОВЕКУ "
        f"(твои реальные прошлые реплики ему, {len(past_replies)} всего, показаны последние {len(sample)}):\n"
        f"{examples_block}\n\n"
        f"Используй эти примеры как ориентир по тону, длине, лексике для ЭТОГО конкретного человека — "
        f"они важнее общего профиля стиля, потому что отражают именно эти отношения.\n"
        f"{name_note}"
    )


def _contains_capture_phrase(text: str) -> bool:
    lowered = text.lower()
    return any(phrase in lowered for phrase in settings.capture_phrases)


def _contact_label(chat) -> str:
    """Человеко-читаемая подпись контакта для уведомлений: имя (@юзернейм)."""
    name = " ".join(filter(None, [chat.first_name, chat.last_name])).strip()
    handle = f"@{chat.username}" if chat.username else ""
    if name and handle:
        return f"{name} ({handle})"
    return name or handle or f"чат {chat.id}"


def _truncate(text: str, limit: int = 300) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


async def _capture_task_from_chat(chat_id: int, text: str, contact_label: str) -> None:
    extracted = extract_task(text)
    title = extracted["title"] if extracted else text.strip()
    due_at = extracted["due_at"] if extracted else None
    db.create_task(title, due_at, chat_id=chat_id, source="capture")

    if settings.owner_user_id:
        when = f"\nСрок: {due_at}" if due_at else ""
        try:
            await bot.send_message(
                settings.owner_user_id,
                f"📌 Записал в задачи · {contact_label}\n{title}{when}",
            )
        except Exception:
            logger.exception("Не удалось отправить подтверждение о захваченной задаче")


_pending_texts: dict[int, list[str]] = {}
_pending_tasks: dict[int, asyncio.Task] = {}


async def _update_tone_and_summary_bg(chat_id: int) -> None:
    """Фоновая задача: обновляет тон и AI-профиль контакта после накопления сообщений."""
    try:
        messages = db.get_messages_for_analysis(chat_id)
        msg_count = len(messages)
        if msg_count < 4:
            return
        tone = analyze_tone(messages)
        db.update_contact_tone(chat_id, tone)
        if msg_count >= 6 and msg_count % settings.tone_update_interval == 0:
            summary = generate_contact_summary(messages)
            if summary:
                db.update_contact_summary(chat_id, summary)
    except Exception:
        logger.exception("Ошибка при обновлении тона/профиля для чата %s", chat_id)


async def _flush_and_reply(chat_id: int, business_connection_id: str | None, contact_label: str) -> None:
    """Срабатывает после паузы без новых сообщений от этого собеседника."""
    try:
        await asyncio.sleep(settings.message_debounce_seconds)
    except asyncio.CancelledError:
        return

    texts = _pending_texts.pop(chat_id, [])
    _pending_tasks.pop(chat_id, None)
    if not texts:
        return

    combined_text = "\n".join(texts)

    # --- busy mode ---
    if _is_busy():
        reason_part = f" — {_busy_reason}" if _busy_reason else ""
        busy_reply = f"занят сейчас{reason_part}, отвечу позже"
        if _busy_until:
            busy_time = _busy_until.strftime("%H:%M")
            busy_reply += f" (освобожусь около {busy_time})"
        for t in texts:
            db.add_message(chat_id, "user", t)
        if settings.owner_user_id:
            await bot.send_message(
                settings.owner_user_id,
                f"💬 {contact_label}\nСпросил(а): {_truncate(combined_text)}\n\n"
                f"🔴 Ты в режиме «занят». Автоответ: «{busy_reply}»",
            )
        await bot.send_message(
            chat_id=chat_id, text=busy_reply, business_connection_id=business_connection_id
        )
        db.add_message(chat_id, "assistant", busy_reply, mode="auto")
        return

    history = db.get_history(chat_id, limit=30)
    contact = db.get_contact(chat_id)
    profile = _resolve_style_profile(contact)
    style_description = load_style_description(profile)
    examples = load_style_examples(profile)

    # подмешиваем одобренные варианты из A/B тестов в промпт
    approved = db.get_recent_style_feedback(limit=10)
    if approved:
        examples = examples + approved

    system_prompt = build_system_prompt(settings.your_name, style_description, examples)
    system_prompt += _build_contact_context(chat_id, contact)
    system_prompt = _apply_swearing_rule(system_prompt, combined_text)

    # A/B тест
    if settings.ab_test_mode:
        try:
            variant_a, variant_b = generate_reply_pair(system_prompt, history, combined_text)
            variant_a = _strip_unexpected_scripts(humanize(variant_a, settings.typo_probability, settings.casual_probability))
            variant_b = _strip_unexpected_scripts(humanize(variant_b, settings.typo_probability, settings.casual_probability))
            for t in texts:
                db.add_message(chat_id, "user", t)
            ab_id = db.create_ab_test(chat_id, variant_a, variant_b, business_connection_id)
            if settings.owner_user_id:
                keyboard = InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="Отправить А", callback_data=f"ab:{ab_id}:a"),
                    InlineKeyboardButton(text="Отправить Б", callback_data=f"ab:{ab_id}:b"),
                ]])
                contact_tone = (contact or {}).get("tone", "")
                tone_str = f" {contact_tone}" if contact_tone else ""
                await bot.send_message(
                    chat_id=settings.owner_user_id,
                    text=(
                        f"💬 {contact_label}{tone_str}\n"
                        f"Спросил(а): {_truncate(combined_text)}\n\n"
                        f"🅰 Вариант А:\n{variant_a}\n\n"
                        f"🅱 Вариант Б:\n{variant_b}"
                    ),
                    reply_markup=keyboard,
                )
            asyncio.create_task(_update_tone_and_summary_bg(chat_id))
            return
        except Exception:
            logger.exception("Ошибка при генерации A/B вариантов, откат к обычному режиму")

    try:
        reply_text = generate_reply(system_prompt, history, combined_text)
    except Exception:
        logger.exception("Ошибка при обращении к LLM (%s)", settings.llm_provider)
        reply_text = None

    for t in texts:
        db.add_message(chat_id, "user", t)

    if reply_text is None:
        return

    reply_text = _strip_unexpected_scripts(reply_text)
    reply_text = humanize(reply_text, settings.typo_probability, settings.casual_probability)

    if settings.auto_extract_tasks:
        try:
            extracted = extract_task(combined_text)
            if extracted:
                db.create_task(extracted["title"], extracted["due_at"], chat_id=chat_id, source="auto")
        except Exception:
            logger.exception("Ошибка при автоопределении задачи")

    contact_tone = (contact or {}).get("tone", "")
    tone_str = f" {contact_tone}" if contact_tone else ""

    if settings.mode == "draft" and settings.owner_user_id:
        draft_id = db.add_message(
            chat_id, "assistant", reply_text, mode="draft", business_connection_id=business_connection_id
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Отправить", callback_data=f"send:{draft_id}")]]
        )
        await bot.send_message(
            chat_id=settings.owner_user_id,
            text=(
                f"💬 {contact_label}{tone_str}\n"
                f"Спросил(а): {_truncate(combined_text)}\n\n"
                f"Черновик ответа:\n{reply_text}"
            ),
            reply_markup=keyboard,
        )
    else:
        db.add_message(chat_id, "assistant", reply_text, mode="auto")
        await bot.send_message(
            chat_id=chat_id, text=reply_text, business_connection_id=business_connection_id
        )

    asyncio.create_task(_update_tone_and_summary_bg(chat_id))


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
    contact_label = _contact_label(message.chat)

    if is_owner_message:
        # это ты сам вручную ответил человеку в обычном Telegram — секретарский режим
        # присылает боту и такие сообщения тоже
        if text:
            db.add_message(chat_id, "assistant", text, mode="manual")
            if _contains_capture_phrase(text):
                await _capture_task_from_chat(chat_id, text, contact_label)
        return

    if chat_id in settings.excluded_chat_ids:
        logger.info("Чат %s в списке исключений — бот не отвечает", chat_id)
        if text:
            db.add_message(chat_id, "user", text, mode="excluded")
        return

    if not text:
        # пробуем транскрибировать голосовое сообщение
        if message.voice or message.audio:
            voice_obj = message.voice or message.audio
            try:
                tg_file = await bot.get_file(voice_obj.file_id)
                audio_bytes = await bot.download_file(tg_file.file_path)
                transcribed = transcribe_voice(audio_bytes.read() if hasattr(audio_bytes, "read") else bytes(audio_bytes))
                if transcribed:
                    text = f"[голосовое] {transcribed}"
                    logger.info("Транскрибировано голосовое от чата %s: %s", chat_id, text[:80])
                else:
                    logger.info("Не удалось транскрибировать голосовое от чата %s", chat_id)
                    return
            except Exception:
                logger.exception("Ошибка при обработке голосового от чата %s", chat_id)
                return
        else:
            return

    # копим короткую пачку: если за пару секунд прилетит ещё сообщение — ответим на всё сразу
    _pending_texts.setdefault(chat_id, []).append(text)
    existing_task = _pending_tasks.get(chat_id)
    if existing_task and not existing_task.done():
        existing_task.cancel()
    _pending_tasks[chat_id] = asyncio.create_task(
        _flush_and_reply(chat_id, message.business_connection_id, contact_label)
    )


@dp.callback_query(F.data.startswith("ab:"))
async def on_ab_choice(callback: CallbackQuery) -> None:
    if callback.from_user.id != settings.owner_user_id:
        await callback.answer("Недоступно", show_alert=True)
        return
    try:
        _, ab_id_str, choice = callback.data.split(":")
        ab_id = int(ab_id_str)
    except (ValueError, IndexError):
        await callback.answer("Ошибка", show_alert=True)
        return

    test = db.get_ab_test(ab_id)
    if not test:
        await callback.answer("Тест не найден", show_alert=True)
        return
    if test.get("chosen"):
        await callback.answer("Уже выбран вариант", show_alert=True)
        return

    db.resolve_ab_test(ab_id, choice)
    chosen_text = test["variant_a"] if choice == "a" else test["variant_b"]

    try:
        await bot.send_message(
            chat_id=test["chat_id"],
            text=chosen_text,
            business_connection_id=test["business_connection_id"],
        )
    except Exception:
        logger.exception("Не удалось отправить A/B вариант")
        await callback.answer("Не удалось отправить", show_alert=True)
        return

    db.add_message(test["chat_id"], "assistant", chosen_text, mode="auto")
    if callback.message:
        try:
            await callback.message.edit_text(
                f"✅ Отправлен вариант {'А' if choice == 'a' else 'Б'}:\n\n{chosen_text}"
            )
        except Exception:
            pass
    await callback.answer(f"Отправлен вариант {'А' if choice == 'a' else 'Б'}")


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
            logger.warning("Не удалось отредактировать сообщение-черновик после отправки", exc_info=True)
    await callback.answer("Отправлено")


async def run_api() -> None:
    config = uvicorn.Config(api_app, host="0.0.0.0", port=settings.port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


async def setup_menu_button() -> None:
    """Настраивает постоянную кнопку меню в чате с ботом, открывающую CRM —
    надёжнее команды /crm, так как не зависит от того, в каком чате её набрали."""
    if not (settings.webapp_url and settings.owner_user_id):
        logger.warning("WEBAPP_URL или OWNER_USER_ID не заданы — кнопка меню CRM не настроена")
        return
    try:
        await bot.set_chat_menu_button(
            chat_id=settings.owner_user_id,
            menu_button=MenuButtonWebApp(text="CRM", web_app=WebAppInfo(url=settings.webapp_url)),
        )
        logger.info("Кнопка меню CRM настроена для owner_user_id=%s", settings.owner_user_id)
    except Exception:
        logger.exception("Не удалось настроить кнопку меню CRM")


async def main() -> None:
    db.init_db()
    await bot.delete_webhook(drop_pending_updates=True)
    await setup_menu_button()
    await asyncio.gather(
        dp.start_polling(
            bot,
            allowed_updates=[
                "message",
                "callback_query",
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
