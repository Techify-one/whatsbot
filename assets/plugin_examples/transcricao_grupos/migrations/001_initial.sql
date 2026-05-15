CREATE TABLE IF NOT EXISTS plugin_transcricao_grupos_settings (
    chat_jid       TEXT PRIMARY KEY,
    audio_mode     TEXT,
    image_enabled  INTEGER,
    updated_at     REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS plugin_transcricao_grupos_settings_updated
    ON plugin_transcricao_grupos_settings(updated_at);
