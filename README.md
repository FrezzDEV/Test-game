# multi-auto-quiz-tg

Telegram user-account automation for detecting giveaways, extracting participation rules with AI, executing only validated deterministic actions, and notifying a separate admin bot.

## Architecture

```
Telegram user session (Telethon)
        |
        v
message monitor
        |
        v
AI detector/parser
        |
        v
validated GiveawayPlan
        |
        +--> PostgreSQL (plan + history)
        |
        v
deterministic planner
        |
        v
action executor
        |
        +--> join channel
        +--> click button
        +--> comment in linked discussion
        +--> reaction
        +--> explicit number/word answer
        +--> explicit quiz answer
```

The AI does not generate the final comment for action code 1. It selects a pre-approved local `sequence_id`, and the executor loads the actual text from `config/action_sequences.json`.

## AI response contract

The parser returns a stable structure like:

```json
{
  "detected": true,
  "confidence": 0.98,
  "channel_username": "@source_channel",
  "action_codes": [2, 1],
  "write_sequence_id": "giveaway_comment_default_v1",
  "actions": [
    {
      "code": 2,
      "type": "join_channel",
      "channel_username": "@required_channel"
    },
    {
      "code": 1,
      "type": "reply_discussion",
      "target": "source_post",
      "sequence_id": "giveaway_comment_default_v1"
    }
  ],
  "needs_human": false,
  "reason": null,
  "conditions": []
}
```

### Action codes

| Code | Action |
|---:|---|
| 1 | reply in the discussion of the source post |
| 2 | join required channel |
| 3 | click an exact button |
| 4 | add an exact reaction |
| 5 | answer an explicit quiz answer/button |
| 6 | send an exact number or a random number inside an explicitly stated range |
| 7 | send an explicitly stated word |
| 8 | reserved for future composite actions; never executed implicitly |

`action_codes` is the ordered execution list. Each item in `actions[]` carries the same `code` plus the parameters needed by that action.

## Predefined write sequences

The local sequence catalog is controlled by:

```env
ACTION_SEQUENCES_PATH=./config/action_sequences.json
```

A sequence looks like:

```json
{
  "giveaway_comment_default_v1": {
    "description": "Один заранее заданный нейтральный комментарий участника.",
    "steps": [
      {
        "text": "Участвую! 🍀",
        "wait_seconds_after": 0
      }
    ]
  }
}
```

The model may choose only an existing sequence ID. It cannot invent or rewrite the message text.

## Environment

The `.env` file contains:
- Telegram API ID/hash and user-session settings.
- OpenAI API key/model and AI confidence threshold.
- Telegram Bot API token for the separate admin bot.
- Admin chat/user IDs.
- PostgreSQL connection string.
- Monitoring and safety settings.
- Local action-sequence catalog path.

Never commit real credentials, `.env`, Telegram session files, or bot tokens.

## Database

`sql/schema.sql` stores:
- detected giveaways and their full validated plan;
- source channel username and AI confidence;
- action code/type, sequence ID, payload, status and error;
- tracked messages and notifications;
- reminder records.

The schema contains additive `ALTER TABLE ... IF NOT EXISTS` statements so it can upgrade an existing installation.

## Notifications

The admin bot can report:
- detected giveaways and the AI action codes;
- participation results;
- replies specifically to messages authored by the user account;
- mentions of the user account;
- possible win messages.

## Safety / execution rules

`AUTO_JOIN_ENABLED=false` by default.

The system stops automatic execution when:
- the AI confidence is below `AI_MIN_CONFIDENCE`;
- a required parameter is missing or ambiguous;
- CAPTCHA or anti-bot instructions are detected;
- a quiz/word answer is not explicitly available;
- a number target/range is not explicitly available;
- a requested action is not in the deterministic executor.

The executor does not bypass CAPTCHA or anti-bot challenges.

## Telegram comments

Telethon provides the `comment_to` parameter on `send_message` for leaving a comment on a broadcast-channel post through its linked discussion. Action code 1 uses that mechanism.

## Run

1. Copy the template: `cp .env.example .env`.
2. Fill in credentials and monitoring settings.
3. Install dependencies: `pip install -r requirements.txt`.
4. Apply `sql/schema.sql` to PostgreSQL.
5. Start: `python -m app.main`.

The Telethon session is stored under `TG_SESSION_DIR` and is excluded from Git.
