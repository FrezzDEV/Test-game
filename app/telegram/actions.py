import asyncio

from telethon import errors
from telethon.tl.functions.channels import JoinChannelRequest

from app.giveaways.schema import (
    ACTION_CLICK_BUTTON,
    ACTION_JOIN_CHANNEL,
    ACTION_NUMBER_GUESS,
    ACTION_QUIZ,
    ACTION_REACTION,
    ACTION_REPLY_DISCUSSION,
    ACTION_WORD_GUESS,
)
from app.giveaways.sequences import get_sequence
from app.giveaways.strategies import choose_number


class ActionExecutor:
    def __init__(self, client):
        self.client = client

    async def _get_message_for_client(self, message):
        fresh = await self.client.get_messages(message.chat_id, ids=message.id)
        if isinstance(fresh, list):
            return fresh[0] if fresh else None
        return fresh

    async def _send_text(self, action: dict, message, value: str):
        target = action.get("target") or "source_post"
        if target in {"discussion", "linked_discussion", "comment"}:
            return await self.client.send_message(
                message.chat_id,
                value,
                comment_to=message.id,
            )
        return await self.client.send_message(
            message.chat_id,
            value,
            reply_to=message.id,
        )

    @staticmethod
    def _result(ok: bool, sent_messages: list | None = None) -> dict:
        return {
            "ok": ok,
            "message_ids": [
                {
                    "chat_id": int(item.chat_id),
                    "message_id": int(item.id),
                }
                for item in (sent_messages or [])
                if item is not None
            ],
        }

    async def execute(self, action: dict, message) -> dict:
        code = int(action["code"])

        if code == ACTION_JOIN_CHANNEL:
            channel = action.get("channel_username")
            if not channel:
                return self._result(False)
            try:
                await self.client(JoinChannelRequest(channel))
            except errors.UserAlreadyParticipantError:
                return self._result(True)
            return self._result(True)

        if code == ACTION_REPLY_DISCUSSION:
            sequence_id = action.get("sequence_id")
            sequence = get_sequence(sequence_id) if sequence_id else None
            if not sequence:
                return self._result(False)
            steps = sequence.get("steps") or []
            if not steps:
                return self._result(False)

            # Code 1 is always a linked-discussion comment.
            sent_messages = []
            for step in steps:
                text = step.get("text")
                if not text:
                    return self._result(False, sent_messages)
                sent = await self._send_text(
                    {"target": "discussion"},
                    message,
                    text,
                )
                sent_messages.append(sent)
                wait_seconds = float(step.get("wait_seconds_after", 0))
                if wait_seconds > 0:
                    await asyncio.sleep(wait_seconds)
            return self._result(True, sent_messages)

        if code == ACTION_CLICK_BUTTON:
            button_text = action.get("button_text")
            if not button_text:
                return self._result(False)
            account_message = await self._get_message_for_client(message)
            if account_message is None:
                return self._result(False)
            for row in account_message.buttons or []:
                for button in row:
                    if getattr(button, "text", None) == button_text:
                        await account_message.click(text=button_text)
                        return self._result(True)
            return self._result(False)

        if code == ACTION_REACTION:
            emoji = action.get("emoji")
            account_message = await self._get_message_for_client(message)
            react = (
                getattr(account_message, "react", None)
                if account_message
                else None
            )
            if not emoji or react is None:
                return self._result(False)
            await react(emoji)
            return self._result(True)

        if code == ACTION_NUMBER_GUESS:
            value = action.get("number_value")
            if value is None:
                if action.get("min_number") is None or action.get("max_number") is None:
                    return self._result(False)
                value = choose_number(
                    int(action["min_number"]),
                    int(action["max_number"]),
                )
            sent = await self._send_text(
                action,
                message,
                str(value),
            )
            return self._result(True, [sent])

        if code == ACTION_WORD_GUESS:
            value = action.get("word_answer") or action.get("exact_answer")
            if not value:
                return self._result(False)
            sent = await self._send_text(action, message, value)
            return self._result(True, [sent])

        if code == ACTION_QUIZ:
            button_text = action.get("button_text")
            exact_answer = action.get("exact_answer")
            if button_text:
                account_message = await self._get_message_for_client(message)
                if account_message is None:
                    return self._result(False)
                for row in account_message.buttons or []:
                    for button in row:
                        if getattr(button, "text", None) == button_text:
                            await account_message.click(text=button_text)
                            return self._result(True)
            if exact_answer:
                sent = await self._send_text(action, message, exact_answer)
                return self._result(True, [sent])
            return self._result(False)

        return self._result(False)
