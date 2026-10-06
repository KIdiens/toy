# 第 5 组 · worker_faults 竞态 + 启动期异常裸奔

基线 `1e0f285`。两个缺陷的结论**不一样**：一个判定不可触发、一个是真实缺陷并已修。

## 探针

| 探针 | 用途 |
|---|---|
| `probe_worker_faults_race.py` | 判定 `worker_faults += 1` 会不会丢计数（含阳性对照） |
| `probe_main_startup.py` | 真子进程复现 5 种启动失败，抓退出码与末行 |

```bash
cd F:/AI智能底座玩具/toy
python docs/audit/2026-10-07-batches/probes/g5/probe_worker_faults_race.py
python docs/audit/2026-10-07-batches/probes/g5/probe_main_startup.py
```

---

## 缺陷 1：`worker_faults` 非原子自增 → **实测判定不可触发，不加锁**

原审计（`docs/CODE-REVIEW-2026-09-25.md` 第 25 条）建议「用 `itertools.count` 或加锁」。
本组按要求先实测，结论是**当前形态下丢计数不会发生**，因此**不改代码逻辑**，
只补注释 + 前提护栏。

### 实测数据

```
B. 阳性对照（STORE_ATTR 走 Python 层 __setattr__）
   期望=80000  实际=50683  丢=29317        ← 探针确实能测出丢计数
C. 实验组（真实形态：普通 dataclass 实例属性）
   switchinterval=0.005  期望=200000 实际=200000 丢=0
   switchinterval=0.001  期望=200000 实际=200000 丢=0
   switchinterval=1e-06  期望=200000 实际=200000 丢=0
   switchinterval=1e-09  期望=200000 实际=200000 丢=0
```

**关键**：B 段阳性对照能丢 30%~40%，C 段真实形态在任何 `switchinterval`
（哪怕 1e-9）都是 0 丢。所以「不丢」是事实，不是探针无能 —— 这是本条结论成立的前提。

### 为什么不丢（机制）

`self.n += 1` 的字节码是 `LOAD_ATTR → BINARY_OP → STORE_ATTR`：中间**既无CALL
也无向后跳转**，而 CPython 只在这两处检查线程切换。`STORE_ATTR` 走的是 C 层
slot wrapper（MRO 里只有 `object.__setattr__`，无 Python 层 `__setattr__`），
整个读-改-写之间不存在可切换点。

在**真实 `AppContext`** 上另测一轮（`switchinterval=1e-9`，4 线程 × 5 万）：
`期望=200000 实际=200000 丢=0`。

### 影响面（已核实，与任务描述一致）

唯一消费方是 `ports.py` 的 `int(self.worker_faults)`，只做 `/status` 诊断显示；
`tests/unit/test_worker_faults_counter_atomicity.py` 里有一条测试专门盯住这个前提
（新增任何读 `worker_faults` 的逻辑分支都会让它转红）。

### 三个选项的成本对比

| 方案 | 改动量 | 运行时成本 | 语义 | 评价 |
|---|---|---|---|---|
| **保持现状 + 注释 + 护栏**（已选） | 注释 19 行 + 1 个测试文件 | 零 | 依赖「无 Python 层 `__setattr__`」这一前提 | 前提被破坏时护栏立刻报警 |
| 模块级 `Lock` | 需新增锁字段 + 两处 `with`；读写必须整体在锁内 | 每次故障一次加解锁（故障是低频路径，可忽略） | 前提无关，恒安全 | 对一个纯诊断量引入共享锁，收益不抵复杂度 |
| `itertools.count` | `next(c)` 赋回字段 | 略快（省一次加法） | 前提无关 | 但 `worker_faults` 会变成「从计数器派生」，与 dataclass 字段语义不符，`int(self.worker_faults)` 的读法也要跟着改 |

选保持现状，因为**丢计数的前提本身不成立**，加锁是在为一个不可达的状态付复杂度成本。
前提一旦被破坏（free-threading 构建、加 `__setattr__`、改成 `property`），
护栏测试会先转红并指出该换方案。

---

## 缺陷 2：`main()` 启动期异常裸奔 → **真实缺陷，已修**

### 修复前实测（四种故障，真实子进程输出）

| 场景 | 触发 | 退出码 | 末行 |
|---|---|---|---|
| A | `HOST_PORT=not_a_port` | 1 | `ValueError: invalid literal for int() with base 10: 'not_a_port'` |
| B | `DB_PATH` 指向目录 | 1 | `sqlite3.OperationalError: unable to open database file` |
| C | `MOTION_BAUD=fast` | 1 | `ValueError: invalid literal for int() with base 10: 'fast'` |
| D | `LOG_LEVEL=VERBOSE` | 1 | `ValueError: Unknown level: 'VERBOSE'` |
| E | `defaults.toml` 语法错 | 1 | `tomllib.TOMLDecodeError: Expected ']' ...` |

五种都是**裸英文 traceback、退出码全是 1**：既没有中文说明，也分不出「配置错了」
还是「环境坏了」。D 还额外证明 `logging.basicConfig` 自身也是未捕获点。

### 修法

