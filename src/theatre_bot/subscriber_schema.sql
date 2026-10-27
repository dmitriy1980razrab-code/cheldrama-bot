PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS subscribers (
    id INTEGER PRIMARY KEY,
    channel TEXT NOT NULL CHECK (channel IN ('vk', 'max')),
    external_id_hash TEXT NOT NULL,
    external_id_encrypted BLOB,
    display_name_encrypted BLOB,
    interested_in_premieres INTEGER NOT NULL DEFAULT 0 CHECK (interested_in_premieres IN (0, 1)),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'active', 'unsubscribed')),
    subscribed_at TEXT,
    unsubscribed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (channel, external_id_hash)
);

CREATE TABLE IF NOT EXISTS consent_events (
    id INTEGER PRIMARY KEY,
    subscriber_id INTEGER NOT NULL REFERENCES subscribers(id),
    consent_type TEXT NOT NULL
        CHECK (consent_type IN ('personal_data', 'service_notifications', 'marketing')),
    action TEXT NOT NULL CHECK (action IN ('granted', 'revoked')),
    document_version TEXT NOT NULL,
    source TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_consent_events_subscriber
    ON consent_events(subscriber_id, consent_type, id);

CREATE TABLE IF NOT EXISTS subscriptions (
    id INTEGER PRIMARY KEY,
    subscriber_id INTEGER NOT NULL REFERENCES subscribers(id),
    topic_type TEXT NOT NULL CHECK (topic_type IN ('play', 'artist', 'genre', 'all_affiche')),
    topic_key TEXT NOT NULL,
    topic_label TEXT NOT NULL,
    performance_key TEXT,
    play_key TEXT,
    selected_starts_at TEXT,
    notify_changes INTEGER NOT NULL DEFAULT 1,
    remind_24h INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'unsubscribed')),
    subscribed_at TEXT NOT NULL,
    unsubscribed_at TEXT,
    UNIQUE (subscriber_id, topic_type, topic_key)
);

CREATE INDEX IF NOT EXISTS idx_subscriptions_topic
    ON subscriptions(topic_type, topic_key, status);

CREATE TABLE IF NOT EXISTS preferences (
    id INTEGER PRIMARY KEY,
    subscriber_id INTEGER NOT NULL REFERENCES subscribers(id),
    preference_type TEXT NOT NULL CHECK (preference_type IN ('play', 'artist', 'genre')),
    preference_key TEXT NOT NULL,
    preference_label TEXT NOT NULL,
    source TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    UNIQUE (subscriber_id, preference_type, preference_key)
);

CREATE TABLE IF NOT EXISTS subscriber_access_log (
    id INTEGER PRIMARY KEY,
    subscriber_id INTEGER REFERENCES subscribers(id),
    admin_actor TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('list_profiles', 'view_profile')),
    purpose TEXT NOT NULL,
    accessed_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_subscriber_access_log_time
    ON subscriber_access_log(accessed_at);

CREATE TABLE IF NOT EXISTS survey_play_interests (
    subscriber_id INTEGER NOT NULL REFERENCES subscribers(id),
    play_key TEXT NOT NULL,
    play_label TEXT NOT NULL,
    season_start_year INTEGER NOT NULL,
    recorded_at TEXT NOT NULL,
    PRIMARY KEY (subscriber_id, play_key)
);

CREATE TABLE IF NOT EXISTS campaign_targets (
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
    target_type TEXT NOT NULL CHECK (target_type IN ('play','genre','artist')),
    target_key TEXT NOT NULL,
    target_label TEXT NOT NULL,
    PRIMARY KEY (campaign_id, target_type, target_key)
);

CREATE TABLE IF NOT EXISTS campaigns (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    message TEXT NOT NULL,
    channel TEXT NOT NULL DEFAULT 'all'
        CHECK (channel IN ('all', 'vk', 'max')),
    target_type TEXT NOT NULL DEFAULT 'all'
        CHECK (target_type IN ('all', 'play', 'artist', 'genre')),
    target_key TEXT,
    target_label TEXT,
    premieres_only INTEGER NOT NULL DEFAULT 0 CHECK (premieres_only IN (0, 1)),
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'approved', 'scheduled', 'cancelled', 'completed')),
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    approved_by TEXT,
    approved_at TEXT,
    scheduled_at TEXT
);

CREATE TABLE IF NOT EXISTS campaign_recipients (
    id INTEGER PRIMARY KEY,
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
    subscriber_id INTEGER NOT NULL REFERENCES subscribers(id),
    status TEXT NOT NULL DEFAULT 'planned'
        CHECK (status IN ('planned', 'sent', 'failed', 'skipped')),
    reason TEXT,
    sent_at TEXT,
    UNIQUE (campaign_id, subscriber_id)
);

CREATE INDEX IF NOT EXISTS idx_campaign_recipients_status
    ON campaign_recipients(campaign_id, status);

CREATE TABLE IF NOT EXISTS campaign_events (
    id INTEGER PRIMARY KEY,
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
    actor TEXT NOT NULL,
    event_type TEXT NOT NULL
        CHECK (event_type IN ('created', 'previewed', 'approved', 'scheduled', 'cancelled')),
    details TEXT,
    recorded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS performance_observations (
    source_key TEXT PRIMARY KEY,
    play_key TEXT NOT NULL,
    play_title TEXT NOT NULL,
    starts_at TEXT NOT NULL,
    status TEXT NOT NULL,
    observed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notification_queue (
    id INTEGER PRIMARY KEY,
    subscriber_id INTEGER NOT NULL REFERENCES subscribers(id),
    subscription_id INTEGER NOT NULL REFERENCES subscriptions(id),
    performance_key TEXT NOT NULL,
    planned_starts_at TEXT,
    deliver_after TEXT,
    notification_type TEXT NOT NULL
        CHECK (notification_type IN ('rescheduled', 'removed', 'reminder_24h')),
    message TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'sent', 'failed', 'cancelled')),
    created_at TEXT NOT NULL,
    sent_at TEXT,
    failure_reason TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    last_attempt_at TEXT,
    next_attempt_at TEXT,
    UNIQUE (subscriber_id, performance_key, notification_type, message)
);

