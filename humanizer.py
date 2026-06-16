import random


def humanize(text: str, typo_probability: float = 0.05, casual_probability: float = 0.3) -> str:
    """
    Лёгкая постобработка ответа, чтобы текст не выглядел идеально "ИИшным".
    Не меняет смысл — только поверхностные мелочи:
    - изредка переставляет две соседние буквы в одном слове (как реальная опечатка);
    - изредка убирает точку в конце и делает первую букву маленькой.

    Параметры лучше держать небольшими (0.03-0.08 для опечаток), иначе текст
    начинает выглядеть наигранно, а не естественно.
    """
    if not text:
        return text

    words = text.split(" ")

    if random.random() < typo_probability:
        candidates = [i for i, w in enumerate(words) if len(w) > 4 and w.isalpha()]
        if candidates:
            idx = random.choice(candidates)
            letters = list(words[idx])
            pos = random.randint(0, len(letters) - 2)
            letters[pos], letters[pos + 1] = letters[pos + 1], letters[pos]
            words[idx] = "".join(letters)

    text = " ".join(words)

    if random.random() < casual_probability:
        if text.endswith("."):
            text = text[:-1]
        if text and text[0].isupper():
            text = text[0].lower() + text[1:]

    return text
