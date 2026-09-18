CREATE TABLE IF NOT EXISTS giveaways (
    id BIGSERIAL PRIMARY KEY,
    chat_id BIGINT NOT NULL,
    message_id BIGINT NOT NULL,
    giveaway_type TEXT NOT NULL,
    status TEXT NOT NULL,
    detected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(chat_id, message_id)
);

CREATE INDEX IF NOT EXISTS idx_giveaways_detected_at
ON giveaways(detected_at);

CREATE TABLE IF NOT EXISTS participation_actions (
    id BIGSERIAL PRIMARY KEY,
    giveaway_id BIGINT REFERENCES giveaways(id) ON DELETE CASCADE,
    action_type TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL,
    executed_at TIMESTAMPTZ,
    error TEXT
);

CREATE TABLE IF NOT EXISTS tracked_messages (
    id BIGSERIAL PRIMARY KEY,
    chat_id BIGINT NOT NULL,
    message_id BIGINT NOT NULL,
    kind TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(chat_id, message_id, kind)
);

CREATE TABLE IF NOT EXISTS notifications (
    id BIGSERIAL PRIMARY KEY,
    chat_id BIGINT,
    message_id BIGINT,
    kind TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    sent_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS reminders (
    id BIGSERIAL PRIMARY KEY,
    giveaway_id BIGINT REFERENCES giveaways(id) ON DELETE CASCADE,
    remind_at TIMESTAMPTZ NOT NULL,
    kind TEXT NOT NULL,
    sent BOOLEAN NOT NULL DEFAULT false
);

CREATE INDEX IF NOT EXISTS idx_reminders_due
ON reminders(sent, remind_at);
