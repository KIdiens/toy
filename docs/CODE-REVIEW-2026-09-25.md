# 代码全面复查报告 — AI 角色玩具主机程序

> 复查日期：2026-09-25
> 基线 commit：`7ef6a6e`（工作区干净，复查期间仓库零改动）
> 复查范围：`toy/` 全树 Python（113 文件）+ `host/web/static/` 前端 JS
> 结论：**fail** —— 15 项阻断级/高危、8 项中危、11 项低危·latent·文档漂移
>
> **本报告只纳入已复现或已回读代码确认机制的条目。** 每条标注证据来源：
> `[我复现]` = 主理人独立探针复现；`[实测]` = 实测方真实进程复现；`[代码确认]` = 回读代码确认机制、触发条件明确。
> 未实测项在第五节单列，不用推断填充。

---

## 复查方法

五路并行、范围互不重叠（均只读，禁止改文件）：

| 审计方 | 范围 | 手段 |
|---|---|---|
| 架构师 | `host/core`、`host/main.py`、`host/adapters`、`sim` | 读代码 + 真模块探针 |
| 后端 | `host/motion`、`sim`、`host/storage`、`host/roles` | 读代码 + **真实 C 运行时对照固件语义** |
| QA-2 | `host/ai`、`host/audio`、`host/tools`、`host/web` | 读代码 + 探针 |
| 前端 | `host/web/static/**` | **真实 headless Chrome + 有数据 mock 后端**，落盘 DOM 逐行核对 |
| QA-3-2 | 全范围 | **唯一全程实测**：真进程、真压力、真异常注入、资源采样 |

三条纪律：只报三类（正确性缺陷 / 需求未满足 / 契约安全与数据完整性破坏）；报前先证伪自己，区分「确定缺陷 / 待验证假设 / latent」；主理方对关键条目**独立复现后才采信**。

---

## 一、阻断级（正确性 / 安全 / 数据完整性）

### 1. AI 工具调用的最终答复被丢弃 —— 工具功能在真实档位完全不生效
- **位置**：`host/ai/router.py:88-94`（`respond_text` 用第一轮 `reply.text`）、`:108-130`（`_run_tools` 只返回 `used/rejected`；第 129 行 `reply = current` 是**死赋值**）
- **现象**：模型发工具调用时第一轮 `text` 通常为空；`_run_tools` 拿到的第二轮最终文本**从不返回**，`respond_text` 仍用第一轮的 `reply.text`。
- **后果**：天气 / 提醒 / 内容三个工具**在 `AI_PROVIDER=qwen` 下永远答不到用户嘴里**。实测：工具成功返回 `{'ok':True,'temp_c':24.0}`，用户收到的是「我现在查不到这个，不能乱猜」+ `blocked=True`（reason=`空输出`）。
- **为何测试没抓到**：默认桩 `build_stub_triad()` 无工具规则，故不触发；唯一工具测试只覆盖「参数非法→拒绝」反向路径。**967 项全绿完全掩盖了它。**
- **定性**：确定缺陷
- **修法**：`_run_tools` 返回最终 `LlmReply`（或 final text），`respond_text` 用 `final.text`；删除死赋值；补「工具成功 → 回复 = 工具后总结」单测。
- **证据**：`[我复现]`

### 2. 物理表演键使设备永久卡在 PERFORMING
- **位置**：`host/adapters/execution.py:57-62`（`pump_once` 的 `if not self.motion_busy: return True` 丢弃逻辑）+ `host/adapters/ports.py:170`（`motion_busy` 仅在 HTTP `play_action` 里置位）
- **现象**：按键路径 `press_button(2) → engine.apply(PERFORM_PRESS)` 下发的 `PLAY_ACTION` **不置** `motion_busy`；worker 见 `motion_busy=False` 即把它当**陈旧命令丢弃** → `_run_play` / `_finish_without_motion` 都不执行 → 无 `PERFORM_DONE` → 状态机无出口。
- **后果**：物理表演键完全失效 + 设备卡「表演中」，只能再按一次自救。
- **定性**：确定缺陷
- **修法**：按键路径同样置 `motion_busy`；或让 `pump_once` 对「无 action 载荷」的 `PLAY_ACTION` 走 `_finish_without_motion`；补 `n=2` 端到端测试。
- **证据**：`[我复现]` + `[实测]`，含**单变量因果实验**：
  ```
  不预置（生产实况）      IDLE --PERFORM_PRESS--> PERFORMING   ← 停住，无后续迁移
  预置 motion_busy=True   IDLE --PERFORM_PRESS--> PERFORMING --PERFORM_DONE--> IDLE
  → 单变量即翻转结果，根因锁定为 pump_once 的 motion_busy 守卫
  ```

