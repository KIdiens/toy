# P1 独立对抗性复验报告（提交 33cbbc6，基线 7ef6a6e）

只读复验，仓库零改动（`git status -s` 空）。所有探针在 `F:/AI智能底座玩具/_verify_p1/`（仓库之外）。
被测方与整改方为同一人，故本报告**不使用整改方的测试文件作为证据**（`tests/unit/test_review_*`、
`tests/integration/test_error_envelope.py` 一律不采信），全部用自写用例。

## 0. 结论速览

| 组 | 内容 | 结论 |
|---|---|---|
| 1 | engine EXIT 改用迁移前代数 | **成立**（穷举 32 条迁移全部符合；THINKING 的 NET_REQUEST 代数未被破） |
| 2 | state_machine.destination() | **成立**（不改变状态；但预判不传 context，与 apply 不一致——当前等价） |
| 3 | play_action 入口预判 + validate_action 返回值 | **成立**（SPEAKING/RECORDING 拒绝且零副作用；PERFORMING+replace_current 正常） |
| 4 | pump_once 放过无载荷 PLAY_ACTION | **成立**（表演键闭环通了；"踢出合法态"的假设被我证伪——新旧实现完全一致） |
| 5 | _enforce_recording_timeout | **成立但不完整** —— 新增缺陷 D2：`_recording_since_ms` 残留可把**新一轮**录音立刻收尾 |
| 6 | ai/router 最终答复 | **成立**（最终答复 = 工具回合后文本；无工具/拒工具路径回归正常） |
| 7 | 幂等重放返回同一条 | **成立但不完整** —— 新增缺陷 D1：`idempotency_key:""` 重放 → **500** |
| 8 | 角色包原子性/残留排除/归一化 | **成立但不完整**（3 处小缺口：换入失败的异常类型仍是裸 OSError → HTTP 500 而非 400） |
| 9 | database BEGIN/COMMIT translate | **成立**（写锁 → StorageError → HTTP 500/50001 信封；BEGIN 实跑 0.28s） |
| 10 | 编号范围 + due_at ValueError→422 | **成立**（3 条原可达 500 已全部 422+信封） |
| 11 | web/app 通用 Exception 兜底 | **成立**（404/405/422/409 未改码；500 响应体零外泄 且 日志有全量堆栈） |

**阻断级新问题：0 个。** 两个新缺陷 D1（中危）/D2（低-中），均在新增代码路径上，建议并入下一批。

## 1. 必跑回归面（C）

```
$ python -m pytest tests -o addopts="" -q -p no:cacheprovider --basetemp=_verify_p1/basetemp
996 passed in 30.12s
$ python -m ruff check .
All checks passed!
$ python -m sim --replay tests/fixtures/sessions/*.jsonl --diff
RESULT: PASS（15/15 条，逐行一致，含时间戳）
```
注：不带 `--basetemp` 时沙箱的批量删除闸门会拦 pytest 的 tmp 清理，进程以 exit=1 结束（仅清理阶段，
测试本身 996 全绿）。这不是被测代码的问题，但会误导肉眼判读，故记录。

黄金回放逐行结论：15/15 PASS（already_armed 4 行 / estop_during_motion 5 / frame_out_of_range 4 /
frame_seq_regress 4 / frame_unarmed 3 / interlock_dock_open 3 / interlock_estop 3 /
interlock_role_mismatch 3 / interlock_unstable 5 / line_overflow 3 / pwm_init_fail 3 /
swap_role_during_motion 5 / unknown_command 3 / unplug_during_motion 5 / watchdog_timeout 7）。
**诚实边界**：该链路只覆盖 `sim`，与本次改动的 `engine.apply` / ports / workers 无交集，
所以它对"核心命令通路"没有回归价值——真正的回归证据是下面第 [1][4] 条的自写用例与真 HTTP 闭环。

真进程真 HTTP（`python -m host.main --simulate`，自写探针 `p_c4_real_http.py`）：
```
按键对话 n=1×2 : RECORDING -> THINKING -> SPEAKING -> IDLE        （含 SPEAKING ✓，终态 IDLE ✓）
表演键   n=2   : PERFORMING -> IDLE                              （不卡死 ✓）
表演键连击 ×3  : IDLE（fault=None，worker_faults 未增长）           ✓
录音兜底       : 15.8s 后离开 RECORDING，日志 "RECORDING 驻留超时 15s，自动结束本轮录音" ✓
进程已终止，tasklist 无 host.main 残留（仅剩 2 个 WorkBuddy 的 xhs_mcp_server，非泄漏）。
```

