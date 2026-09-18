import asyncio
import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path

from telethon import TelegramClient, errors, events
from telethon.tl.functions.channels import JoinChannelRequest

import app.admin.notifier as notify
from app.config import (
    ACCOUNT_ACTION_DELAY_MS,
    ACTION_RETRY_DELAY_SECONDS,
    AUTO_JOIN_ENABLED,
    EVENT_LEASE_SECONDS,
    EVENT_RECOVERY_POLL_SECONDS,
    MONITORED_CHANNELS,
    NUMBER_SEND_RETRIES,
    REMINDER_INTERVAL_MINUTES,
    RETRY_POLL_SECONDS,
    SCAN_OLD_MESSAGES,
    TG_2FA_PASSWORD,
    TG_ACCOUNTS_PATH,
    TG_API_HASH,
    TG_API_ID,
    TG_PHONE,
    TG_SESSION,
    TG_SESSION_DIR,
)
from app.database.db import (
    claim_channel_event,
    claim_retry_giveaway,
    claim_stale_channel_event,
    complete_channel_event,
    get_account_rows,
    get_completed_action_keys,
    get_giveaway_id,
    mark_action,
    mark_number_sent,
    mark_processed,
    maybe_clear_number_pool,
    record_giveaway_version,
    release_channel_event,
    release_number,
    renew_channel_event_lease,
    reserve_number,
    schedule_reminder,
    set_giveaway_status,
    track_message,
    update_account_runtime,
    upsert_account,
    upsert_channel,
    upsert_channel_account,
)
from app.giveaways.detector import detect
from app.giveaways.planner import action_key, build_plan
from app.giveaways.state import (
    GIVEAWAY_EXECUTING,
    GIVEAWAY_FAILED,
    GIVEAWAY_NEEDS_HUMAN,
    GIVEAWAY_PARSED,
    GIVEAWAY_PARTIAL,
    GIVEAWAY_READY,
    GIVEAWAY_SKIPPED,
    GIVEAWAY_SUCCESS,
)
from app.telegram.actions import ActionExecutor
from app.telegram.session_config import (
    SessionCredentials,
    credential_candidates,
    load_session_credentials,
)

Path(TG_SESSION_DIR).mkdir(parents=True, exist_ok=True)


@dataclass
class TelegramAccount:
    session_name: str
    session_path: str
    client: TelegramClient
    executor: ActionExecutor
    user_id: int
    username: str | None


