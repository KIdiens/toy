# QA3 实测层复查报告（基线 commit 7ef6a6e）

verdict：**fail** —— 核心闭环/并发/资源/崩溃恢复/视觉门禁全绿，但存在
**1 个核心交互缺陷 + 3 类可达 500（无统一信封）+ 1 个导入原子性/启动 brick**。

探针与原始输出：`F:/AI智能底座玩具/_audit_qa3/`（仓库外），`out_*.txt` 为真实输出。
只读审计：真实进程 + 真实压力 + 真实异常注入。

---

## 缺陷

### [D1] P1 · 正确性缺陷 —— 表演键(button 2) 使设备永久卡在 PERFORMING
复现（真实进程）：
```
python -m host.main --simulate --port 8765 --db data/qa3.sqlite3
curl --noproxy '*' -X POST localhost:8765/api/v1/dev/button -H 'Content-Type: application/json' -d '{"request_id":"p","n":2}'
curl --noproxy '*' localhost:8765/api/v1/status
```
实测输出（out_03.txt）：
```
press2 -> 200 {'code': 0, 'data': {'state': 'PERFORMING'}}
  +0.3s ('PERFORMING',...)  ... +3.0s ('PERFORMING',...)
FINAL: PERFORMING        ← 永久停
after 2nd press2: IDLE   ← 再按一次才自救
```
根因：按键路径 `press_button → engine.apply(PERFORM_PRESS)` 下发的 PLAY_ACTION **不置**
`motion_busy`（该置位只在 HTTP `play_action`：ports.py:170）；worker `execution.py:66`
见 `motion_busy=False` 就把该命令当「陈旧命令」丢弃 → 无 PERFORM_DONE → 状态机无出口。
仅 `tests/integration/test_p0_closure.py` 覆盖对话键(n=1)，表演键(n=2)无端到端测试。
后果：物理表演键完全失效 + 设备卡「表演中」，需再按一次自救。
定性：确定缺陷（正确性 / 需求未满足）。
修法：按键路径也置 `motion_busy`，或让 worker 对「无 action 载荷」的 PLAY_ACTION 直接走
`_finish_without_motion`（而非按 motion_busy 丢弃）；补 n=2 端到端测试。

### [D2] P1 · 契约安全破坏 —— 三个可达 500 且**不返回统一信封**
复现（真实进程）：
```
curl --noproxy '*' -w '%{http_code}\n' -X POST localhost:8765/api/v1/reminders -H 'Content-Type: application/json' \
  -d '{"request_id":"x","profile_id":"default","text":"喝水","due_at_utc":"not-a-date"}'
curl --noproxy '*' -w '%{http_code}\n' 'localhost:8765/api/v1/memories?profile_id=99999999999999999999'
curl --noproxy '*' -w '%{http_code}\n' -X DELETE 'localhost:8765/api/v1/reminders/99999999999999999999'
```
实测（out_09b.txt；body 均为 `Internal Server Error`，非 `{code,data,message}`）：
```
POST reminders bad due        status=500
GET  memories profile_id=9*19 status=500     （9*18位→200；9*5000位→422）
DELETE reminders id=9*30      status=500
```
服务端根因（ASGI traceback）：
```
create_reminder → tools/reminders.py:29 _normalize_due   ValueError: 非法时间: not-a-date
list_memories   → memories_repo.py:89                    OverflowError: Python int too large to convert to SQLite INTEGER
cancel_reminder → reminders_repo.py:120                  OverflowError: Python int too large to convert to SQLite INTEGER
```
后果：用户可控输入 → 500（应 422）且响应体不是统一信封（SPEC §5 强制）。
定性：确定缺陷（契约安全 + 正确性）。
修法：`_resolve_profile`/`cancel_reminder`/`get_reminder` 对 int 加范围界（越界→ClarifyError 422）；
`ReminderCreateRequest.due_at_utc` 加格式约束或由 `create_reminder` 捕获 ValueError 转 422；补兜底 handler。

### [D3] P1 · 契约安全破坏 —— DB 被锁时写请求 500 且**无信封**
复现：外部连接对同一 db `BEGIN EXCLUSIVE` 期间 `POST /api/v1/reminders`。
实测（out_13.txt）：
```
锁中写入 -> status=500 耗时5.5s body=Internal Server Error
锁中读取 -> status=200（WAL 允许读）
释放后写入 -> status=201
服务端: sqlite3.OperationalError: database is locked
```
根因：`storage/database.py:82` 的 `BEGIN IMMEDIATE` 在 `try:` **之外** → `OperationalError`
未经 `translate()` 转 StorageError，逃逸出 app 异常处理器（与上一轮同源，仍存在）。
定性：确定缺陷（契约安全）。
修法：把 BEGIN IMMEDIATE 移入 try 并 translate；或 app 层加 `sqlite3.Error→50001` 兜底。

### [D4] P1 · 数据完整性/需求未满足 —— 角色包导入非原子 + crash 残留 brick
实测（out_10b.txt，隔离 root）：
```
[A] 压缩炸弹(真 DEFLATE 1026x) 已拒绝 ✓
[B] 第二次 rename 失败 → OSError；导入后 target 仍存在? False
    ❌ 旧版本目录消失（违反 AC-25）；残留 .bak? True
[C] crash 残留 2.bak → load_all 抛 ConfigError 角色 2 版本冲突: 1.0.0 != 7.7.7
```
根因：`roles/rolepack.py:132-135` 先 rename 到 backup 再 rename incoming，`except` 只 rmtree(incoming)
不还原 backup；`registry.load_all:38` 的 `iterdir()` 不排除 `*.bak`/`*.incoming`。
后果：导入失败或导入中崩溃 → 旧角色丢失；下次启动 load_all 抛 ConfigError → **服务无法启动**。
定性：确定缺陷（数据完整性 AC-25）。
修法：except 还原 backup；load_all 跳过 `*.bak`/`*.incoming`；改用单次原子交换；补崩溃注入测试。

