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
    Call,
    MediaStream,
    VideoQuality,
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
    peek_current,
    remove_song,
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
_play_tokens: dict[int, int] = {}
_fail_streak: dict[int, int] = {}


def bump_play_token(chat_id: int) -> int:
    """Invalidate any in-flight play_song() for this chat."""
    token = _play_tokens.get(int(chat_id), 0) + 1
    _play_tokens[int(chat_id)] = token
    return token


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

def _drop_song(chat_id: int, song: dict, keep_file: bool = False) -> None:
    dropped = remove_song(chat_id, song)
    if dropped and not keep_file:
        delete_file(dropped.get("file_path", ""))


def _reset_fail_streak(chat_id: int) -> None:
    _fail_streak.pop(int(chat_id), None)


async def _continue_with_next(chat_id: int, token: int, reason: str) -> None:
    """After a transient play failure, try the next queued song instead of stalling."""
    chat_id = int(chat_id)

    if _play_tokens.get(chat_id) != token:
        return
    if _fail_streak.get(chat_id, 0) >= 2:
        LOGGER.warning(f"[PLAY] {chat_id}: stopping retries after repeated failures")
        return

    nxt = peek_current(chat_id)
    if not nxt:
        return

    _fail_streak[chat_id] = _fail_streak.get(chat_id, 0) + 1
    LOGGER.info(f"[PLAY] {chat_id}: trying next song after {reason}")

    try:
        lang = chat_strings(chat_id)
        msg = await rich_send(
            bot, chat_id,
            rich_heading(lang["next_track_title"], level=3)
            + rich_kv_table([(lang["kv_title"], f"<code>{rich_esc(nxt['title'])}</code>")]),
        )
        await play_song(chat_id, msg, nxt)
    except Exception as e:
        LOGGER.warning(f"[PLAY] {chat_id}: next-song retry failed: {e}")


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
    """Ensure the assistant has VC admin rights; promote it through the bot when possible."""

    chat_id = int(chat_id)

    try:
        me = assistant.me or await assistant.get_me()
        aid = me.id
    except Exception as e:
        LOGGER.warning(f"[VC] assistant identity failed: {e}")
        return False

    async def _lookup_member():
        try:
            member = await assistant.get_chat_member(chat_id, aid)
            if member is not None:
                return member
        except Exception:
            pass
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

    def _has_vc_rights(member) -> bool:
        if member is None:
            return False
        status = str(getattr(member, "status", "")).lower()
        if "owner" in status or "creator" in status:
            return True
        priv = getattr(member, "privileges", None)
        return (
            "administrator" in status
            and bool(getattr(priv, "can_manage_video_chats", False))
        )

    member = await _lookup_member()
    if _has_vc_rights(member):
        return True

    try:
        bot_member = await bot.get_chat_member(chat_id, "me")
    except Exception:
        bot_member = None

    bot_status = str(getattr(bot_member, "status", "")).lower()
    bot_admin = bot_status in ("administrator", "creator", "owner")

    if not bot_admin:
        LOGGER.warning(
            f"[VC] Bot is not admin in {chat_id}; cannot auto-promote assistant. "
            "Make the bot admin with Manage Video Chats + Invite Users."
        )
        # Last resort: if the assistant is already an admin with promote rights,
        # let it try to grant itself the VC right.
        priv = getattr(member, "privileges", None)
        if (
            member is not None
            and "administrator" in str(getattr(member, "status", "")).lower()
            and bool(getattr(priv, "can_promote_members", False))
        ):
            try:
                await assistant.promote_chat_member(
                    chat_id,
                    aid,
                    privileges=ChatAdministratorRights(
                        can_manage_chat=True,
                        can_manage_video_chats=True,
                        can_invite_users=True,
                    ),
                )
                LOGGER.info(f"[VC] Assistant updated its own rights in {chat_id}")
                await asyncio.sleep(1)
                return True
            except Exception as e:
                LOGGER.warning(f"[VC] assistant self-promote failed: {e}")
        return False

    if member is None:
        joined = False
        link = None
        try:
            link = await bot.create_chat_invite_link(chat_id, member_limit=1)
            await assistant.join_chat(link.invite_link)
            joined = True
            LOGGER.info(f"[VC] Assistant joined {chat_id}")
        except Exception as e:
            LOGGER.warning(f"[VC] assistant invite-join failed: {e}")
        finally:
            if link is not None:
                try:
                    await bot.revoke_chat_invite_link(chat_id, link.invite_link)
                except Exception:
                    pass

        if not joined:
            # Public groups can be joined without an invite link.
            try:
                chat = await bot.get_chat(chat_id)
                username = getattr(chat, "username", None)
                if username:
                    await assistant.join_chat(f"@{username}")
                    joined = True
                    LOGGER.info(f"[VC] Assistant joined public group {chat_id}")
            except Exception as e:
                LOGGER.warning(f"[VC] assistant public join failed: {e}")

        await _cache_assistant_peer()
        member = await _lookup_member()
        if _has_vc_rights(member):
            return True

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
        await asyncio.wait_for(_create_vc(chat_id), timeout=15)
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
                timeout=20,
            )
        except Exception:
            fixed = False
    else:
        fixed = False

    if fixed:
        try:
            await asyncio.wait_for(_create_vc(chat_id), timeout=15)
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

    token = bump_play_token(chat_id)

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
        LOGGER.warning(f"[PLAY] {chat_id}: resolve failed: {e}")
        try:
            _drop_song(chat_id, song)
        except Exception:
            pass

        await _fail(
            rich_heading(lang["download_failed_title"], level=3)
            + rich_note(f"<code>{rich_esc(e)}</code>"),
        )
        return

    song["file_path"] = media_path

    if _play_tokens.get(chat_id) != token:
        delete_file(media_path)
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

    if _play_tokens.get(chat_id) != token:
        delete_file(media_path)
        return

    # ─────────────────────────────────────────
    # PLAY STREAM
    # ─────────────────────────────────────────

    played = False

    if _play_tokens.get(chat_id) != token:
        delete_file(media_path)
        return

    # Try joining the existing group call first. Creating a group call while
    # one is already active makes Telegram close the old call, which used to
    # trigger a stream-end/call-left loop and wipe the queue.
    for attempt in range(2):

        if _play_tokens.get(chat_id) != token:
            delete_file(media_path)
            return

        try:

            if is_video:
                stream = MediaStream(
                    media_path,
                    audio_parameters=AudioQuality.HIGH,
                    video_parameters=VideoQuality.SD_480p,
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
            attempt_timeout = (
                config.PLAY_JOIN_TIMEOUT if attempt == 0 else config.PLAY_TIMEOUT
            )
            await asyncio.wait_for(
                call_py.play(chat_id, stream),
                timeout=attempt_timeout,
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
                f"{attempt_timeout}s"
            )

            if attempt == 0:
                try:
                    from ZaraMusic.core.call import mark_leaving
                    mark_leaving(chat_id)
                except Exception:
                    pass
                try:
                    await asyncio.wait_for(
                        call_py.leave_call(chat_id),
                        timeout=10,
                    )
                except Exception:
                    pass
                await asyncio.sleep(1)
                continue

            try:
                _drop_song(chat_id, song, keep_file=True)
            except Exception:
                pass

            await _fail(
                rich_heading(lang["effects_playback_failed_title"], level=3)
                + rich_note("Playback timed out. Please try again."),
            )
            await _continue_with_next(chat_id, token, "play timeout")
            return

        except NoActiveGroupCall:

            if attempt == 0:
                LOGGER.info(f"[VC] NoActiveGroupCall — Creating VC in {chat_id}")
                ok = await _ensure_vc(chat_id, message)

                if ok:
                    continue

                try:
                    _drop_song(chat_id, song, keep_file=True)
                except Exception:
                    pass

                return

        except TelegramServerError as e:
            LOGGER.error(f"[PLAY] TelegramServerError: {e}")

            if attempt == 0:
                await asyncio.sleep(2)
                continue

            try:
                _drop_song(chat_id, song, keep_file=True)
            except Exception:
                pass

            await _fail(
                rich_heading(lang["playback_failed_server_title"], level=3)
                + rich_note(f"<code>{rich_esc(e)}</code>"),
            )
            await _continue_with_next(chat_id, token, "telegram server error")
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
                    _drop_song(chat_id, song, keep_file=True)
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
                    _drop_song(chat_id, song, keep_file=True)
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
                _drop_song(chat_id, song, keep_file=True)
            except Exception:
                pass

            await _fail(
                rich_heading(lang["effects_playback_failed_title"], level=3)
                + rich_note(f"<code>{rich_esc(e)}</code>"),
            )
            LOGGER.error(f"[PLAY ERROR] {e}")
            await _continue_with_next(chat_id, token, "play error")
            return

    if not played:
        return

    # play() returning only means the join request finished. Confirm the
    # stream is actually ACTIVE before telling the user it is playing.
    confirmed = False
    for _ in range(6):
        try:
            active_calls = await asyncio.wait_for(call_py.calls, timeout=5)
            active = active_calls.get(chat_id)
            # For a bot stream the outgoing media is exposed as "capture";
            # "playback" is the incoming side and stays IDLE.
            if active is not None and active.capture == Call.Status.ACTIVE:
                confirmed = True
                break
        except Exception as e:
            LOGGER.warning(f"[PLAY] {chat_id}: active-call check failed: {e}")
        await asyncio.sleep(1)

    LOGGER.info(f"[PLAY] {chat_id}: playback confirmed={confirmed}")

    if not confirmed:
        try:
            calls_now = await asyncio.wait_for(call_py.calls, timeout=5)
            LOGGER.info(
                f"[PLAY] {chat_id}: calls="
                + str([(k, str(v.playback), str(v.capture)) for k, v in calls_now.items()])
            )
        except Exception as e:
            LOGGER.warning(f"[PLAY] {chat_id}: calls diagnostic failed: {e}")

        try:
            await asyncio.wait_for(call_py.leave_call(chat_id), timeout=10)
        except Exception:
            pass
        try:
            _drop_song(chat_id, song)
        except Exception:
            pass
        await _fail(
            rich_heading(lang["effects_playback_failed_title"], level=3)
            + rich_note("Playback did not start. Please try again."),
        )
        await _continue_with_next(chat_id, token, "playback not active")
        return

    try:
        from ZaraMusic.core.call import clear_stream_end_guard
        clear_stream_end_guard(chat_id)
    except Exception:
        pass

    _reset_fail_streak(chat_id)

    if _play_tokens.get(chat_id) != token:
        # A skip/stop landed while call_py.play() was starting. If nothing is
        # queued anymore, make sure the stale stream is torn down.
        if peek_current(chat_id) is None:
            try:
                await asyncio.wait_for(call_py.leave_call(chat_id), timeout=10)
            except Exception:
                pass
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