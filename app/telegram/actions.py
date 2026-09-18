from telethon.tl.functions.channels import JoinChannelRequest


class ActionExecutor:
    def __init__(self, client):
        self.client = client

    async def execute(self, action: dict, message) -> bool:
        kind = action["type"]

        if kind == "join_channel":
            channel = action.get("channel")
            if not channel:
                return False
            await self.client(JoinChannelRequest(channel))
            return True

        if kind == "click_button":
            button_text = action.get("text")
            if not button_text:
                return False
            for row in message.buttons or []:
                for button in row:
                    if getattr(button, "text", None) == button_text:
                        await message.click(text=button_text)
                        return True
            return False

        # Free-form comments, reactions, quizzes and guesses are kept explicit
        # until their exact Telegram target/rules are known.
        return False
