import asyncio
import hashlib
from dataclasses import dataclass
from pathlib import Path

from telethon import TelegramClient, errors, events

import app.admin.notifier as notify
from app.config import (
    ACCOUNT_ACTION_DELAY_MS,
    ACTION_RETRY_DELAY_SECONDS,
    AUTO_JOIN_ENABLED,
    MONITORED_CHANNELS,
    NUMBER_SEND_RETRIES,
    REMINDER_INTERVAL_MINUTES,
    RETRY_POLL_SECONDS,
    SCAN_OLD_MESSAGES,
    TG_2FA_PASSWORD,
    TG_API_HASH,
    TG_API_ID,
    TG_PHONE,
    TG_SESSION,
    TG_SESSION_DIR,
)
from app.database.db import (
    claim_channel_event,
    claim_retry_giveaway,
    get_completed_action_keys,
    mark_action,
    mark_number_sent,
    mark_processed,
    maybe_clear_number_pool,
    record_giveaway_version,
    release_number,
    reserve_number,
    schedule_reminder,
    set_giveaway_status,
    upsert_account,
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
    GIVEAWAY_SUCCESS,
)
from app.telegram.actions import ActionExecutor

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

    def _session_paths(self) -> list[Path]:
        paths = sorted(Path(TG_SESSION_DIR).glob("*.session"))
        return paths or [Path(TG_SESSION_DIR) / f"{TG_SESSION}.session"]

    async def start_all(self) -> list[TelegramAccount]:
        if self.accounts:
            return self.accounts

        kwargs = {}
        if TG_PHONE:
            kwargs["phone"] = TG_PHONE
        if TG_2FA_PASSWORD:
            kwargs["password"] = TG_2FA_PASSWORD

        for session_path in self._session_paths():
            client = TelegramClient(
                str(session_path.with_suffix("")),
                TG_API_ID,
                TG_API_HASH,
                catch_up=SCAN_OLD_MESSAGES,
                sequential_updates=False,
                flood_sleep_threshold=60,
            )
            try:
                await client.start(**kwargs)
                me = await client.get_me()
                if me is None:
                    await upsert_account(
                        session_path.stem, None, None, False, "not_authorized"
                    )
                    await client.disconnect()
                    continue
            except Exception as exc:
                await upsert_account(
                    session_path.stem, None, None, False,
                    f"{type(exc).__name__}: {exc}",
                )
                await client.disconnect()
                continue

            account = TelegramAccount(
                session_name=session_path.stem,
                session_path=str(session_path),
                client=client,
                executor=ActionExecutor(client),
                user_id=int(me.id),
                username=getattr(me, "username", None),
            )
            self.accounts.append(account)
            self._account_by_client[client] = account

            await upsert_account(
                account.session_name,
                account.user_id,
                account.username,
                True,
                None,
            )
            await notify.register_account(account)

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

    async def _handle_user_message(self, event) -> None:
        account = self._account_by_client.get(event.client)
        if account is not None:
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

    async def _handle_channel_event(self, event, kind: str) -> None:
        if not event.chat_id or not await self._is_broadcast_channel(event):
            return

        marker = self._event_marker(event, kind)
        if not await claim_channel_event(event.chat_id, event.id, kind, marker):
            return

        chat = await event.get_chat()
        source_username = getattr(chat, "username", None)
        text = event.raw_text or "[message has no text/caption]"

        parsed = await detect(
            text,
            source_channel_username=source_username,
        )
        if not parsed.get("detected"):
            return

        giveaway_type = (
            (parsed.get("actions") or [{}])[0].get("type", "unknown")
        )
        giveaway_id = await mark_processed(
            event.chat_id,
            event.id,
            giveaway_type,
            GIVEAWAY_PARSED,
            plan=parsed,
        )
        await record_giveaway_version(giveaway_id, kind, marker, parsed)
        await schedule_reminder(giveaway_id, REMINDER_INTERVAL_MINUTES)
        await notify.giveaway_detected(event, parsed)

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
            event.message,
            plan,
        )
        await mark_processed(
            event.chat_id,
            event.id,
            giveaway_type,
            result,
            plan=parsed,
        )
        await notify.participation_result(event, parsed, result)

    async def _execute_with_retry(
        self,
        account: TelegramAccount,
        action: dict,
        message,
    ) -> tuple[bool, str | None]:
        last_error = None
        for attempt in range(1, NUMBER_SEND_RETRIES + 1):
            try:
                return await account.executor.execute(action, message), None
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
                return False, f"{type(exc).__name__}: {exc}"
        return False, last_error or "retry_exhausted"

    async def _execute_number_with_retry(
        self,
        giveaway_id: int,
        account: TelegramAccount,
        action: dict,
        message,
    ) -> tuple[bool, dict, str | None]:
        minimum = action.get("number_value")
        maximum = action.get("number_value")
        if minimum is None:
            minimum = action.get("min_number")
            maximum = action.get("max_number")

        if minimum is None or maximum is None:
            return False, dict(action), "number_range_missing"

        last_error = None
        for _ in range(NUMBER_SEND_RETRIES):
            value = await reserve_number(
                giveaway_id,
                account.user_id,
                int(minimum),
                int(maximum),
            )
            if value is None:
                return False, dict(action), "number_pool_exhausted"

            action_for_account = dict(action)
            action_for_account["number_value"] = value
            ok, error = await self._execute_with_retry(
                account,
                action_for_account,
                message,
            )
            if ok:
                await mark_number_sent(giveaway_id, account.user_id, value)
                return True, action_for_account, None

            last_error = error or "number_send_failed"
            await release_number(
                giveaway_id,
                account.user_id,
                value,
            )
            await asyncio.sleep(ACTION_RETRY_DELAY_SECONDS)

        return False, dict(action), last_error

    async def execute_plan_for_all_accounts(
        self,
        giveaway_id: int,
        message,
        plan: list[dict],
    ) -> str:
        if not self.accounts:
            return GIVEAWAY_FAILED

        completed_by_account = {
            account.user_id: await get_completed_action_keys(
                giveaway_id,
                account.user_id,
            )
            for account in self.accounts
        }

        async def run_for_account(index: int, account: TelegramAccount) -> bool:
            if ACCOUNT_ACTION_DELAY_MS * index:
                await asyncio.sleep(
                    (ACCOUNT_ACTION_DELAY_MS * index) / 1000
                )

            all_ok = True
            for step_index, action in enumerate(plan):
                if action_key(action) in completed_by_account[account.user_id]:
                    continue

                if action.get("code") == 6:
                    ok, action_for_account, error = await self._execute_number_with_retry(
                        giveaway_id, account, action, message
                    )
                else:
                    ok, error = await self._execute_with_retry(
                        account, action, message
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
                if not ok:
                    all_ok = False

            return all_ok

        results = await asyncio.gather(
            *[
                run_for_account(index, account)
                for index, account in enumerate(self.accounts)
            ]
        )

        number_actions = [action for action in plan if action.get("code") == 6]
        if number_actions:
            action = number_actions[0]
            exact = action.get("number_value")
            minimum = exact if exact is not None else action.get("min_number")
            maximum = exact if exact is not None else action.get("max_number")
            if minimum is not None and maximum is not None:
                await maybe_clear_number_pool(
                    giveaway_id,
                    [account.user_id for account in self.accounts],
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
                    await set_giveaway_status(request["id"], GIVEAWAY_FAILED)
                    continue

                message = await self.accounts[0].client.get_messages(
                    request["chat_id"],
                    ids=request["message_id"],
                )
                if message is None:
                    await set_giveaway_status(request["id"], GIVEAWAY_FAILED)
                    continue

                plan = build_plan(request["plan"] or {})
                if not plan:
                    await set_giveaway_status(request["id"], GIVEAWAY_NEEDS_HUMAN)
                    continue

                result = await self.execute_plan_for_all_accounts(
                    request["id"],
                    message,
                    plan,
                )
                await set_giveaway_status(request["id"], result)
            except Exception as exc:
                print(f"Retry worker error: {type(exc).__name__}: {exc}")
                await asyncio.sleep(max(1, RETRY_POLL_SECONDS))

    async def run_until_disconnected(self) -> None:
        if not self.accounts:
            return
        await asyncio.gather(
            *[account.client.disconnected for account in self.accounts]
        )

    async def disconnect_all(self) -> None:
        await asyncio.gather(
            *[account.client.disconnect() for account in self.accounts],
            return_exceptions=True,
        )


async def run_telegram_clients() -> None:
    manager = AccountManager()
    await manager.start_all()
    manager._retry_task = asyncio.create_task(manager.retry_worker())
    try:
        print(
            f"Telegram accounts started: {len(manager.accounts)}; "
            f"session directory: {TG_SESSION_DIR}"
        )
        await manager.run_until_disconnected()
    finally:
        if manager._retry_task:
            manager._retry_task.cancel()
            await asyncio.gather(manager._retry_task, return_exceptions=True)
        await manager.disconnect_all()
