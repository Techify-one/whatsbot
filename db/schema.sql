CREATE TABLE IF NOT EXISTS config (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS contacts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    phone           TEXT    NOT NULL UNIQUE,
    name            TEXT    NOT NULL DEFAULT '',
    email           TEXT    NOT NULL DEFAULT '',
    profession      TEXT    NOT NULL DEFAULT '',
    company         TEXT    NOT NULL DEFAULT '',
    address         TEXT    NOT NULL DEFAULT '',
    ai_enabled      INTEGER NOT NULL DEFAULT 1,
    is_group        INTEGER NOT NULL DEFAULT 0,
    group_name      TEXT    NOT NULL DEFAULT '',
    is_archived     INTEGER NOT NULL DEFAULT 0,
    archived_by_app INTEGER NOT NULL DEFAULT 0,
    can_send        INTEGER NOT NULL DEFAULT 1,
    unread_count    INTEGER NOT NULL DEFAULT 0,
    unread_ai_count INTEGER NOT NULL DEFAULT 0,
    created_at      REAL    NOT NULL,
    updated_at      REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_contacts_updated ON contacts(updated_at);
CREATE INDEX IF NOT EXISTS idx_contacts_archived ON contacts(is_archived);

CREATE TABLE IF NOT EXISTS observations (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    text       TEXT    NOT NULL,
    created_at REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_obs_contact ON observations(contact_id);

CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    role       TEXT    NOT NULL,
    content    TEXT    NOT NULL DEFAULT '',
    ts         REAL    NOT NULL,
    media_type TEXT,
    media_path TEXT,
    status     TEXT,
    msg_id     TEXT
);
CREATE INDEX IF NOT EXISTS idx_msg_contact_ts ON messages(contact_id, ts);
CREATE INDEX IF NOT EXISTS idx_msg_id ON messages(msg_id);

CREATE TABLE IF NOT EXISTS usage (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id        INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    call_type         TEXT    NOT NULL,
    model             TEXT    NOT NULL,
    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens      INTEGER NOT NULL DEFAULT 0,
    cost_usd          REAL    NOT NULL DEFAULT 0.0,
    ts                REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_usage_contact_ts ON usage(contact_id, ts);
CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage(ts);

CREATE TABLE IF NOT EXISTS tags (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name  TEXT    NOT NULL UNIQUE,
    color TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS contact_tags (
    contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    tag_id     INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (contact_id, tag_id)
);
CREATE INDEX IF NOT EXISTS idx_ct_tag ON contact_tags(tag_id);

CREATE TABLE IF NOT EXISTS unread_msg_ids (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    msg_id     TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_unread_contact ON unread_msg_ids(contact_id);

CREATE TABLE IF NOT EXISTS executions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    phone        TEXT    NOT NULL,
    trigger_type TEXT    NOT NULL DEFAULT 'webhook',
    status       TEXT    NOT NULL DEFAULT 'running',
    started_at   REAL    NOT NULL,
    completed_at REAL,
    error        TEXT
);
CREATE INDEX IF NOT EXISTS idx_exec_started ON executions(started_at);

CREATE TABLE IF NOT EXISTS execution_steps (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_id INTEGER NOT NULL REFERENCES executions(id) ON DELETE CASCADE,
    step_type    TEXT    NOT NULL,
    status       TEXT    NOT NULL DEFAULT 'ok',
    data         TEXT,
    ts           REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_step_exec ON execution_steps(execution_id);

CREATE TABLE IF NOT EXISTS plugins (
    id            TEXT    PRIMARY KEY,
    version       TEXT    NOT NULL DEFAULT '',
    enabled       INTEGER NOT NULL DEFAULT 0,
    installed_at  REAL    NOT NULL,
    updated_at    REAL    NOT NULL,
    load_error    TEXT
);

CREATE TABLE IF NOT EXISTS plugin_migrations (
    plugin_id  TEXT    NOT NULL,
    version    INTEGER NOT NULL,
    applied_at REAL    NOT NULL,
    PRIMARY KEY (plugin_id, version)
);

CREATE TABLE IF NOT EXISTS tool_overrides (
    name           TEXT    PRIMARY KEY,
    plugin_id      TEXT,
    enabled        INTEGER NOT NULL DEFAULT 1,
    description    TEXT,
    display_label  TEXT,
    updated_at     REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tool_overrides_plugin ON tool_overrides(plugin_id);
