from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command
from aiogram.types import Message

from app.config import ADMIN_CHAT_ID, ADMIN_USER_IDS, TG_BOT_TOKEN

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
    # Database-backed metrics will be wired here next.
    await message.answer("Статистика будет читаться из PostgreSQL.")


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