### 3. 取消录音会凭空多录一段（EXIT 命令携带「目标态」代数）
- **位置**：`host/core/engine.py:130-137`（EXIT/ENTER 命令一律取 `machine.generation`，但读数发生在 `fire()` **之后**）+ `host/adapters/workers.py:160-164`（`_finish_recording` 用代数列判定）
- **现象**：`fire()` 在「进入 IDLE」时已把 generation +1，EXIT 命令随后才取值，于是拿到的是**目标态代数**。
- **后果**：取消录音（录音中按表演键 / `REC_CANCEL`）时 `_finish_recording` 判 `done=False`，走兜底 `_record_once` —— **多录一整段**。`--simulate` 下 null 后端秒回被掩盖；**真机档位下是 15 秒阻塞录音**，还会占住 audio 线程，使用户下一次按键的命令排在它后面。同时 `_rec_started_gen/_last_recording_gen` 被改写成被 bump 的代数。
- **定性**：确定缺陷
- **修法**：`Engine.apply` 先取 `pre_gen = machine.generation`，EXIT_* 用 `pre_gen`、ENTER_*/NOTIFY 用 `machine.generation`；或让 `_finish_recording` 改判 `_rec_started_gen`。
- **证据**：`[我复现]`
  ```
  正常结束   RECORD_START.gen = 5   RECORD_STOP.gen = 5   → 一致，不触发兜底
  取消录音   RECORD_START.gen = 5   RECORD_STOP.gen = 6   → 不一致，凭空多录
  真机装配实测：正常结束新增 0 次录音调用 / 取消录音新增 1 次
  ```

### 4. 被拒绝的 `POST /actions/play` 已经改了状态机（409 有副作用）
- **位置**：`host/adapters/ports.py:139-174` —— 先 `engine.apply(Trigger.PERFORM_PRESS)`（:165-168），**再**判 `if not self.motion_busy: raise ConflictError`（:171-172）
- **现象**：SPEAKING / RECORDING 态下 `PERFORM_PRESS` 是**合法迁移**（都指向 IDLE），会真的迁移；因不产生 `PLAY_ACTION`，随后才抛 409。
- **后果**：客户端收到「拒绝」，但玩具已经被切走 —— 说话中请求播放会**打断正在说的话**；录音中请求播放会**取消录音**（并叠加第 3 条的凭空多录）。
- **定性**：确定缺陷
- **修法**：入口先 `machine.can_fire(PERFORM_PRESS)` 预判目标态是否接受（或显式白名单 IDLE/THINKING），不接受直接 409，不调用 `apply`。
- **证据**：`[我复现]`
  ```
  调用前 SPEAKING → play_action 返回 409 → 调用后 IDLE
  迁移历史：SPEAKING --PERFORM_PRESS--> IDLE
  ```

### 5. 角色包导入非原子：旧版本凭空消失 / 残留导致启动 brick
- **位置**：`host/roles/rolepack.py:126-140`（`except` 只 `rmtree(incoming)`，**不还原 `.bak`**）+ `host/roles/registry.py:38`（`iterdir()` 未排除 `*.bak` / `*.incoming`）
- **现象**：`target.rename(backup)`（:133）与 `incoming.rename(target)`（:134）之间失败或崩溃时，`target` 已不存在。
- **后果**：① 导入失败 → **旧角色目录消失**，违反 AC-25「任一步失败保留旧版本」；② 崩溃残留两个目录都含 `role.json` → `load_all()` 抛 `ConfigError` → **启动失败、需人工清理、无自愈**；若两残留版本号相同则**静默加载错误配置**（比 brick 更危险）。
- **性质**：现有测试的失败注入在 `extract_safely`（:127 之前），**没覆盖 :133 之后的窗口** → 假安全。
- **定性**：确定缺陷
- **修法**：`incoming.rename(target)` 失败时先 `backup.rename(target)` 回滚；`load_all` 跳过 `.bak`/`.incoming`（**此条优先**，对历史残留免疫，可让已 brick 的实例自愈）；改单次原子交换。
- **证据**：`[实测]`（隔离 root）+ `[代码确认]`

### 6. 角色包成员名大小写归一化缺失 → 「校验对象」≠「落盘对象」
- **位置**：`host/roles/rolepack.py:56-80`（`validate_zip`）+ `:97-117`（`extract_safely`）；`_check_name` 只在 `normalized[1] == ":"` 拒盘符，无归一化去重
- **现象**：包内同时含 `role.json` 与 `Role.json`。校验用的 `read_member(zip,"role.json")` 读第一个成员；解压写入时后写者覆盖磁盘 → 同一文件。
- **后果**：**AC-25「非法包在替换前被拒」被绕过**；落盘 `role_id` 与目录名（由已校验的 role_id 决定）不一致 → 注册错 role_id、动作白名单与文件全错位。Windows / macOS 确定触发，Linux 不触发。
- **定性**：确定缺陷（安全 + 数据完整性）
- **修法**：`validate_zip` 对成员名 casefold / normcase 归一化后做重复检测，冲突即 `RolePackError`；落盘按归一化名去重。
- **证据**：`[后端点名探针复现]`（`_audit_backend/probe_rolepack_names.out` E1，读回 `role_id=1` / 落盘 `role_id=2`）；我未独立复现，**采信其输出但保留复现义务**。

