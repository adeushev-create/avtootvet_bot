from config import settings


def generate_reply(system_prompt: str, history: list[dict], incoming_text: str) -> str:
    """
    history — список {"role": "user"/"assistant", "content": str} с последними
    сообщениями переписки в этом чате (assistant = твои прошлые ответы).
    Провайдер переключается через LLM_PROVIDER в .env.
    """
    messages = history + [{"role": "user", "content": incoming_text}]

    if settings.llm_provider == "groq":
        return _generate_groq(system_prompt, messages)
    if settings.llm_provider == "gemini":
        return _generate_gemini(system_prompt, messages)
    if settings.llm_provider == "claude":
        return _generate_claude(system_prompt, messages)
    raise ValueError(f"Неизвестный LLM_PROVIDER: {settings.llm_provider!r}")


def _generate_groq(system_prompt: str, messages: list[dict]) -> str:
    # pip install groq — бесплатный API, OpenAI-совместимый формат
    from groq import Groq

    client = Groq(api_key=settings.groq_api_key)
    response = client.chat.completions.create(
        model=settings.groq_model,
        messages=[{"role": "system", "content": system_prompt}, *messages],
        max_tokens=400,
        temperature=0.8,
    )
    return response.choices[0].message.content.strip()


def _generate_gemini(system_prompt: str, messages: list[dict]) -> str:
    # pip install google-generativeai — бесплатный тариф в Google AI Studio
    import google.generativeai as genai

    genai.configure(api_key=settings.gemini_api_key)
    model = genai.GenerativeModel(settings.gemini_model, system_instruction=system_prompt)

    # Gemini ждёт историю в формате user/model, а не user/assistant
    chat_history = [
        {"role": "model" if m["role"] == "assistant" else "user", "parts": [m["content"]]}
        for m in messages[:-1]
    ]
    chat = model.start_chat(history=chat_history)
    response = chat.send_message(messages[-1]["content"])
    return response.text.strip()


def _generate_claude(system_prompt: str, messages: list[dict]) -> str:
    # pip install anthropic — платный API, оставлено как опция, если понадобится качество выше
    import anthropic

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    response = client.messages.create(
        model=settings.claude_model,
        max_tokens=400,
        system=system_prompt,
        messages=messages,
    )
    parts = [block.text for block in response.content if block.type == "text"]
    return "".join(parts).strip()
