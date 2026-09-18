import hashlib
import json
import random

import asyncpg

from app.config import DATABASE_URL
from app.giveaways.state import VALID_STATUSES

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
    worker_id: str,
    lease_seconds: int,
) -> bool:
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO channel_message_events(
            chat_id,message_id,event_kind,event_marker,
            processing_status,claimed_by,lease_until,processed_at
        )
        VALUES(
            $1,$2,$3,$4,'processing',$5,
            now()+make_interval(secs => $6),NULL
        )
        ON CONFLICT(event_marker) DO UPDATE SET
            processing_status='processing',
            claimed_by=EXCLUDED.claimed_by,
            lease_until=EXCLUDED.lease_until,
            processed_at=NULL
        WHERE channel_message_events.processing_status <> 'processed'
          AND (
              channel_message_events.lease_until IS NULL
              OR channel_message_events.lease_until < now()
          )
        RETURNING id
        """,
        chat_id,
        message_id,
        event_kind,
        event_marker,
        worker_id,
        int(lease_seconds),
    )
    return row is not None


async def renew_channel_event_lease(
    event_marker: str,
    worker_id: str,
    lease_seconds: int,
) -> bool:
    pool = await get_pool()
    result = await pool.execute(
        """
        UPDATE channel_message_events
        SET lease_until=now()+make_interval(secs => $3)
        WHERE event_marker=$1
          AND claimed_by=$2
          AND processing_status='processing'
        """,
        event_marker,
        worker_id,
        int(lease_seconds),
    )
    return result.endswith("1")


async def complete_channel_event(event_marker: str, worker_id: str) -> None:
    pool = await get_pool()
    await pool.execute(
        """
        UPDATE channel_message_events
        SET processing_status='processed',
            processed_at=now(),
            lease_until=NULL
        WHERE event_marker=$1 AND claimed_by=$2
        """,
        event_marker,
        worker_id,
    )


async def release_channel_event(event_marker: str, worker_id: str) -> None:
    pool = await get_pool()
    await pool.execute(
        """
        UPDATE channel_message_events
        SET processing_status='processing',
            claimed_by=NULL,
            lease_until=now()
        WHERE event_marker=$1 AND claimed_by=$2
        """,
        event_marker,
        worker_id,
    )


async def claim_stale_channel_event(
    worker_id: str,
    lease_seconds: int,
) -> dict | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT id,chat_id,message_id,event_kind,event_marker
                FROM channel_message_events
                WHERE processing_status='processing'
                  AND (lease_until IS NULL OR lease_until < now())
                ORDER BY created_at
                FOR UPDATE SKIP LOCKED
                LIMIT 1
                """
            )
            if row is None:
                return None

            await conn.execute(
                """
                UPDATE channel_message_events
                SET claimed_by=$2,
                    lease_until=now()+make_interval(secs => $3)
                WHERE id=$1
                """,
                row["id"],
                worker_id,
                int(lease_seconds),
            )
            return dict(row)


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
            chat_id,message_id,giveaway_type,status,source_channel_username,
            confidence,needs_human,reason,plan,current_version
        )
        VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,0)
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


async def get_giveaway_id(chat_id: int, message_id: int) -> int | None:
    pool = await get_pool()
    value = await pool.fetchval(
        """
        SELECT id FROM giveaways
        WHERE chat_id=$1 AND message_id=$2
        """,
        chat_id,
        message_id,
    )
    return int(value) if value is not None else None


async def set_giveaway_status(giveaway_id: int, status: str) -> None:
    if status not in VALID_STATUSES:
        raise ValueError(f"Invalid giveaway status: {status}")
    pool = await get_pool()
    await pool.execute(
        "UPDATE giveaways SET status=$2 WHERE id=$1",
        giveaway_id,
        status,
    )


