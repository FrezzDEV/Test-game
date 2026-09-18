from telethon import TelegramClient, events

from app.config import (
    AUTO_JOIN_ENABLED,
    MONITORED_CHATS,
    TG_2FA_PASSWORD,
    TG_API_HASH,
    TG_API_ID,
    TG_PHONE,
    TG_SESSION,
)
from app.database.db import already_processed, mark_processed
from app.giveaways.detector import detect
from app.giveaways.planner import build_plan
from app.admin.notifier import notify
from app.telegram.actions import ActionExecutor


client = TelegramClient(TG_SESSION, TG_API_ID, TG_API_HASH)
executor = ActionExecutor(client)


async def handle_message(event) -> None:
    text = event.raw_text or ""
    if not text or not event.chat_id:
        return

    # Keep every incoming message eligible for reply/mention notifications.
    await notify.incoming_message(event)

    if await already_processed(event.chat_id, event.id):
        return

    parsed = await detect(text)
    if not parsed.get("is_giveaway"):
        return

    await notify.giveaway_detected(event, parsed)

    if not AUTO_JOIN_ENABLED or parsed.get("needs_human"):
        await mark_processed(
            event.chat_id,
            event.id,
            parsed.get("type", "unknown"),
            "detected_only",
        )
        return

    results = []
    for action in build_plan(parsed):
        try:
            ok = await executor.execute(action, event.message)
            results.append("ok" if ok else "failed")
        except Exception as exc:
            results.append(f"error:{type(exc).__name__}")

    result = (
        "success"
        if results and all(item == "ok" for item in results)
        else "partial_or_failed"
    )
    await mark_processed(
        event.chat_id,
        event.id,
        parsed.get("type", "unknown"),
        result,
    )
    await notify.participation_result(event, parsed, result)


async def run_telegram_client() -> None:
    kwargs = {}
    if TG_PHONE:
        kwargs["phone"] = TG_PHONE
    if TG_2FA_PASSWORD:
        kwargs["password"] = TG_2FA_PASSWORD

    client.add_event_handler(handle_message, events.NewMessage(chats=MONITORED_CHATS or None))
    await client.start(**kwargs)
    await notify.start_user_client(client)
    print("Telegram giveaway client started")
    await client.run_until_disconnected()
