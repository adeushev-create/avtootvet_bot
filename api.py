import json
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import db
from config import settings
from telegram_auth import validate_init_data
from telegram_bot import bot

app = FastAPI(title="Secretary CRM API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def require_owner(x_telegram_init_data: str = Header(default="")) -> dict:
    data = validate_init_data(x_telegram_init_data, settings.bot_token)
    if not data:
        raise HTTPException(status_code=401, detail="Невалидные данные Telegram WebApp")
    user = json.loads(data.get("user", "{}"))
    if not settings.owner_user_id or user.get("id") != settings.owner_user_id:
        raise HTTPException(status_code=403, detail="Доступ только для владельца")
    return user


class TagIn(BaseModel):
    name: str
    color: str = "#FF6B5E"


class TagUpdateIn(BaseModel):
    name: str | None = None
    color: str | None = None


class ContactTagsIn(BaseModel):
    tag_ids: list[int]


class NotesIn(BaseModel):
    notes: str


class ArchiveIn(BaseModel):
    archived: bool


class TaskIn(BaseModel):
    title: str
    due_at: str | None = None
    chat_id: int | None = None


class TaskStatusIn(BaseModel):
    status: str


@app.get("/api/stats")
def api_stats(_=Depends(require_owner)):
    return db.get_stats()


@app.get("/api/contacts")
def api_contacts(include_archived: bool = False, _=Depends(require_owner)):
    return db.list_contacts(include_archived=include_archived)


@app.get("/api/contacts/{chat_id}")
def api_contact(chat_id: int, _=Depends(require_owner)):
    contact = db.get_contact(chat_id)
    if not contact:
        raise HTTPException(status_code=404, detail="Контакт не найден")
    return contact


@app.get("/api/contacts/{chat_id}/messages")
def api_contact_messages(chat_id: int, _=Depends(require_owner)):
    return db.get_messages(chat_id)


@app.post("/api/contacts/{chat_id}/notes")
def api_set_notes(chat_id: int, body: NotesIn, _=Depends(require_owner)):
    db.update_contact_notes(chat_id, body.notes)
    return {"ok": True}


@app.post("/api/contacts/{chat_id}/archive")
def api_archive_contact(chat_id: int, body: ArchiveIn, _=Depends(require_owner)):
    db.set_contact_archived(chat_id, body.archived)
    return {"ok": True}


@app.post("/api/contacts/{chat_id}/tags")
def api_set_contact_tags(chat_id: int, body: ContactTagsIn, _=Depends(require_owner)):
    db.set_contact_tags(chat_id, body.tag_ids)
    return {"ok": True}


@app.get("/api/tags")
def api_tags(_=Depends(require_owner)):
    return db.list_tags()


@app.post("/api/tags")
def api_create_tag(body: TagIn, _=Depends(require_owner)):
    tag_id = db.create_tag(body.name, body.color)
    return {"id": tag_id}


@app.patch("/api/tags/{tag_id}")
def api_update_tag(tag_id: int, body: TagUpdateIn, _=Depends(require_owner)):
    db.update_tag(tag_id, body.name, body.color)
    return {"ok": True}


@app.delete("/api/tags/{tag_id}")
def api_delete_tag(tag_id: int, _=Depends(require_owner)):
    db.delete_tag(tag_id)
    return {"ok": True}


@app.get("/api/tasks")
def api_tasks(status: str | None = None, _=Depends(require_owner)):
    return db.list_tasks(status)


@app.post("/api/tasks")
def api_create_task(body: TaskIn, _=Depends(require_owner)):
    task_id = db.create_task(body.title, body.due_at, body.chat_id, source="manual")
    return {"id": task_id}


@app.patch("/api/tasks/{task_id}")
def api_update_task(task_id: int, body: TaskStatusIn, _=Depends(require_owner)):
    db.update_task_status(task_id, body.status)
    return {"ok": True}


@app.delete("/api/tasks/{task_id}")
def api_delete_task(task_id: int, _=Depends(require_owner)):
    db.delete_task(task_id)
    return {"ok": True}


@app.post("/api/messages/{message_id}/send")
async def api_send_draft(message_id: int, _=Depends(require_owner)):
    draft = db.get_message(message_id)
    if not draft or draft["mode"] != "draft":
        raise HTTPException(status_code=400, detail="Сообщение не найдено или уже не черновик")
    try:
        await bot.send_message(
            chat_id=draft["chat_id"],
            text=draft["content"],
            business_connection_id=draft["business_connection_id"],
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Не удалось отправить: {e}")
    db.update_message_mode(message_id, "auto")
    return {"ok": True}


@app.post("/api/contacts/{chat_id}/refresh-summary")
async def api_refresh_summary(chat_id: int, _=Depends(require_owner)):
    """Генерирует/обновляет AI-профиль контакта по текущей переписке."""
    from llm_provider import generate_contact_summary, analyze_tone
    messages = db.get_messages_for_analysis(chat_id)
    summary = generate_contact_summary(messages)
    if summary:
        db.update_contact_summary(chat_id, summary)
    tone = analyze_tone(messages)
    db.update_contact_tone(chat_id, tone)
    return {"summary": summary, "tone": tone}


@app.get("/api/contacts/{chat_id}/summary")
def api_get_summary(chat_id: int, _=Depends(require_owner)):
    contact = db.get_contact(chat_id)
    if not contact:
        raise HTTPException(status_code=404, detail="Контакт не найден")
    return {"summary": contact.get("ai_summary"), "tone": contact.get("tone")}


# Статика мини-аппа — подключаем последней, чтобы не перекрыть /api/* маршруты
webapp_dir = Path(__file__).parent / "webapp"
if webapp_dir.exists():
    app.mount("/", StaticFiles(directory=webapp_dir, html=True), name="webapp")
