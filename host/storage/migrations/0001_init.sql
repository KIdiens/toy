-- ============================================================================
-- 0001_init.sql  ── 初始 schema：profiles / memories / reminders / settings
-- ----------------------------------------------------------------------------
-- 版本驱动：本文件由 `PRAGMA user_version` 驱动，只做前向迁移（0 → 1）。
--
-- 立场（对齐 Spec §11「previousSeq 不重置是有意的重放防护」的同款纪律）：
--   只写前向迁移，不写假设性的回滚 DDL。真实回滚语义在无真板/无实物阶段
--   无法验证，写 down 脚本只会制造「看起来安全、实则未验」的假证据。
--   迁移失败一律依靠「单步事务 + 备份/还原」（见 40-storage-design.md §4），
--   而不是 up/down 对称的迁移框架。
--
-- 外键：DDL 只声明 REFERENCES；PRAGMA foreign_keys 是「每连接」开关、不持久化到
--   文件，因此每条连接打开后必须显式 `PRAGMA foreign_keys=ON`（见设计文档 §3.2）。
--
-- 时间列：一律存 ISO-8601 UTC 文本，且**由应用层写入**，不设 CURRENT_TIMESTAMP
--   默认值——时钟必须可被注入（VirtualClock），否则确定性测试无法复现（AC-12）。
--
-- 字段严格照 Spec §6，未增删任何字段。
-- ============================================================================

CREATE TABLE profiles (
    id           INTEGER PRIMARY KEY,
    display_name TEXT    NOT NULL,
    created_at   TEXT    NOT NULL
);

CREATE TABLE memories (
    id         INTEGER PRIMARY KEY,
    profile_id INTEGER NOT NULL REFERENCES profiles (id) ON DELETE CASCADE,
    kind       TEXT    NOT NULL,
    value      TEXT    NOT NULL,
    confirmed  INTEGER NOT NULL DEFAULT 0 CHECK (confirmed IN (0, 1)),
    created_at TEXT    NOT NULL,
    expires_at TEXT
);

CREATE INDEX memories_by_profile ON memories (profile_id, kind);

CREATE TABLE reminders (
    id              INTEGER PRIMARY KEY,
    profile_id      INTEGER REFERENCES profiles (id) ON DELETE CASCADE,
    text            TEXT,
    due_at_utc      TEXT,
    timezone        TEXT,
    repeat_rule     TEXT,
    status          TEXT,
    last_fired_at   TEXT,
    idempotency_key TEXT
);

CREATE INDEX reminders_by_due ON reminders (due_at_utc, status);

CREATE UNIQUE INDEX reminders_idempotency_key_uq ON reminders (idempotency_key);

-- settings 为「每行一个键值」的多行 KV 表（非单行多列）。理由见 40-storage-design.md §5：
--   key 作主键 + value 的列形状只可能是「一行一键」，且新增设置项无需改 schema。
CREATE TABLE settings (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at TEXT
);

PRAGMA user_version = 1;
