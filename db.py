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


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    """Добавляет колонку, если её ещё нет — безопасно для уже существующей базы на Volume."""
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


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
        # миграции для уже существующих баз (на Railway Volume и т.п.)
        _ensure_column(conn, "contacts", "archived", "archived INTEGER DEFAULT 0")
        _ensure_column(conn, "tasks", "source", "source TEXT DEFAULT 'manual'")
        _ensure_column(conn, "messages", "business_connection_id", "business_connection_id TEXT")
        _ensure_column(conn, "contacts", "photo_file_id", "photo_file_id TEXT")

        # одноразовая миграция: раньше тег создавался под именем "жена", теперь — "семья"
        conn.execute("UPDATE tags SET name = 'семья' WHERE name = 'жена'")

        # отдельная таблица — что из стартовых тегов уже было создано (даже если потом удалили).
        # Нужна, чтобы можно было безопасно добавлять новые теги по умолчанию в будущих версиях,
        # не воскрешая то, что ты сам удалил, и не пропуская то, что появилось позже.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS seeded_tags (
                name TEXT PRIMARY KEY
            )
            """
        )
        for name, color in (("работа", "#3db2ff"), ("семья", "#ff5c8a"), ("стартап", "#7b61ff"), ("друзья", "#ff6b5e")):
            already_seeded = conn.execute("SELECT 1 FROM seeded_tags WHERE name = ?", (name,)).fetchone()
            if already_seeded:
                continue
            conn.execute("INSERT OR IGNORE INTO tags (name, color) VALUES (?, ?)", (name, color))
            conn.execute("INSERT OR IGNORE INTO seeded_tags (name) VALUES (?)", (name,))


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


def list_contacts(include_archived: bool = False) -> list[dict]:
    with get_conn() as conn:
        where = "" if include_archived else "WHERE c.archived = 0"
        rows = conn.execute(
            f"""
            SELECT c.chat_id, c.username, c.first_name, c.last_name, c.last_seen, c.notes, c.archived,
                   (SELECT content FROM messages m WHERE m.chat_id = c.chat_id ORDER BY m.id DESC LIMIT 1) AS last_message,
                   (SELECT created_at FROM messages m WHERE m.chat_id = c.chat_id ORDER BY m.id DESC LIMIT 1) AS last_message_at,
                   (SELECT COUNT(*) FROM messages m WHERE m.chat_id = c.chat_id) AS message_count
            FROM contacts c
            {where}
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


def get_contact(chat_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM contacts WHERE chat_id = ?", (chat_id,)).fetchone()
        if not row:
            return None
        tags = conn.execute(
            """
            SELECT t.id, t.name, t.color FROM tags t
            JOIN contact_tags ct ON ct.tag_id = t.id
            WHERE ct.chat_id = ?
            """,
            (chat_id,),
        ).fetchall()
        return {**dict(row), "tags": [dict(t) for t in tags]}


def update_contact_notes(chat_id: int, notes: str) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE contacts SET notes = ? WHERE chat_id = ?", (notes, chat_id))


def set_contact_archived(chat_id: int, archived: bool) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE contacts SET archived = ? WHERE chat_id = ?", (1 if archived else 0, chat_id))


# --- теги / сегменты ---

def list_tags() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT t.id, t.name, t.color, COUNT(ct.chat_id) AS contact_count
            FROM tags t
            LEFT JOIN contact_tags ct ON ct.tag_id = t.id
            GROUP BY t.id
            ORDER BY t.name
            """
        ).fetchall()
        return [dict(r) for r in rows]


def create_tag(name: str, color: str = "#888888") -> int:
    with get_conn() as conn:
        conn.execute("INSERT OR IGNORE INTO tags (name, color) VALUES (?, ?)", (name, color))
        row = conn.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()
        return row["id"]


def update_tag(tag_id: int, name: str | None = None, color: str | None = None) -> None:
    with get_conn() as conn:
        if name is not None:
            conn.execute("UPDATE tags SET name = ? WHERE id = ?", (name, tag_id))
        if color is not None:
            conn.execute("UPDATE tags SET color = ? WHERE id = ?", (color, tag_id))


def delete_tag(tag_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM contact_tags WHERE tag_id = ?", (tag_id,))
        conn.execute("DELETE FROM tags WHERE id = ?", (tag_id,))


def set_contact_tags(chat_id: int, tag_ids: list[int]) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM contact_tags WHERE chat_id = ?", (chat_id,))
        for tag_id in tag_ids:
            conn.execute("INSERT INTO contact_tags (chat_id, tag_id) VALUES (?, ?)", (chat_id, tag_id))


# --- сообщения ---

def add_message(chat_id: int, role: str, content: str, mode: str = "auto", business_connection_id: str | None = None) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO messages (chat_id, role, content, mode, business_connection_id) VALUES (?, ?, ?, ?, ?)",
            (chat_id, role, content, mode, business_connection_id),
        )
        return cur.lastrowid


def get_message(message_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
        return dict(row) if row else None


def update_message_mode(message_id: int, mode: str) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE messages SET mode = ? WHERE id = ?", (mode, message_id))


def get_history(chat_id: int, limit: int = 30) -> list[dict]:
    """Последние сообщения чата для контекста LLM.
    Черновики, которые не были отправлены (mode='draft'), намеренно исключаем —
    собеседник их не видел, и включать их в историю как будто ответ был дан — ошибка.
    Включаем: входящие (user), отправленные ботом (auto), твои ручные ответы (manual)."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT role, content FROM messages
               WHERE chat_id = ? AND NOT (role = 'assistant' AND mode = 'draft')
               ORDER BY id DESC LIMIT ?""",
            (chat_id, limit),
        ).fetchall()
    rows = list(rows)[::-1]
    return [{"role": r["role"], "content": r["content"]} for r in rows]


def get_assistant_messages(chat_id: int, limit: int = 15) -> list[str]:
    """Твои прошлые ответы именно этому контакту (любой mode) — для персонализации
    под конкретного человека, а не только общий профиль стиля."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT content FROM messages WHERE chat_id = ? AND role = 'assistant' ORDER BY id DESC LIMIT ?",
            (chat_id, limit),
        ).fetchall()
    return [r["content"] for r in rows][::-1]