## 2. 逐条报告（团队负责人编号）

### [A-1] host/core/engine.py:132-152 —— EXIT 命令代数
结论：**修复成立**
自写用例：`p_a1_generation_matrix.py`（穷举 TRANSITIONS + WILDCARD，共 32 条迁移）
```
ANOMALIES: 无
RECORDING --PERFORM_PRESS--> IDLE : EXIT ['record_stop'] g[5] (pre=5, post=6)   ← 修复点
RECORDING --REC_CANCEL--> IDLE    : EXIT ['record_stop'] g[5] (pre=5, post=6)   ← 修复点
THINKING 的子迁移 NET_REQUEST:  RECORDING--DIALOG_PRESS-->THINKING g[5] (pre=5,post=5)
                                RECORDING--REC_STOP-->THINKING     g[5] (pre=5,post=5)
                                THINKING --VOLUME_PRESS-->THINKING g[5] (pre=5,post=5)
```
理由：我按"命令在列表中的位置"（EXIT 段 / ENTER 段 / 尾部 NOTIFY）分类，避免 `stop_audio` 这类
同名命令的归属歧义，逐条核对：EXIT 段全部 = pre，ENTER/NOTIFY 段全部 = post。THINKING 只能由
RECORDING 进入且 dest≠IDLE，代数不变，所以 NET_REQUEST 携带的代数与 net worker 校验的
`self.state_machine.generation` 恒等——**"误丢请求卡 THINKING"的担心不成立**。
另：`apply()` 的第三个形参 `generation` 自始至终未被使用（改动前也未使用），属既有死参，非本次引入。

### [A-2] host/core/state_machine.py:195-207 —— destination()
结论：**修复成立（附一条 latent 提醒）**
自写用例：`p_a3_play_action_matrix.py`（前置态 × replace_current 全矩阵，比对调用前后状态）
```
SPEAKING      False/True -> ConflictError(当前状态 SPEAKING 不接受播放指令)  后态 SPEAKING（未被改动）
RECORDING     False/True -> ConflictError(当前状态 RECORDING 不接受播放指令) 后态 RECORDING（未被改动）
```
理由：拒绝时 `state_machine.state` 与 `motion_busy` 都保持原样，`work` 队列无新增命令（残留 2 条来自
`build_context` 的 boot 序列，非本次调用产生）。`destination()` 内部只调 `can_fire`，不写状态，符合声明。
提醒：`destination(trigger)` 调 `can_fire(trigger)` **不传 context**，而稍后的 `engine.apply` 传 payload。
当前 (state, PERFORM_PRESS) 没有 guard，两者等价；一旦以后给 PERFORM_PRESS 加 guard，预判会与实判分叉。
建议要么 `destination(trigger, context=None)` 透传，要么在 docstring 里写明限制。

### [A-3] host/adapters/ports.py:154-190 —— 入口预判 + 用上 validate_action
结论：**修复成立**（未发现合法调用被误判 409）
自写用例：`p_a3_play_action_matrix.py`
```
IDLE     False/True -> OK scheduled=True   后态 PERFORMING
THINKING False/True -> OK scheduled=True   后态 PERFORMING
SPEAKING / RECORDING / FAULT_LOCKED -> ConflictError，后态不变、motion_busy 不变
PERFORMING False -> ConflictError(已有动作在播放…可用 replace_current)
PERFORMING True  -> OK scheduled=True      后态 PERFORMING（replace 路径正常，未被新预判挡住）
非法动作文件 -> ConflictError(动作文件非法: [...])，state=IDLE，fault=None（不再落 FAULT_LOCKED）
```
理由：你特别点名的两个 `replace_current=True` 用例都过了。SPEAKING 的 409 **不是新引入的误判**——
迁移表里 `(SPEAKING, PERFORM_PRESS) -> IDLE`，从来就不允许在说话时起动作；改动前也是 409，只是
"先打断说话再抛 409"，现在副作用被去掉。这是本次最有价值的一处修复。
次要观察：预判被放在 `action_path` 检查**之前**，于是"说话中请求一个不存在的动作"现在报的是
状态错而不是文件错（错误信息优先级变化，无功能影响）。