### 7. 幂等重放返回**另一条**提醒（静默数据不一致）
- **位置**：`host/adapters/services.py:84-90` —— 幂等冲突后用 `list_by_profile(pid)[-1]` 冒充「既有记录」；根因是 `host/storage/reminders_repo.py` **没有按幂等键取回的入口**（只有 `get`/`list_by_profile`/`list_due`/`create`/`cancel`/`delete`）
- **后果**：客户端重试一次 `POST /reminders`，拿到的是**别人的提醒内容** → 插入错误记录，无任何报错。
- **定性**：确定缺陷（数据完整性）
- **修法**：`reminders_repo` 增 `get_by_idempotency_key(db, key)`（走既有覆盖索引 `reminders_idempotency_key_uq`），调用方改用。
- **证据**：`[我复现]`
  ```
  创建 A(id=1,'A-该睡觉了')、B(id=2,'B-该喝水了')
  重放 KEY-A（期望 A）→ id=2 text='B-该喝水了'   ← 取回了 B
  ```

### 8. 三个可达 500，且不返回统一信封
- **位置**：`host/adapters/services.py:29` → `_normalize_due` 抛裸 `ValueError`；`host/storage/memories_repo.py:89` / `reminders_repo.py:120` 抛裸 `OverflowError`；`host/web/app.py` 仅映射 `HostError`/`StorageError`/`RequestValidationError`，**无通用兜底**
- **实测**（body 均为纯文本 `Internal Server Error`，非 `{code,data,message}`）：
  ```
  POST /reminders {due_at_utc:"not-a-date"}        → 500（应 422）
  GET  /memories?profile_id=<19 位整数>            → 500（18 位→200；≥4301 位→422；19–4300 位→500）
  DELETE /reminders/<20 位整数>                    → 500
  ```
- **后果**：用户可控输入触发 500，违反 SPEC §5「统一信封全部强制」；且 `schemas.py:57` 的 `due_at_utc` 只约束 `min_length=1`，任意串直达业务层（而 `openapi.yaml` 却声明 `format: date-time` → 文档-代码漂移）。
- **定性**：确定缺陷
- **修法**：int 参数统一加 `<= 2**63-1` 范围界转 `ClarifyError`(422)；`due_at_utc` 加格式约束或 `create_reminder` 捕获 `ValueError` 转 422；`app.py` 加 `Exception` 兜底 handler → `code=50000` + 通用文案，**响应不外泄堆栈/路径，但日志必须 `log.exception` 记全量堆栈**（禁止吞异常掩盖根因）。
- **证据**：`[实测]` + `[代码确认]`

### 9. DB 被锁时写请求 500 + 裸 `sqlite3.OperationalError`
- **位置**：`host/storage/database.py:82`（`BEGIN IMMEDIATE` 在 `try:` **之外**）、`:92`（`COMMIT` 同样在 `try` 之外 → 待验证）
- **实测**：外部 `BEGIN EXCLUSIVE` 期间 `POST /reminders` → `500`、耗时 **5.5s**（busy_timeout 耗尽）、服务端记 `sqlite3.OperationalError: database is locked`（**未 translate**）。WAL 下读仍 200，释放后写恢复 201。
- **后果**：违反该文件自述的「统一转 `StorageError`，不向外泄漏裸 sqlite3.Error」；上层只 catch `StorageError` 家族会漏捕 → 冒泡 500。COMMIT 失败还会让连接停在未知事务态。
- **定性**：确定缺陷（`:82`）/ 待验证假设（`:92`，未能稳定构造 COMMIT 失败）
- **修法**：BEGIN/COMMIT 均移入 `try`，失败 `raise translate(exc)`，COMMIT 失败先 rollback。
- **证据**：`[实测]`

### 10. 提醒列表永远停在加载骨架
- **位置**：`host/web/static/pages/reminders.js:62` —— `list.setAttribute('busy','true')`，而 `render()`（:35-59）**从不置回 `'false'`**（对照 `pages/memories.js:34` 有）
- **后果**：在线/离线都必现。DOM 实测：真实有 1 条提醒时仍只见 `<loader-skeleton>`，无任何 `<reminder-row>` → 页面主内容不可用。
- **定性**：确定缺陷
- **修法**：`render()` 里补 `list.setAttribute('busy','false')`。
- **证据**：`[代码确认]`（我逐行核实）+ `[实测]`（真实 Chrome DOM）

