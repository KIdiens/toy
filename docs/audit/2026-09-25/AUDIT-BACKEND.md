# 只读审计报告 — 协议一致性 / 角色包安全 / 存储契约

- 基线 commit：`7ef6a6ebfe148492bec2455710091f073b2f988c`（工作区干净，未改动仓库任何文件）
- 范围：`host/motion/**`、`sim/**`、`host/storage/**`、`host/roles/**`（+ 追 storage 契约时发现的 1 处越界项）
- 只报三类：正确性缺陷 / 需求未满足 / 契约安全与数据完整性破坏。不含风格偏好。
- 每条先证伪：所有结论均有可复现探针（脚本与原始输出在本目录）。

## 探针清单（均可复跑）

| 脚本 | 输出 | 用途 |
|------|------|------|
| `probe_sscanf.py` | `probe_sscanf.out` | 用真实 C 运行时(msvcrt) `sscanf` 对照 `sim.wire` 解析 |
| `probe_frame_regex.py` | `probe_frame_regex.out` | 穷举畸形 FRAME 行，判 sim 是否比固件更宽松 |
| `probe_frame_observable.py` | `probe_frame_observable.out` | 把解析分歧落到控制器可观测输出行 |
| `probe_storage_rolepack.py` | `probe_storage_rolepack.out` | 事务裸异常逃逸 + 幂等键取回记录 |
| `probe_rolepack_names.py` | `probe_rolepack_names.out` | 角色包成员名归一化冲突 / ADS |

---

## A. 协议一致性（固件 ↔ 模拟器）

### [P-1] sim/wire.py:45（`_ARM_RE`）/ sim/controller.py:10-11 / tests/integration/test_sim_fault_paths.py:103
- **现象**：`parse_arm` 正则 `^ARM\s*([+-]?\d+)$` 要求行尾紧跟数字。固件 `sscanf("ARM %d %c")==1` 的**格式串空白指令**会把行尾空白吃掉、随后 `%c` 撞到串尾不赋值 → 返回 1 → **接受** `"ARM 1 "`。探针 1（真实 msvcrt `sscanf`）实测：`"ARM 1 "`/`"ARM 1  "`/`"ARM 1\t"` 固件 rc=1（接受）；sim 全部拒绝。
- **后果**：`test_sim_fault_paths.py:103` 断言的 `"ARM 1 "→ERR COMMAND` 与真机行为**相反**（真机回 `OK ARM 1`）——绿色测试固化了错误 oracle；`controller.py:10-11`、`wire.py:42-44` 的 docstring 对 `%c` 语义的描述本身是错的。sim 比固件**更严**（方向安全，坏主机不会被 sim 放行上真机），但违反「逐字复刻/等价」声明。
- **定性**：**确定缺陷**（保真度 + 测试 oracle 错误）。
- **修法**：`_ARM_RE` 改为允许尾随空白 `^ARM\s*([+-]?\d+)\s*$`；同步修正测试与 docstring。若刻意保持 strict，则必须在模拟器文档明确声明「这是刻意的更严契约」，不能既声明等价又行为不等价。

### [P-2] sim/wire.py:46-48（`_FRAME_RE`）/ sim/controller.py:161-178
- **现象**：`_FRAME_RE` 用 `\s*` 允许空匹配、`\d+` 可回溯 → **字段数不足**的行被「把一个数字劈成两段」匹配成功。实测：`parse_frame("FRAME 0 1500 1550 1500")`（仅 4 个数）→ `(0, [1500, 1550, 150, 0])`；固件同串 `sscanf` rc=4（≠5）→ 拒绝。
- **后果**：固件回 `ERR COMMAND`，sim 回 `ERR FRAME`（探针 5 F2 实测）。两者都 `stopMotion`，**安全等价**；但对外错误行不同 → 黄金回放/主机分支判定与真机不一致。穷举 1432 条畸形行**未发现**能把四轴都劈进 `[1400,1600]` 从而「静默接受坏帧」的样例（第 5 段恒为末位单个数字⇒必越界）→ **不构成安全漏洞**，属错误码级保真缺陷。
- **定性**：**确定缺陷**（保真度；非安全漏洞）。
- **修法**：数字组之间要求至少一个分隔空白，如 `^FRAME\s*([+-]?\d+)(?:\s+([+-]?\d+)){4}\s*$`；或先 `line.split()` 再判字段数。补一条 `parse_frame` 单测。

---

## B. 角色包安全

