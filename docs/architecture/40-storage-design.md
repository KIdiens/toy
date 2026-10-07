# 40. 存储层设计（仓储接口契约 / 迁移策略）

> 状态：Accepted（2026-09-21）· 负责人：高见远（首席架构师）
> 依据：`docs/SPEC-host-mvp.md` §4（版本锚定）、§6（表清单）、§10（安全/时间不可靠）、§11（已知坑）、§12（验证步骤）；`docs/architecture/00-architecture-spec.md` §4（目录、≤300 行、入口只装配）、§5（兼容契约）；`docs/decisions/ADR-005-sqlite-stdlib-wrapper.md`。
> 落地文件：`host/storage/database.py`、`host/storage/migrations/0001_init.sql`、`host/storage/{profiles,memories,reminders,settings}_repo.py`。
> 作用：Phase 3 后端施工图。**签名一律定死**，实现照抄；签名变更需回写本文档。

---

## 0. 范围

本文档只覆盖 **SQLite 存储层**：连接与 PRAGMA、迁移、四个仓储的接口契约、数据纪律。不含状态机、AI、音频、web 路由的内部实现。

分层红线（`00-architecture-spec.md` §2）：依赖只能向下；**存储层不得反向 import `host/core`、`host/ai`、`host/web`**；仓储只做读写、不含业务规则；DDL 只出现在 `migrations/`。

---

## 1. 文件与职责

```
host/storage/
├── database.py            # sqlite3 门面：连接/PRAGMA/迁移运行器/备份还原/异常
├── errors.py              # 存储层异常层级（见 §8）
├── migrations/
│   └── 0001_init.sql      # 初始 schema（4 表 3 索引），只前向
├── profiles_repo.py       # Profile 行类型 + 档案 CRUD
├── memories_repo.py       # Memory 行类型 + 记忆 CRUD / 上下文查询
├── reminders_repo.py      # Reminder 行类型 + 提醒 CRUD / 到期认领（幂等）
└── settings_repo.py       # 多行 KV 读写（音量/语言/儿童模式开关）
```

> **与 §4 目录树的一处差异（需 Lead 确认，见 advisory）**：新增 `host/storage/errors.py`。理由：P0 纪律「依赖只能向下、下层禁止反向 import 上层」，而 `00-architecture-spec.md` §4 把「异常分类（…Storage）」放在 `host/core/errors.py`；若存储层去 import 它即构成 **storage → core 的反向依赖**。因此把**存储层自己的异常层级**放在下层 `host/storage/errors.py`（只 import 标准库），上层 `host/core/errors.py` 可**再导出**它（上层 import 下层，合法）。备选（若 Lead 认为不必加文件）：把异常类并入 `host/storage/database.py`。

行类型（`Profile`/`Memory`/`Reminder`）**就近定义在各 repo 文件内**（`@dataclass(frozen=True, slots=True)`），不单开 `models.py`，避免再增文件。

---

## 2. 行类型（frozen dataclass）

字段与 `0001_init.sql` 一一对应；时间一律 `str`（ISO-8601 UTC 文本，见 §9）；`confirmed` 由 `INTEGER 0/1` 映射为 `bool`。

```python
# profiles_repo.py
@dataclass(frozen=True, slots=True)
class Profile:
    id: int
    display_name: str
    created_at: str

# memories_repo.py
@dataclass(frozen=True, slots=True)
class Memory:
    id: int
    profile_id: int
    kind: str
    value: str
    confirmed: bool
    created_at: str
    expires_at: str | None

# reminders_repo.py
@dataclass(frozen=True, slots=True)
class Reminder:
    id: int
    profile_id: int | None
    text: str | None
    due_at_utc: str | None
    timezone: str | None
    repeat_rule: str | None
    status: str | None
    last_fired_at: str | None
    idempotency_key: str | None
```

`settings` 无富类型，直接以 `dict[str, str]` / `str | None` 表示（§5）。

---

## 3. 连接与 PRAGMA（**每连接**，且在事务之前）

### 3.1 Database 门面

```python
# database.py
MIGRATIONS_DIR: Final[Path] = Path(__file__).resolve().parent / "migrations"
BUSY_TIMEOUT_MS: Final[int] = 500

class Database:
    def __init__(self, path: str | os.PathLike[str]) -> None: ...
    def connect(self) -> sqlite3.Connection: ...   # 调用线程的专用连接（线程局部）
    def initialize(self) -> int: ...               # 建库 + 跑迁移，返回最终 user_version
    def close(self) -> None: ...                   # 关闭当前线程的连接
    def close_all(self) -> None: ...               # 关停：关闭所有已开连接
    @property
    def user_version(self) -> int: ...
    def backup(self, dest: str | os.PathLike[str]) -> None: ...
    def restore(self, src: str | os.PathLike[str]) -> None: ...
```