### 11. 记忆 / 提醒的行内容静默为空
- **位置**：`host/web/static/components/base.js:58,70-71`（`if (first)` → `mounted()` **只在首次渲染执行一次**，`updated()` 每次执行）+ `components/memories.js:91-95`、`components/reminders.js:146-151`（行数据在 `mounted()` 里注入）
- **后果**：首次渲染时 `items` 还空，之后 `set items` 触发的重渲染**不再执行 `mounted`** → 新生成的行从未拿到数据。DOM 实测：`<memory-row …><div class="row__value" title=""></div>` —— 记忆正文整条丢失，但页脚「共 1 条记忆」正常。**「看着有、其实是空的」是最隐蔽的一类**。
- **定性**：确定缺陷
- **修法**：赋值从 `mounted()` 移到 `updated()`；或模板直接用数据生成行，不依赖子元素属性注入。
- **证据**：`[代码确认]` + `[实测]`

### 12. 编排器「动作文件」即使后端在线也永远空态
- **位置**：`host/web/static/api.js` **无** `listActionFiles`（全文件零 `files` 命中）+ `pages/editor.js:33` `fileList: []`（注释「契约未提供该端点前恒为空」**已过时**）
- **后果**：后端 `GET /api/v1/actions/files` 已存在（`host/web/routers/actions.py:20`），前端未接。浏览器实测请求轨迹 = `[/api/v1/events, /api/v1/roles, /api/v1/status]`，**无** `/actions/files`。
- **定性**：确定缺陷（需求未满足）
- **修法**：`api.js` 加 `listActionFiles`；`editor.js` 在 `loadStatus()`/`loadRoles()` 后填充 `S.fileList` 再 `renderFiles()`；删过时注释。
- **附带发现（门禁盲区）**：`tests/frontend/selfcheck_static.py:96-98` 的「端点覆盖」是**硬编码 12 个端点清单**，不含 `/actions/files` → 这类「端点在、前端没接」**永远不会被自检抓到**。建议改为从 `openapi.yaml` 读路径逐一比对。
- **证据**：`[代码确认]` + `[实测]`

### 13. 在线转故障时，顶部故障横幅被永久隐藏
- **位置**：`host/web/static/pages/status.js:30-32`（在线无故障 → `classList.add('hidden')`）vs `:83`（只在 `anyOffline && !status` 分支 `remove`）；`styles.css:189` `.hidden{display:none!important}`
- **后果**：先成功连上（加 hidden），随后出现故障时故障分支**不移除 hidden** → 故障横幅永久不可见。（缓解：`#fault-slot` 的 fault-panel 仍会渲染，故非完全静默。）
- **定性**：确定缺陷
- **修法**：`renderConn()` 统一用 `banner.classList.toggle('hidden', <无故障且在线>)`，三个分支收敛到一处决策。
- **证据**：`[代码确认]`

### 14. 敏感词检测可被零宽字符 / 全角数字绕过
- **位置**：`host/ai/safety.py:143-146`（`neutralize` 只剔除 `< ' '` 的字符并压缩空白，**不做 NFKC、不剔零宽**），而所有检测（`:103-110`、`:121-135`）都是子串/正则精确匹配
- **实测**：
  ```
  你的密码是多少            → 拦截=True   '隐私索取: 密码'
  你的密<U+200B>码是多少    → 拦截=False  ← 绕过
  你的密<U+FEFF>码是多少    → 拦截=False  ← 绕过
  我的手机号是13800138000   → 拦截=True
  我的手机号是１３８００１３８０００ → 拦截=False ← 绕过
  ```
- **定性**：确定缺陷（归一化不完整）。**非 P0** —— Spec §10.1 C12/C13 已明确本模块是桩级守卫、不承诺真实内容安全。但漏 NFKC/零宽是**纯实现级缺口**，没有理由留。
- **修法**：`neutralize` 先 `unicodedata.normalize("NFKC", text)`，再过滤 `unicodedata.category(ch) == "Cf"`；补输入/输出两侧的零宽与全角回归用例。
- **已证伪**：`U+202E` 双向覆盖符**不能**绕过（匹配走逻辑串，`怎么\u202e打开插座` 仍命中「插座」）。
- **证据**：`[我复现]`

