import json

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


async def mark_processed(
    chat_id: int,
    message_id: int,
    giveaway_type: str,
    result: str,
    plan: dict | None = None,
) -> int:
    pool = await get_pool()
    plan_json = json.dumps(plan or {}, ensure_ascii=False)
    row = await pool.fetchrow(
        """
        INSERT INTO giveaways(
            chat_id,
            message_id,
            giveaway_type,
            status,
            source_channel_username,
            confidence,
            needs_human,
            reason,
            plan
        )
        VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb)
        ON CONFLICT(chat_id,message_id)
        DO UPDATE SET
            giveaway_type=EXCLUDED.giveaway_type,
            status=EXCLUDED.status,
            source_channel_username=EXCLUDED.source_channel_username,
            confidence=EXCLUDED.confidence,
            needs_human=EXCLUDED.needs_human,
            reason=EXCLUDED.reason,
            plan=EXCLUDED.plan
        RETURNING id
        """,
        chat_id,
        message_id,
        giveaway_type,
        result,
        (plan or {}).get("channel_username"),
        (plan or {}).get("confidence"),
        bool((plan or {}).get("needs_human", False)),
        (plan or {}).get("reason"),
        plan_json,
    )
    return int(row["id"])


async def mark_action(
    giveaway_id: int,
    action: dict,
    step_index: int,
    status: str,
    error: str | None,
) -> None:
    pool = await get_pool()
    payload = json.dumps(action, ensure_ascii=False)
    await pool.execute(
        """
        INSERT INTO participation_actions(
            giveaway_id,
            step_index,
            action_code,
            action_type,
            sequence_id,
            payload,
            status,
            executed_at,
            error
        )
        VALUES($1,$2,$3,$4,$5,$6::jsonb,$7,CASE WHEN $7 IN ('success','failed','error') THEN now() ELSE NULL END,$8)
        """,
        giveaway_id,
        step_index,
        action.get("code"),
        action.get("type", "unknown"),
        action.get("sequence_id"),
        payload,
        status,
        error,
    )


async def get_stats() -> dict:
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        SELECT
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE detected_at >= now() - interval '24 hours') AS last_24h,
            COUNT(*) FILTER (WHERE status = 'success') AS successful,
            COUNT(*) FILTER (WHERE status IN ('partial_or_failed','failed','error')) AS failed,
            COUNT(*) FILTER (WHERE needs_human = true) AS needs_human
        FROM giveaways
        """
    )
    return {key: int(value or 0) for key, value in row.items()}
