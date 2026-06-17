import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


def _parse_excluded_ids(raw: str) -> frozenset[int]:
    return frozenset(int(x) for x in raw.split(",") if x.strip())


DEFAULT_TAG_PROFILE_MAP = {
    "работа": "work",
    "коллеги": "work",
    "клиенты": "work",
    "бизнес": "work",
    "жена": "wife",
    "муж": "wife",
    "семья": "wife",
    "близкие": "wife",
    "стартап": "startup",
    "стартапы": "startup",
    "партнеры": "startup",
    "сбер500": "startup",
}


def _parse_tag_profile_map(raw: str) -> dict:
    """Разбирает строку вида 'тег1:профиль1,тег2:профиль2' в словарь.
    Если переменная не задана — используется набор тегов по умолчанию."""
    if not raw.strip():
        return dict(DEFAULT_TAG_PROFILE_MAP)
    result: dict[str, str] = {}
    for pair in raw.split(","):
        if ":" not in pair:
            continue
        tag, profile = pair.split(":", 1)
        tag, profile = tag.strip().lower(), profile.strip().lower()
        if tag and profile:
            result[tag] = profile
    return result


@dataclass
class Settings:
    bot_token: str = os.getenv("BOT_TOKEN", "")

    # "groq" (бесплатно, рекомендуется по умолчанию) | "gemini" | "claude"
    llm_provider: str = os.getenv("LLM_PROVIDER", "groq")

    groq_api_key: str = os.getenv("GROQ_API_KEY", "")
    groq_model: str = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    claude_model: str = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")

    # вероятность лёгкой "опечатки" (0..1)
    typo_probability: float = float(os.getenv("TYPO_PROBABILITY", "0.05"))
    # вероятность неформальных мелочей (без точки, маленькая буква и т.п.)
    casual_probability: float = float(os.getenv("CASUAL_PROBABILITY", "0.3"))

    # chat_id, для которых бот НЕ должен отвечать автоматически
    excluded_chat_ids: frozenset[int] = field(
        default_factory=lambda: _parse_excluded_ids(os.getenv("EXCLUDED_CHAT_IDS", ""))
    )

    # "draft" — присылать черновик владельцу на проверку перед отправкой (рекомендуется)
    # "auto"  — отправлять собеседнику сразу
    mode: str = os.getenv("REPLY_MODE", "draft")

    # твой личный Telegram user_id (узнать через @userinfobot)
    owner_user_id: int = int(os.getenv("OWNER_USER_ID", "0"))

    your_name: str = os.getenv("YOUR_NAME", "Алекс")

    # публичный HTTPS-адрес, где будет открываться мини-апп (заполняется после деплоя/тоннеля)
    webapp_url: str = os.getenv("WEBAPP_URL", "")

    # порт для FastAPI/uvicorn — многие хостинги сами подставляют переменную PORT
    port: int = int(os.getenv("PORT", "8000"))

    # папка для файла базы данных (crm.db). На Railway сюда монтируется Volume,
    # чтобы данные не стирались при каждом передеплое.
    data_dir: str = os.getenv("DATA_DIR", ".")

    # слово-триггер для быстрых заметок в чате с самим ботом: "запомни ..." — сохранит как задачу
    trigger_word: str = os.getenv("TRIGGER_WORD", "запомни")

    # фразы-триггеры для захвата задач прямо из переписки с реальными людьми:
    # если твой СОБСТВЕННЫЙ ответ человеку содержит одну из этих фраз — бот сохранит задачу
    capture_phrases: tuple[str, ...] = tuple(
        p.strip().lower()
        for p in os.getenv("CAPTURE_PHRASES", "запомни,не забыть,надо запомнить,надо не забыть").split(",")
        if p.strip()
    )

    # автоматически находить задачи во входящих сообщениях (требует доп. вызов LLM на каждое сообщение)
    auto_extract_tasks: bool = os.getenv("AUTO_EXTRACT_TASKS", "true").lower() == "true"

    # карта "тег в CRM -> профиль стиля". Можно добавлять сколько угодно профилей —
    # просто создай style_description_<профиль>.txt и style_examples_<профиль>.txt
    tag_profile_map: dict = field(default_factory=lambda: _parse_tag_profile_map(os.getenv("TAG_PROFILE_MAP", "")))


settings = Settings()
