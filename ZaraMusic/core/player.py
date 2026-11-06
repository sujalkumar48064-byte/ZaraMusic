import asyncio
import os
import random
import time

from pyrogram.enums import ParseMode
from pyrogram.raw.functions.phone import CreateGroupCall
from pyrogram.types import ChatAdministratorRights, Message

from pytgcalls import PyTgCalls
from pytgcalls import filters as fl
from ntgcalls import TelegramServerError
from pytgcalls.exceptions import NoActiveGroupCall
from pytgcalls.types import (
    AudioQuality,
    MediaStream,
    VideoParameters,
    ChatUpdate,
    StreamEnded,
    GroupCallConfig,
    GroupCallParticipant,
    UpdatedGroupCallParticipant,
)

import config

from ZaraMusic import (
    LOGGER,
    assistant,
    bot,
    call_py,
)

from ZaraMusic.core.queue import (
    remove_from_queue,
)

from ZaraMusic.strings import DEFAULT_LANG, get_string
from ZaraMusic.utils.buttons import player_controls_kb, support_updates_pills
from ZaraMusic.utils.db import is_thumbnail_enabled

from ZaraMusic.utils.helpers import delete_file
from ZaraMusic.utils.formatters import (
    parse_dur,
    progress_bar,
    short,
)

from ZaraMusic.utils.language import chat_strings
from ZaraMusic.utils.logs import logger_active
from ZaraMusic.utils.routes import notify_chat, register_panel
from ZaraMusic.utils.thumbnail import thumbnail_html

from richgram import (
    rich_edit,
    rich_esc,
    rich_heading,
    rich_img,
    rich_kv_table,
    rich_note,
    rich_send,
)

from ZaraMusic.utils.youtube import (
    resolve_stream,
    resolve_video_stream,
)

# ─────────────────────────────────────────────
# NOW PLAYING CONTENT
# ─────────────────────────────────────────────


def _now_playing_content(song: dict, lang: dict, show_thumb: bool = True) -> str:
    """Now-playing rich message content (thumbnail only if the chat has it ON)."""

    return (
        rich_heading(
            lang["now_playing_title"],
            level=3
        )
        + thumbnail_html(song, show_thumb)      # picture comes from utils/thumbnail.py
        + rich_kv_table([
            (lang["kv_title"], rich_esc(short(song["title"]))),
            (lang["kv_duration"], rich_esc(song.get("duration", "?"))),
            (lang["kv_by"], rich_esc(song["requester"])),
        ])
        + support_updates_pills(lang)
    )


def _now_playing_kb(elapsed: float, total: float, chat_id=None, lang=None) -> str:
    """Player buttons as rich HTML — append to the message content."""
    return player_controls_kb(elapsed, total, chat_id, lang)


_panel_content: dict = {}


def get_panel_content(chat_id: int, message_id: int):
    return _panel_content.get((chat_id, message_id))


# ─────────────────────────────────────────────
# PROGRESS UPDATER
# ─────────────────────────────────────────────


_closed_panels: set = set()
_progress_tasks: dict = {}


def mark_panel_closed(chat_id: int, message_id: int) -> None:
    _closed_panels.add((chat_id, message_id))
    _panel_content.pop((chat_id, message_id), None)
    task = _progress_tasks.pop((chat_id, message_id), None)
    if task and not task.done():
        task.cancel()


async def _update_progress(
    chat_id: int,
    msg: Message,
    start_t: float,
    total: float,
    content: str,
) -> None:

    while True:

        if (chat_id, msg.id) in _closed_panels:
            _closed_panels.discard((chat_id, msg.id))
            break

        elapsed = min(time.time() - start_t, total)
        kb = _now_playing_kb(elapsed, total, chat_id, chat_strings(chat_id))

        try:
            await rich_edit(msg, content + kb)

        except Exception as e:
            if "MESSAGE_NOT_MODIFIED" not in str(e):
                break

        if elapsed >= total:
            break

        await asyncio.sleep(18)


