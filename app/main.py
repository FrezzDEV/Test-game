import asyncio

from app.admin.bot import run_admin_bot
from app.telegram.client import run_telegram_client


async def main() -> None:
    await asyncio.gather(
        run_telegram_client(),
        run_admin_bot(),
    )


if __name__ == "__main__":
    asyncio.run(main())