### [A-4] host/adapters/execution.py:64-81 —— 无载荷 PLAY_ACTION
结论：**修复成立；你提的"把设备从合法态踢出"假设被我自己证伪**
自写用例：`p_extra_findings.py` 附加-1（把 7ef6a6e 的 pump_once 逐字照抄成 `pump_baseline()`，
在同一装置上做单变量对照，前置 state=PERFORMING）
```
motion_busy 载荷       实现     终态         busy   事件           motion.play
True        无 action  基线     PERFORMING   False  perform_done   0
True        无 action  整改后   PERFORMING   False  perform_done   0   ← 与基线逐项相同
True        有 action  基线     PERFORMING   False  perform_done   1
True        有 action  整改后   PERFORMING   False  perform_done   1
False       无 action  基线     PERFORMING   False  -              0   ← 基线丢弃
False       无 action  整改后   PERFORMING   False  perform_done   0   ← 整改后放行（修复意图）
```
理由：`_run_play` 在基线里**本来就有** `if not action: self._finish_without_motion()`，所以
"motion_busy=True + 无载荷"在改动前后**行为完全一致**，不是新引入的路径。真正被改变的是
"motion_busy=False + 无载荷"：基线静默丢弃（这正是表演键卡死的原因），整改后收尾回 IDLE。
并且"真实动作正在播放时收到无载荷 PLAY_ACTION"在生产里不可达：唯一能产出无载荷 PLAY_ACTION 的
生产者只有物理按键（`press_button(n)` 的 payload 是 `{"n":n}`）；motion worker 只有一个，长动作会把
线程占在 `motion.play()` 里，后到的命令永远排在真实动作命令之后，FIFO（`queue.Queue`）保证先消费真实动作。
表演键三连击自写用例（`p_b2_b3_perform_and_cancel.py`）：3 次/5 次连击均回到 IDLE，无
PERFORMING→PERFORMING 非法序列，无 worker 故障。

### [A-5] host/adapters/workers.py:190-215 —— RECORDING 上限兜底
结论：**修复成立，但引入新缺陷 D2（见下），且该组评定为"不完整"**
自写用例：`p_b4_recording_limit.py`（VirtualClock + 按 500ms tick 网格建模）
```
[1] 未到上限(2000ms/3000ms) 产生事件: []  | since = 0        → 不会误收尾 ✓
[2] 到上限(t=3000) 事件: [('rec_stop', 1)] | 当前 gen = 1 | since = None   → 恰好一次、代数=当前 ✓
[2b] 紧随其后再调(t=3100)：事件: [] | since = 3100            → 不连环触发 ✓
真线程档位（上限压到 0.6s）：1.34s 离开 RECORDING，since=None，历史 RECORDING--REC_STOP-->THINKING ✓
真进程档位（上限 15s）：15.8s 离开 RECORDING（日志证实）✓
真机语义自洽性：DEFAULT_RECORD_LIMIT_S == MAX_RECORD_SECONDS == 15.0，_record_limit_s() 与
自动收尾判据同源 ✓（simulate=False 档位只是取 recorder.policy.max_seconds，无分叉）
```
双重 REC_STOP 不会卡状态机：`_record_once` 的自动收尾与 tick 兜底都投 REC_STOP，先到者完成
RECORDING→THINKING，后到者在 THINKING 无对应迁移、被 `can_fire` 拒绝（`apply` 返回 []），
`rejections` 计数增加但状态不变。**无缺陷**。

