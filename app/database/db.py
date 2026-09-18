import asyncpg

from app.config import DATABASE_URL

_pool = None


async def get_pool():
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(DATABASE_URL)
    return _pool


async def already_processed(chat_id: int, message_id: int) -> bool:
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT 1 FROM giveaways WHERE chat_id=$1 AND message_id=$2",
        chat_id,
        message_id,
    )
    return row is not None


async def mark_processed(chat_id: int, message_id: int, giveaway_type: str, result: str):
    pool = await get_pool()
    await pool.execute(
        """INSERT INTO giveaways(chat_id,message_id,giveaway_type,status)
           VALUES($1,$2,$3,$4)
           ON CONFLICT(chat_id,message_id) DO UPDATE SET status=EXCLUDED.status""",
        chat_id,
        message_id,
        giveaway_type,
        result,
    )
