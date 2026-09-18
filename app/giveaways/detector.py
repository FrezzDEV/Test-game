from app.ai.parser import parse_giveaway


async def detect(
    text: str,
    source_channel_username: str | None = None,
) -> dict:
    return await parse_giveaway(
        text,
        source_channel_username=source_channel_username,
    )