### [D5] P2 · 可观测性 —— worker_alive 不覆盖领域线程；_net_loop 可静默死亡
实测（out_14.txt）：
```
Exception in thread host-net: workers.py:71 → RuntimeError
仍在运行的 host-* 线程: ['host-audio','host-tick']   ← net 已死
worker_alive: True   worker_faults: 0   state: THINKING（卡住）
```
根因：`worker_heartbeat_ms` 只在 `run_forever`（motion worker）刷新；`_net_loop` 无外层 try/except（audio/tick 有）。
后果：net 线程静默死亡后 /status 仍报健康，对话失效且无告警（与 P0-2「禁止静默死亡」矛盾）。
定性：确定缺陷（可观测性；触发窗口窄）。
修法：三领域循环统一外层 try/except + 各自心跳；worker_alive 聚合全部领域线程。

### [D6] P2 · 边界/实际使用风险 —— 无硬件档位 RECORDING 无上限超时
实测（out_12.txt）：
```
after press1: RECORDING → 24s 内一直停在 RECORDING（15s 自动收尾未触发）
```
根因：NullAudioBackend 固定录 2.0s < 15s，`_record_once` 的「录满才自动结束」永不触发；RECORDING 无兜底超时。
后果：出厂「无硬件档位」下按一次对话键后退开 → 永久停 RECORDING。
定性：确定缺陷（边界/实际使用风险）。
修法：为 RECORDING 加上限超时；或无硬件档位显式提示「再按一次结束」。

### [D7] P2 · 边界 —— 换角色期间 THINKING 最长静默 60.5s
实测（out_12.txt）：
```
进入: THINKING gen=1；换角色后 gen=2 state=THINKING；THINKING 驻留实测: 60.5s -> IDLE
```
根因：`switch_active_role` 允许在 THINKING 切换，gen+1 使 net 回包作废，靠 tick 60s 兜底。
定性：确定缺陷（边界）。修法：THINKING 时换角色立即取消本轮（如 DIALOG_PRESS 取消）。

### [D8] P2 · 健壮性 —— 启动期配置损坏抛裸 traceback
实测（out_07.txt）：
```
DB_PATH→目录:      sqlite3.OperationalError: unable to open database file (exit 1)
DB_PATH→超长路径:  OSError [WinError 123] (exit 1)
HOST_PORT 被占用:  uvicorn bind error → rc=3，正常收尾
非simulate+非法MOTION_PORT: 启动成功；播放→FAULT_LOCKED(SAFETY_TRIP, 串口FileNotFound)，worker 存活
```
定性：确定缺陷（可运维性）。修法：Database 初始化/connect 包 ConfigError 并给可执行提示。

---

## 绿线（实测通过）
- 并发 /actions/play：1×200 + 29×409（时间轴不叠加）✓
- 并发按键 40/30 风暴、play×stop×切角色 三路交叉：无非法状态、无卡死、generation 合理 ✓
- AC-22：net worker 占住 8s 期间 status/press 延迟 <0.1ms、取消即时 ✓
- 20× 启停：端口立即空闲、无残留进程 ✓
- 10.5min 长跑：~2800 请求，0×5xx，RSS 75→70MB 无增长，句柄 373→299，线程稳态 6–8 ✓
- 崩溃恢复：SIGKILL(录音/思考/播放)×3 + 490 并发写中途硬杀 → integrity_check=ok、0 半写入、重启可用 ✓
- 坏角色包：非zip/空/缺role.json/坏JSON/坏schema/traversal/绝对路径/符号链接/.py/压缩炸弹(1026x)/超大 → 全 400+信封 ✓
- 边界输入：超长/超大整数/负数/NaN/Infinity/空/null/类型错配/深嵌套(5000)/U+202E → 除 D2 外均 422+信封 ✓
- 视觉 P0 门禁：emoji=0 / 紫粉渐变=0 / AI 模板味=0 ✓

## 未实测（仅推断，注明原因）
- 磁盘写满 / 文件权限拒绝 / 时钟回拨：沙箱无法真实构造，**未实测**。
- SIGTERM：Windows 无原生 SIGTERM（terminate=kill 已测）；CTRL_BREAK 走 uvicorn 优雅关停。
  注：uvicorn 0.53 信号关停后会**重放信号**（server.py:348-349）致 `main()` finally 不执行、rc=3 ——
  框架设计，非 host 缺陷；数据仍 WAL 安全（integrity ok）。
- 缺 pyserial：确认 `--simulate` 在 import 期失败（runtime.py:28 顶层 import serial_link）—— 已知项，仅确认。
- D4 二次 rename 失败：monkeypatch 构造（非真实磁盘故障）。

## 只读合规声明
探针全部在仓库外 `F:/AI智能底座玩具/_audit_qa3/`。
⚠️ 诚实披露：一次导入探针里我构造的「压缩炸弹」实为 ZIP_STORED（未压缩），校验通过后被成功导入，
改写了 `content/roles/1`；已 `git checkout` 复原，并因 `core.autocrlf=true` 发现 checkout 写入 CRLF，
再以 `-c core.autocrlf=false` 精确恢复到 HEAD blob（role.json sha256=5276e9771a807203，`git status` 干净），
角色 2/3 未受影响。后续 rolepack 探针已改为隔离 root。