async def record_giveaway_version(
    giveaway_id: int,
    event_kind: str,
    event_marker: str,
    plan: dict,
) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.fetchrow(
                "SELECT id FROM giveaways WHERE id=$1 FOR UPDATE",
                giveaway_id,
            )
            row = await conn.fetchrow(
                """
                INSERT INTO giveaway_versions(
                    giveaway_id,version_no,event_kind,event_marker,plan
                )
                SELECT $1, COALESCE(MAX(version_no),0)+1, $2, $3, $4::jsonb
                FROM giveaway_versions
                WHERE giveaway_id=$1
                RETURNING version_no
                """,
                giveaway_id,
                event_kind,
                event_marker,
                json.dumps(plan, ensure_ascii=False),
            )
            if row is None:
                raise RuntimeError("Failed to record giveaway version")
            version = int(row["version_no"])
            await conn.execute(
                """
                UPDATE giveaways
                SET current_version=$2
                WHERE id=$1
                """,
                giveaway_id,
                version,
            )
            return version


async def schedule_reminder(
    giveaway_id: int,
    delay_minutes: int,
    kind: str = "giveaway_followup",
) -> None:
    pool = await get_pool()
    await pool.execute(
        """
        INSERT INTO reminders(giveaway_id,remind_at,kind,sent)
        SELECT $1,now()+make_interval(mins => $2),$3,false
        WHERE NOT EXISTS (
            SELECT 1 FROM reminders
            WHERE giveaway_id=$1 AND kind=$3 AND sent=false
        )
        """,
        giveaway_id,
        int(delay_minutes),
        kind,
    )


async def claim_due_reminder(limit: int = 50) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                """
                SELECT r.id,r.giveaway_id,r.kind,g.chat_id,g.message_id
                FROM reminders r
                JOIN giveaways g ON g.id=r.giveaway_id
                WHERE r.sent=false AND r.remind_at<=now()
                ORDER BY r.remind_at
                FOR UPDATE SKIP LOCKED
                LIMIT $1
                """,
                limit,
            )
            if not rows:
                return []
            return [dict(row) for row in rows]


async def mark_reminder_sent(reminder_id: int) -> None:
    pool = await get_pool()
    await pool.execute(
        "UPDATE reminders SET sent=true WHERE id=$1",
        reminder_id,
    )


async def get_completed_action_keys(
    giveaway_id: int,
    account_user_id: int,
) -> set[str]:
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT action_key
        FROM participation_actions
        WHERE giveaway_id=$1 AND account_user_id=$2
          AND status='success' AND action_key IS NOT NULL
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
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


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
            giveaway_id,account_user_id,step_index,action_code,action_type,
            action_key,sequence_id,payload,status,executed_at,error
        )
        VALUES(
            $1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9,
            CASE WHEN $9 IN ('success','failed','error','skipped_number_exhausted')
                 THEN now() ELSE NULL END,
            $10
        )
        ON CONFLICT (giveaway_id,account_user_id,action_key)
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


