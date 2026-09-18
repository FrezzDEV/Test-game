import asyncio

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

    async def execute(self, action: dict, message) -> bool:
        code = int(action["code"])

        if code == ACTION_JOIN_CHANNEL:
            channel = action.get("channel_username")
            if not channel:
                return False
            await self.client(JoinChannelRequest(channel))
            return True

        if code == ACTION_REPLY_DISCUSSION:
            sequence_id = action.get("sequence_id")
            sequence = get_sequence(sequence_id) if sequence_id else None
            if not sequence:
                return False

            target = message.chat_id
            steps = sequence.get("steps") or []
            if not steps:
                return False

            for step in steps:
                text = step.get("text")
                if not text:
                    return False
                await self.client.send_message(
                    target,
                    text,
                    comment_to=message.id,
                )
                wait_seconds = float(step.get("wait_seconds_after", 0))
                if wait_seconds > 0:
                    await asyncio.sleep(wait_seconds)

            return True

        if code == ACTION_CLICK_BUTTON:
            button_text = action.get("button_text")
            if not button_text:
                return False
            for row in message.buttons or []:
                for button in row:
                    if getattr(button, "text", None) == button_text:
                        await message.click(text=button_text)
                        return True
            return False

        if code == ACTION_REACTION:
            emoji = action.get("emoji")
            react = getattr(message, "react", None)
            if not emoji or react is None:
                return False
            await react(emoji)
            return True

        if code == ACTION_NUMBER_GUESS:
            if action.get("number_value") is not None:
                value = str(action["number_value"])
            else:
                value = choose_number(
                    int(action["min_number"]),
                    int(action["max_number"]),
                )
            await self.client.send_message(
                message.chat_id,
                value,
                reply_to=message.id,
            )
            return True

        if code == ACTION_WORD_GUESS:
            value = action.get("word_answer") or action.get("exact_answer")
            if not value:
                return False
            await self.client.send_message(
                message.chat_id,
                value,
                reply_to=message.id,
            )
            return True

        if code == ACTION_QUIZ:
            button_text = action.get("button_text")
            exact_answer = action.get("exact_answer")
            if button_text:
                for row in message.buttons or []:
                    for button in row:
                        if getattr(button, "text", None) == button_text:
                            await message.click(text=button_text)
                            return True
            if exact_answer:
                await self.client.send_message(
                    message.chat_id,
                    exact_answer,
                    reply_to=message.id,
                )
                return True
            return False

        return False
