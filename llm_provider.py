import json
import re
from datetime import datetime, timezone

from config import settings


def _dispatch(system_prompt: str, messages: list[dict], max_tokens: int, temperature: float) -> str:
    if settings.llm_provider == "groq":
        return _complete_groq(system_prompt, messages, max_tokens, temperature)
    if settings.llm_provider == "gemini":
        return _complete_gemini(system_prompt, messages, max_tokens, temperature)
    if settings.llm_provider == "claude":
        return _complete_claude(system_prompt, messages, max_tokens, temperature)
    raise ValueError(f"Неизвестный LLM_PROVIDER: {settings.llm_provider!r}")


def _complete_groq(system_prompt: str, messages: list[dict], max_tokens: int, temperature: float) -> str:
    from groq import Groq

    client = Groq(api_key=settings.groq_api_key)
    response = client.chat.completions.create(
        model=settings.groq_model,
        messages=[{"role": "system", "content": system_prompt}, *messages],
        max_tokens=max_tokens,
        temperature=temperature,
    )
    return response.choices[0].message.content.strip()


def _complete_gemini(system_prompt: str, messages: list[dict], max_tokens: int, temperature: float) -> str:
    import google.generativeai as genai

    genai.configure(api_key=settings.gemini_api_key)
    model = genai.GenerativeModel(settings.gemini_model, system_instruction=system_prompt)

    chat_history = [
        {"role": "model" if m["role"] == "assistant" else "user", "parts": [m["content"]]}
        for m in messages[:-1]
    ]
    chat = model.start_chat(history=chat_history)
    response = chat.send_message(
        messages[-1]["content"],
        generation_config={"max_output_tokens": max_tokens, "temperature": temperature},
    )
    return response.text.strip()


def _complete_claude(system_prompt: str, messages: list[dict], max_tokens: int, temperature: float) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    response = client.messages.create(
        model=settings.claude_model,
        max_tokens=max_tokens,
        temperature=temperature,
        system=system_prompt,
        messages=messages,
    )
    parts = [block.text for block in response.content if block.type == "text"]
    return "".join(parts).strip()


def generate_reply(system_prompt: str, history: list[dict], incoming_text: str) -> str:
    """
    history — список {"role": "user"/"assistant", "content": str} с последними
    сообщениями переписки в этом чате (assistant = твои прошлые ответы).
    """
    messages = history + [{"role": "user", "content": incoming_text}]
    return _dispatch(system_prompt, messages, max_tokens=400, temperature=settings.reply_temperature)


_EXTRACT_SYSTEM_PROMPT = """Ты анализируешь одно сообщение и решаешь, стоит ли превратить его в задачу/напоминание.
Сегодня: {now} (UTC).

Создавай задачу только если в сообщении есть конкретное дело, просьба или договорённость, которую легко забыть.
Не создавай задачу для обычной болтовни, эмоций, вопросов без действия, благодарностей, шуток.

Ответь СТРОГО в виде JSON, без markdown и без пояснений, в одном из двух видов:
{{"task": true, "title": "короткая суть дела на русском", "due_at": "2026-06-17T18:00:00+00:00"}}
{{"task": false}}

Поле due_at указывай только если в сообщении явно назван срок или время (например "завтра", "в субботу", "через час", "в 18:00").
Если срок не назван явно — due_at должен быть null."""


def extract_task(text: str) -> dict | None:
    """
    Лёгкий отдельный вызов LLM: пытается понять, есть ли в тексте дело,
    которое стоит сохранить как задачу. Возвращает {"title", "due_at"} либо None.
    """
    if not text or len(text.strip()) < 3:
        return None

    now = datetime.now(timezone.utc).isoformat()
    system_prompt = _EXTRACT_SYSTEM_PROMPT.format(now=now)

    try:
        raw = _dispatch(
            system_prompt,
            [{"role": "user", "content": text}],
            max_tokens=200,
            temperature=0,
        )
    except Exception:
        return None

    raw = raw.strip()
    raw = re.sub(r"^```(json)?", "", raw).strip()
    raw = re.sub(r"```$", "", raw).strip()

    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return None

    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None

    if not data.get("task"):
        return None

    title = (data.get("title") or "").strip()
    if not title:
        return None

    return {"title": title, "due_at": data.get("due_at")}


