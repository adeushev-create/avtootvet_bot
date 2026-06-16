from pathlib import Path

BASE_DIR = Path(__file__).parent
STYLE_DESCRIPTION_PATH = BASE_DIR / "style_description.txt"
STYLE_EXAMPLES_PATH = BASE_DIR / "style_examples.txt"


def load_style_description() -> str:
    if STYLE_DESCRIPTION_PATH.exists():
        return STYLE_DESCRIPTION_PATH.read_text(encoding="utf-8").strip()
    return "Пиши нейтрально и дружелюбно, без канцелярита."


def load_style_examples(limit: int = 25) -> list[str]:
    """Берёт последние `limit` строк из style_examples.txt — туда нужно
    вставить свои реальные сообщения, по одному на строку."""
    if not STYLE_EXAMPLES_PATH.exists():
        return []
    lines = [
        line.strip()
        for line in STYLE_EXAMPLES_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    return lines[-limit:]
