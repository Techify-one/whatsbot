CREATE TABLE IF NOT EXISTS plugin_lembretes_items (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    phone TEXT    NOT NULL,
    name  TEXT    NOT NULL DEFAULT '',
    text  TEXT    NOT NULL,
    ts    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS plugin_lembretes_items_ts
    ON plugin_lembretes_items(ts DESC);
