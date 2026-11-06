from typing import Union

from pyrogram.enums import ChatMemberStatus, ChatType
from pyrogram.types import CallbackQuery, Message

import config

TRUSTED_IDS: set[int] = {777000, config.OWNER_ID}


async def is_user_authorized(obj: Union[Message, CallbackQuery]) -> bool:
    """
    Returns True if the user is:
    - Bot owner or Telegram system account (777000)
    - A group admin or owner
    """
    if isinstance(obj, CallbackQuery):
        message = obj.message
        user    = obj.from_user
    elif isinstance(obj, Message):
        message = obj
        user    = obj.from_user
    else:
        return False

    if user and user.id in TRUSTED_IDS:
        return True

    if message.chat.type not in (ChatType.SUPERGROUP, ChatType.GROUP, ChatType.CHANNEL):
        return False

    if not user:
        # Anonymous group admins and channel posts have no from_user. Telegram
        # identifies them with sender_chat, which is the chat itself.
        sender_chat = getattr(message, "sender_chat", None)
        return bool(sender_chat and sender_chat.id == message.chat.id)

    try:
        member = await message._client.get_chat_member(message.chat.id, user.id)
        return member.status in (ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR)
    except Exception:
        return False
