"""
Assistant utility functions.
Handles checking whether the assistant is in a group and auto-joining it.
Previously this logic was inline in play.py — now centralised here.
"""

import asyncio

from pyrogram.errors import RPCError, UserAlreadyParticipant
from pyrogram.types import Message

from ZaraMusic import assistant, bot
from richgram import rich_edit, rich_esc, rich_heading, rich_note


async def is_assistant_in(chat_id: int):
    # Prefer the bot client for the membership lookup: it is the bot's own
    # control connection and does not depend on the assistant userbot being
    # responsive at that instant.
    me = getattr(assistant, "me", None)
    assistant_id = getattr(me, "id", None)
    if assistant_id:
        try:
            member = await asyncio.wait_for(
                bot.get_chat_member(chat_id, assistant_id), timeout=8
            )
            return member.status is not None
        except Exception as e:
            err = str(e)
            if "USER_BANNED" in err or "Banned" in err:
                return "banned"

    try:
        me = await asyncio.wait_for(assistant.get_me(), timeout=5)
        member = await asyncio.wait_for(
            assistant.get_chat_member(chat_id, me.id), timeout=8
        )
        return member.status is not None

    except Exception as e:
        err = str(e)
        if "USER_BANNED" in err or "Banned" in err:
            return "banned"
        return False


async def try_join_assistant(chat_id: int, pm: Message) -> bool:
    invite_link = None

    try:
        invite_link = await asyncio.wait_for(
            bot.export_chat_invite_link(chat_id), timeout=10
        )
    except Exception as e:
        # Public groups can be joined without admin invite-link permission.
        try:
            chat = await bot.get_chat(chat_id)
            username = getattr(chat, "username", None)
            if username:
                await asyncio.wait_for(
                    assistant.join_chat(f"@{username}"), timeout=15
                )
                await asyncio.sleep(2)
                return True
        except Exception:
            pass

        await rich_edit(
            pm,
            rich_heading("❍ ʙᴏᴛ ᴀᴅᴍɪɴ ɴᴇᴇᴅᴇᴅ", level=3)
            + rich_note(
                "<p>ᴍᴀᴋᴇ ᴛʜᴇ <b>ʙᴏᴛ</b> ᴀᴅᴍɪɴ ᴡɪᴛʜ "
                "<b>ɪɴᴠɪᴛᴇ ᴜsᴇʀs</b>, ᴏʀ ᴀᴅᴅ ᴛʜᴇ ᴀssɪsᴛᴀɴᴛ "
                "ᴛᴏ ᴛʜɪs ɢʀᴏᴜᴘ ᴍᴀɴᴜᴀʟʟʏ.</p>"
                "<p>ᴀssɪsᴛᴀɴᴛ ᴀᴅᴍɪɴ ɪs ɴᴏᴛ ʀᴇǫᴜɪʀᴇᴅ ғᴏʀ ᴀɴ "
                "ᴇxɪsᴛɪɴɢ ᴠᴏɪᴄᴇ ᴄʜᴀᴛ.</p>"
                f"<p><code>{rich_esc(e)}</code></p>"
            ),
        )
        return False

    try:
        # Normalise joinchat link format
        if invite_link.startswith("https://t.me/+"):
            invite_link = invite_link.replace(
                "https://t.me/+",
                "https://t.me/joinchat/",
            )

        await asyncio.wait_for(assistant.join_chat(invite_link), timeout=15)
        await asyncio.sleep(2)
        return True

    except UserAlreadyParticipant:
        return True

    except RPCError as e:
        # Try the public username as a final fallback.
        try:
            chat = await bot.get_chat(chat_id)
            username = getattr(chat, "username", None)
            if username:
                await asyncio.wait_for(
                    assistant.join_chat(f"@{username}"), timeout=15
                )
                await asyncio.sleep(2)
                return True
        except Exception:
            pass

        await rich_edit(
            pm,
            rich_heading("❍ ᴀssɪsᴛᴀɴᴛ ᴊᴏɪɴ ғᴀɪʟᴇᴅ", level=3)
            + rich_note(f"<code>{rich_esc(e)}</code>"),
        )
        return False

    except Exception as e:
        await rich_edit(
            pm,
            rich_heading("❍ ᴊᴏɪɴ ᴇʀʀᴏʀ", level=3)
            + rich_note(f"<code>{rich_esc(e)}</code>"),
        )
        return False
