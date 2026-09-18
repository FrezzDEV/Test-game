from pathlib import Path

from telethon import TelegramClient, events

import app.admin.notifier as notify
from app.config import (
    AUTO_JOIN_ENABLED,
    MONITORED_CHATS,
    TG_2FA_PASSWORD,
    TG_API_HASH,
    TG_API_ID,
    TG_PHONE,
    TG_SESSION,
    TG_SESSION_DIR,
)
from app.database.db import (
    already_processed,
    mark_action,
    mark_processed,
)
from app.giveaways.detector import detect
from app.giveaways.planner import build_plan
from app.telegram.actions import ActionExecutor


Path(TG_SESSION_DIR).mkdir(parents=True, exist_ok=True)
SESSION_PATH = str(Path(TG_SESSION_DIR) / TG_SESSION)

client = TelegramClient(SESSION_PATH, TG_API_ID, TG_API_HASH)
executor = ActionExecutor(client)


async def handle_message(event) -> None:
    text = event.raw_text or ""
    if not text or not event.chat_id:
        return

    await notify.incoming_message(event)

    if await already_processed(event.chat_id, event.id):
        return

    chat = await event.get_chat()
    source_username = getattr(chat, "username", None)

    parsed = await detect(
        text,
        source_channel_username=source_username,
    )
    if not parsed.get("detected"):
        return

    execution_enabled = AUTO_JOIN_ENABLED and not parsed.get("needs_human")
    status = "ready" if execution_enabled else "detected_only"
    giveaway_id = await mark_processed(
        event.chat_id,
        event.id,
        parsed.get("actions", [{}])[0].get("type", "unknown"),
        status,
        plan=parsed,
    )

    await notify.giveaway_detected(event, parsed)

    if not execution_enabled:
        return

    plan = build_plan(parsed)
    if not plan:
        await mark_processed(
            event.chat_id,
            event.id,
            "unknown",
            "needs_human",
            plan=parsed,
        )
        return

    results = []
    for step_index, action in enumerate(plan):
        try:
            ok = await executor.execute(action, event.message)
            results.append("ok" if ok else "failed")
            await mark_action(
                giveaway_id=giveaway_id,
                action=action,
                step_index=step_index,
                status="success" if ok else "failed",
                error=None if ok else "executor_returned_false",
            )
        except Exception as exc:
            results.append("failed")
            await mark_action(
                giveaway_id=giveaway_id,
                action=action,
                step_index=step_index,
                status="error",
                error=f"{type(exc).__name__}: {exc}",
            )

    result = (
        "success"
        if results and all(item == "ok" for item in results)
        else "partial_or_failed"
    )
    await mark_processed(
        event.chat_id,
        event.id,
        parsed.get("actions", [{}])[0].get("type", "unknown"),
        result,
        plan=parsed,
    )
    await notify.participation_result(event, parsed, result)


async def run_telegram_client() -> None:
    kwargs = {}
    if TG_PHONE:
        kwargs["phone"] = TG_PHONE
    if TG_2FA_PASSWORD:
        kwargs["password"] = TG_2FA_PASSWORD

    client.add_event_handler(
        handle_message,
        events.NewMessage(chats=MONITORED_CHATS or None),
    )
    await client.start(**kwargs)
    await notify.start_user_client(client)
    print(f"Telegram giveaway client started; session: {SESSION_PATH}")
    await client.run_until_disconnected()
