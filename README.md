# multi-auto-quiz-tg

Telegram user-account automation for detecting giveaways, extracting participation rules with AI, executing only validated deterministic actions, and notifying a separate admin bot.

## Architecture

```
Telegram user sessions (Telethon)
        |
        v
broadcast-channel monitor
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

## Multi-account sessions

All `*.session` files directly inside `TG_SESSION_DIR` are loaded on startup. Each file is treated as a separate Telegram user account with its own Telethon client. Separate session files matter because Telethon's default SQLite session storage should not be opened by multiple clients at the same time.

For participation, every discovered account receives the same validated plan. Accounts execute concurrently, with an optional stagger controlled by:

```env
ACCOUNT_ACTION_DELAY_MS=250
```

`0` means no artificial stagger. `250` means account 2 starts 250 ms after account 1, account 3 after 500 ms, and so on.

## Channel monitoring

Every discovered account registers both `NewMessage` and `MessageEdited`. The handler first verifies that the peer is a Telegram **broadcast channel** (`broadcast=true` and not `megagroup=true`). Linked discussion groups and ordinary chats are ignored.

When `MONITORED_CHANNELS` is empty, every broadcast channel that sends updates to the account is considered. When it is set, only the configured channels are processed.

The monitor uses Telethon update catch-up on startup, so missed updates can be delivered after reconnecting.

The database stores a unique event marker for each new/edit event. This prevents multiple accounts from sending the same post to the AI repeatedly while still allowing a distinct edit of the same post to be analyzed again.

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
- action code/type, account ID, sequence ID, payload, status and error;
- temporary unique-number reservations for number-guess actions;
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

## Unique numbers

For a `number_guess` action with an explicit range, the application atomically reserves one different number per account in PostgreSQL. A PostgreSQL advisory transaction lock prevents concurrent account tasks from reserving the same number.

The temporary `number_pool` is cleaned when all active accounts have sent their number or when the available range is exhausted. Successful number values remain in the action history, so cleaning the temporary pool does not permit the same number to be reused for the same giveaway.

An exact single-number answer is therefore available to at most one account under the unique-number rule. Other accounts continue with their remaining plan steps and report the number step as unavailable.

## Telegram comments

Telethon provides the `comment_to` parameter on `send_message` for leaving a comment on a broadcast-channel post through its linked discussion. Action code 1 uses that mechanism.

## Run

1. Copy the template: `cp .env.example .env`.
2. Fill in credentials and monitoring settings.
3. Install dependencies: `pip install -r requirements.txt`.
4. Apply `sql/schema.sql` to PostgreSQL.
5. Start: `python -m app.main`.

The Telethon session is stored under `TG_SESSION_DIR` and is excluded from Git.


## Completion of the 1-8 operational layer

1. **State machine:** giveaway status is persisted as parsed, ready, executing, success, partial, failed, needs_human, retry_requested or skipped.
2. **Edit versions:** every detected edit gets a separate immutable record in `giveaway_versions`; the main giveaway row keeps the current plan/version. Completed actions are not repeated after an edit.
3. **Number retry:** failed numeric sends release the reserved number and retry with another unique value, while successful values stay in action history.
4. **Account registry:** every discovered session is registered in `telegram_accounts` with authorization/error state and last-seen timestamp.
5. **Multi-account notifications:** reply, mention and possible-win notifications identify the specific account that received the event.
6. **Admin controls:** `/status`, `/accounts`, `/pending` and `/retry <giveaway_id>` are backed by PostgreSQL.
7. **Win detector:** new incoming messages with winner-like terms can produce a deduplicated "possible win" notification tied to a recent giveaway.
8. **Reminder worker:** due reminder rows are polled by a background worker and sent through the admin Bot API.

The automation still does not solve CAPTCHAs/anti-bot challenges and does not invent missing answers.