### [R-1] host/roles/rolepack.py:126-140（**已知项，确认**）
- **现象**：`target.rename(backup)`(133) 与 `incoming.rename(target)`(134) 之间失败/崩溃时，`except` 只 `rmtree(incoming)` 后重抛 → **target 已不存在**，旧版本仅残留在 `<name>.bak`。
- **后果**：该角色目录消失（角色丢失），撞上 R-2 会把 `.bak` 当好角色再加载。现有测试 `tests/unit/test_rolepack_guard.py:120-131` 的失败注入发生在 `extract_safely`（L127 之前），**没覆盖** L133 之后的窗口 → 假的安全感。
- **定性**：**确定缺陷**（AC-25「任一步失败保留旧版本」未满足）。
- **修法**：`incoming.rename(target)` 失败时先 `backup.rename(target)` 回滚再 `rmtree(incoming)`；补「rename 后失败」用例（monkeypatch `Path.rename` 令第 2 次抛错）。

### [R-2] host/roles/registry.py:38（**已知项，确认 + 放大后果**）
- **现象**：`load_all` 扫描 `root.iterdir()` 下所有目录，未排除 `*.bak` / `*.incoming`。
- **后果**：`<name>.bak`/`<name>.incoming` 一旦残留（R-1、崩溃，或 rolepack.py:135 `rmtree(backup, ignore_errors=True)` 在 Windows 静默失败），它们含 `role.json` 就被当角色再注册 → 同一 role_id 由两目录提供，`register()` 在 config_version 不同时抛 `ConfigError` → **整个 `load_all()` 失败**（角色全加载不出、`set_active` 失效、`play_action` 全废）。属可用性/数据完整性缺陷。
- **定性**：**确定缺陷**。
- **修法**：`load_all` 显式跳过以 `.bak`/`.incoming` 结尾（或以 `.` 开头）的目录；`import_rolepack` 成功路径不得残留（L135 的 `ignore_errors` 改为失败上报或改名隔离）。

### [R-3] host/roles/rolepack.py:56-80 + 97-117（**新增**）
- **现象**：成员名只做**逐条**校验，**无归一化冲突检测**。大小写不敏感 FS（Windows/macOS）上 `role.json` 与 `Role.json` 落到**同一文件**；`read_member(zip,"role.json")`（供 schema 校验）读到第一个成员，后写入的 `Role.json` 覆盖磁盘。探针 4 E1 实测：read_member 得到 `role_id=1/name=合法`（通过校验），落盘 `role.json` 却是 `role_id=2/name=冒名`。
- **后果**：**「导入前校验的对象」≠「落盘生效的对象」** → AC-25「非法包在替换前被拒绝」被绕过；落盘 role_id 与目录名（由**已校验** role_id 决定）不一致 → 注册进错误 role_id、`_role_dir(role_id)` 指向不存在目录、动作白名单/动作文件全部错位。契约安全 + 数据完整性。
- **定性**：**确定缺陷**（大小写不敏感 FS 上确定；Linux 不触发）。
- **修法**：`validate_zip` 对成员名（`casefold()`/`normcase()` 归一化后）做重复检测，冲突即 `RolePackError`；`extract_safely` 落盘/去重用归一化名。

