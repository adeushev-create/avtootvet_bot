from pathlib import Path

BASE_DIR = Path(__file__).parent


def _suffix(profile: str) -> str:
    return "" if profile == "default" else f"_{profile}"


def load_style_description(profile: str = "default") -> str:
    path = BASE_DIR / f"style_description{_suffix(profile)}.txt"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    if profile != "default":
        return load_style_description("default")
    return "Пиши нейтрально и дружелюбно, без канцелярита."


def load_style_examples(profile: str = "default", limit: int = 40) -> list[str]:
    """Берёт последние `limit` строк из style_examples[_profile].txt — туда нужно
    вставить реальные сообщения, по одному на строку."""
    path = BASE_DIR / f"style_examples{_suffix(profile)}.txt"
    if not path.exists():
        if profile != "default":
            return load_style_examples("default", limit)
        return []
    lines = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    return lines[-limit:]
