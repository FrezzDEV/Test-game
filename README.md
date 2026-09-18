# multi-auto-quiz-tg

Telegram user-account automation for detecting other people's giveaways, parsing participation rules with AI, executing safe deterministic actions, and notifying an admin bot.

## Components

Telegram user session (Telethon) -> giveaway detector -> AI parser -> action planner/executor -> PostgreSQL

Admin Telegram bot (Bot API) -> statistics, status, notifications and reminders

## Environment

The .env file contains:
- Telegram API ID/hash and session settings for the user account.
- OpenAI API key/model for giveaway detection and rule extraction.
- Telegram Bot API token for the separate admin bot.
- Admin chat/user IDs.
- PostgreSQL connection string.
- Monitoring, safety and reminder settings.

Never commit .env, Telegram session files or bot tokens.

## Notifications

The architecture tracks:
- detected giveaways;
- participation result;
- replies to messages sent by the user account;
- mentions of the user account;
- possible win notifications;
- future reminder records in PostgreSQL.

## Safety

AUTO_JOIN_ENABLED=false by default. Unknown rules, CAPTCHAs and anti-bot checks must not be auto-solved or falsely marked as completed. Respect Telegram limits and the rules of monitored chats.

## Run

1. cp .env.example .env
2. Fill credentials.
3. pip install -r requirements.txt
4. Apply sql/schema.sql.
5. python -m app.main
