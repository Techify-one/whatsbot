-- Cooldown bookkeeping: last time an away message was sent to each contact,
-- so the plugin doesn't repeat the notice on every message while closed.
CREATE TABLE IF NOT EXISTS plugin_horario_funcionamento_away_log (
    phone    TEXT PRIMARY KEY,
    last_ts  REAL NOT NULL
);