#### D2（新缺陷，低-中）host/adapters/workers.py:200-206
现象：`_recording_since_ms` 只在"tick 恰好采样到非 RECORDING 态"时才清零。若用户取消录音后
**在同一个 500ms tick 空档内**再次开录，tick 从未观测到 IDLE，上一轮的起点被沿用 → 新一轮录音
在下一个 tick 被**立刻**收尾。
自写用例（`p_b4_recording_limit.py` [3]，严格按 tick 网格建模，非白盒臆造）：
```
tick 采样序列(时刻,态,since): [(13600,'RECORDING',13100), (14100,...13100), ...
                              (15600,'RECORDING',13100)]     ← 全程未观测到非 RECORDING
在 15600→16100 空档：取消 + 立刻重按
[3] 新一轮仅录了 ~100ms（上一轮已录 2900ms），事件: ['rec_stop'] | since = None
```
后果：新一轮录音被截断到 ≤0.5s（触发条件：上一轮已录到接近上限 ~14.5–15s，且取消+重按落在同一
tick 空档内），ASR 拿到近乎空白片段，用户"重按一次重新说"得到的是"没听清"。
修法建议：把计时器与会话绑定——`_recording_since_ms` 记为 `(since_ms, _rec_started_gen)`，代数
不符即清零；或在进入 RECORDING 的路径（audio worker 消费 RECORD_START 时）主动清零。

### [A-6] host/ai/router.py:88-97 / 111-139 —— 工具最终答复
结论：**修复成立**
自写用例：`p_b1_router_tool_text.py`（自建 StubLlm 规则 + 真实 ToolRegistry，不跑整改方测试）
```
A 单轮工具成功: reply_text='好的，我知道了。' blocked=False tool_calls=('get_weather',) llm.calls=2
   [旧行为对照] 第一轮 text = '' | review_output('') -> ok=False reason='空输出'
B 无工具调用   : reply_text='北京今天晴，24 度。' ✓ 未回归
C 工具参数非法 : reply_text='我现在查不到这个，不能乱猜。…' blocked=True reason='工具参数非法，本地拒绝'
D 多轮上限     : used=('get_weather',) llm.calls=2，未死循环
```
理由：死赋值 `reply = current` 已删除，返回三元组，`respond_text` 用 `final.text`。对照实验证明修复前
第一轮 text 为空且被输出审核判"空输出"拦掉——与复查报告 #1 的现象完全一致，修复后场景 A 的答复
正是工具回合后的总结。B/C/D 三条回归路径均正常。

### [A-7] services.py:85-112 + reminders_repo.py:87-97 —— 幂等重放
结论：**修复成立，但不完整 → 新缺陷 D1（中危）**
自写用例：`p_a5_b5_ids_idempotency.py`、`p_a6_b7_envelope_http.py`
```
A(id=1,'A-该睡觉了') / B(id=2,'B-该喝水了')
重放 KEY-A -> id=1 text='A-该睡觉了'                       ← 修复成立（不再是 B）
重放 KEY-A 但改内容 -> id=1 text='A-该睡觉了'               ← 键优先，正确
无 key 两次创建 -> C.id=3 D.id=4 不同                      ← 无键语义正确
```
`list_by_profile(...)[-1]` 冒充已被彻底移除，改走 `get_by_idempotency_key`（走唯一索引）。

#### D1（新缺陷，中危）host/adapters/services.py:107
现象：`idempotency_key` 为**空串**时，`if idempotency_key:` 判为"无键"，但 DB 把 `""` 当真实唯一值：
第一次 201，第二次撞 UNIQUE → 走到 `raise HostError(...)` → **500**。
自写用例（HTTP 层，`p_a6_b7_envelope_http.py`）：
```
第 1 次（idempotency_key='')   201 信封=True code=0
第 2 次（同一空键重放）          500 信封=True code=50000
   {"code":50000,"data":null,"message":"提醒创建未生效：幂等键冲突但未找到既有记录"}
第 3 次（同一空键）              500
```
后果：schema 层 `idempotency_key: str|None = Field(default=None, max_length=64)` **允许空串**，
所以这是"用户可控输入 → 500"，正是 #8 想消灭的那类。可达性：自带前端永远生成 `rmd-<ts>-<rand>`
（`components/reminders.js:233`），故 UI 不可触发；任何直连 API 的调用方都会踩到。定性：契约违反，
非数据损坏（不会返回别人的记录）。
修法：`create_reminder` 里 `idempotency_key = payload.get("idempotency_key") or None`（或 schema 加
`min_length=1`），并把判空改成 `is not None`。

