import hashlib
import json

from app.giveaways.schema import (
    ACTION_CLICK_BUTTON,
    ACTION_JOIN_CHANNEL,
    ACTION_NUMBER_GUESS,
    ACTION_QUIZ,
    ACTION_REACTION,
    ACTION_REPLY_DISCUSSION,
    ACTION_WORD_GUESS,
    GiveawayPlan,
)
from app.giveaways.sequences import get_sequence


def action_key(action: dict) -> str:
    payload = {
        key: value
        for key, value in action.items()
        if key != "number_value"
        or action.get("min_number") == action.get("max_number")
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_plan(parsed: dict) -> list[dict]:
    try:
        plan = GiveawayPlan.model_validate(parsed)
    except Exception:
        return []

    if not plan.detected or plan.needs_human:
        return []

    result: list[dict] = []
    number_action_count = 0

    for action in plan.actions:
        data = action.model_dump()

        if action.code == ACTION_REPLY_DISCUSSION:
            sequence_id = action.sequence_id or plan.write_sequence_id
            sequence = get_sequence(sequence_id) if sequence_id else None
            if not sequence or not sequence.get("steps"):
                return []
            data["sequence_id"] = sequence_id
            data["target"] = "discussion"

        elif action.code == ACTION_JOIN_CHANNEL:
            if not action.channel_username:
                return []
            data["channel_username"] = action.channel_username

        elif action.code == ACTION_CLICK_BUTTON:
            if not action.button_text:
                return []

        elif action.code == ACTION_REACTION:
            if not action.emoji:
                return []

        elif action.code == ACTION_QUIZ:
            if not action.exact_answer and not action.button_text:
                return []

        elif action.code == ACTION_NUMBER_GUESS:
            number_action_count += 1
            if action.number_value is None:
                if action.min_number is None or action.max_number is None:
                    return []
                if action.min_number > action.max_number:
                    return []

        elif action.code == ACTION_WORD_GUESS:
            if not action.word_answer and not action.exact_answer:
                return []
            if not action.word_answer:
                data["word_answer"] = action.exact_answer

        else:
            return []

        result.append(data)

    if number_action_count > 1:
        return []

    return result
