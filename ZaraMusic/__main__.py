import asyncio
import importlib
import os
import re
import sys
import threading
import time

import requests
from flask import Flask
from pyrogram import idle
from pyrogram.types import BotCommand, BotCommandScopeChat, ChatAdministratorRights

import config
from ZaraMusic import LOGGER, assistant, bot, call_py
from ZaraMusic.modules import ALL_MODULES
from ZaraMusic.utils.logs import logger_active
from richgram import (
    rich_esc,
    rich_heading,
    rich_kv_table,
    rich_send,
)

ASSISTANT_USERNAME: str = ""

# ── Flask health check ────────────────────────────────────────────────────────

_flask = Flask(__name__)


@_flask.route("/")
def _home():
    return "Zara Music is running", 200


@_flask.route("/health")
def _health():
    return "OK", 200


def _run_flask() -> None:
    _flask.run(host="0.0.0.0", port=config.PORT, use_reloader=False)


# ── Keep-Alive ────────────────────────────────────────────────────────────────

def _keep_alive() -> None:
    url = (
        os.getenv("KEEP_ALIVE_URL")
        or os.getenv("RENDER_EXTERNAL_URL")
        or "https://zaramusic.onrender.com"
    ).rstrip("/")
    if not url.endswith("/health"):
        url += "/health"

    while True:
        try:
            resp = requests.get(url, timeout=15)
            if resp.status_code == 200:
                LOGGER.info(f"Keep-alive OK → {url}")
            else:
                LOGGER.warning(f"Keep-alive status {resp.status_code} → {url}")
        except Exception as e:
            LOGGER.warning(f"Keep-alive ping failed: {e}")
        time.sleep(240)  # 4 minutes — Render sleeps after 15 min idle


# ── Startup notification ──────────────────────────────────────────────────────


async def _notify_owner(me, assistant_username: str, assistant_id: int = 0) -> None:
    if not logger_active():
        return

    try:
        content = (
            rich_heading(
                "🎵 ᴢᴀʀᴀᴍᴜꜱɪᴄ ꜱᴛᴀʀᴛᴇᴅ 💕",
                level=3
            )
            + rich_kv_table([
                (
                    "ʙᴏᴛ",
                    f"@{rich_esc(me.username or 'N/A')}"
                ),
                (
                    "ᴀꜱꜱɪꜱᴛᴀɴᴛ",
                    f"@{rich_esc(assistant_username or 'N/A')} "
                    f"<code>{assistant_id}</code>"
                ),
            ])
        )

        await rich_send(
            bot,
            config.LOGGER_ID,
            content,
        )

    except Exception as e:
        LOGGER.warning(
            f"Logger Notification Error : {e}"
        )

# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":

    # 1. MongoDB
    try:
        from ZaraMusic.utils.db import start_mongo, start_mongo_retry
        ok = start_mongo()
        if ok:
            LOGGER.info("MongoDB ready.")
        else:
            LOGGER.warning("MongoDB not connected — continuing without DB.")
            start_mongo_retry()
    except Exception as e:
        LOGGER.warning(f"MongoDB startup error: {e} — continuing without DB.")

    # 2. Flask
    threading.Thread(target=_run_flask, daemon=True).start()
    LOGGER.info(f"Flask health server on port {config.PORT}")

    # 3. Keep-alive ping
    threading.Thread(target=_keep_alive, daemon=True).start()
    LOGGER.info("Keep-alive thread started")

    # 4. Bot start
    for attempt in range(10):
        try:
            bot.start()
            LOGGER.info("Bot client started")
            break
        except Exception as e:
            if "FLOOD_WAIT" in str(e):
                m    = re.search(r"(\d+)", str(e))
                wait = min(int(m.group(1)) + 5 if m else 300, 1800)
                LOGGER.warning(f"FLOOD_WAIT — sleeping {wait}s (attempt {attempt + 1}/10)")
                time.sleep(wait)
            else:
                LOGGER.error(f"Bot start failed: {e}")
                sys.exit(1)
    else:
        LOGGER.error("Bot failed to start after 10 attempts")
        sys.exit(1)

    me = bot.get_me()
    LOGGER.info(f"Bot: @{me.username}")
    import ZaraMusic
    from pytgcalls import PyTgCalls
    ZaraMusic.call_py = PyTgCalls(assistant)
    call_py = ZaraMusic.call_py

    # 5. Set bot commands, admin rights & profile
    bot_commands = [
        BotCommand("start", "✧ sᴛᴀʀᴛ ᴛʜᴇ ʙᴏᴛ ✧"),
        BotCommand("help", "✧ ɢᴇᴛ ʜᴇʟᴘ ᴍᴇɴᴜ ✧"),
        BotCommand("play", "✧ ᴘʟᴀʏ ᴀ sᴏɴɢ ✧"),
        BotCommand("vplay", "✧ ᴘʟᴀʏ ᴀ ᴠɪᴅᴇᴏ ✧"),
        BotCommand("cplay", "✧ ᴘʟᴀʏ ɪɴ ʟɪɴᴋᴇᴅ ᴄʜᴀɴɴᴇʟ ✧"),
        BotCommand("cvplay", "✧ ᴠɪᴅᴇᴏ ɪɴ ʟɪɴᴋᴇᴅ ᴄʜᴀɴɴᴇʟ ✧"),
        BotCommand("playforce", "✧ ᴘʟᴀʏ ɴᴏᴡ, ᴋᴇᴇᴘ ǫᴜᴇᴜᴇ ✧"),
        BotCommand("vplayforce", "✧ ᴠɪᴅᴇᴏ ɴᴏᴡ, ᴋᴇᴇᴘ ǫᴜᴇᴜᴇ ✧"),
        BotCommand("cplayforce", "✧ ᴄʜᴀɴɴᴇʟ ᴘʟᴀʏ ɴᴏᴡ ✧"),
        BotCommand("cvplayforce", "✧ ᴄʜᴀɴɴᴇʟ ᴠɪᴅᴇᴏ ɴᴏᴡ ✧"),
        BotCommand("pause", "✧ ᴘᴀᴜsᴇ ᴘʟᴀʏʙᴀᴄᴋ ✧"),
        BotCommand("cpause", "✧ ᴘᴀᴜsᴇ ᴄʜᴀɴɴᴇʟ ✧"),
        BotCommand("resume", "✧ ʀᴇsᴜᴍᴇ ᴘʟᴀʏʙᴀᴄᴋ ✧"),
        BotCommand("cresume", "✧ ʀᴇsᴜᴍᴇ ᴄʜᴀɴɴᴇʟ ✧"),
        BotCommand("skip", "✧ sᴋɪᴘ sᴏɴɢ ✧"),
        BotCommand("cskip", "✧ sᴋɪᴘ ɪɴ ᴄʜᴀɴɴᴇʟ ✧"),
        BotCommand("stop", "✧ sᴛᴏᴘ & ᴄʟᴇᴀʀ ✧"),
        BotCommand("cstop", "✧ sᴛᴏᴘ ᴄʜᴀɴɴᴇʟ ✧"),
        BotCommand("end", "✧ ᴇɴᴅ sᴛʀᴇᴀᴍ ✧"),
        BotCommand("cend", "✧ ᴇɴᴅ ᴄʜᴀɴɴᴇʟ sᴛʀᴇᴀᴍ ✧"),
        BotCommand("clear", "✧ ᴄʟᴇᴀʀ ǫᴜᴇᴜᴇ ✧"),
        BotCommand("cclear", "✧ ᴄʟᴇᴀʀ ᴄʜᴀɴɴᴇʟ ǫᴜᴇᴜᴇ ✧"),
        BotCommand("seek", "✧ sᴇᴇᴋ ғᴏʀᴡᴀʀᴅ ✧"),
        BotCommand("cseek", "✧ ᴄʜᴀɴɴᴇʟ sᴇᴇᴋ ✧"),
        BotCommand("seekback", "✧ sᴇᴇᴋ ʙᴀᴄᴋ ✧"),
        BotCommand("cseekback", "✧ ᴄʜᴀɴɴᴇʟ sᴇᴇᴋ ʙᴀᴄᴋ ✧"),
        BotCommand("speed", "✧ sᴇᴛ ᴘʟᴀʏʙᴀᴄᴋ sᴘᴇᴇᴅ ✧"),
        BotCommand("cspeed", "✧ ᴄʜᴀɴɴᴇʟ sᴘᴇᴇᴅ ✧"),
        BotCommand("speedreset", "✧ ʀᴇsᴇᴛ sᴘᴇᴇᴅ ✧"),
        BotCommand("cspeedreset", "✧ ʀᴇsᴇᴛ ᴄʜᴀɴɴᴇʟ sᴘᴇᴇᴅ ✧"),
        BotCommand("bass", "✧ sᴇᴛ ʙᴀss ʙᴏᴏsᴛ ✧"),
        BotCommand("cbass", "✧ ᴄʜᴀɴɴᴇʟ ʙᴀss ✧"),
        BotCommand("bassoff", "✧ ᴅɪsᴀʙʟᴇ ʙᴀss ✧"),
        BotCommand("cbassoff", "✧ ᴅɪsᴀʙʟᴇ ᴄʜᴀɴɴᴇʟ ʙᴀss ✧"),
        BotCommand("effecton", "✧ ᴇɴᴀʙʟᴇ ᴇғғᴇᴄᴛs ✧"),
        BotCommand("ceffecton", "✧ ᴄʜᴀɴɴᴇʟ ᴇғғᴇᴄᴛs ᴏɴ ✧"),
        BotCommand("effectoff", "✧ ᴅɪsᴀʙʟᴇ ᴇғғᴇᴄᴛs ✧"),
        BotCommand("ceffectoff", "✧ ᴄʜᴀɴɴᴇʟ ᴇғғᴇᴄᴛs ᴏғғ ✧"),
        BotCommand("effects", "✧ ᴠɪᴇᴡ ᴇғғᴇᴄᴛs ✧"),
        BotCommand("ceffects", "✧ ᴄʜᴀɴɴᴇʟ ᴇғғᴇᴄᴛs ✧"),
        BotCommand("autoplay", "✧ ᴀᴜᴛᴏ-ᴘʟᴀʏ ʀᴇʟᴀᴛᴇᴅ sᴏɴɢs ✧"),
        BotCommand("cautoplay", "✧ ᴄʜᴀɴɴᴇʟ ᴀᴜᴛᴏ-ᴘʟᴀʏ ✧"),
        BotCommand("language", "✧ ᴄʜᴀɴɢᴇ ʙᴏᴛ ʟᴀɴɢᴜᴀɢᴇ ✧"),
        BotCommand("setlang", "✧ sᴇᴛ ʟᴀɴɢᴜᴀɢᴇ ✧"),
        BotCommand("lang", "✧ ᴄʜᴀɴɢᴇ ʟᴀɴɢᴜᴀɢᴇ ✧"),
        BotCommand("pcreate", "✧ ᴄʀᴇᴀᴛᴇ ᴀ ᴘʟᴀʏʟɪsᴛ ✧"),
        BotCommand("padd", "✧ ᴀᴅᴅ sᴏɴɢ ᴛᴏ ᴘʟᴀʏʟɪsᴛ ✧"),
        BotCommand("premove", "✧ ʀᴇᴍᴏᴠᴇ sᴏɴɢ ғʀᴏᴍ ᴘʟᴀʏʟɪsᴛ ✧"),
        BotCommand("pview", "✧ ᴠɪᴇᴡ ᴘʟᴀʏʟɪsᴛs ✧"),
        BotCommand("pplay", "✧ ᴘʟᴀʏ ᴀ ᴘʟᴀʏʟɪsᴛ ✧"),
        BotCommand("cpplay", "✧ ᴘʟᴀʏ ᴘʟᴀʏʟɪsᴛ ɪɴ ᴄʜᴀɴɴᴇʟ ✧"),
        BotCommand("pdelete", "✧ ᴅᴇʟᴇᴛᴇ ᴀ ᴘʟᴀʏʟɪsᴛ ✧"),
        BotCommand("addchannel", "✧ ʟɪɴᴋ ᴀ ᴄʜᴀɴɴᴇʟ ғᴏʀ ᴘʟᴀʏ ✧"),
        BotCommand("setchannel", "✧ ʟɪɴᴋ ᴀ ᴄʜᴀɴɴᴇʟ ✧"),
        BotCommand("delchannel", "✧ ᴜɴʟɪɴᴋ ᴄʜᴀɴɴᴇʟ ✧"),
        BotCommand("removechannel", "✧ ᴜɴʟɪɴᴋ ᴄʜᴀɴɴᴇʟ ✧"),
        BotCommand("thumbnail", "✧ sᴏɴɢ ᴛʜᴜᴍʙɴᴀɪʟ ᴏɴ/ᴏғғ ✧"),
        BotCommand("cthumbnail", "✧ ᴄʜᴀɴɴᴇʟ ᴛʜᴜᴍʙɴᴀɪʟ ✧"),
        BotCommand("ping", "✧ ʙᴏᴛ sᴛᴀᴛs ✧"),
        BotCommand("speedtest", "✧ ɴᴇᴛᴡᴏʀᴋ sᴘᴇᴇᴅ ✧"),
        BotCommand("spt", "✧ ɴᴇᴛᴡᴏʀᴋ sᴘᴇᴇᴅ ✧"),
        BotCommand("id", "✧ sʜᴏᴡ ᴄʜᴀᴛ ᴀɴᴅ ᴜsᴇʀ ɪᴅ ✧"),
        BotCommand("reboot", "✧ ʀᴇsᴛᴀʀᴛ ᴘʟᴀʏʙᴀᴄᴋ ✧"),
    ]

    owner_commands = [
        BotCommand("stats", "✧ sʏsᴛᴇᴍ sᴛᴀᴛs ✧"),
        BotCommand("broadcast", "✧ ʙʀᴏᴀᴅᴄᴀsᴛ ᴍᴇssᴀɢᴇ ✧"),
        BotCommand("gcast", "✧ ɢʟᴏʙᴀʟ ʙʀᴏᴀᴅᴄᴀsᴛ ✧"),
        BotCommand("logger", "✧ ʟᴏɢɢᴇʀ sᴇᴛᴛɪɴɢs ✧"),
        BotCommand("gblock", "✧ ʙʟᴏᴄᴋ ᴀ ɢʀᴏᴜᴘ ✧"),
        BotCommand("gunblock", "✧ ᴜɴʙʟᴏᴄᴋ ᴀ ɢʀᴏᴜᴘ ✧"),
        BotCommand("ublock", "✧ ʙʟᴏᴄᴋ ᴀ ᴜsᴇʀ ✧"),
        BotCommand("uunblock", "✧ ᴜɴʙʟᴏᴄᴋ ᴀ ᴜsᴇʀ ✧"),
        BotCommand("blocklist", "✧ ᴠɪᴇᴡ ʙʟᴏᴄᴋ ʟɪsᴛ ✧"),
    ]

    try:
        bot.set_bot_commands(bot_commands)
        LOGGER.info(f"Bot commands set ({len(bot_commands)})")
    except Exception as e:
        LOGGER.warning(f"Could not set bot commands: {e}")

    try:
        bot.set_bot_commands(
            bot_commands + owner_commands,
            scope=BotCommandScopeChat(chat_id=config.OWNER_ID),
        )
        LOGGER.info(f"Owner commands set ({len(owner_commands)})")
    except Exception as e:
        LOGGER.warning(f"Could not set owner commands: {e}")

    try:
        bot.set_bot_default_privileges(
            ChatAdministratorRights(
                can_manage_chat=True,
                can_delete_messages=True,
                can_manage_video_chats=True,
                can_invite_users=True,
                can_promote_members=True,
            )
        )
        LOGGER.info("Default admin rights set")
    except Exception as e:
        if "RIGHTS_NOT_MODIFIED" in str(e):
            LOGGER.info("Default admin rights already up to date.")
        else:
            LOGGER.warning(f"Could not set default admin rights: {e}")

    try:
        bot.set_bot_info_short_description(config.BOT_SHORT_BIO)
        bot.set_bot_info_description(config.BOT_DESCRIPTION)
        LOGGER.info("Bot bio and description set")
    except Exception as e:
        LOGGER.warning(f"Could not set bot bio/description: {e}")

    # 6. Assistant — started
    try:
        if not assistant.is_connected:
            assistant.start()
        am = assistant.get_me()
        ASSISTANT_USERNAME = am.username or ""
        ASSISTANT_ID = am.id
        LOGGER.info(f"Assistant: @{ASSISTANT_USERNAME or 'N/A'} (id {ASSISTANT_ID})")
    except Exception as e:
        LOGGER.error(f"Assistant start failed: {e}")
        sys.exit(1)

    # 7. PyTgCalls
    call_py.start()
    LOGGER.info("PyTgCalls started")

    # 8. Block middleware
    try:
        from ZaraMusic.utils.decorators import register_block_middleware
        register_block_middleware()
        LOGGER.info("Block middleware registered")
    except Exception as e:
        LOGGER.warning(f"Block middleware load failed: {e}")

    # 9. Load modules
    for mod in ALL_MODULES:
        try:
            importlib.import_module(f"ZaraMusic.modules.{mod}")
            LOGGER.info(f"Loaded module: {mod}")
        except Exception as e:
            LOGGER.error(f"Failed to load module {mod}: {e}")

    # 10. Stream-end handler
    try:
        import ZaraMusic.core.call  # noqa: F401
    except Exception as e:
        LOGGER.error(f"Failed to load call handler: {e}")

    # 11. Notify owner
    loop = asyncio.get_event_loop()
    if config.USE_COOKIES and config.COOKIES_URL:
        from ZaraMusic.utils.youtube import save_cookies
        loop.run_until_complete(save_cookies())
    loop.run_until_complete(_notify_owner(me, ASSISTANT_USERNAME, ASSISTANT_ID))


    # 12. Watchdog
    from ZaraMusic.core.watcher import watchdog
    loop.create_task(watchdog())
    LOGGER.info("Watchdog started")

    LOGGER.info("ZaraMusic is running")

    idle()

    # ── Graceful shutdown ─────────────────────────────────────────────────────
    try:
        bot.stop()
    except Exception:
        pass

    try:
        assistant.stop()
    except Exception:
        pass

    LOGGER.info("✧ ZaraMusic stopped ✧")