- `connect()` 维护 `threading.local()`，**首次**为当前线程建连接后立即执行（顺序不可调换）：
  1. `PRAGMA foreign_keys = ON`
  2. `PRAGMA journal_mode = WAL`
  3. `PRAGMA busy_timeout = 500`
  4. `con.row_factory = sqlite3.Row`
- 连接**不跨线程共享**（`sqlite3` 默认 `check_same_thread=True`）。核心（单线程 actor）与 uvicorn 的线程池各用各的连接。

### 3.1.1 撞锁为什么**快速失败**（`BUSY_TIMEOUT_MS = 500`）

`busy_timeout` 是「撞上写锁后**等多久**再放弃」，取值决定撞锁请求的最坏耗时：
每个撞锁的写请求会**独占一个 DB 连接**整整 `busy_timeout` 毫秒。

- **500 是够用的**：实测无竞争写事务 p95 ≈ 2.9ms，500ms 是其约 170 倍，正常并发
  重叠绝无伤。不取 0：并发写仍需吸收那 2~3ms 的重叠。
- **5000 会拖垮服务**：每个撞锁写请求独占连接 5 秒（实测 5.57s），Pi 4B 上足以
  让并发写请求堆积、连接池耗尽。而这段时间它**什么也没做** —— 对方事务提交后
  重试必然成功，长等没有收益，只有代价。
- **快速失败需要契约配套**（否则只是把 500 换个数字）：撞锁由 `StorageBusy` 表达，
  web 层映射 **503 + `Retry-After` + `code=50301`**（见 `docs/api/openapi.yaml` 的
  `components.responses.StorageBusy`）。确定性存储故障（磁盘坏、表不存在、约束
  失败）仍是 500 / `50001`。无竞争写事务耗时实测见
  `tests/integration/test_error_envelope.py`。

> 该数值与实现的一致性由 `tests/contract/test_storage_design_doc_consistency.py`
> 钉住（读 `database.py` 的真实常量与本文档比对），再漂移会被测试拦住。

### 3.2 为什么「每连接开外键、且在事务之前」是硬纪律

`PRAGMA foreign_keys` **不持久化到数据库文件**，是连接级开关，默认 **OFF**。更关键的是：**在事务进行中执行该 PRAGMA 会被静默忽略**（不报错、读回仍是 0）。实测（§12-A/B）：

- 新连接未设置 → `PRAGMA foreign_keys` = `0`；
- 先跑了任意 DML（自动开启隐式事务）再 `PRAGMA foreign_keys=ON` → 读回仍是 `0`，**外键形同不存在**；
- 新连接**连上即设** → 读回 `1`，孤儿插入被 `FOREIGN KEY constraint failed` 拒绝。

因此 `connect()` 必须**在第一条 DML 之前**完成 PRAGMA；这也是「外键必须每连接开、不能一次开全局」的落地方式（否则 AC-13 的「删除生效」与级联删除都会静默失效）。

### 3.3 事务约定

- 写操作：显式 `BEGIN IMMEDIATE` … `COMMIT`，异常 `ROLLBACK`（`database.py` 提供 `transaction(db)` 上下文管理器）。
- 只读查询走隐式读事务即可。
- 禁止在事务中间改 PRAGMA（见 §3.2）。

---

## 4. 迁移策略

### 4.1 立场：**只做前向迁移，不写假设性的回滚 DDL**

对齐 Spec §11「`previousSeq` 不重置是**有意的重放防护**」的同款纪律：**不去制造「看起来对称、实则未验证」的假安全**。真实 down 迁移的回滚语义在无真板/无实物阶段**无法验证**，写出来只是纸面正确。迁移失败一律依靠**单步事务 + 备份/还原**兜底，而不是 up/down 迁移框架。`0001_init.sql` 顶部已写明此立场。

### 4.2 运行器

```python
# database.py（内部）
def discover_migrations(directory: Path) -> list[tuple[int, Path]]
def split_statements(text: str) -> list[str]          # 基于 sqlite3.complete_statement，稳健切分
def apply_migration(con: sqlite3.Connection, version: int, sql_text: str) -> None
```