# --- транскрипция голосовых ---

def transcribe_voice(audio_bytes: bytes, filename: str = "voice.ogg") -> str | None:
    """Транскрибирует голосовое сообщение через Groq Whisper (бесплатно).
    Возвращает текст или None при ошибке."""
    if settings.llm_provider != "groq" or not settings.groq_api_key:
        return None
    try:
        from groq import Groq
        client = Groq(api_key=settings.groq_api_key)
        result = client.audio.transcriptions.create(
            file=(filename, audio_bytes),
            model="whisper-large-v3",
            language="ru",
            response_format="text",
        )
        return result.strip() if isinstance(result, str) else result.text.strip()
    except Exception:
        return None


# --- анализ тональности ---

_TONE_PROMPT = """Проанализируй тональность переписки ниже.
Ответь ТОЛЬКО одним из трёх вариантов (без пояснений, только одно слово или эмодзи+слово):
- 🟢 тёплая
- 🟡 нейтральная
- 🔴 напряжённая

Переписка:
{messages}"""


def analyze_tone(messages: list[dict]) -> str:
    """Возвращает строку типа '🟢 тёплая' или '🔴 напряжённая'."""
    if not messages:
        return "🟡 нейтральная"
    text = "\n".join(f"{'→' if m['role']=='assistant' else '←'} {m['content']}" for m in messages[-20:])
    try:
        result = _dispatch(
            _TONE_PROMPT.format(messages=text),
            [{"role": "user", "content": "Определи тональность"}],
            max_tokens=20,
            temperature=0,
        )
        result = result.strip().lower()
        if "напряж" in result or "🔴" in result:
            return "🔴 напряжённая"
        if "тёпл" in result or "тепл" in result or "🟢" in result:
            return "🟢 тёплая"
        return "🟡 нейтральная"
    except Exception:
        return "🟡 нейтральная"


# --- профиль контакта ---

_SUMMARY_PROMPT = """На основе переписки ниже составь очень короткое описание (2-3 предложения) этого контакта.
Кто он, о чём вы обычно общаетесь, какой характер общения.
Пиши от первого лица (как будто описываешь сам себе: "Коллега по...", "Друг с которым...").
Только русский язык, без лишних слов, без перечислений с тире.

Переписка (→ это мои ответы, ← это их сообщения):
{messages}"""


def generate_contact_summary(messages: list[dict]) -> str | None:
    """Генерирует 2-3 предложения о контакте на основе переписки."""
    if len(messages) < 6:
        return None
    text = "\n".join(f"{'→' if m['role']=='assistant' else '←'} {m['content']}" for m in messages[-30:])
    try:
        result = _dispatch(
            _SUMMARY_PROMPT.format(messages=text),
            [{"role": "user", "content": "Составь описание контакта"}],
            max_tokens=200,
            temperature=0.3,
        )
        return result.strip() or None
    except Exception:
        return None


# --- A/B тест: два варианта ответа ---

def generate_reply_pair(system_prompt: str, history: list[dict], incoming_text: str) -> tuple[str, str]:
    """Генерирует два разных варианта ответа с разными температурами.
    Возвращает (вариант_A, вариант_B)."""
    messages = history + [{"role": "user", "content": incoming_text}]
    variant_a = _dispatch(system_prompt, messages, max_tokens=400, temperature=0.5)
    variant_b = _dispatch(system_prompt, messages, max_tokens=400, temperature=0.9)
    return variant_a.strip(), variant_b.strip()
