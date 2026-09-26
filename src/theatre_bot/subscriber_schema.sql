PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS subscribers (
    id INTEGER PRIMARY KEY,
    channel TEXT NOT NULL CHECK (channel IN ('vk', 'max')),
    external_id_hash TEXT NOT NULL,
    external_id_encrypted BLOB,
    display_name_encrypted BLOB,
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
    notification_type TEXT NOT NULL
        CHECK (notification_type IN ('rescheduled', 'removed', 'reminder_24h')),
    message TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'sent', 'failed', 'cancelled')),
    created_at TEXT NOT NULL,
    sent_at TEXT,
    failure_reason TEXT,
    UNIQUE (subscriber_id, performance_key, notification_type, message)
);

CREATE INDEX IF NOT EXISTS idx_notification_queue_status
    ON notification_queue(status, created_at);
