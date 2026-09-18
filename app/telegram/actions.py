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

    async def _send_text(self, action: dict, message, value: str) -> None:
        target = action.get("target") or "source_post"
        if target in {"discussion", "linked_discussion", "comment"}:
            await self.client.send_message(
                message.chat_id,
                value,
                comment_to=message.id,
            )
            return
        await self.client.send_message(
            message.chat_id,
            value,
            reply_to=message.id,
        )

    async def execute(self, action: dict, message) -> bool:
        code = int(action["code"])

        if code == ACTION_JOIN_CHANNEL:
            channel = action.get("channel_username")
            if not channel:
                return False
            try:
                await self.client(JoinChannelRequest(channel))
            except errors.UserAlreadyParticipantError:
                return True
            return True

        if code == ACTION_REPLY_DISCUSSION:
            sequence_id = action.get("sequence_id")
            sequence = get_sequence(sequence_id) if sequence_id else None
            if not sequence:
                return False
            steps = sequence.get("steps") or []
            if not steps:
                return False

            target_override = action.get("target") or "discussion"
            for step in steps:
                text = step.get("text")
                if not text:
                    return False
                await self._send_text({"target": target_override}, message, text)
                wait_seconds = float(step.get("wait_seconds_after", 0))
                if wait_seconds > 0:
                    await asyncio.sleep(wait_seconds)
            return True

        if code == ACTION_CLICK_BUTTON:
            button_text = action.get("button_text")
            if not button_text:
                return False
            account_message = await self._get_message_for_client(message)
            if account_message is None:
                return False
            for row in account_message.buttons or []:
                for button in row:
                    if getattr(button, "text", None) == button_text:
                        await account_message.click(text=button_text)
                        return True
            return False

        if code == ACTION_REACTION:
            emoji = action.get("emoji")
            account_message = await self._get_message_for_client(message)
            react = getattr(account_message, "react", None) if account_message else None
            if not emoji or react is None:
                return False
            await react(emoji)
            return True

        if code == ACTION_NUMBER_GUESS:
            value = action.get("number_value")
            if value is None:
                if action.get("min_number") is None or action.get("max_number") is None:
                    return False
                value = choose_number(
                    int(action["min_number"]),
                    int(action["max_number"]),
                )
            await self._send_text(action, message, str(value))
            return True

        if code == ACTION_WORD_GUESS:
            value = action.get("word_answer") or action.get("exact_answer")
            if not value:
                return False
            await self._send_text(action, message, value)
            return True

        if code == ACTION_QUIZ:
            button_text = action.get("button_text")
            exact_answer = action.get("exact_answer")
            if button_text:
                account_message = await self._get_message_for_client(message)
                if account_message is None:
                    return False
                for row in account_message.buttons or []:
                    for button in row:
                        if getattr(button, "text", None) == button_text:
                            await account_message.click(text=button_text)
                            return True
            if exact_answer:
                await self._send_text(action, message, exact_answer)
                return True
            return False

        return False
