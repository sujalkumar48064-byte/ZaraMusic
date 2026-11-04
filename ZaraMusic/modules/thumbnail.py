from pyrogram import filters
from pyrogram.types import Message

from ZaraMusic import bot
from ZaraMusic.core.channels import target_chat
from ZaraMusic.modules.block import group_allowed, user_allowed
from ZaraMusic.utils.db import is_thumbnail_enabled, set_thumbnail_enabled
from ZaraMusic.utils.language import chat_strings
from ZaraMusic.utils.permissions import is_user_authorized
from richgram import rich_heading, rich_kv_table, rich_note, rich_send


@bot.on_message(
    filters.group
    & filters.command(["thumbnail", "cthumbnail"])
    & group_allowed
    & user_allowed
)
async def thumbnail_cmd(_, message: Message) -> None:

    chat_id = await target_chat(message)
    if chat_id is None:
        return
    lang = chat_strings(chat_id)
    args = [a.lower() for a in message.command[1:]]

    # /thumbnail -> current status
    if not args:
        on = is_thumbnail_enabled(chat_id)
        await rich_send(
            bot, chat_id,
            rich_heading(lang["thumb_title"], level=3)
            + rich_kv_table([
                (lang["autoplay_kv_status"],
                 lang["autoplay_status_on"] if on else lang["autoplay_status_off"]),
            ])
            + rich_note(lang["thumb_status_note"]),
        )
        return

    if not await is_user_authorized(message):
        await rich_send(
            bot, chat_id,
            rich_heading(lang["admin_only_title"], level=3)
            + rich_note(lang["admin_only_note"]),
        )
        return

    who = message.from_user.mention if message.from_user else lang["autoplay_someone"]

    if args[0] in ("on", "enable"):
        set_thumbnail_enabled(chat_id, True)
        await rich_send(
            bot, chat_id,
            rich_heading(lang["thumb_enabled_title"], level=3)
            + rich_note(lang["thumb_enabled_note"].format(who)),
        )
        return

    if args[0] in ("off", "disable"):
        set_thumbnail_enabled(chat_id, False)
        await rich_send(
            bot, chat_id,
            rich_heading(lang["thumb_disabled_title"], level=3)
            + rich_note(lang["thumb_disabled_note"].format(who)),
        )
        return

    await rich_send(
        bot, chat_id,
        rich_heading(lang["thumb_title"], level=3)
        + rich_note(lang["thumb_status_note"]),
    )