- `logging.basicConfig` 提到 `load_settings` **之前**（配置错时日志已就绪），
  并把二次 `basicConfig(force=True)` 也放进同一个 `try`。
  **实测踩到的坑**：root logger 已有 handler 时 `basicConfig(force=False)` 是 no-op、
  连 level 都不校验，非法 `LOG_LEVEL` 会推迟到 `force=True` 那句才抛 —— 放 try 外又裸奔。
- 三处启动调用各自 `try`，走 `_report_startup_failure`：
  `log.exception` 留全量 traceback 进日志 + 终端 `print` 一行中文 + 返回非 0。
- 退出码：`0` 正常 / `2` 配置错 / `3` 运行时环境错，**绝不 return 0**。
- `start_workers` 失败时先`context.close()` 再上报，避免半启动状态泄漏线程。

### 修复后实测

| 场景 | 退出码 | 末行 |
|---|---|---|
| A / C | **2** | `启动失败[配置加载]：配置项的取值不是合法数字（...）。请检查 HOST_PORT / MOTION_BAUD 等环境变量，或 host/config/defaults.toml 里的对应值。` |
| B | **3** | `启动失败[运行时装配]：运行时装配失败（OperationalError: unable to open database file）。请检查 DB_PATH 指向的目录是否可写、串口是否被占用、依赖是否安装。` |
| D | **2** | `启动失败[配置加载]：LOG_LEVEL 取值非法（Unknown level: 'VERBOSE'）。请改成 DEBUG / INFO / WARNING / ERROR / CRITICAL 之一。` |
| E | **2** | `启动失败[配置加载]：配置文件 host/config/defaults.toml 语法错误（...）。请检查 TOML 方括号是否成对、是否有中文标点。` |

### 误伤面分析（本组最需要回答的问题）

**会不会把「本该 crash 让人看到 traceback」的编程错误变成一行安静的中文提示？**

不会。分界线是**异常类型白名单**，不是「捕获一切再按阶段归类」：

- 命中 `_CONFIG_ERRORS`（ValueError / TOMLDecodeError / KeyError /
  FileNotFoundError / PermissionError / OSError）或 `_RUNTIME_HINTS`
  （sqlite3.Error / OSError / ImportError / RuntimeError）
  → 判为数据/环境问题，给中文摘要 + 对应退出码。
- **不命中**（`TypeError` / `AttributeError` / `NameError` / `IndexError` …）
  → 原样 `raise`，保留 Python 原生 traceback 与完整堆栈。
  用例：`test_programming_error_still_raises_with_native_traceback`、
  `test_programming_error_is_not_in_whitelist`。

即：编程错误只会因为多了一层调用栈而暴露得**更完整**，不会被安静掉。

**已知代价（诚实记录）**：白名单里的 `RuntimeError` 会让「编程 bug 恰好抛
RuntimeError」也走中文摘要这条路。之所以可接受：`log.exception` 仍留全量
traceback、退出码非 0、摘要带阶段名；而最常见的编程错误类型
（TypeError / AttributeError / NameError）**不在**白名单，仍原样抛出。
真正想区分时，正确做法是收窄异常类型，而不是放宽。

**加的严格检查会误伤哪类合法输入**：

- `logging.basicConfig` 提前到 `load_settings` 之前：合法输入无影响，
  但若宿主进程已自行配置过 root logger，这句是 no-op（不覆盖既有 handler），
  不改变既有日志行为。
- 退出码从「统一 1」变成「2/3」：**这是行为变更**。若有运维脚本按「非 0 即失败」
  判断，不受影响；若按「必须等于 1」判断则需调整。
- `_RUNTIME_HINTS` 含 `RuntimeError`：如上所述。

---

## 测试

| 文件 | 条数 | 覆盖 |
|---|---|---|
| `tests/unit/test_worker_faults_counter_atomicity.py` | 7 | 缺陷 1 的前提护栏 + 阳性对照 |
| `tests/unit/test_main_startup_failure.py` | 19 | 缺陷 2 的退出码 / 中文提示 / 误伤面 / 不起 uvicorn |

`main()` 的测试全程 `monkeypatch` 掉 `uvicorn.run`（哨兵在被调用时直接
`AssertionError`），**绝不起真服务**；另有一条 `test_uvicorn_never_started_for_failure_paths`
显式断言失败路径没碰过它。

### 判别力（什么改动会让它转红）

- 退出码改成 0 / 两个阶段用同一个码 / 删掉中文那行 / `basicConfig` 挪回
  `load_settings` 之后 / 把白名单放宽成 `except Exception` —— 逐条都有对应用例转红。
- 缺陷 1 的护栏测的是**前提**而非竞态（竞态不可触发，断言「不丢」会是恒绿假测试）。
  实测注入 Python 层 `__setattr__` 后，
  `test_no_python_level_setattr_on_context_class` 与
  `test_real_shape_loses_nothing_under_aggressive_switching` 立即转红。
- 每个文件都带**阳性对照**（缺陷 1 的 B 段对照、缺陷 2 的成功路径用例），
  避免「探针/用例本身没判别力」的假绿。

### 反向验证

回退 `host/main.py` 到基线后：`test_main_startup_failure.py` **17 failed, 2 passed**
（仍绿的 2 条正是断言「不应回归」的那两条）。恢复修复后 26 条全绿。