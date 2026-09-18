PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS plays (
    id INTEGER PRIMARY KEY,
    source_url TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    normalized_title TEXT NOT NULL,
    genre TEXT,
    age_rating TEXT,
    duration_minutes INTEGER,
    director TEXT,
    summary TEXT,
    image_url TEXT,
    catalog_kind TEXT NOT NULL DEFAULT 'affiche',
    is_active INTEGER NOT NULL DEFAULT 1,
    source_updated_at TEXT,
    synced_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_plays_normalized_title
    ON plays(normalized_title);

CREATE TABLE IF NOT EXISTS artists (
    id INTEGER PRIMARY KEY,
    source_url TEXT NOT NULL UNIQUE,
    full_name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    image_url TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    synced_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_artists_normalized_name
    ON artists(normalized_name);

CREATE TABLE IF NOT EXISTS roles (
    play_id INTEGER NOT NULL REFERENCES plays(id) ON DELETE CASCADE,
    artist_id INTEGER NOT NULL REFERENCES artists(id) ON DELETE CASCADE,
    role_name TEXT,
    PRIMARY KEY (play_id, artist_id, role_name)
);

CREATE TABLE IF NOT EXISTS performances (
    id INTEGER PRIMARY KEY,
    play_id INTEGER NOT NULL REFERENCES plays(id),
    starts_at TEXT NOT NULL,
    venue TEXT,
    ticket_url TEXT,
    ticket_event_id TEXT,
    status TEXT NOT NULL DEFAULT 'scheduled',
    source_key TEXT NOT NULL UNIQUE,
    synced_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_performances_starts_at
    ON performances(starts_at);

CREATE TABLE IF NOT EXISTS response_templates (
    id INTEGER PRIMARY KEY,
    intent TEXT NOT NULL,
    channel TEXT NOT NULL DEFAULT 'all',
    variant TEXT NOT NULL,
    template TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    UNIQUE (intent, channel, variant)
);

CREATE TABLE IF NOT EXISTS unrecognized_requests (
    id INTEGER PRIMARY KEY,
    channel TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    reviewed_at TEXT
);
