from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command
from aiogram.types import Message

from app.config import ADMIN_CHAT_ID, ADMIN_USER_IDS, TG_BOT_TOKEN
from app.database.db import (
    get_account_rows,
    get_account_stats,
    get_pending_giveaways,
    get_stats,
    request_giveaway_retry,
)

router = Router()
dp = Dispatcher()
dp.include_router(router)
_bot = Bot(TG_BOT_TOKEN) if TG_BOT_TOKEN else None


def is_admin(message: Message) -> bool:
    return message.from_user is not None and message.from_user.id in ADMIN_USER_IDS


@router.message(Command("start"))
async def start_handler(message: Message):
    if is_admin(message):
        await message.answer("/stats /status /accounts /pending /retry <id> /help")


@router.message(Command("status"))
async def status_handler(message: Message):
    if not is_admin(message):
        return
    stats = await get_stats()
    accounts = await get_account_stats()
    await message.answer(
        "🟢 Status\n"
        f"Accounts connected: {accounts['online']}/{accounts['total']}\n"
        f"Unauthorized: {accounts['unauthorized']}\n"
        f"Disabled: {accounts['disabled']}\n"
        f"Giveaways 24h: {stats['last_24h']}\n"
        f"Executing: {stats['executing']}\n"
        f"Needs human: {stats['needs_human']}"
    )


@router.message(Command("accounts"))
async def accounts_handler(message: Message):
    if not is_admin(message):
        return
    rows = await get_account_rows()
    if not rows:
        await message.answer("Аккаунты не зарегистрированы.")
        return
    lines = ["👤 Аккаунты"]
    for row in rows:
        state = "✅" if row["authorized"] and row["enabled"] and row["connected"] else "⚠️"
        username = f"@{row['username']}" if row["username"] else "-"
        lines.append(
            f"{state} {row['session_name']} | "
            f"user_id={row['user_id'] or '-'} | {username}"
        )
    await message.answer("\n".join(lines)[:4000])


@router.message(Command("pending"))
async def pending_handler(message: Message):
    if not is_admin(message):
        return
    rows = await get_pending_giveaways()
    if not rows:
        await message.answer("Очередь пустая.")
        return
    lines = ["📥 Giveaway queue"]
    for row in rows:
        lines.append(
            f"#{row['id']} | {row['status']} | "
            f"{row['source_channel_username'] or '-'} | "
            f"{row['chat_id']}:{row['message_id']}"
        )
    await message.answer("\n".join(lines)[:4000])


@router.message(Command("retry"))
async def retry_handler(message: Message):
    if not is_admin(message):
        return
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Использование: /retry <giveaway_id>")
        return
    ok = await request_giveaway_retry(int(parts[1]))
    await message.answer("✅ retry запрошен" if ok else "❌ giveaway не найден или уже выполняется")


@router.message(Command("stats"))
async def stats_handler(message: Message):
    if not is_admin(message):
        return
    stats = await get_stats()
    await message.answer(
        "📊 Статистика\n"
        f"Всего: {stats['total']}\n"
        f"24ч: {stats['last_24h']}\n"
        f"Успешно: {stats['successful']}\n"
        f"Ошибки: {stats['failed']}\n"
        f"Выполняется: {stats['executing']}\n"
        f"Нужен человек: {stats['needs_human']}"
    )


@router.message(Command("help"))
async def help_handler(message: Message):
    if not is_admin(message):
        return
    await message.answer(
        "/stats — статистика\n"
        "/status — состояние\n"
        "/accounts — аккаунты\n"
        "/pending — проблемные giveaway\n"
        "/retry <id> — повторить участие\n"
        "/help — команды"
    )


async def send_admin_message(text: str) -> bool:
    if _bot and ADMIN_CHAT_ID:
        await _bot.send_message(ADMIN_CHAT_ID, text)
        return True
    return False


async def run_admin_bot() -> None:
    if not _bot:
        print("Admin bot disabled: TG_BOT_TOKEN is not set")
        return
    print("Admin bot started")
    await dp.start_polling(_bot)