CREATE INDEX IF NOT EXISTS idx_notification_queue_status
    ON notification_queue(status, created_at);

CREATE TABLE IF NOT EXISTS inbound_events (
    id INTEGER PRIMARY KEY,
    channel TEXT NOT NULL CHECK (channel IN ('vk', 'max')),
    event_id TEXT NOT NULL,
    received_at TEXT NOT NULL,
    UNIQUE (channel, event_id)
);

CREATE TABLE IF NOT EXISTS outgoing_reply_queue (
    id INTEGER PRIMARY KEY,
    channel TEXT NOT NULL CHECK (channel IN ('vk', 'max')),
    dedupe_hash TEXT NOT NULL UNIQUE,
    vk_random_id INTEGER,
    payload_encrypted BLOB NOT NULL,
    source_type TEXT NOT NULL DEFAULT 'webhook'
        CHECK (source_type IN ('webhook', 'service_notification', 'campaign')),
    source_key TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'processing', 'sent', 'failed', 'cancelled')),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    last_attempt_at TEXT,
    next_attempt_at TEXT,
    locked_until TEXT,
    sent_at TEXT,
    failure_reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_outgoing_reply_queue_delivery
    ON outgoing_reply_queue(status, next_attempt_at, locked_until, id);

CREATE TABLE IF NOT EXISTS notification_checks (
    performance_key TEXT NOT NULL,
    starts_at TEXT NOT NULL,
    stage TEXT NOT NULL CHECK (stage IN ('evening', 'morning', 'three_hours', 'prepare')),
    checked_at TEXT NOT NULL,
    PRIMARY KEY (performance_key, starts_at, stage)
);

CREATE TABLE IF NOT EXISTS spam_guard_state (
    actor_hash TEXT PRIMARY KEY,
    text_tokens REAL NOT NULL,
    action_tokens REAL NOT NULL,
    last_refill REAL NOT NULL,
    repeat_hash TEXT,
    repeat_count INTEGER NOT NULL,
    repeat_since REAL NOT NULL,
    blocked_until REAL NOT NULL DEFAULT 0,
    last_seen REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_spam_guard_state_expiry ON spam_guard_state(last_seen);

CREATE TABLE IF NOT EXISTS interest_survey_state (
    actor_hash TEXT PRIMARY KEY,
    decline_count INTEGER NOT NULL DEFAULT 0,
    never_offer INTEGER NOT NULL DEFAULT 0,
    next_offer_at TEXT,
    stage TEXT NOT NULL,
    payload_encrypted BLOB,
    expires_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_interest_survey_expiry ON interest_survey_state(expires_at);


CREATE TABLE IF NOT EXISTS channel_menu_hints (
    actor_hash TEXT PRIMARY KEY,
    shown_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS admin_users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('advertising','analytics','observer','support')),
    active INTEGER NOT NULL DEFAULT 1,
    last_seen_at TEXT,
    notify_channel TEXT CHECK(notify_channel IN ('vk','max')),
    notify_id_encrypted BLOB,
    alert_cursor INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS admin_audit (
    id INTEGER PRIMARY KEY, actor TEXT NOT NULL, action TEXT NOT NULL,
    object_key TEXT, recorded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS feedback_tickets (
    id INTEGER PRIMARY KEY,
    subscriber_id INTEGER NOT NULL REFERENCES subscribers(id),
    department TEXT NOT NULL CHECK(department IN ('advertising','social','technical')),
    status TEXT NOT NULL DEFAULT 'new' CHECK(status IN ('new','working','closed')),
    assigned_to INTEGER REFERENCES admin_users(id),
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_feedback_tickets_queue ON feedback_tickets(department,status,updated_at);
CREATE TABLE IF NOT EXISTS feedback_messages (
    id INTEGER PRIMARY KEY,
    ticket_id INTEGER NOT NULL REFERENCES feedback_tickets(id),
    direction TEXT NOT NULL CHECK(direction IN ('incoming','outgoing')),
    body_encrypted BLOB NOT NULL,
    staff_id INTEGER REFERENCES admin_users(id),
    outgoing_queue_id INTEGER REFERENCES outgoing_reply_queue(id),
    dedupe_key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_feedback_messages_ticket ON feedback_messages(ticket_id,id);
CREATE TABLE IF NOT EXISTS feedback_sessions (
    actor_hash TEXT PRIMARY KEY, stage TEXT NOT NULL,
    token TEXT NOT NULL, department TEXT,
    ticket_id INTEGER REFERENCES feedback_tickets(id), expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS preference_changes (
    id INTEGER PRIMARY KEY,
    preference_type TEXT NOT NULL, preference_label TEXT NOT NULL,
    action TEXT NOT NULL CHECK(action IN ('added','removed')), recorded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS staff_notify_challenges (
    staff_id INTEGER PRIMARY KEY REFERENCES admin_users(id), channel TEXT,
    id_encrypted BLOB, code_hash TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS feedback_alert_events (
    id INTEGER PRIMARY KEY, ticket_id INTEGER NOT NULL REFERENCES feedback_tickets(id), created_at TEXT NOT NULL
);