### [R-4] host/roles/rolepack.py:45-53 + 128-130（**新增，低危**）
- **现象**：`_check_name` 只在 `normalized[1] == ":"` 时拒（盘符），成员名其它位置的冒号放行；Windows 下 `role.json:evil` 是 NTFS ADS 写法。探针 4 E3 实测：仅含成员 `role.json:evil` 的包通过校验，导入后 `role.json` **被凭空创建为 0 字节文件** → `import_rolepack` 的「落盘后必须含 role.json」(L129) 被空文件满足。
- **后果**：仅当绕过 `content.py` 的 `read_member` 前置校验、直接调 `import_rolepack` 时才把「空 role.json 包」当合法导入（当前 web 路径因先 `read_member` 而**不可利用**）→ 纵深防御缺口，且跨平台口径不一致（Linux 下是普通文件名）。
- **定性**：**确定缺陷（低危 / 防御纵深）**。
- **修法**：`_check_name` 整体拒绝含 `":"`（及 `\`）的成员名，限定安全字符集；`import_rolepack` 对落盘 `role.json` 加「非空且可解析」校验。

---

## C. 存储契约

### [S-1] host/storage/database.py:82（**已知项，确认 + 实测**）
- **现象**：`transaction()` 的 `con.execute("BEGIN IMMEDIATE")` 在 `try` **之外**。探针 3 A 实测：WAL 库、线程 A 持写锁，线程 B 调 `db.transaction()` → 等 busy_timeout(≈5.5s) 后抛**裸** `sqlite3.OperationalError: database is locked`（**不是** StorageError）。
- **后果**：违反 docstring「异常 … 统一转成 StorageError」与设计 §8；上层只 catch StorageError 家族会漏捕 → 可能冒泡 500。多线程/多进程写竞争下可达。
- **定性**：**确定缺陷**。
- **修法**：把 BEGIN 放进 `try`，失败时 `raise translate(exc)`。

### [S-2] host/storage/database.py:92（**新增**）
- **现象**：`else: con.execute("COMMIT")` 同样在 `try` **之外**。COMMIT 失败（SQLITE_FULL / SQLITE_BUSY / I/O error）时裸 `sqlite3.Error` 直接逃逸，且事务可能仍开着。
- **后果**：同 S-1 契约泄漏；COMMIT 失败后连接进入未知事务态，后续 `BEGIN IMMEDIATE` 再抛（叠加 S-1）。
- **定性**：**待验证假设**（代码路径明确；未能稳定构造 COMMIT 失败，未实测到）。
- **修法**：`try: con.execute("COMMIT") except sqlite3.Error: rollback; raise translate(...)`。

### [S-3] host/adapters/services.py:84-90（**越界项**：追 storage §6.3 契约时发现，文件不在四个枚举目录内）
- **现象**：幂等键重复时 `reminders_repo.create` 抛 `UniqueViolation`（§6.3 允许），调用方拿不到既有 id 就用 `reminders_repo.list_by_profile(pid)[-1]` 充当「既有记录」。探针 3 C 实测：先建 A(KEY-A)、B(KEY-B)，重放 KEY-A → 取回的是 `[-1]` 选中的 **B**（`text='B-睡觉'`, `key='KEY-B'`），而非 A。
- **后果**：重复提交返回**另一条提醒** → 客户端高亮/插入错误记录，静默数据不一致（失效模式 #2）。根因：`reminders_repo` 无「按幂等键取回」入口（§6.3 仅说调用方据此处理）。
- **定性**：**确定缺陷**（数据完整性）。
- **修法**：`reminders_repo` 增 `get_by_idempotency_key(db, key) -> Reminder | None`（走覆盖索引 `reminders_idempotency_key_uq`），调用方改用；找不到再按现有 HostError 如实抛出。

---

## D. 已证伪 / 不报（附理由，供交叉核对）

| 项 | 结论 |
|----|------|
| `connect()` 中途 PRAGMA 失败 → 连接未登记泄漏（database.py:64-73） | **不单列**：`foreign_keys`/`busy_timeout`/`row_factory` 不会失败；仅 `journal_mode=WAL` 在并发抢锁瞬间可能失败，届时抛裸错且连接未登记（归入 S-1 家族）。建议把 PRAGMA 段也纳入 try 并在成功后再登记。 |
| `reminders` 上 `UNIQUE(idempotency_key)` 对 NULL 的语义 | **证伪**：SQLite 多个 NULL 互不冲突 → 无幂等键的提醒可无限创建，是**期望**行为（键可选）。非缺陷。 |
| `extract_safely` 越界防护（`destination not in target.parents`） | **证伪**：对 `\`、`..`、绝对路径、盘符、符号链接逐条试过，均被拦（或原地contained）。未发现穿越。 |
| zipfile 解压大小是否受声明 `file_size` 限制 | **证伪**：CPython `ZipExtFile` 按中央目录声明大小截断 → 声明大小校验有效，压缩炸弹声明无法绕过。 |
| `Scenario.controller_args` 用 McController 关键字、`Session._known_args` 用 make_controller 关键字 | **低危/潜在**：二者关键字集不重合（`dock/estop` vs `docked/estop_ok`）。当前内置场景只用 `pwm_ready`（两边都认）故不触发；一旦有场景通过 `controller_args` 设 `dock=`，录制/回放会失配。属可扩展性隐患，未达「确缺陷」门槛，仅提示。 |

---

## E. 汇总（按严重度）

| 编号 | 位置 | 定性 | 类别 |
|------|------|------|------|
| P-1 | sim/wire.py:45 + 测试:103 | 确定缺陷 | 保真度 / 测试 oracle |
| P-2 | sim/wire.py:46-48 | 确定缺陷（非安全） | 保真度 |
| R-1 | rolepack.py:133-140 | 确定缺陷 | 需求未满足(AC-25) |
| R-2 | registry.py:38 | 确定缺陷 | 数据完整性 / 可用性 |
| R-3 | rolepack.py:56-117 | 确定缺陷 | 契约安全（Windows） |
| R-4 | rolepack.py:45-53 | 确定缺陷（低危） | 契约安全（纵深） |
| S-1 | database.py:82 | 确定缺陷 | 契约（异常泄漏） |
| S-2 | database.py:92 | 待验证假设 | 契约（异常泄漏） |
| S-3 | services.py:84-90 | 确定缺陷（越界） | 数据完整性 |
