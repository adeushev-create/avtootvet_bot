import json
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import db
from config import settings
from telegram_auth import validate_init_data

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
    color: str = "#888888"


class ContactTagsIn(BaseModel):
    tag_ids: list[int]


class NotesIn(BaseModel):
    notes: str


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
def api_contacts(_=Depends(require_owner)):
    return db.list_contacts()


@app.get("/api/contacts/{chat_id}/messages")
def api_contact_messages(chat_id: int, _=Depends(require_owner)):
    return db.get_messages(chat_id)


@app.post("/api/contacts/{chat_id}/notes")
def api_set_notes(chat_id: int, body: NotesIn, _=Depends(require_owner)):
    db.update_contact_notes(chat_id, body.notes)
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


@app.get("/api/tasks")
def api_tasks(status: str | None = None, _=Depends(require_owner)):
    return db.list_tasks(status)


@app.post("/api/tasks")
def api_create_task(body: TaskIn, _=Depends(require_owner)):
    task_id = db.create_task(body.title, body.due_at, body.chat_id)
    return {"id": task_id}


@app.patch("/api/tasks/{task_id}")
def api_update_task(task_id: int, body: TaskStatusIn, _=Depends(require_owner)):
    db.update_task_status(task_id, body.status)
    return {"ok": True}


# Статика мини-аппа — подключаем последней, чтобы не перекрыть /api/* маршруты
webapp_dir = Path(__file__).parent / "webapp"
if webapp_dir.exists():
    app.mount("/", StaticFiles(directory=webapp_dir, html=True), name="webapp")
