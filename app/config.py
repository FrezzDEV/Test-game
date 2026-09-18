import os

from dotenv import load_dotenv


load_dotenv()


def env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).lower() in {"1", "true", "yes", "on"}


TG_API_ID = int(os.environ["TG_API_ID"])
TG_API_HASH = os.environ["TG_API_HASH"]
TG_SESSION = os.getenv("TG_SESSION", "giveaway_auto_join")
TG_SESSION_DIR = os.getenv("TG_SESSION_DIR", "./sessions")
TG_PHONE = os.getenv("TG_PHONE") or None
TG_2FA_PASSWORD = os.getenv("TG_2FA_PASSWORD") or None

OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")
AI_MIN_CONFIDENCE = float(os.getenv("AI_MIN_CONFIDENCE", "0.85"))

TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN") or None
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID") or "0")
ADMIN_USER_IDS = {
    int(x.strip())
    for x in os.getenv("ADMIN_USER_IDS", "").split(",")
    if x.strip()
}

DATABASE_URL = os.environ["DATABASE_URL"]
MONITORED_CHANNELS = [
    x.strip()
    for x in (
        os.getenv("MONITORED_CHANNELS")
        or os.getenv("MONITORED_CHATS", "")
    ).split(",")
    if x.strip()
]

AUTO_JOIN_ENABLED = env_bool("AUTO_JOIN_ENABLED", False)
SCAN_OLD_MESSAGES = env_bool("SCAN_OLD_MESSAGES", False)
TIMEZONE = os.getenv("TIMEZONE", "Europe/Kyiv")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Participation fan-out across all discovered user sessions.
ACCOUNT_ACTION_DELAY_MS = max(
    0,
    int(os.getenv("ACCOUNT_ACTION_DELAY_MS", "250")),
)

NUMBER_SEND_RETRIES = max(1, int(os.getenv("NUMBER_SEND_RETRIES", "3")))
ACTION_RETRY_DELAY_SECONDS = max(
    0.0,
    float(os.getenv("ACTION_RETRY_DELAY_SECONDS", "2")),
)
RETRY_POLL_SECONDS = max(1, int(os.getenv("RETRY_POLL_SECONDS", "5")))
REMINDER_POLL_SECONDS = max(1, int(os.getenv("REMINDER_POLL_SECONDS", "15")))

# Retry / scheduler controls
NUMBER_SEND_RETRIES = max(1, int(os.getenv("NUMBER_SEND_RETRIES", "3")))
ACTION_RETRY_DELAY_SECONDS = max(
    0.0,
    float(os.getenv("ACTION_RETRY_DELAY_SECONDS", "2")),
)
RETRY_POLL_SECONDS = max(1, int(os.getenv("RETRY_POLL_SECONDS", "5")))
REMINDER_POLL_SECONDS = max(1, int(os.getenv("REMINDER_POLL_SECONDS", "15")))

# Notifications / reminders
NOTIFY_ON_WIN = env_bool("NOTIFY_ON_WIN", True)
NOTIFY_ON_REPLY = env_bool("NOTIFY_ON_REPLY", True)
NOTIFY_ON_MENTION = env_bool("NOTIFY_ON_MENTION", True)
REMINDER_INTERVAL_MINUTES = int(os.getenv("REMINDER_INTERVAL_MINUTES", "30"))

ACTION_SEQUENCES_PATH = os.getenv(
    "ACTION_SEQUENCES_PATH",
    "./config/action_sequences.json",
)