# ─────────────────────────────────────────────
# AUTO START VC
# ─────────────────────────────────────────────

def _drop_current(chat_id: int) -> None:
    song = remove_from_queue(chat_id, 0)
    if song:
        delete_file(song.get("file_path", ""))


async def _fail_status(chat_id: int, status_message, content: str) -> None:

    if status_message is not None:
        try:
            await rich_edit(status_message, content)
            return
        except Exception:
            pass

    try:
        await rich_send(bot, chat_id, content)
    except Exception:
        pass


async def _ensure_assistant_admin(chat_id: int) -> bool:
    """Make the assistant a group admin so it can start the voice chat."""

    try:
        me = assistant.me or await assistant.get_me()
        aid = me.id
    except Exception as e:
        LOGGER.warning(f"[VC] assistant identity failed: {e}")
        return False

    try:
        bot_member = await bot.get_chat_member(chat_id, "me")
        bot_status = str(getattr(bot_member, "status", "")).lower()
        if bot_status not in ("administrator", "creator", "owner"):
            LOGGER.info(f"[VC] Bot is not admin in {chat_id}; cannot promote assistant")
            return False
    except Exception:
        pass

    async def _lookup_member():
        try:
            return await bot.get_chat_member(chat_id, aid)
        except Exception:
            return None

    async def _cache_assistant_peer():
        # Bots can only resolve a raw user id after seeing that user's access hash.
        try:
            query = me.first_name or ""
            async for found in bot.get_chat_members(chat_id, query=query, limit=20):
                if getattr(getattr(found, "user", None), "id", None) == aid:
                    break
        except Exception as e:
            LOGGER.warning(f"[VC] assistant lookup failed: {e}")

    member = await _lookup_member()
    if member is None:
        await _cache_assistant_peer()
        member = await _lookup_member()

    if member is not None:
        status = str(getattr(member, "status", "")).lower()
        priv = getattr(member, "privileges", None)
        if "owner" in status:
            return True
        if "administrator" in status and getattr(priv, "can_manage_video_chats", False):
            return True

    if member is None:
        link = None
        try:
            link = await bot.create_chat_invite_link(chat_id, member_limit=1)
            await assistant.join_chat(link.invite_link)
            LOGGER.info(f"[VC] Assistant joined {chat_id}")
        except Exception as e:
            LOGGER.warning(f"[VC] assistant join failed: {e}")
        finally:
            if link is not None:
                try:
                    await bot.revoke_chat_invite_link(chat_id, link.invite_link)
                except Exception:
                    pass
        await _cache_assistant_peer()

    try:
        await bot.promote_chat_member(
            chat_id,
            aid,
            privileges=ChatAdministratorRights(
                can_manage_chat=True,
                can_manage_video_chats=True,
                can_invite_users=True,
            ),
        )
        LOGGER.info(f"[VC] Assistant promoted in {chat_id}")
        await asyncio.sleep(1)
        return True
    except Exception as e:
        LOGGER.warning(f"[VC] assistant promote failed: {e}")
        return False


async def _create_vc(chat_id: int) -> None:
    chat = await assistant.get_chat(chat_id)
    await assistant.invoke(
        CreateGroupCall(
            peer=await assistant.resolve_peer(chat.id),
            random_id=random.randint(10000, 99999),
        )
    )


