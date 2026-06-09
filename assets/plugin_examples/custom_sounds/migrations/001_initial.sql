CREATE TABLE IF NOT EXISTS plugin_custom_sounds_sounds (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    name     TEXT    NOT NULL,
    mimetype TEXT    NOT NULL DEFAULT 'audio/mpeg',
    data     TEXT    NOT NULL,
    ts       INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS plugin_custom_sounds_sounds_ts
    ON plugin_custom_sounds_sounds(ts DESC);