### [A-8] roles/rolepack.py + roles/registry.py —— 角色包
结论：**修复成立，3 处小缺口**
自写用例：`p_b6_rolepack.py`
```
B6-1 残留目录: load_all -> [(1,'1.0.0','小星'),(2,'1.0.0','小波'),(3,'1.0.0','小云')]
      单变量对照，旧逻辑(无过滤)在同一目录树 -> ConfigError: 角色 1 版本冲突: 9.9.9 != 1.0.0
B6-2 换入失败: 目标目录仍存在；role.json 逐字节不变 True；整棵目录逐字节不变 True
      残留 .incoming 存在 False | 残留 .bak 存在 False
B6-3 大小写冲突 Role.json/role.json -> RolePackError（归一化后冲突）
      反斜杠归一化冲突 ROLE.JSON     -> RolePackError
      冒号(ADS) role.json:evil      -> RolePackError: 拒绝含冒号的成员名
      盘符 C:/role.json             -> RolePackError: 拒绝绝对路径
      正常包                        -> 通过
B6-4 正常包仍能导入（回归）✓
```
缺口（均为低危，建议随手补）：
1. **换入失败的异常类型**：`import_rolepack` 在 `incoming.rename(target)` 抛 OSError 时能正确还原旧版，
   但随后 `raise`（裸重抛）出来的是 **PermissionError/OSError**，不是 `RolePackError`。而
   `content.py:63` 只 `except rolepack.RolePackError` → 这个分支会落到通用 Exception handler，
   返回 **500/50000**，而契约要的是 400/40001。自写用例实测：`抛出异常类型: PermissionError`。
   修法：还原成功后同样 `raise RolePackError(...) from exc`（与另一分支保持一致）。
2. `RolePackError` 之外："落盘后必须含 role.json"这条在**大小写不敏感文件系统**上会被单成员
   `Role.json` 满足——`import_rolepack` 在 Windows 上直接**导入成功**且落盘文件是 `Role.json`
   （实测 `落盘: ['Role.json']`），Linux 上则不一致。HTTP 路径安全（`read_member` 走 zip 内精确名，
   先报"角色包缺少 role.json"），故只是纵深缺口。
3. `validate_zip` 未拒绝"只有一个 `Role.json`"的包（只拒重复），同上，属同一处纵深。

### [A-9] host/storage/database.py:79-105 —— BEGIN/COMMIT translate
结论：**修复成立**
自写用例：`p_a_db_lock.py` / `p_a_db_lock_http.py`
```
写锁期间 -> StorageError: OperationalError: database is locked（被翻译为 StorageError 家族 True，0.28s）
释放后写入恢复: id = 1 | 连接未停在未知事务态: 1
HTTP: status 500 | content-type application/json | {"code":50001,"data":null,"message":"OperationalError: database is locked"}
```
理由：BEGIN 已进 try/translate，锁冲突不再是裸 `sqlite3.OperationalError`；COMMIT 失败路径带
`contextlib.suppress(sqlite3.Error)` 的 ROLLBACK，代码正确但**我同样无法稳定构造 COMMIT 失败**
（与报告一致的"待验证"），不判缺陷。
次要观察（既有、非本次）：`message` 里带 `OperationalError` 这个异常类名，与 #11 "响应不外泄类名"
的口径不一致；StorageError 这条 handler 本次未改，不属本次整改问题。

### [A-10] services.py:23-38, 85-112 —— 编号范围 + due_at
结论：**修复成立**
自写用例：`p_a5_b5_ids_idempotency.py`（22 个取值）+ `p_a6_b7_envelope_http.py`
```
2**63-1   -> ClarifyError: 提醒不存在（未误判，正常走查找）
2**63     -> ClarifyError: 提醒编号超出可表示范围（不再 OverflowError）
-2**63    -> ClarifyError: 提醒不存在        ;  -2**63-1 -> ClarifyError 超出范围
0 / -1    -> ClarifyError: 提醒不存在
'default' -> ClarifyError: 非法提醒编号（get_reminder 不吃别名；_resolve_profile 吃别名 -> id=1）
'1'/'+1'/' 12 ' -> 正常解析；'0x10'/'1.5'/''/None -> ClarifyError
'9'*19 / '9'*100 -> ClarifyError 超出范围（原 500 点）
_resolve_profile('99999999999999999999') -> ClarifyError（原 500 点）
HTTP: due_at_utc=not-a-date -> 422/42200 信封；profile_id 19 位 -> 422/42200；DELETE 20 位 -> 422/42200
```
未发现合法取值被误判。`True → int(True)=1`、`1.9 → int(1.9)=1` 属静默截断，但 HTTP 入口的编号都
是路径/查询字符串（`"1.9"` 会被拒），不可达，不判缺陷。