async def _ensure_vc(chat_id: int, status_message=None) -> bool:

    lang = chat_strings(chat_id)
    chat_id = int(chat_id)
    last_err = None

    try:
        await asyncio.wait_for(_create_vc(chat_id), timeout=25)
        LOGGER.info(f"[VC] Created in {chat_id}")
        await asyncio.sleep(1)
        return True
    except Exception as e:
        last_err = e
        err = str(e).lower()
        if "already" in err or "groupcall_already_started" in err:
            LOGGER.info(f"[VC] Already active in {chat_id}")
            return True

    err = str(last_err or "").lower()
    need_fix = any(
        x in err
        for x in (
            "admin",
            "participant",
            "channel_private",
            "not_in_chat",
        )
    )

    if need_fix:
        try:
            fixed = await asyncio.wait_for(
                _ensure_assistant_admin(chat_id),
                timeout=35,
            )
        except Exception:
            fixed = False
    else:
        fixed = False

    if fixed:
        try:
            await asyncio.wait_for(_create_vc(chat_id), timeout=25)
            LOGGER.info(f"[VC] Created in {chat_id} after assistant setup")
            await asyncio.sleep(1)
            return True
        except Exception as e:
            last_err = e
            err = str(e).lower()
            if "already" in err or "groupcall_already_started" in err:
                LOGGER.info(f"[VC] Already active in {chat_id}")
                return True
            LOGGER.warning(f"[VC] create after assistant setup failed: {e}")

    err = str(last_err or "").lower()

    if "chat_admin_required" in err or "admin" in err:
        await _fail_status(
            chat_id,
            status_message,
            rich_heading(lang["vc_perm_missing_title"], level=3)
            + rich_note(lang["vc_perm_missing_note"]),
        )
        return False

    err_text = (
        "Voice chat start timed out"
        if isinstance(last_err, asyncio.TimeoutError)
        else str(last_err)
    )
    await _fail_status(
        chat_id,
        status_message,
        rich_heading(lang["vc_start_failed_title"], level=3)
        + rich_note(f"<code>{rich_esc(err_text)}</code>"),
    )
    return False


# ─────────────────────────────────────────────
# MAIN PLAY FUNCTION
# ─────────────────────────────────────────────