- **发现**：扫描 `migrations/NNNN_*.sql`，取 `NNNN` 为整数版本号，升序排序。文件名前缀即版本，与 `user_version` 同口径。
- **执行顺序**：`initialize()` 读 `PRAGMA user_version`（初值 0），只应用 `version > current` 的迁移，按升序逐个执行；每个迁移是**一个独立事务**。
- **单步事务**：
  ```
  BEGIN IMMEDIATE
  for stmt in split_statements(sql_text): con.execute(stmt)
  PRAGMA user_version = <version>        # 与文件内的 PRAGMA 双保险、值一致
  COMMIT
  # 任一步异常 → ROLLBACK → raise MigrationError(version, cause)
  ```
- **失败回滚**：**整个迁移步骤原子回滚**。`PRAGMA user_version` 是写进数据库头且**参与事务**的（§12-A 实测：事务内设 5，`ROLLBACK` 后回到 0）——因此失败后版本号不会前进，不会留下半张表。
- 不使用 `executescript()` 跑迁移：它在执行前会隐式 `COMMIT`，破坏外层事务、使整步不再原子。故用 `split_statements()` 逐句执行。

### 4.3 备份 / 还原（§14.4 回滚测试的存储层支撑）

- `backup(dest)`：`sqlite3.Connection.backup(target)` 在线备份到 `dest`（不阻塞写）。
- `restore(src)`：**要求应用已静默**（状态机停、无活跃连接）——`close_all()` 后用备份文件替换工作库，再重开连接。这是「回滚」在生产环境的唯一可靠手段。
- 实测往返（§12-C）：删除数据后以备份还原，行数恢复。

---

## 5. `settings` 表：**多行 KV**（一行一键），不是单行多列

**结论**：多行键值（每行 = 一个设置项，`key` 主键 + `value`）。

**理由**：
1. **列形状即证据**：Spec §6 给的是 `key` PK + `value` + `updated_at`。只要存在 `key` 列，语义就必然是「一行一键」；单行多列方案不会有 `key` 主键。
2. **免迁移扩展**：新增设置项（如将来的 `language`）只需 `INSERT`，**不改 schema、不新增迁移文件**——这与「只前向迁移、避免无谓版本」的立场一致。
3. **写放大最小**：改一项只 UPSERT 一行；单行多列会整行重写并放大并发冲突面。
4. **与 API 对齐**：`GET/PATCH /settings` 是字段级补丁语义，KV 的 UPSERT 直接映射。

**已知键（规范名，非 schema 强制）**：`volume`、`language`、`child_mode_enabled`、`night_mode_enabled`、`memory_enabled`、`mute`。`value` 一律存字符串（`"true"/"false"`、`"low"/"mid"/"high"`、`"zh"/"en"`）；**布尔与枚举的解析在业务层**，存储层不做解释（仓储只做读写）。

---

## 6. 仓储接口契约（签名定死）

> 约定：`db: Database` 为首参（由它负责连接与 PRAGMA）；`now: str` 为调用方传入的 ISO-8601 UTC 文本（**存储层不取系统时间**，见 §9）。所有返回行类型为 frozen dataclass。异常见 §8。

### 6.1 `profiles_repo.py`

```python
def create(db: Database, display_name: str, *, now: str) -> int            # 返回新 id
def get(db: Database, profile_id: int) -> Profile                          # RecordNotFound
def exists(db: Database, profile_id: int) -> bool
def list_all(db: Database) -> list[Profile]                                # 按 id 升序
def rename(db: Database, profile_id: int, display_name: str) -> None       # RecordNotFound
def delete(db: Database, profile_id: int) -> bool                          # 硬删除；级联删 memories/reminders；幂等
```

### 6.2 `memories_repo.py`

```python
def add(db: Database, profile_id: int, kind: str, value: str, *, now: str,
        confirmed: bool = False, expires_at: str | None = None) -> int     # ForeignKeyViolation
def get(db: Database, memory_id: int) -> Memory                            # RecordNotFound
def list_by_profile(db: Database, profile_id: int, *, kind: str | None = None,
                    include_expired: bool = False, now: str | None = None) -> list[Memory]
def list_for_context(db: Database, profile_id: int, *, now: str) -> list[Memory]
def set_confirmed(db: Database, memory_id: int, confirmed: bool) -> None    # RecordNotFound
def update_value(db: Database, memory_id: int, value: str) -> None          # RecordNotFound
def delete(db: Database, memory_id: int) -> bool                            # 硬删除 + 单步事务；幂等（无该行返回 False）
def clear_profile(db: Database, profile_id: int) -> int                     # 「清空」硬删除，返回删除条数
def purge_expired(db: Database, *, now: str) -> int                         # 硬删除过期行，返回清理条数
```

