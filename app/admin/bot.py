from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command
from aiogram.types import Message

from app.config import ADMIN_CHAT_ID, ADMIN_USER_IDS, TG_BOT_TOKEN
from app.database.db import get_stats

router = Router()
dp = Dispatcher()
dp.include_router(router)

_bot = Bot(TG_BOT_TOKEN) if TG_BOT_TOKEN else None


def is_admin(message: Message) -> bool:
    return message.from_user is not None and message.from_user.id in ADMIN_USER_IDS


@router.message(Command("start"))
async def start_handler(message: Message):
    if not is_admin(message):
        return
    await message.answer(
        "Админка активна. Команды: /stats, /status, /help"
    )


@router.message(Command("status"))
async def status_handler(message: Message):
    if not is_admin(message):
        return
    await message.answer("Статус: Telegram monitor + giveaway detector запущены.")


@router.message(Command("stats"))
async def stats_handler(message: Message):
    if not is_admin(message):
        return
    try:
        stats = await get_stats()
        await message.answer(
            "📊 Статистика\n"
            f"Всего найдено: {stats['total']}\n"
            f"За 24 часа: {stats['last_24h']}\n"
            f"Успешно: {stats['successful']}\n"
            f"Ошибки: {stats['failed']}\n"
            f"Нужен человек: {stats['needs_human']}"
        )
    except Exception as exc:
        await message.answer(f"Статистика недоступна: {type(exc).__name__}")


@router.message(Command("help"))
async def help_handler(message: Message):
    if not is_admin(message):
        return
    await message.answer(
        "/stats — статистика\n"
        "/status — состояние сервиса\n"
        "/help — команды"
    )


async def send_admin_message(text: str) -> None:
    if _bot and ADMIN_CHAT_ID:
        await _bot.send_message(ADMIN_CHAT_ID, text)


async def run_admin_bot() -> None:
    if not _bot:
        print("Admin bot disabled: TG_BOT_TOKEN is not set")
        return
    print("Admin bot started")
    await dp.start_polling(_bot)
