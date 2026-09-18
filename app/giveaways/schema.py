from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


ACTION_REPLY_DISCUSSION = 1
ACTION_JOIN_CHANNEL = 2
ACTION_CLICK_BUTTON = 3
ACTION_REACTION = 4
ACTION_QUIZ = 5
ACTION_NUMBER_GUESS = 6
ACTION_WORD_GUESS = 7
ACTION_MULTIPLE_ACTIONS = 8

ACTION_TYPES = {
    ACTION_REPLY_DISCUSSION: "reply_discussion",
    ACTION_JOIN_CHANNEL: "join_channel",
    ACTION_CLICK_BUTTON: "click_button",
    ACTION_REACTION: "reaction",
    ACTION_QUIZ: "quiz",
    ACTION_NUMBER_GUESS: "number_guess",
    ACTION_WORD_GUESS: "word_guess",
    ACTION_MULTIPLE_ACTIONS: "multiple_actions",
}

ActionType = Literal[
    "reply_discussion",
    "join_channel",
    "click_button",
    "reaction",
    "quiz",
    "number_guess",
    "word_guess",
    "multiple_actions",
]


class GiveawayAction(BaseModel):
    model_config = ConfigDict(extra="ignore")

    code: int = Field(ge=1, le=8)
    type: ActionType
    target: str | None = None
    channel_username: str | None = None
    button_text: str | None = None
    emoji: str | None = None
    sequence_id: str | None = None
    exact_answer: str | None = None
    min_number: int | None = None
    max_number: int | None = None
    number_value: int | None = None
    word_answer: str | None = None

    @model_validator(mode="after")
    def validate_code_type(self):
        expected = ACTION_TYPES.get(self.code)
        if expected != self.type:
            raise ValueError(
                f"Action code {self.code} does not match type {self.type!r}"
            )
        return self


class GiveawayPlan(BaseModel):
    model_config = ConfigDict(extra="ignore")

    detected: bool
    confidence: float = Field(ge=0, le=1)
    channel_username: str | None = None
    action_codes: list[int] = Field(default_factory=list)
    write_sequence_id: str | None = None
    actions: list[GiveawayAction] = Field(default_factory=list)
    needs_human: bool = False
    reason: str | None = None
    conditions: list[str] = Field(default_factory=list)

    @property
    def has_number_action(self) -> bool:
        return any(action.code == ACTION_NUMBER_GUESS for action in self.actions)

    @model_validator(mode="after")
    def normalize_action_codes(self):
        actual = [action.code for action in self.actions]
        if not self.action_codes:
            self.action_codes = actual
        elif self.action_codes != actual:
            self.action_codes = actual
        return self


def safe_empty_plan(reason: str) -> dict:
    return GiveawayPlan(
        detected=False,
        confidence=0.0,
        needs_human=False,
        reason=reason,
    ).model_dump()