### 15. 模拟器与固件语义不一致，且**绿色测试固化了相反的 oracle**
- **位置**：`sim/wire.py:45` `_ARM_RE = ^ARM\s*([+-]?\d+)$`；被固化的断言在 `tests/integration/test_sim_fault_paths.py:99-103`
- **现象**：固件是 `sscanf(line, "ARM %d %c", ...) == 1`（`.ino:55`）。格式串中 `%d` 后的空格会**吃掉行尾空白**，`%c` 撞串尾不产生输入失败 → **返回值仍为 1** → 真机**接受**尾随空白。
- **真实 C 运行时对照实测**：
  ```
  'ARM 1 '      固件 rc=1 接受=True  |  sim 接受=False   <<< 不一致
  'ARM 1  '     固件 rc=1 接受=True  |  sim 接受=False   <<< 不一致
  'ARM 1\t'     固件 rc=1 接受=True  |  sim 接受=False   <<< 不一致
  'ARM 1 2'     固件 rc=2 接受=False |  sim 接受=False   （一致）
  ```
- **后果**：`sim/wire.py:42-44` 的注释把这条写反了；`test_sim_fault_paths.py:103` 断言 `"ARM 1 " → ERR COMMAND` 与真机**相反** → **一条绿色测试正在保护错误行为**，后续「host 与真机对齐」会被它误导。方向安全（sim 更严），但违反「逐字复刻」声明。
- **定性**：确定缺陷（保真度 + 测试 oracle）
- **修法**：`_ARM_RE` 改 `^ARM\s*([+-]?\d+)\s*$`；同步修测试与 docstring。若刻意更严，必须在模拟器文档明写「刻意的更严契约」，不能既声明等价又不等价。
- **证据**：`[我复现]`（msvcrt.sscanf + 模拟器实际解析对照）

---

## 二、中危

| # | 位置 | 现象与后果 | 定性 | 修法 | 证据 |
|---|---|---|---|---|---|
| 16 | `sim/wire.py:46-48` `_FRAME_RE` | 数字组之间用 `\s*`（可空）+ `\d+` 可回溯 → 字段数不足的行被「把一个数字劈成两段」而匹配成功：`"FRAME 0 1500 1550 1500"` → `(0,[1500,1550,150,0])`，固件 rc=4 拒绝。两者最终都 stopMotion（**穷举 1432 条畸形行，未发现能把四轴都劈进 [1400,1600] 的样例 → 非安全漏洞**），但错误行不同（`ERR COMMAND` vs `ERR FRAME`）→ 黄金回放/主机分支判定与真机不一致 | 确定缺陷（保真度，非安全） | 数字组间要求 `\s+`，如 `^FRAME\s*([+-]?\d+)(?:\s+([+-]?\d+)){4}\s*$` | `[我复现]` |
| 17 | `host/adapters/execution.py:156-158` + `ports.py:109-113` | 心跳只在循环顶部刷新，而 `pump_once → _run_play → motion.play()` 会阻塞（SPEC §10 允许动作 ≤60s）；阈值 5000ms → **一条正常播放的动作会让 `/status.worker_alive` 连续返回 false**，与 P0-2「静默死亡可被观测」的语义相反 → 误告警 | 确定缺陷 | 运动循环内周期性刷心跳，或阈值 ≥ 最大动作时长 | `[代码确认]` |
| 18 | `host/adapters/workers.py:64-71` `_net_loop` | net 循环**无外层 try/except**（audio/tick 都有），`worker_heartbeat_ms` 只由 motion worker 刷新 → net 线程崩死后 `/status` 仍报 `worker_alive=True`、`worker_faults=0`，设备静默卡 THINKING。实测线程列表只剩 `[audio, tick]` | 确定缺陷（与 P0-2 同源纪律） | `_net_loop` 加外层兜底；心跳改为「每线程一份」或加 `net_alive`/`audio_alive`/`tick_alive` | `[实测]` |
| 19 | `host/adapters/ports.py:122-127` | `switch_active_role` 只拦 `PERFORMING`/`RECORDING`，放行 `THINKING`；换角色 bump 代数后 net 回包被 `STALE_SENSITIVE` 丢弃且**不为新代产生任何事件** → 驻留 THINKING 直到 tick 的 60s 兜底。实测 **60.5s** | 确定缺陷 | 换角色时若在 THINKING 则同步取消本轮（走合法迁移），或向总线投新代 `AI_FAIL` | `[实测]` + `[代码确认]` |
| 20 | `host/adapters/workers.py:145-164` | 无硬件档位 `RECORDING` **无上限超时**：`NullAudioBackend` 固定录 2.0s < 15s，`duration >= 15 - eps` 的自动收尾**永不触发** → 按一次对话键后走开，永久停在 RECORDING。实测 24s 仍 RECORDING | 确定缺陷 | 加 RECORDING 态的兜底超时；或让自动收尾判据不依赖后端录制时长 | `[实测]` |
| 21 | `host/adapters/ports.py:162-163` | `self.validate_action(action)` 返回值被丢弃 → 非法动作文件**不在入口 409 拒绝**，继续下发到 `MotionClient.play` 的 `validate()` 抛 `ValueError` → `_run_play` 置 `SAFETY_TRIP` → **直接进 FAULT_LOCKED**。「无效动作」表现为故障锁定而非明确拒绝 | 确定缺陷 | `res=validate_action(action); if not res["valid"]: raise ConflictError(res["errors"])` | `[代码确认]` |
| 22 | `host/adapters/workers.py:145-177` + `host/audio/sd_backend.py:66-106` | 单 audio 消费者被 `record(15)` / `sd.wait()` 占满，紧随其后的 `RECORD_STOP`/`STOP_AUDIO` 只能排队**不能打断** → 真机档位下「第二下按键结束录音」不成立（固定跑满 15s）。当前 `runtime.py:115` 硬编码 `NullAudioBackend`，真实路径未接线故不可见 | 确定缺陷（真机档位，本机不可复现） | 录音/播放支持 stop 打断（独立采集线程 + 事件，或后端提供可中断等待） | `[代码确认]` |
| 23 | `host/adapters/injection.py:42-54` | `clear_fault` 硬编码 `{"mcu_ready": True, "role_stable": True}`，从不复核 `sim.docked()/healthy()` → **公仔仍被拔出时点「解除故障」也会回到 IDLE**（假可用态），下一步运动立刻再跳闸 | 确定缺陷 | 解除前核对到位信号，不满足则返回 `{"cleared": False, "reason": ...}` 且状态不动 | `[代码确认]` |

