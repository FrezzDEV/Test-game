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
        +--> PostgreSQL (channel-partitioned plan + history)
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

The AI only analyzes the source broadcast-channel post text. It does not access the linked discussion, chat history, polls, buttons or Telegram entities.

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
      "target": "discussion",
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
| 1 | reply in the linked discussion of the source post |
| 2 | join required channel |
| 3 | click an exact button |
| 4 | add an exact reaction |
| 5 | answer an explicit quiz answer/button |
| 6 | send an exact number or a random number inside an explicitly stated range |
| 7 | send an explicitly stated word |
| 8 | reserved for future composite actions; never executed implicitly |

`action_codes` is checked against `actions[].code`. An inconsistent response is rejected instead of silently rewritten. When the list is omitted, the application derives it from the validated action objects.

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

All `*.session` files directly inside `TG_SESSION_DIR` are loaded on startup. Each file is treated as a separate Telegram user account with its own Telethon client.

The first discovered session (alphabetical order) is the **primary account**. It still participates in every giveaway like every other enabled account. It is additionally used for admin-requested retry/source-message retrieval and is automatically subscribed to configured `MONITORED_CHANNELS`.

### Per-session API credentials

Global `TG_API_ID/TG_API_HASH` in `.env` are optional fallbacks. For a session such as `account_a.session`, create private `config/accounts.json`:

```json
{
  "account_a": {
    "api_id": 123456,
    "api_hash": "0123456789abcdef0123456789abcdef",
    "phone": "+380...",
    "2fa_password": "optional"
  }
}
```

The application tries the global credentials first and, when they fail, retries that session with its session-specific credentials from `config/accounts.json`. The private JSON file is ignored by Git. A safe template is provided as `config/accounts.example.json`.

If neither source contains an API ID/hash for a session, that session is recorded as unauthorized with an explicit error instead of silently using invalid credentials.

For participation, every enabled and authorized discovered account receives the same validated plan. Accounts execute concurrently, with an optional stagger controlled by:

```env
ACCOUNT_ACTION_DELAY_MS=250
```

## Channel monitoring and isolation

Every discovered account registers both `NewMessage` and `MessageEdited`. The handler first verifies that the peer is a Telegram broadcast channel (`broadcast=true` and not `megagroup=true`). Linked discussion groups and ordinary chats are ignored.

When `MONITORED_CHANNELS` is empty, every broadcast channel that sends updates to the account is considered. When it is set, only the configured channels are processed.

The database remains one PostgreSQL database, but channel data is explicitly partitioned by channel ID. `monitored_channels` stores the channel registry, `channel_accounts` stores the account-to-channel association, and giveaway/action/number history remains linked to the source channel through the giveaway's `chat_id`.

The stored number history therefore stays isolated to the giveaway/channel data. The existing uniqueness rule is still **per giveaway**, not global across unrelated giveaways.

### Event recovery

Channel event claims use a lease. If the process dies after claiming an event but before finishing it, a background recovery worker can reclaim the expired event and process it again instead of losing it permanently.

New events and message edits have separate immutable event markers. An edit of an existing giveaway creates a new version.

## Edit reconciliation

Each detected giveaway edit is stored in `giveaway_versions`, with the current plan/version mirrored on the main giveaway row.

Completed actions are identified by a deterministic action key. An unchanged requirement is not repeated after an edit; a changed requirement gets a different action key and can execute. If an edited post is no longer detected as a giveaway, the existing giveaway is marked `skipped` and the non-giveaway version is still recorded.

Version numbering is protected by a row lock on the giveaway, so concurrent edits cannot allocate the same version number.

## Environment

The `.env` file contains:
- optional global Telegram API ID/hash and session defaults;
- per-session credential path;
- OpenAI API key/model and AI confidence threshold;
- Telegram Bot API token for the separate admin bot;
- admin chat/user IDs;
- PostgreSQL connection string;
- monitoring and safety settings;
- local action-sequence catalog path.

Never commit real credentials, `.env`, `config/accounts.json`, Telegram session files, or bot tokens.

## Database

`sql/schema.sql` stores:
- detected giveaways and their full validated plan;
- source channel username and AI confidence;
- immutable giveaway plan versions;
- action code/type, account ID, sequence ID, payload, status and error;
- temporary unique-number reservations for number-guess actions;
- successful number values in action history;
- monitored channels and account-to-channel associations;
- tracked outgoing participation messages;
- notifications and reminder records;
- channel event processing state and recovery leases.

The schema uses additive `ALTER TABLE ... IF NOT EXISTS` statements so it can upgrade an existing installation.

## Notifications

The admin bot can report:
- detected giveaways and the AI action codes;
- participation results;
- replies specifically to messages authored by a user account;
- mentions of a user account;
- possible win messages correlated with that account's tracked participation.

`/status` reports actual current connection state, not merely whether the session is authorized. A heartbeat updates that state while the process is running, and graceful shutdown marks accounts disconnected.

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

The temporary `number_pool` is cleaned when all participating accounts have sent their number or when the available range is exhausted. Successful number values remain in action history, so cleaning the temporary pool does not permit the same number to be reused for the same giveaway.

An exact single-number answer is therefore available to at most one account under the unique-number rule. Other accounts continue with their remaining plan steps and report the number step as unavailable.

## Telegram comments

Action code 1 always uses Telethon's `comment_to` mechanism to leave a comment through the linked discussion of the broadcast-channel post. The AI cannot change this destination and never writes the final comment text itself.

## Run

1. Copy the template: `cp .env.example .env`.
2. Fill in credentials and monitoring settings.
3. For per-session credentials, copy `config/accounts.example.json` to private `config/accounts.json` and replace the placeholders.
4. Install dependencies: `pip install -r requirements.txt`.
5. Apply `sql/schema.sql` to PostgreSQL.
6. Start: `python -m app.main`.

The Telethon session files are stored under `TG_SESSION_DIR` and are excluded from Git.

## Operational layer

1. **State machine:** giveaway status is persisted as parsed, ready, executing, success, partial, failed, needs_human, retry_requested or skipped.
2. **Edit versions:** detected edits are immutable versions and are reconciled by deterministic action keys.
3. **Number retry:** failed numeric sends release the reservation and retry with another unique value, while successful values stay in action history.
4. **Account registry:** discovered sessions are registered with authorization, enabled, live connection, and error state.
5. **Primary account:** the first discovered session remains a normal participant while also handling primary source retrieval/retries and configured-channel auto-subscription.
6. **Channel partitioning:** one PostgreSQL instance is used, with explicit channel/account associations and all giveaway history tied to its source channel.
7. **Win correlation:** outgoing participation messages are tracked so winner-like incoming messages can be tied to the correct account/giveaway when Telegram provides a reply relationship.
8. **Event recovery:** claimed channel events have leases and can be reclaimed after a worker failure.

The automation still does not solve CAPTCHAs/anti-bot challenges and does not invent missing answers.