### [A-11] host/web/app.py:102-110 —— 通用 Exception 兜底
结论：**修复成立**
自写用例：`p_a6_b7_envelope_http.py`
```
GET /api/v1/nope                    404 信封=True code=40400
GET /nope                           404 信封=True code=40400
PUT /api/v1/status                  405 信封=True code=40500
POST /reminders 缺 request_id       422 信封=True code=42201
POST /dev/button n=9                422 信封=True code=42201
GET /api/v1/status（正常）          200 信封=True code=0
POST /actions/play（FAULT_LOCKED）  409 信封=True code=40900
GET /memories 注入 RuntimeError     500 信封=True code=50000
  响应体外泄项: 无        日志中带 exc_info 的记录数: 1 | 含 Traceback: True
```
理由：既有状态码全部未被吞掉或改码（Starlette 的 HTTPException 走内层 ExceptionMiddleware，
不受挂在 ServerErrorMiddleware 上的 Exception handler 影响）；未预期异常**同时**满足
"响应体不含 secret/RuntimeError/impl.py/Traceback/boom"与"日志含全量堆栈"两条。

## 3. 附带发现（既有行为，非本次引入 —— 未列入你给的"已知未修"清单，故单列）

`p_extra_findings.py` 附加-2/附加-3，根因相同：自环迁移也会走 `ENTER_COMMANDS`。
```
附加-2 THINKING 按音量键: 迁移 THINKING--VOLUME_PRESS-->THINKING，ASR/LLM 实际被调用 2 次
        （ENTER_COMMANDS[THINKING]=(NET_REQUEST,) 在自环上又发了一次请求 → 重复推理/重复计费）
附加-3 SPEAKING 按音量键: SPEAKING--VOLUME_PRESS-->SPEAKING 触发 PLAY_AUDIO（payload 无 clip）
        → _play_clip 直接回投 PLAY_DONE → SPEAKING->IDLE，**正在说的那句话被截断**
        与 state_machine.py:9 的注释"音量键是内部迁移（不改状态）"矛盾
```
两条都只影响"在对话/说话过程中按音量键"这一交互，非本次改动引入（`ENTER_COMMANDS` 逻辑与迁移表
本次均未变），但既然写到了同一段通路，建议与 #19/#21 一起进下一批。物理按键目前只能经
`POST /dev/button` 注入（无 MCU 按键生产者），所以危害面在 SIM 档位即可复现。

## 4. 总评

**真正成立（证据充分）**：#1 EXIT 代数、#2 destination、#3 409 去副作用 + 非法动作入口拒绝、
#4 表演键闭环、#6 工具最终答复、#9 事务 translate、#10 编号/时间校验、#11 统一信封兜底。
其中 #3 与 #1 是我认为最有价值的两处（一个消除了"拒绝却已改状态"，一个消除了真机档位下"多录 15 秒"）。

**成立但不完整（各带一个缺陷，需跟进）**：
- #5：兜底本身有效（真进程 15.8s 生效），但 `_recording_since_ms` 残留会把**新一轮**录音截断到 ≤0.5s（D2，低-中）。
- #7：幂等语义已正确，但空串键重放 → 500（D1，中危，用户可控输入）。
- #8：原子性与归一化都成立（旧版逐字节还原、残留排除、大小写/冒号拒绝），但换入失败抛裸 OSError，
  在 HTTP 层表现为 500 而非契约要求的 400（低危，与同函数另一分支不一致）。

**证据不足**：#9 的 COMMIT 失败分支（无法稳定构造，仅回读代码确认逻辑正确）——与复查报告结论一致，不算缺陷。

**新的阻断级问题：无。** D1 是我建议最优先跟进的（"用户可控输入 500"是 #8 的原目标，属同一类）；
D2 次之（窄时序窗口）；#8 的异常类型一行可修。
