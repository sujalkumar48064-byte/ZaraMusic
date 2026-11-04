"""
Utility commands:
  /id    — get IDs of message / user / chat / replied message
"""

from pyrogram import filters
from pyrogram.types import Message

from ZaraMusic import bot
from ZaraMusic.modules.block import user_allowed
from richgram import (
    rich_heading,
    rich_kv_table,
    rich_reply,
)


# ── /id ────────────────────────────────────────────────────────────────────────

@bot.on_message(filters.command("id") & user_allowed)
async def id_cmd(client, message: Message) -> None:

    chat       = message.chat
    your_id    = message.from_user.id if message.from_user else "N/A"
    message_id = message.id
    reply      = message.reply_to_message

    # ── Base rows ──────────────────────────────────────────────────────────────
    rows = [
        ("<a href='{}'>ᴍᴇssᴀɢᴇ ɪᴅ</a>".format(message.link), f"<code>{message_id}</code>"),
        (f"<a href='tg://user?id={your_id}'>ʏᴏᴜʀ ɪᴅ</a>", f"<code>{your_id}</code>"),
    ]

    # ── Optional: lookup another user by username or ID ───────────────────────
    args = message.command[1:]
    if args:
        try:
            target    = await client.get_users(args[0])
            target_id = target.id
            rows.append((f"<a href='tg://user?id={target_id}'>ᴜsᴇʀ ɪᴅ</a>", f"<code>{target_id}</code>"))
        except Exception:
            await rich_reply(
                message,
                rich_heading("❍ ᴜsᴇʀ ɴᴏᴛ ғᴏᴜɴᴅ", level=3),
            )
            return

    # ── Chat ID ────────────────────────────────────────────────────────────────
    if chat.username:
        chat_link = f"https://t.me/{chat.username}"
    else:
        chat_link = f"tg://user?id={chat.id}"

    rows.append((f"<a href='{chat_link}'>ᴄʜᴀᴛ ɪᴅ</a>", f"<code>{chat.id}</code>"))

    # ── Replied message ────────────────────────────────────────────────────────
    if reply and not getattr(reply, "empty", True):

        # Replied user ID
        if reply.from_user and not getattr(reply, "sender_chat", None):
            rows.append((f"<a href='{reply.link}'>ʀᴇᴘʟɪᴇᴅ ᴍsɢ ɪᴅ</a>", f"<code>{reply.id}</code>"))
            rows.append((
                f"<a href='tg://user?id={reply.from_user.id}'>ʀᴇᴘʟɪᴇᴅ ᴜsᴇʀ</a>",
                f"<code>{reply.from_user.id}</code>",
            ))

        # Forwarded from channel
        fwd_chat = getattr(reply, "forward_from_chat", None)
        if fwd_chat:
            rows.append(("ғᴡᴅ ᴄʜᴀɴɴᴇʟ", fwd_chat.title))
            rows.append(("ғᴡᴅ ᴄʜᴀɴɴᴇʟ ɪᴅ", f"<code>{fwd_chat.id}</code>"))

        # Sender chat (anonymous group admin / channel post)
        sender_chat = getattr(reply, "sender_chat", None)
        if sender_chat:
            rows.append(("sᴇɴᴅᴇʀ ᴄʜᴀᴛ", sender_chat.title))
            rows.append(("sᴇɴᴅᴇʀ ᴄʜᴀᴛ ɪᴅ", f"<code>{sender_chat.id}</code>"))

    content = (
        rich_heading("❍ ɪᴅ ɪɴғᴏ", level=3)
        + rich_kv_table(rows)
    )

    await rich_reply(message, content)