async def track_message(
    giveaway_id: int,
    account_user_id: int,
    chat_id: int,
    message_id: int,
    kind: str,
) -> None:
    pool = await get_pool()
    await pool.execute(
        """
        INSERT INTO tracked_messages(
            giveaway_id,account_user_id,chat_id,message_id,kind
        )
        VALUES($1,$2,$3,$4,$5)
        ON CONFLICT(chat_id,message_id,kind)
        DO UPDATE SET
            giveaway_id=EXCLUDED.giveaway_id,
            account_user_id=EXCLUDED.account_user_id
        """,
        giveaway_id,
        account_user_id,
        chat_id,
        message_id,
        kind,
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
            await conn.execute(
                "SELECT pg_advisory_xact_lock($1)",
                int(giveaway_id),
            )

            existing = await conn.fetchrow(
                """
                SELECT number_value FROM number_pool
                WHERE giveaway_id=$1 AND account_user_id=$2
                """,
                giveaway_id,
                account_user_id,
            )
            if existing is not None:
                return int(existing["number_value"])

            capacity = int(maximum) - int(minimum) + 1
            if capacity <= 0:
                return None

            for _ in range(40):
                candidate = random.randint(int(minimum), int(maximum))
                used = await conn.fetchval(
                    """
                    SELECT EXISTS(
                        SELECT 1 FROM number_pool
                        WHERE giveaway_id=$1 AND number_value=$2
                    ) OR EXISTS(
                        SELECT 1 FROM participation_actions
                        WHERE giveaway_id=$1 AND action_code=6
                          AND status='success'
                          AND payload->>'number_value'=$3
                    )
                    """,
                    giveaway_id,
                    candidate,
                    str(candidate),
                )
                if used:
                    continue

                row = await conn.fetchrow(
                    """
                    INSERT INTO number_pool(
                        giveaway_id,account_user_id,number_value,
                        minimum,maximum,status
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

            row = await conn.fetchrow(
                """
                SELECT candidate
                FROM generate_series($1::bigint,$2::bigint) AS candidate
                WHERE NOT EXISTS(
                    SELECT 1 FROM number_pool np
                    WHERE np.giveaway_id=$3
                      AND np.number_value=candidate
                )
                AND NOT EXISTS(
                    SELECT 1 FROM participation_actions pa
                    WHERE pa.giveaway_id=$3
                      AND pa.action_code=6
                      AND pa.status='success'
                      AND pa.payload->>'number_value'=candidate::text
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
                    giveaway_id,account_user_id,number_value,
                    minimum,maximum,status
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
        SET status='sent',sent_at=now()
        WHERE giveaway_id=$1 AND account_user_id=$2 AND number_value=$3
        """,
        giveaway_id,
        account_user_id,
        number_value,
    )


async def release_number(
    giveaway_id: int,
    account_user_id: int,
    number_value: int,
) -> None:
    pool = await get_pool()
    await pool.execute(
        """
        DELETE FROM number_pool
        WHERE giveaway_id=$1 AND account_user_id=$2
          AND number_value=$3 AND status='reserved'
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
            total_sent = await conn.fetchval(
                """
                SELECT COUNT(*) FROM number_pool
                WHERE giveaway_id=$1 AND status='sent'
                """,
                giveaway_id,
            )
            historical_sent = await conn.fetchval(
                """
                SELECT COUNT(DISTINCT payload->>'number_value')
                FROM participation_actions
                WHERE giveaway_id=$1 AND action_code=6
                  AND status='success' AND payload ? 'number_value'
                """,
                giveaway_id,
            )
            account_count = len(set(account_user_ids))
            complete = int(historical_sent or 0) >= capacity or (
                account_count > 0 and int(total_sent or 0) >= account_count
            )
            if complete:
                await conn.execute(
                    "DELETE FROM number_pool WHERE giveaway_id=$1",
                    giveaway_id,
                )
                return True
    return False


async def upsert_account(
    session_name: str,
    user_id: int | None,
    username: str | None,
    authorized: bool,
    error: str | None = None,
    connected: bool = False,
) -> None:
    pool = await get_pool()
    await pool.execute(
        """
        INSERT INTO telegram_accounts(
            session_name,user_id,username,enabled,authorized,
            connected,last_seen_at,last_error
        )
        VALUES($1,$2,$3,true,$4,$5,now(),$6)
        ON CONFLICT(session_name)
        DO UPDATE SET
            user_id=EXCLUDED.user_id,
            username=EXCLUDED.username,
            authorized=EXCLUDED.authorized,
            connected=EXCLUDED.connected,
            last_seen_at=now(),
            last_error=EXCLUDED.last_error
        """,
        session_name,
        user_id,
        username,
        authorized,
        connected,
        error,
    )


async def update_account_runtime(
    user_id: int,
    connected: bool,
) -> None:
    pool = await get_pool()
    await pool.execute(
        """
        UPDATE telegram_accounts
        SET connected=$2,last_seen_at=now()
        WHERE user_id=$1
        """,
        user_id,
        connected,
    )


async def upsert_channel(chat_id: int, username: str | None) -> None:
    pool = await get_pool()
    await pool.execute(
        """
        INSERT INTO monitored_channels(chat_id,username,last_seen_at)
        VALUES($1,$2,now())
        ON CONFLICT(chat_id)
        DO UPDATE SET
            username=COALESCE(EXCLUDED.username,monitored_channels.username),
            last_seen_at=now()
        """,
        chat_id,
        username,
    )


async def upsert_channel_account(chat_id: int, account_user_id: int) -> None:
    pool = await get_pool()
    await pool.execute(
        """
        INSERT INTO channel_accounts(channel_id,account_user_id)
        VALUES($1,$2)
        ON CONFLICT(channel_id,account_user_id)
        DO UPDATE SET last_seen_at=now()
        """,
        chat_id,
        account_user_id,
    )


async def get_account_rows() -> list[dict]:
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT id,session_name,user_id,username,enabled,authorized,
               connected,
               (
                   connected=true
                   AND last_seen_at >= now()-interval '90 seconds'
               ) AS live,
               last_seen_at,last_error
        FROM telegram_accounts
        ORDER BY session_name
        """
    )
    return [dict(row) for row in rows]


async def get_account_stats() -> dict:
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        SELECT COUNT(*) AS total,
               COUNT(*) FILTER(
                   WHERE enabled=true
                     AND authorized=true
                     AND connected=true
                     AND last_seen_at >= now()-interval '90 seconds'
               ) AS online,
               COUNT(*) FILTER(WHERE authorized=false) AS unauthorized,
               COUNT(*) FILTER(WHERE enabled=false) AS disabled
        FROM telegram_accounts
        """
    )
    return {key: int(value or 0) for key, value in row.items()}


async def get_pending_giveaways(limit: int = 20) -> list[dict]:
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT id,chat_id,message_id,status,source_channel_username,
               confidence,needs_human,reason,detected_at
        FROM giveaways
        WHERE status IN (
            'needs_human','retry_requested','partial_or_failed','failed'
        )
        ORDER BY detected_at DESC
        LIMIT $1
        """,
        limit,
    )
    return [dict(row) for row in rows]


async def request_giveaway_retry(giveaway_id: int) -> bool:
    pool = await get_pool()
    result = await pool.execute(
        """
        UPDATE giveaways SET status='retry_requested'
        WHERE id=$1
          AND status IN ('needs_human','partial_or_failed','failed')
        """,
        giveaway_id,
    )
    return result.endswith("1")


async def claim_retry_giveaway() -> dict | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT id,chat_id,message_id,plan
                FROM giveaways
                WHERE status='retry_requested'
                ORDER BY detected_at
                FOR UPDATE SKIP LOCKED
                LIMIT 1
                """
            )
            if row is None:
                return None
            await conn.execute(
                "UPDATE giveaways SET status='executing' WHERE id=$1",
                row["id"],
            )
            return dict(row)


async def find_related_giveaway(
    chat_id: int,
    reply_to_message_id: int | None,
    account_user_id: int,
) -> dict | None:
    pool = await get_pool()

    if reply_to_message_id:
        row = await pool.fetchrow(
            """
            SELECT g.id,g.chat_id,g.message_id,
                   g.source_channel_username,g.detected_at
            FROM tracked_messages tm
            JOIN giveaways g ON g.id=tm.giveaway_id
            WHERE tm.chat_id=$1
              AND tm.message_id=$2
              AND tm.account_user_id=$3
            ORDER BY g.detected_at DESC
            LIMIT 1
            """,
            chat_id,
            reply_to_message_id,
            account_user_id,
        )
        if row:
            return dict(row)

    rows = await pool.fetch(
        """
        SELECT id,chat_id,message_id,source_channel_username,detected_at
        FROM giveaways
        WHERE chat_id=$1
          AND detected_at >= now()-interval '30 days'
          AND status <> 'skipped'
        ORDER BY detected_at DESC
        LIMIT 2
        """,
        chat_id,
    )
    # Do not guess which giveaway a free-standing winner post belongs to.
    if len(rows) == 1:
        return dict(rows[0])
    return None


async def claim_notification(dedupe_key: str) -> bool:
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO notifications(dedupe_key,kind,sent_at)
        VALUES($1,'dedupe',now())
        ON CONFLICT(dedupe_key) DO NOTHING
        RETURNING id
        """,
        dedupe_key,
    )
    return row is not None


async def get_stats() -> dict:
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        SELECT COUNT(*) AS total,
               COUNT(*) FILTER(
                   WHERE detected_at>=now()-interval '24 hours'
               ) AS last_24h,
               COUNT(*) FILTER(WHERE status='success') AS successful,
               COUNT(*) FILTER(
                   WHERE status IN('partial_or_failed','failed')
               ) AS failed,
               COUNT(*) FILTER(WHERE needs_human=true) AS needs_human,
               COUNT(*) FILTER(WHERE status='executing') AS executing
        FROM giveaways
        """
    )
    return {key: int(value or 0) for key, value in row.items()}
