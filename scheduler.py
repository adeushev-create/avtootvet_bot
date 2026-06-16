import asyncio
import logging

import db

logger = logging.getLogger("scheduler")


async def reminder_loop(bot, owner_user_id: int, interval_seconds: int = 60) -> None:
    if not owner_user_id:
        logger.warning("OWNER_USER_ID не задан — напоминания отправляться не будут")
        return

    while True:
        try:
            for task in db.get_due_tasks():
                text = f"⏰ Напоминание: {task['title']}"
                if task.get("chat_id"):
                    text += f"\n(связано с чатом {task['chat_id']})"
                await bot.send_message(chat_id=owner_user_id, text=text)
                db.mark_task_reminded(task["id"])
        except Exception:
            logger.exception("Ошибка в reminder_loop")
        await asyncio.sleep(interval_seconds)
