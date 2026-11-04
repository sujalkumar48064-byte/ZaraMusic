import config
from ZaraMusic.utils.db import is_logger_enabled


def logger_active() -> bool:
    return bool(config.LOGGER_ID) and is_logger_enabled()