- **删除语义（Lead 已裁定，A3）**：`delete` / `clear_profile` / `purge_expired` 一律**硬删除**（Schema 无 `deleted_at`），各自在**单步事务**内执行（§3.3）。`delete` 幂等——命中 0 行返回 `False`、**不抛 `RecordNotFound`**，便于「重复点删除」不报错。这是 AC-13「删除后不再注入上下文」的存储层保证。
- `list_by_profile`：走 `memories_by_profile(profile_id, kind)`；`kind=None` 查该档案全部；默认 **不返回已过期**（`expires_at IS NULL OR expires_at > now`）。
- `list_for_context`：**仅供记忆注入使用**——只取 `confirmed = 1` 且未过期，供 `host/ai/router.py` 构造上下文；**调用方必须把结果当作不可信数据做数据化封装**（§7.3）。

### 6.3 `reminders_repo.py`

```python
PENDING: Final[str] = "pending"
FIRED: Final[str] = "fired"
CANCELLED: Final[str] = "cancelled"

def create(db: Database, *, text: str, due_at_utc: str, timezone: str,
           profile_id: int | None = None, repeat_rule: str | None = None,
           idempotency_key: str | None = None, status: str = PENDING,
           now: str) -> int                    # UniqueViolation（idempotency_key 重复）
def get(db: Database, reminder_id: int) -> Reminder            # RecordNotFound
def list_by_profile(db: Database, profile_id: int, *,
                    status: str | None = None) -> list[Reminder]
def list_due(db: Database, *, now_utc: str, status: str = PENDING) -> list[Reminder]
def claim_due(db: Database, reminder_id: int, *, fired_at: str) -> bool
def cancel(db: Database, reminder_id: int) -> bool             # status -> CANCELLED（幂等）
def delete(db: Database, reminder_id: int) -> bool             # 硬删除；幂等
```

- `list_due`：走 `reminders_by_due(due_at_utc, status)`；`WHERE due_at_utc <= :now_utc AND status = :status ORDER BY due_at_utc`。
- `claim_due`：**幂等键的落地**——
  `UPDATE reminders SET status=:fired, last_fired_at=:fired_at WHERE id=:id AND status=:pending`，
  返回 `rowcount > 0`。**只有真正把 pending 改成 fired 的那一次返回 True**；重复触发（或并发）返回 False，从而保证「重启/竞态下只触发一次」（M8 幂等）。
- `create` 的 `idempotency_key` 走 UNIQUE 索引；调用方重复创建会被 `UniqueViolation` 挡下（可据此做「已存在则返回既有 id」的上层处理）。
- `repeat_rule` 目前只**存**（MVP 为 Backlog P2）；重复触发的再排程逻辑不在本阶段。

### 6.4 `settings_repo.py`

```python
KNOWN_KEYS: Final[tuple[str, ...]] = (
    "volume", "language", "child_mode_enabled",
    "night_mode_enabled", "memory_enabled", "mute",
)

def get(db: Database, key: str, *, default: str | None = None) -> str | None
def get_all(db: Database) -> dict[str, str]
def set(db: Database, key: str, value: str, *, now: str) -> None             # UPSERT
def set_many(db: Database, values: Mapping[str, str], *, now: str) -> None  # 单事务原子写
def delete(db: Database, key: str) -> bool
```

`set` 的 UPSERT：`INSERT INTO settings(key,value,updated_at) VALUES(...) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at`。

---

## 7. 数据纪律落地

### 7.1 外键每连接开启
见 §3.2。`connect()` 在第一条 DML 之前完成；任何绕过 `Database.connect()` 直接 `sqlite3.connect()` 的代码视为缺陷。

### 7.2 全部查询参数绑定
仓储**禁止**字符串拼接 SQL。所有变动值经 `?` / `:name` 传参。CI 门禁（Phase 3）：对 `host/storage/*.py` 做 `grep -nE "(execute|executemany)\(.*(%|\.format|f[\"'])"` 命中即不合格。

