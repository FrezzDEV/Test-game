CREATE TABLE IF NOT EXISTS giveaways (
    id BIGSERIAL PRIMARY KEY,
    chat_id BIGINT NOT NULL,
    message_id BIGINT NOT NULL,
    giveaway_type TEXT NOT NULL,
    status TEXT NOT NULL,
    detected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    source_channel_username TEXT,
    confidence DOUBLE PRECISION,
    needs_human BOOLEAN NOT NULL DEFAULT false,
    reason TEXT,
    plan JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE(chat_id, message_id)
);

ALTER TABLE giveaways
    ADD COLUMN IF NOT EXISTS source_channel_username TEXT;
ALTER TABLE giveaways
    ADD COLUMN IF NOT EXISTS confidence DOUBLE PRECISION;
ALTER TABLE giveaways
    ADD COLUMN IF NOT EXISTS needs_human BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE giveaways
    ADD COLUMN IF NOT EXISTS reason TEXT;
ALTER TABLE giveaways
    ADD COLUMN IF NOT EXISTS plan JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS idx_giveaways_detected_at
ON giveaways(detected_at);

CREATE INDEX IF NOT EXISTS idx_giveaways_status
ON giveaways(status);

CREATE TABLE IF NOT EXISTS participation_actions (
    id BIGSERIAL PRIMARY KEY,
    giveaway_id BIGINT REFERENCES giveaways(id) ON DELETE CASCADE,
    step_index INTEGER NOT NULL DEFAULT 0,
    action_code INTEGER,
    action_type TEXT NOT NULL,
    sequence_id TEXT,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL,
    executed_at TIMESTAMPTZ,
    error TEXT
);

ALTER TABLE participation_actions
    ADD COLUMN IF NOT EXISTS step_index INTEGER NOT NULL DEFAULT 0;
ALTER TABLE participation_actions
    ADD COLUMN IF NOT EXISTS action_code INTEGER;
ALTER TABLE participation_actions
    ADD COLUMN IF NOT EXISTS sequence_id TEXT;

CREATE INDEX IF NOT EXISTS idx_actions_giveaway
ON participation_actions(giveaway_id);

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
