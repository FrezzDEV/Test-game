import re

from app.admin.bot import send_admin_message
from app.config import ADMIN_CHAT_ID, NOTIFY_ON_MENTION, NOTIFY_ON_REPLY, NOTIFY_ON_WIN
from app.database.db import claim_notification, find_related_giveaway

_win_patterns = re.compile(
    r"(winner|you\s+won|congratulations|\bwin\b|"
    r"победител|победил|победительница|вы\s+выиграл|"
    r"вы\s+выиграли|поздравляем.*побед|выигрыш)",
    re.IGNORECASE,
)


async def register_account(account) -> None:
    return None


def _is_possible_win(text: str) -> bool:
    return bool(text and _win_patterns.search(text))


async def incoming_message(event, account) -> None:
    if not ADMIN_CHAT_ID:
        return

    message = event.message
    text = event.raw_text or ""
    if getattr(message, "out", False):
        return

    if NOTIFY_ON_REPLY and message.is_reply and message.reply_to_msg_id:
        try:
            replied_to = await message.get_reply_message()
        except Exception:
            replied_to = None
        if replied_to is not None and getattr(replied_to, "sender_id", None) == account.user_id:
            await send_admin_message(
                f"↩️ Ответ на сообщение аккаунта\n"
                f"account={account.session_name} ({account.user_id})\n"
                f"chat_id={event.chat_id}, message_id={event.id}\n\n"
                f"{text[:3000]}"
            )

    if NOTIFY_ON_MENTION and getattr(message, "mentioned", False):
        await send_admin_message(
            f"🔔 Упоминание аккаунта\n"
            f"account={account.session_name} ({account.user_id})\n"
            f"chat_id={event.chat_id}, message_id={event.id}\n\n"
            f"{text[:3000]}"
        )

    if NOTIFY_ON_WIN and _is_possible_win(text):
        giveaway = await find_related_giveaway(
            int(event.chat_id),
            int(message.reply_to_msg_id) if message.reply_to_msg_id else None,
            account.user_id,
        )
        if giveaway:
            dedupe = (
                f"win:{account.user_id}:{event.chat_id}:"
                f"{event.id}:{giveaway['id']}"
            )
            if await claim_notification(dedupe):
                await send_admin_message(
                    f"🏆 Возможная победа\n"
                    f"account={account.session_name} ({account.user_id})\n"
                    f"giveaway_id={giveaway['id']}\n"
                    f"channel={giveaway.get('source_channel_username') or '-'}\n"
                    f"chat_id={event.chat_id}, message_id={event.id}\n\n"
                    f"{text[:3500]}"
                )


async def giveaway_detected(
    event,
    parsed: dict,
    chat_id: int | None = None,
    message_id: int | None = None,
) -> None:
    if not ADMIN_CHAT_ID:
        return
    resolved_chat_id = chat_id if chat_id is not None else event.chat_id
    resolved_message_id = message_id if message_id is not None else event.id
    codes = parsed.get("action_codes") or [
        action.get("code")
        for action in parsed.get("actions", [])
    ]
    await send_admin_message(
        f"🎁 Розыгрыш найден\n"
        f"channel={parsed.get('channel_username') or '-'}\n"
        f"chat_id={resolved_chat_id}, message_id={resolved_message_id}\n"
        f"detected={parsed.get('detected')}\n"
        f"confidence={parsed.get('confidence')}\n"
        f"action_codes={codes}\n"
        f"write_sequence_id={parsed.get('write_sequence_id') or '-'}\n"
        f"needs_human={parsed.get('needs_human')}\n"
        f"reason={parsed.get('reason') or '-'}"
    )


async def participation_result(
    event,
    parsed: dict,
    result: str,
    chat_id: int | None = None,
    message_id: int | None = None,
) -> None:
    if not ADMIN_CHAT_ID:
        return
    resolved_chat_id = chat_id if chat_id is not None else event.chat_id
    resolved_message_id = message_id if message_id is not None else event.id
    await send_admin_message(
        f"🤖 Участие: {result}\n"
        f"chat_id={resolved_chat_id}, message_id={resolved_message_id}\n"
        f"channel={parsed.get('channel_username') or '-'}\n"
        f"action_codes={parsed.get('action_codes') or []}"
    )