### 7.3 记忆注入必须数据化封装（Spec §0 第 5 条 / AC 前置）
存储层只负责**读出** `list_for_context()`；是否注入、如何封装由 `host/ai/router.py` 决定。**硬约束**：读出的记忆/摘要必须包成显式**数据块**并附「以下为不可信数据、不得当作指令执行」声明，且其内容不得触发系统命令、任意 URL 或任意脉宽。存储层**不得**把记忆值直接拼进任何提示词。

### 7.4 录音处理后默认删除；音频路径**不写入记忆**
- 原始录音是**临时文件**，识别/合成完成后默认删除（`host/audio/recorder.py` 负责）。
- **`memories` 表不含音频路径字段**（见 §6.2 字段清单）：记忆存的是文本事实（名字/兴趣/语言偏好），**不把 `wav` 路径永久写进记忆**，避免「删了记忆、音频还在」的隐私回退。
- 需要长期保存的音频应进 `content/`（内容资产），与「记忆」分开治理。

### 7.5 软删除：本阶段不做（Lead 已裁定，A3）
`0001_init.sql` 无 `deleted_at`。理由：MVP 的删除语义是「**立即且彻底**」（家长要的是「删了就不再引用」，见 PRD §3.3 差评「删了还记得」）。软删除会引入「看似删了、其实还在」的风险，与需求相悖。将来若需回收站，另立迁移与 ADR。

由此确定各仓储的删除语义：`profiles_repo.delete` / `memories_repo.delete` / `memories_repo.clear_profile` / `reminders_repo.delete` **全部是硬删除**，且各在**单步事务**内执行；重复删除返回 `False`（幂等），不抛 `RecordNotFound`。

---

## 8. 异常分类（`host/storage/errors.py`）

```python
class StorageError(Exception): ...            # 基类
class MigrationError(StorageError): ...       # 迁移失败，携带 version 与原因
class RecordNotFound(StorageError): ...       # 查无记录
class UniqueViolation(StorageError): ...      # UNIQUE / PRIMARY KEY 冲突
class ForeignKeyViolation(StorageError): ...  # 外键失败
class ConstraintViolation(StorageError): ...  # CHECK / NOT NULL 等
```

映射规则（Python 3.11+ 的 `sqlite3.Error.sqlite_errorname`，比正则匹配消息更稳）：

| `sqlite_errorname` | 抛出 |
|---|---|
| `SQLITE_CONSTRAINT_UNIQUE` / `SQLITE_CONSTRAINT_PRIMARYKEY` | `UniqueViolation` |
| `SQLITE_CONSTRAINT_FOREIGNKEY` | `ForeignKeyViolation` |
| `SQLITE_CONSTRAINT_CHECK` / `SQLITE_CONSTRAINT_NOTNULL` | `ConstraintViolation` |
| 其它 `sqlite3.Error` | 原样或包 `StorageError` |

**唯一定义处与依赖方向（Lead 已裁定，A1，2026-09-21）**：

- 存储层异常的**唯一定义处 = `host/storage/errors.py`**（下层）。该模块只 import 标准库，不 import 本项目任何上层模块。
- `host/core/errors.py` **不再重复定义** `StorageError`（及其子类），一律**再导出**：
  ```python
  from host.storage.errors import (  # noqa: F401  再导出，唯一定义处在下层
      StorageError, MigrationError, RecordNotFound,
      UniqueViolation, ForeignKeyViolation, ConstraintViolation,
  )
  ```
  上层（`core` / `adapters` / `web`）只从 `host.core.errors` 导入即可捕获，同时满足「异常类型唯一定义处」与「依赖只能向下（上层 import 下层）」。
- **禁止**在其它模块定义同名异常；新增存储异常只在 `host/storage/errors.py` 增补，并同步补进 `core/errors.py` 的再导出清单。

---

## 9. 时间与确定性

- 所有时间列存 **ISO-8601 UTC 文本**（`YYYY-MM-DDTHH:MM:SSZ`），由**调用方**经 `now: str` 传入——`host/core/clock.py` 的 `RealClock`/`VirtualClock` 提供该字符串。
- **DDL 不设 `CURRENT_TIMESTAMP` 默认值**：否则时间戳来自 SQLite 而非注入时钟，`VirtualClock` 无法驱动，AC-12/AC-13 不可复现。
- 存储层**不 import `host/core/clock.py`**（避免 storage→core 反向依赖），只接收已格式化的 `str`。

---

## 10. 与验收标准的映射

