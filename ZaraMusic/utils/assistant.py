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
    try:
        me     = await assistant.get_me()
        member = await assistant.get_chat_member(chat_id, me.id)
        return member.status is not None

    except Exception as e:
        err = str(e)
        if "USER_BANNED" in err or "Banned" in err:
            return "banned"
        return False


async def try_join_assistant(chat_id: int, pm: Message) -> bool:
    invite_link = None

    try:
        invite_link = await bot.export_chat_invite_link(chat_id)
    except Exception as e:
        # Public groups can be joined without admin invite-link permission.
        try:
            chat = await bot.get_chat(chat_id)
            username = getattr(chat, "username", None)
            if username:
                await assistant.join_chat(f"@{username}")
                await asyncio.sleep(2)
                return True
        except Exception:
            pass

        await rich_edit(
            pm,
            rich_heading("❍ ʙᴏᴛ ᴀᴅᴍɪɴ ɴᴇᴇᴅᴇᴅ", level=3)
            + rich_note(
                "<p>ᴍᴀᴋᴇ ᴛʜᴇ <b>ʙᴏᴛ</b> ᴀᴅᴍɪɴ ᴡɪᴛʜ "
                "<b>ɪɴᴠɪᴛᴇ ᴜsᴇʀs</b> + <b>ᴍᴀɴᴀɢᴇ ᴠɪᴅᴇᴏ ᴄʜᴀᴛs</b>.</p>"
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

        await assistant.join_chat(invite_link)
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
                await assistant.join_chat(f"@{username}")
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
