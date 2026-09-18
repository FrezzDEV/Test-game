import asyncio

from app.admin.bot import send_admin_message
from app.config import ADMIN_CHAT_ID, REMINDER_POLL_SECONDS, TG_BOT_TOKEN
from app.database.db import claim_due_reminder


async def reminder_worker() -> None:
    if not TG_BOT_TOKEN or not ADMIN_CHAT_ID:
        return

    while True:
        try:
            reminders = await claim_due_reminder(limit=50)
            for reminder in reminders:
                await send_admin_message(
                    "⏰ Напоминание по розыгрышу\n"
                    f"giveaway_id={reminder['giveaway_id']}\n"
                    f"chat_id={reminder['chat_id']}\n"
                    f"message_id={reminder['message_id']}\n"
                    f"kind={reminder['kind']}"
                )
        except Exception as exc:
            print(f"Reminder worker error: {type(exc).__name__}: {exc}")

        await asyncio.sleep(max(1, REMINDER_POLL_SECONDS))
