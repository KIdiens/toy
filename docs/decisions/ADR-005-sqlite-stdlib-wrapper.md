# ADR-005: 使用标准库 sqlite3 门面 + user_version 版本化迁移

## Status
Accepted (2026-09-21)

## Background
开发书 §11.1 直接给出裸 DDL（`profiles` / `memories` + `memories_by_profile` 索引），§11.2/§11.3 要求「开启外键约束」「所有查询用参数绑定」「支持查看/删除/清空/导出」；§14.4 要求「数据库迁移能恢复备份」。本阶段数据规模小、单机单写者。

## Decision
使用标准库 `sqlite3`，封装为 `host/storage/database.py` 门面：

- 连接统一开启 `PRAGMA foreign_keys=ON`；所有语句参数绑定；禁用字符串拼接。
- 迁移用 `PRAGMA user_version` 驱动，`migrations/NNNN_*.sql` 前向迁移，启动时按版本顺序执行，每步在事务内。
- 提供 `backup()/restore()`（`sqlite3` 在线备份 API）配合 §14.4 的回滚测试。
- 仓储（`profiles_repo` 等）只做存取，不含业务规则。

排除：SQLAlchemy 2.0.54（MIT，2026-09-15）——对 4 张表属过剩，安装体积约 41MB，异步能力在本设计（线程模型）下无用；Alembic 迁移框架对单机 SQLite 过重。SQLModel/peewee——收益不足以覆盖新增依赖与学习成本。

**升级触发条件**：表数 > 10、或多写者/跨库迁移、或需要复杂关系建模时，迁移到 SQLAlchemy 2.0 + Alembic。

## Consequences
- 正面：零依赖；SQL 透明可审；外键与参数绑定显式；迁移可测（前向 + 备份/还原）。
- 负面：无 ORM 的对象映射，需要手写 Row→dataclass 映射；复杂查询需手写 SQL（当前不存在）。
- 约束：DDL/迁移 SQL 只在 `migrations/` 出现；业务层不写 DDL。

## Related ADRs
ADR-001（运行时含 sqlite3）、ADR-002（记忆/提醒作为状态机的下游能力）。
