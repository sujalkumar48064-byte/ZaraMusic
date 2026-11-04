from ZaraMusic.strings import DEFAULT_LANG, get_string
from ZaraMusic.utils.db import get_chat_lang
from ZaraMusic.utils.routes import notify_chat


def chat_strings(chat_id: int) -> dict:
    """This chat's chosen language strings (falls back to English)."""
    try:
        lang = get_chat_lang(notify_chat(chat_id))   # channel VC -> its group's language
    except Exception:
        lang = DEFAULT_LANG
    return get_string(lang)