async def play_song(
    chat_id: int,
    message: Message,
    song: dict,
) -> None:

    chat_id = int(chat_id)
    lang = chat_strings(chat_id)
    url = song.get("url")

    if not url:
        return

    loading_text = (
        rich_heading(lang["loading_title"], level=3)
        + rich_kv_table([(lang["kv_song"], rich_esc(short(song['title'])))])
    )

    try:
        await rich_edit(message, loading_text)

    except Exception:
        message = await rich_send(bot, chat_id, loading_text)

    async def _fail(content: str) -> None:
        if message is not None:
            try:
                await rich_edit(message, content)
                return
            except Exception:
                pass
        try:
            await rich_send(bot, chat_id, content)
        except Exception:
            pass

    # ─────────────────────────────────────────
    # RESOLVE STREAM
    # ─────────────────────────────────────────

    is_video = song.get("video", False)

    try:
        if is_video:
            media_path = await resolve_video_stream(url)
        else:
            media_path = await resolve_stream(url)

    except Exception as e:
        try:
            _drop_current(chat_id)
        except Exception:
            pass

        await _fail(
            rich_heading(lang["download_failed_title"], level=3)
            + rich_note(f"<code>{rich_esc(e)}</code>"),
        )
        return

    try:
        size = os.path.getsize(media_path)
    except OSError:
        size = 0
    LOGGER.info(
        f"[PLAY] {chat_id}: resolved {'video' if is_video else 'audio'} "
        f"{media_path} ({size} bytes)"
    )

    # ─────────────────────────────────────────
    # AUTO EFFECTS
    # ─────────────────────────────────────────

    if not is_video:
        try:
            from ZaraMusic.modules.effects import maybe_apply_effects
            media_path = await maybe_apply_effects(chat_id, media_path)

        except Exception as fx_err:
            LOGGER.warning(f"[Effects] Skipped: {fx_err}")

    song["file_path"] = media_path

    # ─────────────────────────────────────────
    # PLAY STREAM
    # ─────────────────────────────────────────

    played = False

    # Ensure the voice chat exists before asking PyTgCalls to play.
    # This avoids a long hang when the assistant has no VC-admin rights.
    try:
        active_calls = await asyncio.wait_for(call_py.calls, timeout=10)
    except Exception:
        active_calls = {}

    if chat_id not in active_calls:
        LOGGER.info(f"[VC] Not connected — ensuring VC for {chat_id}")
        if not await _ensure_vc(chat_id, message):
            try:
                _drop_current(chat_id)
            except Exception:
                pass
            return

    for attempt in range(2):

        try:

            if is_video:
                stream = MediaStream(
                    media_path,
                    audio_parameters=AudioQuality.HIGH,
                    video_parameters=VideoParameters(
                        854,
                        480,
                        max(15, min(config.VIDEO_FPS, 30)),
                        adjust_by_height=False,
                    ),
                )
            else:
                stream = MediaStream(
                    media_path,
                    audio_parameters=AudioQuality.HIGH,
                    video_flags=MediaStream.Flags.IGNORE,
                )

            LOGGER.info(
                f"[PLAY] {chat_id}: calling play "
                f"({'video' if is_video else 'audio'})"
            )
            started = time.time()
            await asyncio.wait_for(
                call_py.play(chat_id, stream),
                timeout=config.PLAY_TIMEOUT,
            )
            LOGGER.info(
                f"[PLAY] {chat_id}: play returned in "
                f"{time.time() - started:.1f}s"
            )

            played = True
            break

        except asyncio.TimeoutError:
            LOGGER.error(
                f"[PLAY] {chat_id}: play() timed out after "
                f"{config.PLAY_TIMEOUT}s"
            )

            if attempt == 0:
                try:
                    await asyncio.wait_for(
                        call_py.leave_call(chat_id),
                        timeout=10,
                    )
                except Exception:
                    pass
                await asyncio.sleep(2)
                continue

            try:
                _drop_current(chat_id)
            except Exception:
                pass

            await _fail(
                rich_heading(lang["effects_playback_failed_title"], level=3)
                + rich_note("Playback timed out. Please try again."),
            )
            return

        except NoActiveGroupCall:

            if attempt == 0:
                LOGGER.info(f"[VC] NoActiveGroupCall — Creating VC in {chat_id}")
                ok = await _ensure_vc(chat_id, message)

                if ok:
                    continue

                try:
                    _drop_current(chat_id)
                except Exception:
                    pass

                return

        except TelegramServerError as e:
            LOGGER.error(f"[PLAY] TelegramServerError: {e}")

            if attempt == 0:
                await asyncio.sleep(2)
                continue

            try:
                _drop_current(chat_id)
            except Exception:
                pass

            await _fail(
                rich_heading(lang["playback_failed_server_title"], level=3)
                + rich_note(f"<code>{rich_esc(e)}</code>"),
            )
            return

        except Exception as e:

            err = str(e).lower()

            vc_missing = any(
                x in err
                for x in (
                    "groupcallnotfound",
                    "not_in_group_call",
                    "groupcall_forbidden",
                    "not in group call",
                    "no active group call",
                )
            )

            # auto create vc (string-based fallback)
            if vc_missing and attempt == 0:
                LOGGER.info(f"[VC] Creating VC in {chat_id}")
                ok = await _ensure_vc(chat_id, message)

                if ok:
                    continue

                try:
                    _drop_current(chat_id)
                except Exception:
                    pass

                return

            # admin permission error — try creating the VC with the other client
            if "chat_admin_required" in err or "admin" in err:
                if attempt == 0:
                    LOGGER.info(f"[VC] Admin required — trying VC create in {chat_id}")
                    ok = await _ensure_vc(chat_id, message)

                    if ok:
                        continue

                try:
                    _drop_current(chat_id)
                except Exception:
                    pass

                await _fail(
                    rich_heading(lang["vc_perm_missing_title"], level=3)
                    + rich_note(lang["playback_perm_missing_note"]),
                )
                LOGGER.error(f"[ADMIN ERROR] {e}")
                return

            # generic error
            try:
                _drop_current(chat_id)
            except Exception:
                pass

            await _fail(
                rich_heading(lang["effects_playback_failed_title"], level=3)
                + rich_note(f"<code>{rich_esc(e)}</code>"),
            )
            LOGGER.error(f"[PLAY ERROR] {e}")
            return

    if not played:
        return

    try:
        from ZaraMusic.core.autoplay import schedule_prefetch
        schedule_prefetch(chat_id, song)
    except Exception:
        pass

    # ─────────────────────────────────────────
    # RESET SEEK
    # ─────────────────────────────────────────

    try:
        from ZaraMusic.modules.seek import set_seek_state
        set_seek_state(chat_id, 0)
    except Exception:
        pass

    # ─────────────────────────────────────────
    # DATABASE TRACKING
    # ─────────────────────────────────────────

    try:
        from ZaraMusic.database import (
            add_served_chat,
            add_served_user,
            increment_play_count,
        )

        add_served_chat(notify_chat(chat_id))      # never put a channel in the broadcast list
        requester_id = song.get("requester_id")

        if requester_id:
            add_served_user(requester_id)

        increment_play_count(chat_id)

    except Exception as db_err:
        LOGGER.warning(f"[DB ERROR] {db_err}")



    total = parse_dur(song.get("duration", "0:00"))
    content = _now_playing_content(song, lang, is_thumbnail_enabled(chat_id))
    kb = _now_playing_kb(0, total, chat_id, lang)

    try:
        pmsg = await rich_edit(message, content + kb)
        if pmsg is None:
            pmsg = message
    except Exception:
        pmsg = await rich_send(bot, chat_id, content + kb)

    _panel_content[(chat_id, pmsg.id)] = content
    # channel play: the panel lives in the group but its buttons drive the channel VC
    register_panel(pmsg.chat.id, pmsg.id, chat_id)
    LOGGER.info(f"[PLAY] {chat_id}: panel updated")

    for key, task in list(_progress_tasks.items()):
        if key[0] == chat_id and not task.done():
            task.cancel()
            _progress_tasks.pop(key, None)

    progress = asyncio.create_task(
        _update_progress(
            chat_id,
            pmsg,
            time.time(),
            total,
            content,
        )
    )
    _progress_tasks[(chat_id, pmsg.id)] = progress
    progress.add_done_callback(
        lambda _t, key=(chat_id, pmsg.id): _progress_tasks.pop(key, None)
    )

    # ─────────────────────────────────────────
    # LOGGER
    # ─────────────────────────────────────────

    if logger_active():
        en = get_string(DEFAULT_LANG)
        logger_content = (
            rich_heading(en["logger_nowplaying_title"], level=3)
            + rich_kv_table([
                (en["kv_title"], rich_esc(song.get("title", "?"))),
                (en["kv_duration"], rich_esc(song.get("duration", "?"))),
                (en["kv_by"], rich_esc(song.get("requester", "?"))),
                (en["kv_chat_name"], rich_esc(await _chat_name(chat_id, message))),
                (en["kv_chat_id"], f"<code>{chat_id}</code>"),
            ])
        )

        asyncio.create_task(
            rich_send(
                bot,
                config.LOGGER_ID,
                logger_content,
            )
        )


async def _chat_name(chat_id: int, message=None) -> str:
    """Best-effort chat title for the log message (group title / user's name)."""
    chat = getattr(message, "chat", None)
    name = getattr(chat, "title", None) or getattr(chat, "first_name", None)
    # channel play: the panel message sits in the group, but the log should name the channel
    if name and getattr(chat, "id", chat_id) == chat_id:
        return str(name)
    try:
        chat = await bot.get_chat(chat_id)
        return str(getattr(chat, "title", None) or getattr(chat, "first_name", None) or chat_id)
    except Exception:
        return str(chat_id)