import asyncio

from app.admin.bot import run_admin_bot
from app.admin.reminders import reminder_worker
from app.telegram.client import run_telegram_clients


async def main() -> None:
    await asyncio.gather(
        run_telegram_clients(),
        run_admin_bot(),
        reminder_worker(),
    )


if __name__ == "__main__":
    asyncio.run(main())