| AC / 步骤 | 存储层职责 | 落点 |
|---|---|---|
| AC-12 记忆持久化 | 写入后关连接、重开可读 | `memories_repo.add` + `get`；`Database` 连接可重开 |
| AC-13 删除生效 | 删除后不再出现在上下文 | `memories_repo.delete` → `list_for_context` 不再返回；`clear_profile` 清空 |
| AC-14 记忆开关 | 开关关闭时不写入 | `settings_repo.get("memory_enabled")` 由业务层判定后决定是否调 `add` |
| Spec §12 步骤 2/4 | 无硬件全链路含记忆读写 | `python -m host.main --simulate` 经 web → storage |
| Spec §12 步骤 6 | 门禁 | 外键开启、参数绑定、无字面色值、无 emoji |
| 幂等（M8） | 只触发一次 | `reminders_repo.claim_due` 条件更新 |

---

## 11. 明确不做（本阶段）

- 不写任何 **down/回滚迁移 DDL**（§4.1）。
- 不引入 ORM / 迁移框架（ADR-005）。
- 不做 `STRICT` 表（SQLite 3.37+ 特性）：虽本机 3.50 支持，但会让部署端最低 SQLite 版本被动抬高；类型与范围约束由 **Pydantic + DDL 的 NOT NULL/CHECK** 承担，足够。
- 不做软删除、不做多写者/跨库、不做连接池（单机单写者，WAL + busy_timeout 足够）。
- 不把音频路径写入记忆；不保留原始录音（§7.4）。

---

## 12. 实测证据（2026-09-21，本机 CPython 3.13.14 / SQLite 3.50.6）

### A. 迁移脚本可执行（Python `sqlite3` 与 `sqlite3` CLI 各跑一遍，结果一致）

```
tables      : ['memories', 'profiles', 'reminders', 'settings']
indexes     : ['memories_by_profile', 'reminders_by_due', 'reminders_idempotency_key_uq']
user_version: 1
CLI:  apply rc=0 ; integrity_check=ok
```

### B. 外键开关语义（每连接、且在事务之前）

```
默认（新连接，未设置）        : 0
DML 之后设 ON，读回           : 0   <- 事务进行中：被静默忽略
新连接·连上即设 ON，读回      : 1
FK ON  -> 孤儿 memories 插入  : 被拒 IntegrityError | FOREIGN KEY constraint failed
FK ON  -> 孤儿 reminders 插入 : 被拒 IntegrityError | FOREIGN KEY constraint failed
```

### C. 约束与索引

```
confirmed=2 -> 被拒 : IntegrityError | CHECK constraint failed: confirmed IN (0, 1)
重复 idempotency_key 'K1' -> 被拒 : UNIQUE constraint failed: reminders.idempotency_key
EXPLAIN memories_by_profile   -> SEARCH memories USING COVERING INDEX memories_by_profile (profile_id=? AND kind=?)
EXPLAIN reminders_by_due      -> SEARCH reminders USING COVERING INDEX reminders_by_due (due_at_utc<?)
EXPLAIN idempotency_key_uq    -> SEARCH reminders USING COVERING INDEX reminders_idempotency_key_uq (idempotency_key=?)
ON DELETE CASCADE: profile 1 删除前 memories/reminders=(1,1) -> 删除后=(0,0)
```

### D. 迁移 runner 事务语义

```
A0 user_version 初值            : 0
A1 事务内设 5，读到             : 5
A2 ROLLBACK 后 version          : 0   | 表 t 残留: 0    <- user_version 参与事务
B1 apply 0001 -> True | version : 1
B3 失败迁移返回: OperationalError near ";" | version 仍 1 | good 表残留 0  <- 整步原子回滚
C1 备份库行数 1 / C2 删除后 0 / C3 还原后 1               <- 备份/还原往返成立
```

> 复现方式（Phase 3 可提升为 `tests/contract/test_storage_schema.py`）：
> `python - <<'PY'` 读入 `host/storage/migrations/0001_init.sql`，用 `sqlite3.connect(tmp)` + 逐句执行 + 上述断言。本次为一次性脚本，跑完已清理。

---

## 13. 变更记录

| 日期 | 变更 | 影响 |
|---|---|---|
| 2026-09-21 | 初版：数据库门面 + 四仓储契约 + 迁移策略 | Phase 3 后端施工图 |
| 2026-09-21 | 落 A1 裁定：存储异常唯一定义处 = `host/storage/errors.py`，`core/errors.py` 只再导出（§8）；A3 裁定：各 `delete` 明确为硬删除 + 单步事务、幂等（§6.1/§6.2/§6.3/§7.5） | 异常与删除语义定死 |