class AccountManager:
    def __init__(self) -> None:
        self.accounts: list[TelegramAccount] = []
        self._account_by_client: dict[object, TelegramAccount] = {}
        self._retry_task: asyncio.Task | None = None
        self._recovery_task: asyncio.Task | None = None
        self._heartbeat_task: asyncio.Task | None = None
        self._worker_id = f"manager:{uuid.uuid4()}"

    def _session_paths(self) -> list[Path]:
        paths = sorted(Path(TG_SESSION_DIR).glob("*.session"))
        return paths or [Path(TG_SESSION_DIR) / f"{TG_SESSION}.session"]

    async def start_all(self) -> list[TelegramAccount]:
        if self.accounts:
            return self.accounts

        per_session = load_session_credentials(TG_ACCOUNTS_PATH)
        env_credentials = SessionCredentials(
            api_id=TG_API_ID or None,
            api_hash=TG_API_HASH,
            phone=TG_PHONE,
            two_fa_password=TG_2FA_PASSWORD,
        )

        for session_path in self._session_paths():
            candidates = credential_candidates(
                session_path.stem,
                per_session,
                env_credentials,
            )
            if not candidates:
                await upsert_account(
                    session_path.stem,
                    None,
                    None,
                    False,
                    "missing Telegram api_id/api_hash",
                    False,
                )
                continue

            account = await self._start_session(session_path, candidates)
            if account is None:
                continue

            self.accounts.append(account)
            self._account_by_client[account.client] = account
            await upsert_account(
                account.session_name,
                account.user_id,
                account.username,
                True,
                None,
                account.client.is_connected(),
            )
            await notify.register_account(account)

        await self._auto_subscribe_primary()

        for account in self.accounts:
            account.client.add_event_handler(
                self._handle_new_message,
                events.NewMessage(chats=MONITORED_CHANNELS or None),
            )
            account.client.add_event_handler(
                self._handle_edited_message,
                events.MessageEdited(chats=MONITORED_CHANNELS or None),
            )
            account.client.add_event_handler(
                self._handle_user_message,
                events.NewMessage(),
            )

        if SCAN_OLD_MESSAGES:
            for account in self.accounts:
                await account.client.catch_up()

        return self.accounts

    async def _start_session(
        self,
        session_path: Path,
        candidates: list[SessionCredentials],
    ) -> TelegramAccount | None:
        errors_seen: list[str] = []

        for credentials in candidates:
            if not credentials.api_id or not credentials.api_hash:
                continue

            client = TelegramClient(
                str(session_path.with_suffix("")),
                credentials.api_id,
                credentials.api_hash,
                catch_up=SCAN_OLD_MESSAGES,
                sequential_updates=False,
                flood_sleep_threshold=60,
            )
            kwargs = {}
            phone = credentials.phone
            two_fa_password = credentials.two_fa_password
            if phone:
                kwargs["phone"] = phone
            if two_fa_password:
                kwargs["password"] = two_fa_password

            try:
                await client.start(**kwargs)
                me = await client.get_me()
                if me is None:
                    raise RuntimeError("session is not authorized")

                return TelegramAccount(
                    session_name=session_path.stem,
                    session_path=str(session_path),
                    client=client,
                    executor=ActionExecutor(client),
                    user_id=int(me.id),
                    username=getattr(me, "username", None),
                )
            except Exception as exc:
                errors_seen.append(
                    f"{type(exc).__name__}: {exc}"
                )
                await client.disconnect()

        await upsert_account(
            session_path.stem,
            None,
            None,
            False,
            "; ".join(errors_seen)[-3000:] or "session_start_failed",
            False,
        )
        return None

    async def _auto_subscribe_primary(self) -> None:
        if not self.accounts or not MONITORED_CHANNELS:
            return

        primary = self.accounts[0]
        for channel_ref in MONITORED_CHANNELS:
            try:
                await primary.client(JoinChannelRequest(channel_ref))
            except errors.UserAlreadyParticipantError:
                pass
            except errors.RPCError as exc:
                print(
                    f"Primary auto-subscribe failed for {channel_ref}: "
                    f"{type(exc).__name__}: {exc}"
                )

            try:
                entity = await primary.client.get_entity(channel_ref)
            except Exception as exc:
                print(
                    f"Primary channel lookup failed for {channel_ref}: "
                    f"{type(exc).__name__}: {exc}"
                )
                continue

            if not getattr(entity, "broadcast", False) or getattr(entity, "megagroup", False):
                continue

            chat_id = int(entity.id)
            username = getattr(entity, "username", None)
            await self._register_channel(chat_id, username)

    async def _register_channel(
        self,
        chat_id: int,
        username: str | None,
    ) -> None:
        await upsert_channel(chat_id, username)
        for account in self.accounts:
            await upsert_channel_account(chat_id, account.user_id)

    async def _handle_user_message(self, event) -> None:
        account = self._account_by_client.get(event.client)
        if account is not None:
            await update_account_runtime(account.user_id, event.client.is_connected())
            await notify.incoming_message(event, account)

    async def _is_broadcast_channel(self, event) -> bool:
        try:
            chat = await event.get_chat()
        except Exception:
            return False
        return bool(
            getattr(chat, "broadcast", False)
            and not getattr(chat, "megagroup", False)
        )

    @staticmethod
    def _event_marker(event, kind: str) -> str:
        message = event.message
        text = event.raw_text or ""
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        timestamp = (
            getattr(message, "edit_date", None)
            if kind == "edited"
            else getattr(message, "date", None)
        )
        timestamp_value = timestamp.isoformat() if timestamp else "none"
        return f"{kind}:{message.id}:{timestamp_value}:{digest}"

    async def _handle_new_message(self, event) -> None:
        await self._handle_channel_event(event, "new")

    async def _handle_edited_message(self, event) -> None:
        await self._handle_channel_event(event, "edited")

    async def _renew_event_lease(self, event_marker: str, worker_id: str) -> None:
        interval = max(5, EVENT_LEASE_SECONDS // 3)
        while True:
            await asyncio.sleep(interval)
            try:
                renewed = await renew_channel_event_lease(
                    event_marker,
                    worker_id,
                    EVENT_LEASE_SECONDS,
                )
                if not renewed:
                    return
            except Exception as exc:
                print(
                    f"Event lease renewal error for {event_marker}: "
                    f"{type(exc).__name__}: {exc}"
                )

    async def _handle_channel_event(self, event, kind: str) -> None:
        if not event.chat_id or not await self._is_broadcast_channel(event):
            return

        account = self._account_by_client.get(event.client)
        if account is None:
            return

        marker = self._event_marker(event, kind)
        if not await claim_channel_event(
            event.chat_id,
            event.id,
            kind,
            marker,
            str(account.user_id),
            EVENT_LEASE_SECONDS,
        ):
            return

        worker_id = str(account.user_id)
        lease_task = asyncio.create_task(
            self._renew_event_lease(marker, worker_id)
        )
        try:
            await self._process_channel_message(
                account,
                int(event.chat_id),
                event.message,
                kind,
                marker,
            )
        except Exception as exc:
            await release_channel_event(marker, worker_id)
            print(
                f"Channel event processing error for {event.chat_id}:{event.id}: "
                f"{type(exc).__name__}: {exc}"
            )
        else:
            await complete_channel_event(marker, worker_id)
        finally:
            lease_task.cancel()
            await asyncio.gather(lease_task, return_exceptions=True)

    async def _process_channel_message(
        self,
        account: TelegramAccount,
        chat_id: int,
        message,
        kind: str,
        marker: str,
    ) -> None:
        try:
            chat = await account.client.get_entity(chat_id)
        except Exception:
            chat = None

        source_username = getattr(chat, "username", None) if chat else None
        await self._register_channel(chat_id, source_username)

        text = getattr(message, "raw_text", None) or ""
        parsed = await detect(
            text or "[message has no text/caption]",
            source_channel_username=source_username,
        )

        giveaway_id = await get_giveaway_id(chat_id, int(message.id))
        if not parsed.get("detected"):
            if giveaway_id is not None:
                await record_giveaway_version(
                    giveaway_id,
                    kind,
                    marker,
                    parsed,
                )
                await set_giveaway_status(giveaway_id, GIVEAWAY_SKIPPED)
            return

        giveaway_type = (
            (parsed.get("actions") or [{}])[0].get("type", "unknown")
        )
        giveaway_id = await mark_processed(
            chat_id,
            int(message.id),
            giveaway_type,
            GIVEAWAY_PARSED,
            plan=parsed,
        )
        await record_giveaway_version(giveaway_id, kind, marker, parsed)
        await schedule_reminder(
            giveaway_id,
            REMINDER_INTERVAL_MINUTES,
        )
        await notify.giveaway_detected(event=None, parsed=parsed, chat_id=chat_id, message_id=int(message.id))

        if parsed.get("needs_human"):
            await set_giveaway_status(giveaway_id, GIVEAWAY_NEEDS_HUMAN)
            return

        if not AUTO_JOIN_ENABLED:
            await set_giveaway_status(giveaway_id, GIVEAWAY_READY)
            return

        plan = build_plan(parsed)
        if not plan:
            await set_giveaway_status(giveaway_id, GIVEAWAY_NEEDS_HUMAN)
            return

        await set_giveaway_status(giveaway_id, GIVEAWAY_EXECUTING)
        result = await self.execute_plan_for_all_accounts(
            giveaway_id,
            message,
            plan,
        )
        await mark_processed(
            chat_id,
            int(message.id),
            giveaway_type,
            result,
            plan=parsed,
        )
        await notify.participation_result(
            event=None,
            parsed=parsed,
            result=result,
            chat_id=chat_id,
            message_id=int(message.id),
        )

    async def _execute_with_retry(
        self,
        account: TelegramAccount,
        action: dict,
        message,
    ) -> tuple[bool, str | None, list[dict]]:
        last_error = None
        for attempt in range(1, NUMBER_SEND_RETRIES + 1):
            try:
                execution = await account.executor.execute(action, message)
                if isinstance(execution, dict):
                    return (
                        bool(execution.get("ok")),
                        None,
                        list(execution.get("message_ids") or []),
                    )
                return bool(execution), None, []
            except errors.FloodWaitError as exc:
                last_error = f"FloodWaitError: {exc.seconds}s"
                await asyncio.sleep(exc.seconds)
            except errors.SlowModeWaitError as exc:
                last_error = f"SlowModeWaitError: {exc.seconds}s"
                await asyncio.sleep(exc.seconds)
            except (TimeoutError, ConnectionError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt < NUMBER_SEND_RETRIES:
                    await asyncio.sleep(ACTION_RETRY_DELAY_SECONDS)
            except errors.RPCError as exc:
                return False, f"{type(exc).__name__}: {exc}", []
        return False, last_error or "retry_exhausted", []

    async def _execute_number_with_retry(
        self,
        giveaway_id: int,
        account: TelegramAccount,
        action: dict,
        message,
    ) -> tuple[bool, dict, str | None, list[dict]]:
        minimum = action.get("number_value")
        maximum = action.get("number_value")
        if minimum is None:
            minimum = action.get("min_number")
            maximum = action.get("max_number")

        if minimum is None or maximum is None:
            return False, dict(action), "number_range_missing", []

        last_error = None
        for _ in range(NUMBER_SEND_RETRIES):
            value = await reserve_number(
                giveaway_id,
                account.user_id,
                int(minimum),
                int(maximum),
            )
            if value is None:
                return False, dict(action), "number_pool_exhausted", []

            action_for_account = dict(action)
            action_for_account["number_value"] = value
            ok, error, message_ids = await self._execute_with_retry(
                account,
                action_for_account,
                message,
            )
            if ok:
                await mark_number_sent(
                    giveaway_id,
                    account.user_id,
                    value,
                )
                return True, action_for_account, None, message_ids

            last_error = error or "number_send_failed"
            await release_number(
                giveaway_id,
                account.user_id,
                value,
            )
            await asyncio.sleep(ACTION_RETRY_DELAY_SECONDS)

        return False, dict(action), last_error, []

    async def execute_plan_for_all_accounts(
        self,
        giveaway_id: int,
        message,
        plan: list[dict],
    ) -> str:
        rows = await get_account_rows()
        enabled_user_ids = {
            int(row["user_id"])
            for row in rows
            if row["user_id"] is not None and row["enabled"] and row["authorized"]
        }
        participants = [
            account
            for account in self.accounts
            if account.user_id in enabled_user_ids
        ]
        if not participants:
            return GIVEAWAY_FAILED

        completed_by_account = {
            account.user_id: await get_completed_action_keys(
                giveaway_id,
                account.user_id,
            )
            for account in participants
        }

        async def run_for_account(
            index: int,
            account: TelegramAccount,
        ) -> bool:
            if ACCOUNT_ACTION_DELAY_MS * index:
                await asyncio.sleep(
                    (ACCOUNT_ACTION_DELAY_MS * index) / 1000
                )

            all_ok = True
            for step_index, action in enumerate(plan):
                if action_key(action) in completed_by_account[account.user_id]:
                    continue

                if action.get("code") == 6:
                    (
                        ok,
                        action_for_account,
                        error,
                        message_ids,
                    ) = await self._execute_number_with_retry(
                        giveaway_id,
                        account,
                        action,
                        message,
                    )
                else:
                    ok, error, message_ids = await self._execute_with_retry(
                        account,
                        action,
                        message,
                    )
                    action_for_account = dict(action)

                await mark_action(
                    giveaway_id,
                    account.user_id,
                    action_for_account,
                    step_index,
                    "success" if ok else "failed",
                    error,
                )
                if ok:
                    for sent in message_ids:
                        try:
                            await track_message(
                                giveaway_id,
                                account.user_id,
                                int(sent["chat_id"]),
                                int(sent["message_id"]),
                                "participation",
                            )
                        except Exception as exc:
                            print(
                                f"Tracking sent message failed: "
                                f"{type(exc).__name__}: {exc}"
                            )
                else:
                    all_ok = False

            return all_ok

        results = await asyncio.gather(
            *[
                run_for_account(index, account)
                for index, account in enumerate(participants)
            ]
        )

        number_actions = [
            action for action in plan if action.get("code") == 6
        ]
        if number_actions:
            action = number_actions[0]
            exact = action.get("number_value")
            minimum = (
                exact
                if exact is not None
                else action.get("min_number")
            )
            maximum = (
                exact
                if exact is not None
                else action.get("max_number")
            )
            if minimum is not None and maximum is not None:
                await maybe_clear_number_pool(
                    giveaway_id,
                    [account.user_id for account in participants],
                    int(minimum),
                    int(maximum),
                )

        if results and all(results):
            return GIVEAWAY_SUCCESS
        if any(results):
            return GIVEAWAY_PARTIAL
        return GIVEAWAY_FAILED

    async def retry_worker(self) -> None:
        while True:
            try:
                request = await claim_retry_giveaway()
                if request is None:
                    await asyncio.sleep(max(1, RETRY_POLL_SECONDS))
                    continue

                if not self.accounts:
                    await set_giveaway_status(
                        request["id"],
                        GIVEAWAY_FAILED,
                    )
                    continue

                primary = self.accounts[0]
                message = await primary.client.get_messages(
                    request["chat_id"],
                    ids=request["message_id"],
                )
                if message is None:
                    await set_giveaway_status(
                        request["id"],
                        GIVEAWAY_FAILED,
                    )
                    continue

                plan = build_plan(request["plan"] or {})
                if not plan:
                    await set_giveaway_status(
                        request["id"],
                        GIVEAWAY_NEEDS_HUMAN,
                    )
                    continue

                result = await self.execute_plan_for_all_accounts(
                    request["id"],
                    message,
                    plan,
                )
                await set_giveaway_status(request["id"], result)
            except Exception as exc:
                print(
                    f"Retry worker error: {type(exc).__name__}: {exc}"
                )
                await asyncio.sleep(max(1, RETRY_POLL_SECONDS))

    async def event_recovery_worker(self) -> None:
        while True:
            try:
                stale = await claim_stale_channel_event(
                    f"{self._worker_id}:recovery",
                    EVENT_LEASE_SECONDS,
                )
                if stale is None:
                    await asyncio.sleep(
                        max(1, EVENT_RECOVERY_POLL_SECONDS)
                    )
                    continue

                recovered = False
                for account in self.accounts:
                    try:
                        message = await account.client.get_messages(
                            stale["chat_id"],
                            ids=stale["message_id"],
                        )
                        if message is None:
                            continue

                        chat = await account.client.get_entity(
                            stale["chat_id"]
                        )
                        if not getattr(chat, "broadcast", False) or getattr(
                            chat, "megagroup", False
                        ):
                            continue

                        await self._process_channel_message(
                            account,
                            int(stale["chat_id"]),
                            message,
                            stale["event_kind"],
                            stale["event_marker"],
                        )
                        recovered = True
                        break
                    except Exception as exc:
                        print(
                            f"Event recovery attempt failed for "
                            f"{stale['chat_id']}:{stale['message_id']} "
                            f"using {account.session_name}: "
                            f"{type(exc).__name__}: {exc}"
                        )

                if recovered:
                    await complete_channel_event(
                        stale["event_marker"],
                        f"{self._worker_id}:recovery",
                    )
                else:
                    await release_channel_event(
                        stale["event_marker"],
                        f"{self._worker_id}:recovery",
                    )
            except Exception as exc:
                print(
                    f"Event recovery worker error: "
                    f"{type(exc).__name__}: {exc}"
                )
                await asyncio.sleep(
                    max(1, EVENT_RECOVERY_POLL_SECONDS)
                )

    async def heartbeat_worker(self) -> None:
        while True:
            try:
                for account in self.accounts:
                    await update_account_runtime(
                        account.user_id,
                        account.client.is_connected(),
                    )
            except Exception as exc:
                print(
                    f"Account heartbeat error: {type(exc).__name__}: {exc}"
                )
            await asyncio.sleep(30)

    async def run_until_disconnected(self) -> None:
        if not self.accounts:
            return
        await asyncio.gather(
            *[account.client.disconnected for account in self.accounts]
        )

    async def disconnect_all(self) -> None:
        for account in self.accounts:
            try:
                await update_account_runtime(account.user_id, False)
            except Exception:
                pass

        await asyncio.gather(
            *[
                account.client.disconnect()
                for account in self.accounts
            ],
            return_exceptions=True,
        )


async def run_telegram_clients() -> None:
    manager = AccountManager()
    await manager.start_all()
    manager._retry_task = asyncio.create_task(manager.retry_worker())
    manager._recovery_task = asyncio.create_task(
        manager.event_recovery_worker()
    )
    manager._heartbeat_task = asyncio.create_task(
        manager.heartbeat_worker()
    )
    try:
        print(
            f"Telegram accounts started: {len(manager.accounts)}; "
            f"primary={manager.accounts[0].session_name if manager.accounts else '-'}; "
            f"session directory: {TG_SESSION_DIR}"
        )
        await manager.run_until_disconnected()
    finally:
        for task in (
            manager._retry_task,
            manager._recovery_task,
            manager._heartbeat_task,
        ):
            if task:
                task.cancel()
        await asyncio.gather(
            manager._retry_task,
            manager._recovery_task,
            manager._heartbeat_task,
            return_exceptions=True,
        )
        await manager.disconnect_all()
