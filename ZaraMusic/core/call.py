import asyncio
import time

from pytgcalls import filters as fl
from ntgcalls import TelegramServerError
from pytgcalls.exceptions import NoActiveGroupCall
from pytgcalls.types import (
    ChatUpdate,
    StreamEnded,
)

from ZaraMusic import LOGGER, bot, call_py
from ZaraMusic.core.queue import clear_queue, peek_current, pop_current, queue_lock, queue_size
from ZaraMusic.utils.helpers import delete_file
from ZaraMusic.utils.language import chat_strings
from richgram import rich_esc, rich_heading, rich_kv_table, rich_note, rich_send


_leaving: set[int] = set()
_last_stream_end: dict[int, float] = {}
_stream_end_guard: dict[int, float] = {}


def mark_stream_end_guard(chat_id: int, seconds: float = 30.0) -> None:
    """Ignore stream-end events while a stream is being replaced."""
    _stream_end_guard[int(chat_id)] = time.monotonic() + seconds


def clear_stream_end_guard(chat_id: int) -> None:
    _stream_end_guard.pop(int(chat_id), None)


def mark_leaving(chat_id: int) -> None:
    """Suppress the matching call-left update after an intentional leave."""
    cid = int(chat_id)
    _leaving.add(cid)
    mark_stream_end_guard(cid, 5.0)

    async def _clear():
        await asyncio.sleep(3)
        _leaving.discard(cid)

    try:
        asyncio.create_task(_clear())
    except RuntimeError:
        _leaving.discard(cid)


async def leave_vc(chat_id: int) -> None:
    """
    Leave voice chat and clean the queue (AutoPlay's ON/OFF setting is kept).
    """

    try:
        from ZaraMusic.core.player import bump_play_token
        bump_play_token(chat_id)
    except Exception:
        pass

    # Drop pending autoplay suggestions, but keep the ON/OFF setting: once
    # an admin turns AutoPlay on it stays on until they turn it off.
    try:
        from ZaraMusic.core.autoplay import reset_autoplay_state
        reset_autoplay_state(chat_id)
    except Exception:
        pass

    # Delete queued files
    for song in clear_queue(chat_id):
        try:
            delete_file(song.get("file_path", ""))
        except Exception:
            pass

    mark_leaving(chat_id)
    try:
        await call_py.leave_call(chat_id)

    except NoActiveGroupCall:
        pass

    except TelegramServerError as e:
        LOGGER.error(f"Leave VC TelegramServerError: {e}")

    except Exception as e:
        LOGGER.error(f"Leave VC Error: {e}")


@call_py.on_update(fl.chat_update(ChatUpdate.Status.LEFT_CALL))
async def on_call_left(_: object, update: ChatUpdate) -> None:
    """
    The voice chat ended from OUTSIDE our own leave_vc() flow — someone
    ended it from Telegram's UI, the assistant got kicked, Telegram
    dropped the call, etc. (KICKED / LEFT_GROUP / CLOSED_VOICE_CHAT /
    DISCARDED_CALL / BUSY_CALL — see pytgcalls' ChatUpdate.Status).

    No stream is left to ever fire `stream_end` for, so without this the
    queue keeps thinking a song is still "playing": /play just appends
    behind that ghost entry and nothing plays again until the bot is
    restarted. Clearing state here means the next /play starts clean.
    """

    chat_id = int(update.chat_id)

    # A call-left update can arrive after we already started a new call (the
    # old call is being torn down by Telegram). Do not let that stale event
    # wipe the new queue/play token.
    await asyncio.sleep(2)

    if chat_id in _leaving:
        _leaving.discard(chat_id)
        return

    try:
        active_calls = await asyncio.wait_for(call_py.calls, timeout=5)
    except Exception:
        active_calls = {}

    if chat_id in active_calls:
        LOGGER.info(f"[VC] Ignoring stale call-left for active call in {chat_id}")
        return

    LOGGER.warning(f"[VC] Call ended externally in {chat_id} ({update.status}) — clearing state")

    try:
        from ZaraMusic.core.player import bump_play_token
        bump_play_token(chat_id)
    except Exception:
        pass

    for song in clear_queue(chat_id):
        try:
            delete_file(song.get("file_path", ""))
        except Exception:
            pass

    try:
        from ZaraMusic.core.autoplay import reset_autoplay_state
        reset_autoplay_state(chat_id)
    except Exception:
        pass

    try:
        lang = chat_strings(chat_id)
        await rich_send(
            bot, chat_id,
            rich_heading(lang["vc_ended_title"], level=3)
            + rich_note(lang["vc_ended_note"]),
        )
    except Exception:
        pass


@call_py.on_update(fl.stream_end())
async def on_stream_end(_: object, update: StreamEnded) -> None:
    """
    Automatically play the next song when the current stream ends.
    If the queue is empty, AutoPlay (when ON) adds one related song
    before we give up and leave the voice chat.
    """

    chat_id = update.chat_id
    lang = chat_strings(chat_id)

    # Telegram can deliver duplicate/stale stream-end updates while the next
    # track is being resolved. Ignoring the duplicate prevents it from
    # popping the next queue item and starting two streams at once.
    now = time.monotonic()
    if now < _stream_end_guard.get(chat_id, 0.0):
        LOGGER.info(f"[VC] Ignoring stream-end during stream replacement in {chat_id}")
        return
    if now - _last_stream_end.get(chat_id, 0.0) < 2.0:
        LOGGER.info(f"[VC] Ignoring duplicate stream-end in {chat_id}")
        return
    _last_stream_end[chat_id] = now

    # Remove finished song
    async with queue_lock(chat_id):
        done = pop_current(chat_id)
        nxt = peek_current(chat_id)

    if done:
        await asyncio.sleep(1)

        try:
            delete_file(done.get("file_path", ""))

        except Exception:
            pass

    # ── Queue is empty: let AutoPlay try to add ONE related song ────────────────
    if not nxt:
        try:
            from ZaraMusic.core.autoplay import autoplay_next

            if await autoplay_next(chat_id, done):
                nxt = peek_current(chat_id)

        except Exception as ap_err:
            LOGGER.warning(f"[AutoPlay] autoplay_next error: {ap_err}")

    # ── Play next song ───────────────────────────────────────────────────────
    if nxt:

        from ZaraMusic.core.player import play_song

        # start getting the *next* suggestion ready while this song plays
        try:
            from ZaraMusic.core.autoplay import schedule_prefetch
            schedule_prefetch(chat_id, nxt)
        except Exception:
            pass

        try:
            msg = await rich_send(
                bot, chat_id,
                rich_heading(lang["next_track_title"], level=3)
                + rich_kv_table([(lang["kv_title"], f"<code>{rich_esc(nxt['title'])}</code>")]),
            )

            await play_song(chat_id, msg, nxt)

        except (NoActiveGroupCall, TelegramServerError) as e:
            LOGGER.error(f"Next Song VC Error: {e}")

        except Exception as e:
            LOGGER.error(f"Next Song Error: {e}")

            await rich_send(
                bot, chat_id,
                rich_heading(lang["generic_error_title"], level=3)
                + rich_note(f"<code>{rich_esc(e)}</code>"),
            )

        return

    # ── Queue completely finished (AutoPlay OFF or nothing usable found) ───────
    await leave_vc(chat_id)

    await rich_send(
        bot, chat_id,
        rich_heading(lang["queue_finished_title"], level=3)
        + rich_note(lang["queue_finished_note"]),
    )

