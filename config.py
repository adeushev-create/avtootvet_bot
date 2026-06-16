import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


def _parse_excluded_ids(raw: str) -> frozenset[int]:
    return frozenset(int(x) for x in raw.split(",") if x.strip())


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


settings = Settings()
