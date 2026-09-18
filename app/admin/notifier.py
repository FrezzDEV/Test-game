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
        await send_admin_message(
            f"↩️ Ответ на наше сообщение
"
            f"chat_id={event.chat_id}, message_id={event.id}

{text[:3000]}"
        )

    if NOTIFY_ON_MENTION:
        entities = getattr(message, "entities", None) or []
        mentioned = any(getattr(e, "user_id", None) == _ME.id for e in entities)
        if mentioned or getattr(message, "mentioned", False):
            await send_admin_message(
                f"🔔 Нас отметили
chat_id={event.chat_id}, message_id={event.id}

{text[:3000]}"
            )


async def giveaway_detected(event, parsed: dict) -> None:
    if not ADMIN_CHAT_ID:
        return
    await send_admin_message(
        f"🎁 Розыгрыш найден
chat_id={event.chat_id}, message_id={event.id}
"
        f"type={parsed.get('type')}, confidence={parsed.get('confidence')}
"
        f"needs_human={parsed.get('needs_human')}"
    )


async def participation_result(event, parsed: dict, result: str) -> None:
    if not ADMIN_CHAT_ID:
        return
    await send_admin_message(
        f"🤖 Участие: {result}
chat_id={event.chat_id}, message_id={event.id}
"
        f"type={parsed.get('type')}"
    )


async def notify_win(text: str) -> None:
    if NOTIFY_ON_WIN and ADMIN_CHAT_ID:
        await send_admin_message(f"🏆 Возможная победа

{text[:3500]}")
