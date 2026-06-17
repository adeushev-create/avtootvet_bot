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