def get_messages(chat_id: int, limit: int = 200) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, role, content, mode, created_at FROM messages WHERE chat_id = ? ORDER BY id ASC LIMIT ?",
            (chat_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


# --- задачи / напоминания ---

def create_task(title: str, due_at: str | None, chat_id: int | None = None, source: str = "manual") -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO tasks (chat_id, title, due_at, source) VALUES (?, ?, ?, ?)",
            (chat_id, title, due_at, source),
        )
        return cur.lastrowid


def list_tasks(status: str | None = None) -> list[dict]:
    with get_conn() as conn:
        query = """
            SELECT t.*, c.first_name AS contact_first_name, c.username AS contact_username
            FROM tasks t
            LEFT JOIN contacts c ON c.chat_id = t.chat_id
        """
        if status:
            query += " WHERE t.status = ?"
            rows = conn.execute(
                query + " ORDER BY t.due_at IS NULL, t.due_at ASC", (status,)
            ).fetchall()
        else:
            rows = conn.execute(
                query + " ORDER BY t.status = 'done', t.due_at IS NULL, t.due_at ASC"
            ).fetchall()
    return [dict(r) for r in rows]


def update_task_status(task_id: int, status: str) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE tasks SET status = ? WHERE id = ?", (status, task_id))


def delete_task(task_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))


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
        total_contacts = conn.execute("SELECT COUNT(*) c FROM contacts WHERE archived = 0").fetchone()["c"]
        total_messages = conn.execute("SELECT COUNT(*) c FROM messages").fetchone()["c"]

        # задачи — все, а не только открытые
        total_tasks = conn.execute("SELECT COUNT(*) c FROM tasks").fetchone()["c"]
        open_tasks = conn.execute("SELECT COUNT(*) c FROM tasks WHERE status='open'").fetchone()["c"]
        done_tasks = conn.execute("SELECT COUNT(*) c FROM tasks WHERE status='done'").fetchone()["c"]
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()
        overdue_tasks = conn.execute(
            "SELECT COUNT(*) c FROM tasks WHERE status='open' AND due_at IS NOT NULL AND due_at < ?",
            (now_iso,),
        ).fetchone()["c"]

        # разбивка по источнику
        tasks_by_source = conn.execute(
            "SELECT COALESCE(source, 'manual') source, COUNT(*) c FROM tasks GROUP BY source"
        ).fetchall()

        by_day = conn.execute(
            """
            SELECT date(created_at) d, COUNT(*) c FROM messages
            WHERE created_at >= datetime('now', '-7 days')
            GROUP BY d ORDER BY d
            """
        ).fetchall()
        by_hour = conn.execute(
            """
            SELECT CAST(strftime('%H', created_at) AS INTEGER) h, COUNT(*) c
            FROM messages WHERE role = 'user'
            GROUP BY h ORDER BY h
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
            SELECT t.name, t.color, COUNT(ct.chat_id) cnt FROM tags t
            LEFT JOIN contact_tags ct ON ct.tag_id = t.id
            GROUP BY t.id
            """
        ).fetchall()
        mode_distribution = conn.execute(
            """
            SELECT mode, COUNT(*) c FROM messages WHERE role = 'assistant' GROUP BY mode
            """
        ).fetchall()
    return {
        "total_contacts": total_contacts,
        "total_messages": total_messages,
        "total_tasks": total_tasks,
        "open_tasks": open_tasks,
        "done_tasks": done_tasks,
        "overdue_tasks": overdue_tasks,
        "tasks_by_source": [dict(r) for r in tasks_by_source],
        "messages_by_day": [dict(r) for r in by_day],
        "messages_by_hour": [dict(r) for r in by_hour],
        "top_contacts": [dict(r) for r in top_contacts],
        "tag_distribution": [dict(r) for r in tag_distribution],
        "mode_distribution": [dict(r) for r in mode_distribution],
    }