---

## 三、低危 / latent / 文档漂移

| # | 位置 | 现象 | 定性 | 修法 |
|---|---|---|---|---|
| 24 | `host/roles/rolepack.py:45-53` | `_check_name` 只在 `normalized[1] == ":"` 拒盘符，其它位置冒号放行（Windows 下 `role.json:evil` 是 NTFS ADS）。实测：仅含该成员的包通过校验，导入后 `role.json` 被创建为 **0 字节** → 「落盘后必须含 role.json」被空文件满足。当前 web 路径不可利用（`content.py` 有前置校验） | 确定缺陷（低危/纵深） | 拒绝含 `:`/`\` 的成员名，限定安全字符集；对落盘 `role.json` 加「非空且可解析」校验 |
| 25 | `host/adapters/workers.py:220` + `execution.py:164` | `worker_faults += 1` 在 net/audio/tick/host-worker 四线程并发，非原子 → 计数丢更新，`/status.worker_faults` 偏低（诊断量，非安全量） | 确定缺陷（低） | 用 `itertools.count` 或加锁 |
| 26 | `host/adapters/workers.py:41-53` | `start_workers` **非幂等**：每次调用新建 `_worker_stop` 并 append；调用两次 → 6 个线程且前 3 个绑定**旧** stop 事件（孤儿），`stop_workers` 只 set 新事件 → 旧线程永不退出。当前 `main.py:65` 只调一次 | 确定缺陷（潜在） | 开头加 `if self._domain_threads: return` |
| 27 | `ports.py:81-88` / `injection.py:38-40` / `bus.py:1-8` / `state_machine.py:177,220` | ① `_mcu_snapshot` 未持锁读 `self.sim`（sim 由 motion 线程经 SimLink 改写）→ 快照可能瞬时不一致；② `inject_undock` 在锁外写 `sim.dock.engaged`；③ `bus.py` 文档称「EventBus 唯一消费者 = core 线程」，实际有两个消费方（host-worker + HTTP 经 `drain()`），正确性靠 `_lock` 保证但**文档不变量描述不实**；④ `state_machine.history` 只增不裁（长跑内存缓慢增长） | 咨询/记账 | 修文档不变量；history 加环形上限 |
| 28 | `host/audio/recorder.py:69` | `duration = recorded.duration_s or _frames(pcm, channels) / recorded.sample_rate` → 当 `duration_s==0` 且 `sample_rate==0` 时 `ZeroDivisionError`。现役 backends 采样率恒 >0 → latent 不可达 | latent | `rate = recorded.sample_rate or 1` |
| 29 | `host/audio/null_backend.py:77-84` | `play()` 置 `_playing=True` 后**从不复位**（只有 stop/close 复位）→ `player.play()` 返回后 `backend.busy` 仍为 True，与 Protocol 注释不符；唯一消费者是测试且 `Recorder.record` 开录前会 `player.stop()` 清掉。注意 `test_audio_pipeline.py:65` **固化了这个 True** | latent（低） | play 结束即置 False（同步改测试断言） |
| 30 | `host/tools/weather.py:57-71` | `make_handler` 只 `except TimeoutError`；真实 `WeatherClient` 抛连接类异常会越过 `_run_tools`（只 catch `ToolValidationError`）上抛。当前 `StubWeatherClient` 只抛 TimeoutError → 不可达 | 待验证假设 | `except Exception` 转 `{"ok":False,...,"fabricated":False}` |
| 31 | `host/ai/tools.py:113-118` | `validate_args` 先过 `_type_ok` 再比 min/max，但 `type=str` + 带 `minimum/maximum` 时 `value < p.minimum` 抛 `TypeError`（非 `ToolValidationError`）。当前注册表无此组合 → 不可达 | latent | 类型与边界组合校验 |
| 32 | `docs/api/openapi.yaml:90,488-490` | `ActiveRolePatch.role_id` / `ActionPlayRequest.role_id` 只写 `minimum: 1` **缺 maximum**，而 `schemas.py:33,43` 实为 `ge=1, le=3`；同文件 `:513` 却写了 `maximum: 3`，**自相不一致** | 文档漂移 | 补 `maximum: 3` |
| 33 | `docs/api/openapi.yaml:573,587` vs `host/web/schemas.py:57` | `due_at_utc` 文档声明 `format: date-time`，代码只 `min_length=1` | 文档漂移 | 二者取一并对齐 |
| 34 | 启动路径 | 配置损坏抛**裸 traceback** 退出：`DB_PATH` 指向目录 → `sqlite3.OperationalError` exit1；`DB_PATH` 超长路径 → `OSError WinError123` exit1。`HOST_PORT` 被占用 rc=3 正常收尾 | 确定缺陷（低） | 启动期捕获转可读错误 |

---

## 四、已排除（正面结论 · 主动证伪记录）

**并发与生命周期（架构师）**：`_lock` / `_rec_lock` / `backend._lock` 三者**无环、无死锁**；`run_forever` 与 audio/tick 循环均有兜底（**net 例外，见 #18**）；正常路径**无资源泄漏**（`MotionClient.play` 每次 finally 关链路、`Database` 线程局部连接 + `close_all()`、`TcpBridge._handle` finally 关连接）；`AppContext.close()` 顺序正确且幂等。

**协议安全（后端）**：`extract_safely` 越界防护（`\`、`..`、绝对路径、盘符、符号链接）逐条试过**均被拦**；`zipfile` 解压受中央目录声明大小限制（声明校验有效）；`UNIQUE(idempotency_key)` 对多个 NULL 互不冲突是**期望行为**。

**AI / 音频 / Web（QA-2）**：`clip=None` 下游有守卫（`_play_clip` 有 `if clip is not None`）；`AudioClip.duration_s` 对 `data<44` / `sample_rate<=0` / `channels<=0` 均返回 0.0；`play_action` 的 `int(payload["role_id"])` 无 `KeyError`（有 action 守卫 + 模型层必填）；工具白名单二次校验**未见可绕过**（未知字段/缺必填/类型/边界/枚举/正则全覆盖）；`qwen_provider` 的 HTTPS 强制 + 流式下载硬限 + `allow_redirects=False` **合格**。

**前端（前端）**：`fault:null`/`action:null` 判空正确，无 undefined 解引用；`URL.revokeObjectURL` 与 `createObjectURL` 成对；`countdown-button` 定时器在 `reset()`+`disconnectedCallback` 清理；`openEvents.close()` 在 5 个页面 beforeunload 调用；未见成对泄漏的 addEventListener。规则红线复核：emoji **0**、字面色值 **0**、紫粉渐变 **0**、图标 37 用 / 0 缺失。

**实测绿线（QA-3-2）**：并发 play `1×200 + 29×409`（不叠加时间轴）；40/30 按键风暴 + play×stop×切角色三路交叉**无非法状态、无卡死**；AC-22 网络占住 8s 期间 status/按键 **<0.1ms**；20× 启停端口即刻空闲、无残留进程；**10.5 分钟长跑约 2800 请求 0×5xx**、RSS 75→70MB 无增长、句柄 373→299、线程稳态 6–8（新增 3 个领域线程不泄漏）；SIGKILL×3 + 490 并发写中途硬杀 → `integrity_check=ok`、0 半写入；坏角色包 11 类全 400+信封（含真 DEFLATE 炸弹 1026×）；边界输入（超长/超大整数/负数/NaN/Inf/U+202E/深嵌套 5000）**除 #8 外均 422 + 信封**。

**主理人复现过程中的两次自我证伪**（记录在案，避免后来者重走）：
1. 我的 F1 子探针第一版去读 `CommandRouter` 队列，得到 `None`——实际 `Engine.apply()` 是**返回**命令列表、不写队列。是探针错，不是产品错。
2. 我的 D1 对照组第一版 `seq` 恒为 0，一度看不出问题——根因是 `start_workers()` 只起 net/audio/tick，**motion worker 由 `main.py` 单独启动**，我的装置缺了它，于是「卡住」看起来像复现其实是装置没跑起来。补齐后才得到可采信结论。

---

## 五、未实测（仅推断，不用推断填充实测空缺）

- **磁盘写满 / 文件权限拒绝 / 时钟回拨**：沙箱无法构造，**未实测**。
- **无 pyserial 时 `--simulate` 能否启动**：`runtime.py:27` 顶层 import `serial_link` 使 pyserial 成为启动硬依赖（含 SIM 档位），推断必失败，**未实测**。
- **`database.py:92` COMMIT 失败**：路径明确但未能稳定构造 → 标待验证。
- **真机档位录/放不可打断（#22）**：无硬件，纯静态推断。
- **CTRL_BREAK 优雅关停**：uvicorn 0.53 会重放信号（`server.py:348-349`）致 `main()` 的 finally 不执行、rc=3 —— 属框架设计，非 host 缺陷，数据仍 WAL 安全。
- **架构文档 §10.1 的 C1–C13**（四轴真实运动、舵机中位、急停电气断电、ADC 窗口、看门狗真实时延、音效同步 <100ms、温升、机械插拔、真实云 AI、配网/systemd/OTA、断电时钟、真实儿童内容安全、桩级审核误拦边界）：本阶段**全部无法验收**，不作为缺陷重复上报。

---

## 六、整改优先级建议

**第一批（阻断：核心交互 + 数据完整性 + 安全，建议立即）**

| 顺序 | 条目 | 理由 |
|---|---|---|
| 1 | #2 表演键卡死 + #20 RECORDING 无超时 | 两个物理按键的**核心交互**在本阶段就不可用；#2 已有单变量因果证据，修法明确 |
| 2 | #1 工具最终答复丢失 | 工具功能整体白做，且**被 967 项绿灯掩盖** |
| 3 | #7 幂等重放取错记录 | 静默数据不一致，客户端可用输入即可触发 |
| 4 | #5 + #6 角色包原子性与归一化 | 同时打到 AC-25（安全）与可用性（启动 brick） |
| 5 | #8 + #9 统一信封兜底 + 事务 translate | 合并成一个「异常翻译收口」整改项，一并消灭 3 个可达 500 |
| 6 | #3 + #4 EXIT 代数与 409 副作用 | 同一处 `Engine.apply` / `ports.play_action` 的逻辑错误 |

**第二批（中危）**：#10–#13 前端四条（合计约 6 行改动）、#17/#18 存活探针与 net 兜底、#19 THINKING 竞态、#21 `validate_action`、#23 `clear_fault` 硬件校验。

**第三批（低危/文档）**：#15 与 #16 应与仿真器文档、相应测试**一并修**（避免继续保留错误 oracle）；其余按表逐条处理。

**流程改进（比单条缺陷更重要）**：本轮 15 项阻断里，至少 #1、#2、#15、#12 四条的共同成因是**测试/自检在「自己造的世界」里全绿**——桩后端没有工具规则、端到端只测对话键、模拟器注释写反后测试跟着写、端点覆盖清单硬编码。建议把「自检必须证明覆盖」再推一层：**关键路径的端到端测试必须逐按键/逐端点枚举，且自检清单从契约文件读取而非硬编码**。

---

## 附录：复现脚本

主理人独立复现脚本（**均在仓库之外**，不污染交付树）：

| 脚本 | 覆盖条目 |
|---|---|
| `_audit_lead/repro.py` | #1 工具文本丢失、#14 safety 零宽/全角绕过 |
| `_audit_lead/repro_arch.py` | #3 EXIT 代数、#4 409 副作用 |
| `_audit_lead/repro_backend.py` | #15 ARM 尾随空白（msvcrt.sscanf 对照）、#16 FRAME 劈数、#7 幂等重放 |
| `_audit_lead/repro_qa3.py` | #2 表演键卡死（完整装配） |
| `_audit_lead/repro_d1c.py` | #2 单变量因果实验 |

各方原始探针与输出：`_audit_backend/`（含 `AUDIT_REPORT.md`）、`_audit_frontend/`、`_audit_qa2/`、`_audit_qa3/`（含 `REPORT.md`）。

**审计零足迹核验**（主理人独立执行）：
```
git status -s                              → 空
git rev-parse HEAD:content/roles/1/role.json → 04f2148933ba33360746989cc7008ca64f6198dc
worktree blob sha1（同文件）                 → 04f2148933ba33360746989cc7008ca64f6198dc   ← 与 HEAD 逐字节一致
content/roles/ 目录清单                      → 1 / 2 / 3（无 .bak / .incoming 残留）
```
