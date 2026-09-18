from app.config import (
    ADMIN_CHAT_ID,
    NOTIFY_ON_MENTION,
    NOTIFY_ON_REPLY,
    NOTIFY_ON_WIN,
)
from app.admin.bot import send_admin_message

_user_client = None
_ME = None


async def start_user_client(client) -> None:
    global _user_client, _ME
    _user_client = client
    _ME = await client.get_me()


async def incoming_message(event) -> None:
    if not _user_client or not _ME or not ADMIN_CHAT_ID:
        return

    message = event.message
    text = event.raw_text or ""

    if NOTIFY_ON_REPLY and message.is_reply and message.reply_to_msg_id:
        try:
            replied_to = await message.get_reply_message()
        except Exception:
            replied_to = None

        if replied_to is not None and getattr(replied_to, "sender_id", None) == _ME.id:
            await send_admin_message(
                f"↩️ Ответ на наше сообщение\n"
                f"chat_id={event.chat_id}, message_id={event.id}\n\n"
                f"{text[:3000]}"
            )

    if NOTIFY_ON_MENTION and getattr(message, "mentioned", False):
        await send_admin_message(
            f"🔔 Нас отметили\n"
            f"chat_id={event.chat_id}, message_id={event.id}\n\n"
            f"{text[:3000]}"
        )


async def giveaway_detected(event, parsed: dict) -> None:
    if not ADMIN_CHAT_ID:
        return

    codes = parsed.get("action_codes") or [
        action.get("code") for action in parsed.get("actions", [])
    ]
    await send_admin_message(
        f"🎁 Розыгрыш найден\n"
        f"channel={parsed.get('channel_username') or '-'}\n"
        f"chat_id={event.chat_id}, message_id={event.id}\n"
        f"detected={parsed.get('detected')}\n"
        f"confidence={parsed.get('confidence')}\n"
        f"action_codes={codes}\n"
        f"write_sequence_id={parsed.get('write_sequence_id') or '-'}\n"
        f"needs_human={parsed.get('needs_human')}\n"
        f"reason={parsed.get('reason') or '-'}"
    )


async def participation_result(event, parsed: dict, result: str) -> None:
    if not ADMIN_CHAT_ID:
        return
    await send_admin_message(
        f"🤖 Участие: {result}\n"
        f"chat_id={event.chat_id}, message_id={event.id}\n"
        f"channel={parsed.get('channel_username') or '-'}\n"
        f"action_codes={parsed.get('action_codes') or []}"
    )


async def notify_win(text: str) -> None:
    if NOTIFY_ON_WIN and ADMIN_CHAT_ID:
        await send_admin_message(f"🏆 Возможная победа\n\n{text[:3500]}")
