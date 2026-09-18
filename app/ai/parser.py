import json

from openai import AsyncOpenAI

from app.config import AI_MIN_CONFIDENCE, OPENAI_API_KEY, OPENAI_MODEL


client = AsyncOpenAI(api_key=OPENAI_API_KEY)

SYSTEM_PROMPT = """
Analyze a Telegram post and extract a giveaway participation plan.
Return ONLY JSON:
{
  "is_giveaway": bool,
  "confidence": number,
  "type": "join_channel|click_button|comment_text|number_guess|word_guess|quiz|reaction|multiple_actions|unknown",
  "actions": [
    {
      "type": "...",
      "channel": null,
      "text": null,
      "emoji": null,
      "min_number": null,
      "max_number": null
    }
  ],
  "needs_human": bool,
  "reason": string|null
}
Never invent missing requirements or answers.
For ambiguous requirements, set needs_human=true.
If a number/word answer is not explicitly present in the post, do not invent it.
For possible CAPTCHA/anti-bot requirements, set needs_human=true.
"""


async def parse_giveaway(text: str) -> dict:
    response = await client.responses.create(
        model=OPENAI_MODEL,
        input=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
    )
    result = json.loads(response.output_text)
    if result.get("confidence", 0) < AI_MIN_CONFIDENCE and result.get("is_giveaway"):
        result["needs_human"] = True
    return result
