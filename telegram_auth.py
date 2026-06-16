import hashlib
import hmac
from urllib.parse import parse_qsl


def validate_init_data(init_data: str, bot_token: str) -> dict | None:
    """
    Проверяет подпись initData, которую Telegram передаёт мини-аппу.
    Возвращает словарь полей при успехе, либо None при невалидной подписи.
    Алгоритм описан в https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
    """
    if not init_data:
        return None
    try:
        parsed = dict(parse_qsl(init_data, strict_parsing=True))
    except ValueError:
        return None

    received_hash = parsed.pop("hash", None)
    if not received_hash:
        return None

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(computed_hash, received_hash):
        return None
    return parsed
