import json

from openai import AsyncOpenAI
from pydantic import ValidationError

from app.config import AI_MIN_CONFIDENCE, OPENAI_API_KEY, OPENAI_MODEL
from app.giveaways.schema import ACTION_TYPES, GiveawayPlan
from app.giveaways.sequences import available_sequence_ids


client = AsyncOpenAI(api_key=OPENAI_API_KEY)


def _invalid_ai_plan(reason: str) -> dict:
    return GiveawayPlan(
        detected=False,
        confidence=0.0,
        needs_human=True,
        reason=reason,
    ).model_dump()


def _normalize_payload(data: dict) -> dict:
    normalized = dict(data)

    if "detected" not in normalized and "is_giveaway" in normalized:
        normalized["detected"] = bool(normalized["is_giveaway"])

    action_codes_by_type = {value: key for key, value in ACTION_TYPES.items()}
    actions = []
    for raw_action in normalized.get("actions", []) or []:
        action = dict(raw_action)
        action_type = action.get("type")
        if "code" not in action and action_type in action_codes_by_type:
            action["code"] = action_codes_by_type[action_type]
        if (
            action.get("type") in {"number_guess", "word_guess"}
            and not action.get("exact_answer")
            and action.get("word_answer")
        ):
            action["exact_answer"] = action["word_answer"]
        actions.append(action)

    normalized["actions"] = actions
    return normalized


SYSTEM_PROMPT_TEMPLATE = """
Analyze a Telegram post and extract ONLY the participation rules.
Do not solve puzzles unless the answer is explicitly present in the post.
Do not invent usernames, answers, comments, ranges, buttons, emojis, or requirements.
The executor will perform actions later; your job is to describe the plan.

Return ONLY valid JSON with this exact top-level structure:
{
  "detected": true,
  "confidence": 0.0,
  "channel_username": "@source_channel_or_null",
  "action_codes": [2, 1],
  "write_sequence_id": "giveaway_comment_default_v1_or_null",
  "actions": [
    {
      "code": 2,
      "type": "join_channel",
      "target": "required_channel",
      "channel_username": "@required_channel",
      "button_text": null,
      "emoji": null,
      "sequence_id": null,
      "exact_answer": null,
      "min_number": null,
      "max_number": null,
      "number_value": null,
      "word_answer": null
    },
    {
      "code": 1,
      "type": "reply_discussion",
      "target": "source_post",
      "channel_username": null,
      "button_text": null,
      "emoji": null,
      "sequence_id": "giveaway_comment_default_v1",
      "exact_answer": null,
      "min_number": null,
      "max_number": null,
      "number_value": null,
      "word_answer": null
    }
  ],
  "needs_human": false,
  "reason": null,
  "conditions": []
}

Action code map:
1 = reply_discussion
2 = join_channel
3 = click_button
4 = reaction
5 = quiz
6 = number_guess
7 = word_guess
8 = multiple_actions (reserved; normally use several actions instead)

Rules:
- "detected" is true only when the post is actually a giveaway/contest.
- "channel_username" means the SOURCE channel where this post was detected. The application supplies it separately; never guess it from a random linked channel.
- "action_codes" must be in the exact execution order and must match actions[].code.
- For action code 1, NEVER generate the final comment text. Return only "sequence_id".
- write_sequence_id must be one of the allowed sequence IDs below.
- A sequence is a local, pre-approved path of text/messages. The model may select an ID, but never invent its content.
- For action code 2, return the required channel username exactly as written or as an unambiguous public username.
- For action code 3, return the exact button text if known.
- For action code 4, return the exact required emoji if known.
- For action code 5, auto-execution is allowed only when the exact answer/button is explicitly present in the post; otherwise needs_human=true.
- For action code 6, use number_value when an exact number is explicitly given. If the post explicitly provides a valid numeric range, min_number/max_number may be used. Never invent a target or range.
- For action code 7, use word_answer only when the exact word is explicitly present. Otherwise needs_human=true.
- CAPTCHA, anti-bot checks, external web forms, ambiguous instructions, or missing critical data => needs_human=true.
- If there are multiple required actions, return multiple action objects in the exact order required.

Allowed sequence IDs:
{sequence_ids}
"""


async def parse_giveaway(
    text: str,
    source_channel_username: str | None = None,
) -> dict:
    sequence_ids = available_sequence_ids()
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
        sequence_ids=", ".join(sequence_ids) if sequence_ids else "(none)"
    )
    source_context = json.dumps(
        {
            "source_channel_username": source_channel_username,
            "message_text": text,
        },
        ensure_ascii=False,
    )

    try:
        response = await client.responses.create(
            model=OPENAI_MODEL,
            input=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": source_context},
            ],
        )
        raw = json.loads(response.output_text)
        data = _normalize_payload(raw)

        if source_channel_username:
            data["channel_username"] = source_channel_username

        plan = GiveawayPlan.model_validate(data)

        if plan.detected and plan.confidence < AI_MIN_CONFIDENCE:
            plan.needs_human = True
            plan.reason = (
                plan.reason
                or f"AI confidence below threshold ({AI_MIN_CONFIDENCE:.2f})"
            )

        return plan.model_dump()
    except (json.JSONDecodeError, TypeError, ValidationError, ValueError) as exc:
        return _invalid_ai_plan(
            f"AI plan validation failed: {type(exc).__name__}"
        )
