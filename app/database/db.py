import hashlib
import json
import random

import asyncpg

from app.config import DATABASE_URL

_pool = None


async def get_pool():
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(DATABASE_URL)
    return _pool


async def claim_channel_event(
    chat_id: int,
    message_id: int,
    event_kind: str,
    event_marker: str,
) -> bool:
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO channel_message_events(
            chat_id, message_id, event_kind, event_marker
        )
        VALUES($1,$2,$3,$4)
        ON CONFLICT(event_marker) DO NOTHING
        RETURNING id
        """,
        chat_id,
        message_id,
        event_kind,
        event_marker,
    )
    return row is not None


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


async def get_completed_action_keys(
    giveaway_id: int,
    account_user_id: int,
) -> set[str]:
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT action_key
        FROM participation_actions
        WHERE giveaway_id=$1
          AND account_user_id=$2
          AND status='success'
          AND action_key IS NOT NULL
        """,
        giveaway_id,
        account_user_id,
    )
    return {str(row["action_key"]) for row in rows}


def _action_key_for_storage(action: dict) -> str:
    payload = {
        key: value
        for key, value in action.items()
        if key != "number_value"
        or action.get("min_number") == action.get("max_number")
    }
    raw_key = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


async def mark_action(
    giveaway_id: int,
    account_user_id: int,
    action: dict,
    step_index: int,
    status: str,
    error: str | None,
) -> None:
    pool = await get_pool()
    payload = json.dumps(action, ensure_ascii=False)
    action_key_value = _action_key_for_storage(action)

    await pool.execute(
        """
        INSERT INTO participation_actions(
            giveaway_id,
            account_user_id,
            step_index,
            action_code,
            action_type,
            action_key,
            sequence_id,
            payload,
            status,
            executed_at,
            error
        )
        VALUES(
            $1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9,
            CASE WHEN $9 IN (
                'success','failed','error','skipped_number_exhausted'
            ) THEN now() ELSE NULL END,
            $10
        )
        ON CONFLICT (giveaway_id, account_user_id, action_key)
        DO UPDATE SET
            step_index=EXCLUDED.step_index,
            payload=EXCLUDED.payload,
            status=EXCLUDED.status,
            executed_at=EXCLUDED.executed_at,
            error=EXCLUDED.error
        """,
        giveaway_id,
        account_user_id,
        step_index,
        action.get("code"),
        action.get("type", "unknown"),
        action_key_value,
        action.get("sequence_id"),
        payload,
        status,
        error,
    )


async def reserve_number(
    giveaway_id: int,
    account_user_id: int,
    minimum: int,
    maximum: int,
) -> int | None:
    if minimum > maximum:
        return None

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # One giveaway gets one allocator lock, so concurrent account
            # tasks cannot reserve the same number.
            await conn.execute(
                "SELECT pg_advisory_xact_lock($1)",
                int(giveaway_id),
            )

            existing = await conn.fetchrow(
                """
                SELECT number_value
                FROM number_pool
                WHERE giveaway_id=$1
                  AND account_user_id=$2
                """,
                giveaway_id,
                account_user_id,
            )
            if existing is not None:
                return int(existing["number_value"])

            capacity = int(maximum) - int(minimum) + 1
            used = await conn.fetchval(
                "SELECT COUNT(*) FROM number_pool WHERE giveaway_id=$1",
                giveaway_id,
            )
            if int(used or 0) >= capacity:
                return None

            for _ in range(40):
                candidate = random.randint(int(minimum), int(maximum))
                row = await conn.fetchrow(
                    """
                    INSERT INTO number_pool(
                        giveaway_id,
                        account_user_id,
                        number_value,
                        minimum,
                        maximum,
                        status
                    )
                    VALUES($1,$2,$3,$4,$5,'reserved')
                    ON CONFLICT DO NOTHING
                    RETURNING number_value
                    """,
                    giveaway_id,
                    account_user_id,
                    candidate,
                    minimum,
                    maximum,
                )
                if row is not None:
                    return int(row["number_value"])

            # Near exhaustion, find the first remaining free number.
            row = await conn.fetchrow(
                """
                SELECT candidate
                FROM generate_series(
                    $1::bigint,
                    $2::bigint
                ) AS candidate
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM number_pool np
                    WHERE np.giveaway_id=$3
                      AND np.number_value=candidate
                )
                LIMIT 1
                """,
                minimum,
                maximum,
                giveaway_id,
            )
            if row is None:
                return None

            candidate = int(row["candidate"])
            inserted = await conn.fetchrow(
                """
                INSERT INTO number_pool(
                    giveaway_id,
                    account_user_id,
                    number_value,
                    minimum,
                    maximum,
                    status
                )
                VALUES($1,$2,$3,$4,$5,'reserved')
                ON CONFLICT DO NOTHING
                RETURNING number_value
                """,
                giveaway_id,
                account_user_id,
                candidate,
                minimum,
                maximum,
            )
            return int(inserted["number_value"]) if inserted else None


async def mark_number_sent(
    giveaway_id: int,
    account_user_id: int,
    number_value: int,
) -> None:
    pool = await get_pool()
    await pool.execute(
        """
        UPDATE number_pool
        SET status='sent',
            sent_at=now()
        WHERE giveaway_id=$1
          AND account_user_id=$2
          AND number_value=$3
        """,
        giveaway_id,
        account_user_id,
        number_value,
    )


async def maybe_clear_number_pool(
    giveaway_id: int,
    account_user_ids: list[int],
    minimum: int,
    maximum: int,
) -> bool:
    if minimum > maximum:
        return False

    pool = await get_pool()
    capacity = int(maximum) - int(minimum) + 1

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock($1)",
                int(giveaway_id),
            )

            total_reserved = await conn.fetchval(
                "SELECT COUNT(*) FROM number_pool WHERE giveaway_id=$1",
                giveaway_id,
            )
            total_sent = await conn.fetchval(
                """
                SELECT COUNT(*)
                FROM number_pool
                WHERE giveaway_id=$1
                  AND status='sent'
                """,
                giveaway_id,
            )

            account_count = len(set(account_user_ids))
            complete = (
                int(total_reserved or 0) >= capacity
                or (
                    account_count > 0
                    and int(total_sent or 0) >= account_count
                )
            )

            if complete:
                await conn.execute(
                    "DELETE FROM number_pool WHERE giveaway_id=$1",
                    giveaway_id,
                )
                return True

    return False


async def get_stats() -> dict:
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        SELECT
            COUNT(*) AS total,
            COUNT(*) FILTER (
                WHERE detected_at >= now() - interval '24 hours'
            ) AS last_24h,
            COUNT(*) FILTER (WHERE status = 'success') AS successful,
            COUNT(*) FILTER (
                WHERE status IN ('partial_or_failed','failed','error')
            ) AS failed,
            COUNT(*) FILTER (WHERE needs_human = true) AS needs_human
        FROM giveaways
        """
    )
    return {key: int(value or 0) for key, value in row.items()}
