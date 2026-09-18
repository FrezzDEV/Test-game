from app.giveaways.planner import build_plan
from app.giveaways.schema import (
    ACTION_JOIN_CHANNEL,
    ACTION_REPLY_DISCUSSION,
)
from app.giveaways.sequences import get_sequence


def test_builds_join_and_comment_plan():
    parsed = {
        "detected": True,
        "confidence": 0.99,
        "channel_username": "@source_channel",
        "action_codes": [ACTION_JOIN_CHANNEL, ACTION_REPLY_DISCUSSION],
        "write_sequence_id": "giveaway_comment_default_v1",
        "actions": [
            {
                "code": ACTION_JOIN_CHANNEL,
                "type": "join_channel",
                "channel_username": "@required_channel",
            },
            {
                "code": ACTION_REPLY_DISCUSSION,
                "type": "reply_discussion",
                "target": "source_post",
            },
        ],
        "needs_human": False,
    }

    plan = build_plan(parsed)

    assert len(plan) == 2
    assert plan[1]["sequence_id"] == "giveaway_comment_default_v1"


def test_unknown_sequence_requires_human():
    parsed = {
        "detected": True,
        "confidence": 0.99,
        "channel_username": "@source_channel",
        "action_codes": [ACTION_REPLY_DISCUSSION],
        "write_sequence_id": "missing",
        "actions": [
            {
                "code": ACTION_REPLY_DISCUSSION,
                "type": "reply_discussion",
            }
        ],
        "needs_human": False,
    }

    assert build_plan(parsed) == []


def test_sequence_exists():
    assert get_sequence("giveaway_comment_default_v1")["steps"]
