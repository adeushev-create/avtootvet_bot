import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from config import settings

DB_PATH = Path(settings.data_dir) / "crm.db"


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS contacts (
                chat_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                notes TEXT DEFAULT ''
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                mode TEXT DEFAULT 'auto',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                color TEXT DEFAULT '#888888'
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS contact_tags (
                chat_id INTEGER NOT NULL,
                tag_id INTEGER NOT NULL,
                PRIMARY KEY (chat_id, tag_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                title TEXT NOT NULL,
                due_at TIMESTAMP,
                status TEXT DEFAULT 'open',
                reminded INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )


# --- контакты ---

def upsert_contact(chat_id: int, username: str | None, first_name: str | None, last_name: str | None) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        existing = conn.execute("SELECT chat_id FROM contacts WHERE chat_id = ?", (chat_id,)).fetchone()
        if existing:
            conn.execute(
                "UPDATE contacts SET username=?, first_name=?, last_name=?, last_seen=? WHERE chat_id=?",
                (username, first_name, last_name, now, chat_id),
            )
        else:
            conn.execute(
                "INSERT INTO contacts (chat_id, username, first_name, last_name, first_seen, last_seen) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (chat_id, username, first_name, last_name, now, now),
            )


def list_contacts() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT c.chat_id, c.username, c.first_name, c.last_name, c.last_seen, c.notes,
                   (SELECT content FROM messages m WHERE m.chat_id = c.chat_id ORDER BY m.id DESC LIMIT 1) AS last_message,
                   (SELECT COUNT(*) FROM messages m WHERE m.chat_id = c.chat_id) AS message_count
            FROM contacts c
            ORDER BY c.last_seen DESC
            """
        ).fetchall()
        result = []
        for r in rows:
            tags = conn.execute(
                """
                SELECT t.id, t.name, t.color FROM tags t
                JOIN contact_tags ct ON ct.tag_id = t.id
                WHERE ct.chat_id = ?
                """,
                (r["chat_id"],),
            ).fetchall()
            result.append({**dict(r), "tags": [dict(t) for t in tags]})
        return result


def update_contact_notes(chat_id: int, notes: str) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE contacts SET notes = ? WHERE chat_id = ?", (notes, chat_id))


# --- теги / сегменты ---

def list_tags() -> list[dict]:
    with get_conn() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM tags").fetchall()]


def create_tag(name: str, color: str = "#888888") -> int:
    with get_conn() as conn:
        conn.execute("INSERT OR IGNORE INTO tags (name, color) VALUES (?, ?)", (name, color))
        row = conn.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()
        return row["id"]


def set_contact_tags(chat_id: int, tag_ids: list[int]) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM contact_tags WHERE chat_id = ?", (chat_id,))
        for tag_id in tag_ids:
            conn.execute("INSERT INTO contact_tags (chat_id, tag_id) VALUES (?, ?)", (chat_id, tag_id))


# --- сообщения ---

def add_message(chat_id: int, role: str, content: str, mode: str = "auto") -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO messages (chat_id, role, content, mode) VALUES (?, ?, ?, ?)",
            (chat_id, role, content, mode),
        )


def get_history(chat_id: int, limit: int = 10) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT role, content FROM messages WHERE chat_id = ? ORDER BY id DESC LIMIT ?",
            (chat_id, limit),
        ).fetchall()
    rows = list(rows)[::-1]
    return [{"role": r["role"], "content": r["content"]} for r in rows]


def get_messages(chat_id: int, limit: int = 200) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT role, content, mode, created_at FROM messages WHERE chat_id = ? ORDER BY id ASC LIMIT ?",
            (chat_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


# --- задачи / напоминания ---

def create_task(title: str, due_at: str | None, chat_id: int | None = None) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO tasks (chat_id, title, due_at) VALUES (?, ?, ?)",
            (chat_id, title, due_at),
        )
        return cur.lastrowid


def list_tasks(status: str | None = None) -> list[dict]:
    with get_conn() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE status = ? ORDER BY due_at IS NULL, due_at ASC",
                (status,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM tasks ORDER BY status = 'done', due_at IS NULL, due_at ASC"
            ).fetchall()
    return [dict(r) for r in rows]


def update_task_status(task_id: int, status: str) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE tasks SET status = ? WHERE id = ?", (status, task_id))


def get_due_tasks() -> list[dict]:
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE status='open' AND reminded=0 AND due_at IS NOT NULL AND due_at <= ?",
            (now,),
        ).fetchall()
    return [dict(r) for r in rows]


def mark_task_reminded(task_id: int) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE tasks SET reminded = 1 WHERE id = ?", (task_id,))


# --- статистика ---

def get_stats() -> dict:
    with get_conn() as conn:
        total_contacts = conn.execute("SELECT COUNT(*) c FROM contacts").fetchone()["c"]
        total_messages = conn.execute("SELECT COUNT(*) c FROM messages").fetchone()["c"]
        open_tasks = conn.execute("SELECT COUNT(*) c FROM tasks WHERE status='open'").fetchone()["c"]
        by_day = conn.execute(
            """
            SELECT date(created_at) d, COUNT(*) c FROM messages
            WHERE created_at >= datetime('now', '-7 days')
            GROUP BY d ORDER BY d
            """
        ).fetchall()
        top_contacts = conn.execute(
            """
            SELECT c.chat_id, c.first_name, c.username, COUNT(m.id) cnt
            FROM contacts c JOIN messages m ON m.chat_id = c.chat_id
            GROUP BY c.chat_id ORDER BY cnt DESC LIMIT 5
            """
        ).fetchall()
        tag_distribution = conn.execute(
            """
            SELECT t.name, COUNT(ct.chat_id) cnt FROM tags t
            LEFT JOIN contact_tags ct ON ct.tag_id = t.id
            GROUP BY t.id
            """
        ).fetchall()
    return {
        "total_contacts": total_contacts,
        "total_messages": total_messages,
        "open_tasks": open_tasks,
        "messages_by_day": [dict(r) for r in by_day],
        "top_contacts": [dict(r) for r in top_contacts],
        "tag_distribution": [dict(r) for r in tag_distribution],
    }
