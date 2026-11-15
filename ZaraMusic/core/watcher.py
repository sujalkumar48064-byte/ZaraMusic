import asyncio

from ZaraMusic import LOGGER, assistant


async def watchdog() -> None:
    """Keep the assistant userbot connected without hammering Telegram."""
    while True:
        await asyncio.sleep(60)
        if getattr(assistant, "is_connected", None) is False:
            try:
                LOGGER.warning("[WATCHDOG] assistant disconnected; reconnecting")
                await asyncio.wait_for(assistant.start(), timeout=30)
                LOGGER.info("[WATCHDOG] assistant reconnected")
            except Exception as e:
                LOGGER.warning(f"[WATCHDOG] assistant reconnect failed: {e}")
