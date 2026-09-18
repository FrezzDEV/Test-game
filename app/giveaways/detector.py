from app.ai.parser import parse_giveaway


async def detect(text: str) -> dict:
    return await parse_giveaway(text)
