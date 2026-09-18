import asyncio
import hashlib
from dataclasses import dataclass
from pathlib import Path

from telethon import TelegramClient, events

import app.admin.notifier as notify
from app.config import (
    ACCOUNT_ACTION_DELAY_MS,
    AUTO_JOIN_ENABLED,
    MONITORED_CHATS,
    TG_2FA_PASSWORD,
    TG_API_HASH,
    TG_API_ID,
    TG_PHONE,
    TG_SESSION,
    TG_SESSION_DIR,
)
from app.database.db import (
    claim_channel_event,
    get_completed_action_keys,
    mark_action,
    mark_number_sent,
    mark_processed,
    maybe_clear_number_pool,
    release_number,
    reserve_number,
)
from app.giveaways.detector import detect
from app.giveaways.planner import action_key, build_plan
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

    def _session_paths(self) -> list[Path]:
        paths = sorted(Path(TG_SESSION_DIR).glob("*.session"))
        if paths:
            return paths
        return [Path(TG_SESSION_DIR) / TG_SESSION]

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
                catch_up=True,
                sequential_updates=False,
                flood_sleep_threshold=60,
            )
            await client.start(**kwargs)
            me = await client.get_me()
            if me is None:
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

        for account in self.accounts:
            account.client.add_event_handler(
                self._handle_new_message,
                events.NewMessage(chats=MONITORED_CHATS or None),
            )
            account.client.add_event_handler(
                self._handle_edited_message,
                events.MessageEdited(chats=MONITORED_CHATS or None),
            )

        for account in self.accounts:
            await account.client.catch_up()

        if self.accounts:
            await notify.start_user_client(self.accounts[0].client)

        return self.accounts

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
        if not event.chat_id:
            return
        if not await self._is_broadcast_channel(event):
            return

        marker = self._event_marker(event, kind)
        claimed = await claim_channel_event(
            chat_id=event.chat_id,
            message_id=event.id,
            event_kind=kind,
            event_marker=marker,
        )
        if not claimed:
            return

        # Only the first database claimant handles channel notifications,
        # preventing one notification per account for the same post/edit.
        await notify.incoming_message(event)

        chat = await event.get_chat()
        source_username = getattr(chat, "username", None)
        text = event.raw_text or "[message has no text/caption]"

        parsed = await detect(
            text,
            source_channel_username=source_username,
        )
        if not parsed.get("detected"):
            return

        giveaway_id = await mark_processed(
            event.chat_id,
            event.id,
            (parsed.get("actions") or [{}])[0].get("type", "unknown"),
            "ready"
            if AUTO_JOIN_ENABLED and not parsed.get("needs_human")
            else "detected_only",
            plan=parsed,
        )

        await notify.giveaway_detected(event, parsed)

        if not AUTO_JOIN_ENABLED or parsed.get("needs_human"):
            return

        plan = build_plan(parsed)
        if not plan:
            await mark_processed(
                event.chat_id,
                event.id,
                "unknown",
                "needs_human",
                plan=parsed,
            )
            return

        result = await self.execute_plan_for_all_accounts(
            giveaway_id=giveaway_id,
            message=event.message,
            plan=plan,
        )
        await mark_processed(
            event.chat_id,
            event.id,
            (parsed.get("actions") or [{}])[0].get("type", "unknown"),
            result,
            plan=parsed,
        )
        await notify.participation_result(event, parsed, result)

    async def execute_plan_for_all_accounts(
        self,
        giveaway_id: int,
        message,
        plan: list[dict],
    ) -> str:
        if not self.accounts:
            return "failed"

        completed_by_account: dict[int, set[str]] = {}
        for account in self.accounts:
            completed_by_account[account.user_id] = await get_completed_action_keys(
                giveaway_id,
                account.user_id,
            )

        number_assignments = await self._allocate_number_assignments(
            giveaway_id=giveaway_id,
            plan=plan,
            completed_by_account=completed_by_account,
        )

        async def run_for_account(index: int, account: TelegramAccount) -> bool:
            delay_seconds = (ACCOUNT_ACTION_DELAY_MS * index) / 1000
            if delay_seconds > 0:
                await asyncio.sleep(delay_seconds)

            all_ok = True
            for step_index, action in enumerate(plan):
                key = action_key(action)
                if key in completed_by_account[account.user_id]:
                    continue

                action_for_account = dict(action)
                if action.get("code") == 6:
                    assigned = number_assignments.get(account.user_id)
                    if assigned is None:
                        await mark_action(
                            giveaway_id=giveaway_id,
                            account_user_id=account.user_id,
                            action=action,
                            step_index=step_index,
                            status="skipped_number_exhausted",
                            error="No unique number available",
                        )
                        all_ok = False
                        continue
                    action_for_account["number_value"] = assigned

                try:
                    ok = await account.executor.execute(
                        action_for_account,
                        message,
                    )
                except Exception as exc:
                    ok = False
                    error = f"{type(exc).__name__}: {exc}"
                else:
                    error = None if ok else "executor_returned_false"

                await mark_action(
                    giveaway_id=giveaway_id,
                    account_user_id=account.user_id,
                    action=action_for_account,
                    step_index=step_index,
                    status="success" if ok else "failed",
                    error=error,
                )

                if action.get("code") == 6:
                    assigned = number_assignments.get(account.user_id)
                    if assigned is not None:
                        if ok:
                            await mark_number_sent(
                                giveaway_id,
                                account.user_id,
                                assigned,
                            )
                        else:
                            await release_number(
                                giveaway_id,
                                account.user_id,
                                assigned,
                            )

                if not ok:
                    all_ok = False

            return all_ok

        results = await asyncio.gather(
            *[
                run_for_account(index, account)
                for index, account in enumerate(self.accounts)
            ],
            return_exceptions=False,
        )


        await self._maybe_cleanup_number_pool(
            giveaway_id=giveaway_id,
            plan=plan,
        )

        if results and all(results):
            return "success"
        if any(results):
            return "partial_or_failed"
        return "failed"

    async def _allocate_number_assignments(
        self,
        giveaway_id: int,
        plan: list[dict],
        completed_by_account: dict[int, set[str]],
    ) -> dict[int, int]:
        number_actions = [action for action in plan if action.get("code") == 6]
        if not number_actions:
            return {}

        action = number_actions[0]
        exact_value = action.get("number_value")
        minimum = action.get("min_number")
        maximum = action.get("max_number")

        if exact_value is not None:
            minimum = exact_value
            maximum = exact_value

        if minimum is None or maximum is None:
            return {}

        assignments: dict[int, int] = {}
        action_key_value = action_key(action)
        for account in self.accounts:
            if action_key_value in completed_by_account.get(account.user_id, set()):
                continue

            value = await reserve_number(
                giveaway_id=giveaway_id,
                account_user_id=account.user_id,
                minimum=int(minimum),
                maximum=int(maximum),
            )
            if value is not None:
                assignments[account.user_id] = value

        return assignments

    async def _maybe_cleanup_number_pool(
        self,
        giveaway_id: int,
        plan: list[dict],
    ) -> None:
        number_actions = [action for action in plan if action.get("code") == 6]
        if not number_actions:
            return

        action = number_actions[0]
        exact_value = action.get("number_value")
        minimum = exact_value if exact_value is not None else action.get("min_number")
        maximum = exact_value if exact_value is not None else action.get("max_number")
        if minimum is None or maximum is None:
            return

        await maybe_clear_number_pool(
            giveaway_id=giveaway_id,
            account_user_ids=[account.user_id for account in self.accounts],
            minimum=int(minimum),
            maximum=int(maximum),
        )

    async def run_until_disconnected(self) -> None:
        if not self.accounts:
            print("No authorized Telegram session files found.")
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
    print(
        f"Telegram accounts started: {len(manager.accounts)}; "
        f"session directory: {TG_SESSION_DIR}"
    )
    await manager.run_until_disconnected()
