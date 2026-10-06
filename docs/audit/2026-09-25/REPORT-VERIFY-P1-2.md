# P1 独立对抗性复验 · 第二轮（复验提交 `62c603e`，父 `33cbbc6`）

复验人：`p1-verifier`（只读；未改仓库任何文件、未提交、未 checkout/reset）
工程根：`F:/AI智能底座玩具/toy/`　探针目录：`F:/AI智能底座玩具/_verify_p1/`
Python：`C:/Users/Administrator/.workbuddy/binaries/python/versions/3.13.12/python.exe`

## 必跑三项（本轮实跑输出）

| 项 | 命令 | 结果 |
|---|---|---|
| 全量测试 | `python -m pytest tests -o addopts="" -q` | `1001 passed in 31.76s` |
| 静态检查 | `python -m ruff check .` | `All checks passed!` |
| 黄金回放 | `python -m sim --replay tests/fixtures/sessions/*.jsonl --diff` | `RESULT: PASS` × **15**（全绿） |
| 真进程真 HTTP | `q2_c_real_http.py`（`python -m host.main --simulate`） | 三条链路全部符合预期（见 R2-2） |
| 工作区 | `git status -s` | 空（干净） |
| 进程残留 | psutil 扫描 `host.main`/探针 | 无残留（仅环境自带 `xhs_mcp_server`） |

---

## R2-1　自环迁移修复（`engine.apply` changed==False 只回 NOTIFY）

**结论：修复成立。** 无回归面。

自写用例：`python _verify_p1/q2_1_selfloop.py`

真实输出（节选）：
```
自环（changed==False）四态：
  IDLE        -> 命令种类 ['notify']  payload={'state': 'IDLE', ...} generation=post
  THINKING    -> ['notify']  SPEAKING -> ['notify']  PERFORMING -> ['notify']
非自环 32 条迁移：逐条与迁移表结构/代数一致（无差异）
所有进入 IDLE 的迁移都含 stop_audio + stop_motion
音量键分支：SELF_TEST/RECORDING/FAULT_LOCKED 不追加 VOLUME_STEP；四态各追加 1 条
VOLUME_STEP 只进 audio 队列（motion/net 队列均无）
```

理由（逐条回答你的三个疑点）：
1. **有无合法路径依赖自环的 EXIT/ENTER 命令？** 没有。四条自环分别是 `IDLE/THINKING/SPEAKING/PERFORMING → 自身`。逐条核对 `EXIT_COMMANDS`/`ENTER_COMMANDS`：
   - `IDLE` 无 EXIT 命令；`ENTER_COMMANDS[IDLE]=(STOP_AUDIO, STOP_MOTION)` —— 纯「停」命令，且**任何进入 IDLE 的迁移都已带上这两条**（上表实测），自环不再重复下发是**冗余消除**，非功能删除。
   - `THINKING` 无 EXIT 命令；`ENTER_COMMANDS[THINKING]=(NET_REQUEST,)` —— 自环丢 NET_REQUEST 恰好**消除**「按音量键触发重复 ASR/LLM 调用与计费」（正是一轮我报的附带发现）。
   - `SPEAKING` EXIT 为 `STOP_AUDIO`、ENTER 为 `PLAY_AUDIO` —— 自环若照旧下发，就是「停播 + 从头重播」截断回复；现在改为只进档，语义正确。
   - `PERFORMING` EXIT 为 `STOP_MOTION`、ENTER 为 `PLAY_ACTION` —— 同理由；你实测的「重发 play_action」正是此条。
2. **只有 EXIT/ENTER 会「推进状态」的合法流？** 没有：这四个状态的 ENTER 命令里不含任何产生后续事件的命令（REC_START/NET_REQUEST/PLAY_AUDIO/PLAY_ACTION 均不在自环组合内被依赖）。
3. **NOTIFY 是否照常发？** 是，且 payload 为 `{state: result.dest, trigger: result.trigger}`、`generation` 取迁移后——与「静默丢事件」在 UI 上是两回事，自环可见。

---

## R2-2　音量键接线端到端

**结论：功能成立（真进档、连按回原档、四态无非法态/无副作用、只进 audio 队列）。但**接线不完整**：只改 `Player` 不落库，导致 `GET /settings` 与实际播放音量分叉、重启丢失（见 R2-4）。**

自写用例：`q2_2_volume.py`、`q2_2b_volume_persist.py`、`q2_c_real_http.py`

真实输出：
```
=== 2A: IDLE 连按 3 次 ===
  档位序列: ['mid', 'high', 'low', 'mid']   回到原档: True | 音量系数: 0.6
  未处理命令告警: []   worker_faults: 0
=== 2B: 四态各按一次 ===
  IDLE/THINKING/SPEAKING/PERFORMING  按前=按后  档位 mid->high  状态未变=OK  告警=[]
=== 链路 3（真进程真 HTTP，n=3 连按 3 次）===
  三次状态快照: ['IDLE','IDLE','IDLE','IDLE']  状态始终 IDLE: True
  服务日志音量档位序列: ['high', 'low', 'mid']        ← 真进档，端到端打通
  fault: None | worker_alive: True | 日志 Traceback/ERROR: 0
```
理由：`VOLUME_STEP` 走 audio 队列 → `workers._handle_audio_command` 的 `VOLUME_STEP` 分支 → `Player.cycle_level()`，与 `Player.VOLUME_LEVELS/LEVEL_ORDER` 的 low↔mid↔high 环一致；motion worker 未记未处理告警（已并入 `AUDIO_KINDS`）。四态自环不再产生非法态。

---

## R2-3　`_recording_since_ms` 写竞态 —— **D2 未修彻底**

**结论：新缺陷/修复不完整。** 快路径（audio 线程空闲）已修；但「取消后重按落在同一个 tick 间隔内」时，新一轮录音仍会被旧起点收尾（可截断到 <0.5s）。B/C 两段都能复现。

文件:行：`host/adapters/workers.py:154`（起点在 `_record_once` 写）与 `host/adapters/workers.py:196-218`（`_enforce_recording_timeout` 仅在 **tick 采样到** 非 RECORDING 时才 `= None`）

自写用例：`python _verify_p1/q2_3_recording.py`

真实输出：
```
=== A. 快路径（audio 线程空闲）===
  重按后状态: RECORDING | since = 72214870
  新一轮 age=0.80s 后状态=RECORDING → 仍未被收尾（快路径已修）   ← A 通过

=== B. 阻塞 recorder（模拟真机 record(15s) 占住 audio 线程）===
  第 1 轮开始 level=1.0s block=1.2s  since=None
  重按后状态: RECORDING | audio 线程仍阻塞中(上一轮未返回) → since 仍是旧值: 72218627
  新一轮 age=1.60s 后状态=SPEAKING  !!! 新一轮被旧起点收尾（缺陷仍在）
  迁移历史: [... ('IDLE','DIALOG_PRESS','RECORDING'), ('RECORDING','REC_STOP','THINKING') ...]

=== C. 确定性模型（VirtualClock + tick 500ms 网格）===
  新一轮（age=0）立即产生事件: ['rec_stop'] → !!! 仍被旧起点收尾
```
现象与根因：修复把起点写进了 `_record_once`（audio 线程），但**清零点依赖 tick 去「采样到」IDLE**。当「RECORDING→IDLE 取消」与「IDLE→RECORDING 重按」落在**相邻两次 tick 之间**（间隔 ≤ `TICK_INTERVAL_S`=0.5s，实测 0.03s 即可），tick 从未观察到 IDLE，旧起点一路带进新一轮。此时若 audio 线程尚未跑到新一轮的 `_record_once`（真机 `recorder.record` 会阻塞整个录音时长；或被上一轮占住），下一拍 tick 就用旧起点算出「已超时」→ 立刻 `REC_STOP`。
后果：用户「取消后马上重按」的新一轮录音被**静默截断**（可能只有几百毫秒），且 `REC_STOP` 是不带 `auto` 的正常收尾，UI 上表现为「刚开口就说完了」。
修法（任选，推荐①）：
① 把「录音起点」与**本次录音尝试**绑定：状态机进入 RECORDING 的**计数**（如 `len([r for r in history if r.dest is RECORDING])`）作为 key，tick 只在 key 未变时计时；key 变了就 `since = now`。该计数由状态机线程同步维护，不受 audio 线程阻塞影响。
② 在**离开 RECORDING 的那条迁移被应用时**（状态机线程侧）同步 `_recording_since_ms = None`，而不是等 tick 采样；这需要状态机暴露一个变更回调（现有 `history.append` 处挂 hook 即可）。
③ 最小改动兜底：`_enforce_recording_timeout` 里若发现 `history` 末尾出现了新的 `dest==RECORDING`，则重置起点。

---

## R2-4　音量档位不落库 → `GET /settings` 分叉 + 重启丢失

**结论：新引入的不完整（中低危，用户可见）。** 非阻断。

文件:行：
- `host/adapters/workers.py:142-143`：`VOLUME_STEP` 只 `player.cycle_level()`，不写 settings。
- `host/adapters/services.py:132-146`：`patch_settings` 才同时写 DB + `player.set_level`。
- `host/adapters/runtime.py:116`：`Player(backend, clock=clock)` **未从 settings 注入 level**（默认 mid），boot 不 hydrate 持久化音量（此条为**既有**缺口）。

自写用例：`python _verify_p1/q2_2b_volume_persist.py`、`q2_c_real_http.py`

真实输出：
```
=== 1) 用音量键把档位从 mid 改到 high ===
  按一次后 player: high | settings: mid   → 分叉? True
=== 2) 重启（同一 DB）===
  重启后 player: mid | settings: mid      → 按键音量丢失: True
=== 3) 对照：走 API patch_settings 改音量 ===
  改后   player: high | settings: high
  重启后 player: mid  | settings: high    → 重启后仍分叉（既有缺口）
=== 真 HTTP 链路 3 ===
  GET /settings.volume_level: mid -> mid  | 是否联动? False   （但服务日志确已 high→low→mid）
```
后果：① 用户按音量键后 `GET /settings` 仍报旧档，家长界面**显示与实际播放音量不一致**；② 按键调的音量**重启即丢**；③ 该分叉在**不改任何 API** 的情况下仅凭按键即可产生（本轮新增接线带来的新可达路径）。
说明与权衡：`docs/architecture/00-architecture-spec.md:232` 规定「音量值的真源在 `host/audio/volume.py`」（该文件实际不存在，真源在 `player.py`），若按此解读，按键只改运行期档位是**设计意图**，那么真正缺的是「`GET /settings` 应读运行期真源」或「按键后回写持久化」。请按产品口径二选一，任选其一即可闭环：
- 方案 a：`VOLUME_STEP` 生效后回写 `settings["volume"]`（与 `patch_settings` 同源），并让 `runtime.py:116` 用持久化值构造 `Player`。
- 方案 b：保持「按键=临时档位」，但让 `GET /settings.volume_level` 从 `player.level` 回读，并在 SPEC 明确「按键档位不持久化」。

---

## R2-5　`validate_zip` 精确名要求 —— **引入误拒（回归）**

**结论：新缺陷（回归）。** 合法前缀包 `./role.json`、`.\role.json` 被拒，上一版可正常导入。

文件:行：`host/roles/rolepack.py:109`
```python
if ROLE_JSON not in {info.filename.replace("\\", "/") for info in files}:
```
该集合未归一化 `./` 前缀，于是成员名 `"./role.json"` 与 `"role.json"` 不相等 → 判「缺少 role.json」。

自写用例：`python _verify_p1/q2_4_rolepack_validate.py`（新旧版对照）、`q2_5_rolepack_http_idem.py`

真实输出（rolepack 层新旧对照）：
```
用例                     上一版(33cbbc6)   本版(62c603e)
A 正常包                 放行             放行              一致
B 根级+子目录同名         放行             放行              一致
C 只有目录条目 role.json/ 放行*            拒绝              *两版导入都失败 → 非回归
D role.json 仅在子目录    放行*            拒绝              *两版导入都失败 → 非回归
E 点斜杠前缀 ./role.json  放行→**导入成功** 拒绝(缺少 role.json)  ← 回归（误拒）
F 反斜杠+点 .\role.json   放行→**导入成功** 拒绝(缺少 role.json)  ← 回归（误拒）
G 大小写冲突              拒绝             拒绝              一致
I 无 role.json           放行*            拒绝              *两版导入都失败 → 非回归
```
HTTP 层（`q2_5` 5-4）：
```
POST /api/v1/content/import  (包内仅 "./role.json")
  HTTP: 400 {'code': 40001, 'message': '角色包缺少 role.json'}   ← 用户拿到「你的包没有 role.json」，但包里其实有
```
理由：zip 成员名 `./role.json` 是**合法且常见**（部分打包器/`zip -r .` 会带 `./` 前缀），并非攻击面。`_check_name`/`extract_safely` 都能正确处理它（`Path(dest)/"./role.json"` 正常解析为 `dest/role.json`），唯独第 109 行的集合比较没做同样的归一化。
修法：把该判定与 `_fold_name` 复用同一套归一化（并**顺带折叠 `.` 段**），例如：
```python
def _fold_name(name: str) -> str:
    norm = name.replace("\\", "/")
    parts = [p for p in norm.split("/") if p not in ("", ".")]
    return "/".join(parts).rstrip("/").casefold()
```
改用 `_fold_name(ROLE_JSON) in {_fold_name(i.filename) for i in files}`。注意此修法与 R2-6 是同一处根因，合并修即可。

---

## R2-6　「校验对象≠落盘对象」洞**未堵住**（`.` 段别名），且可升级为 500 + 旧版本被销毁

**结论：修复不彻底（该洞为**预存**，但本轮正是冲它去的加固，未生效）。** 另叠加一个「失败却未保留旧版本」的 AC-25 违约。

文件:行：`host/roles/rolepack.py:48-55`（`_fold_name` 用 `rstrip("/").casefold()`，**保留 `./` 前缀**）、`:66-68`（`_check_name` 仅在**穿越检查**时剔除 `"."`，该结果不用于归一化去重）、`:109`（精确名判定）、`:137-146`（`extract_safely` 按原始成员名落盘，`.` 段被文件系统折叠 → 后者覆盖前者）

自写用例：`python _verify_p1/q2_4b_rolepack_alias.py`、`q2_5b_alias_old_compare.py`、`q2_5_rolepack_http_idem.py`

真实输出：
```
=== 别名覆盖包：role.json(role_id=1) + ./role.json(role_id=2) ===
（rolepack 层，新旧对照）
  上一版33cbbc6 | validate_zip=放行 | 导入成功，落盘 role.json role_id=2   ← 预存洞
  本版62c603e  | validate_zip=放行 | 导入成功，落盘 role.json role_id=2   ← 未堵住
（HTTP 层）
  发起前 roles/1 的 role.json role_id: 1
  HTTP: 500 {'code': 50000, 'message': '角色 2 版本冲突: 9.9.9 != 1.0.0'}
  落盘 roles/1/role.json: {'role_id': 2, 'name': '被落盘2', 'config_version': '9.9.9'}
  → 校验对象 role_id=1，落盘对象 role_id=2 → 一致? False
  roles/1 逐字节是否被改动（应保持旧版本）: False     ← 旧版本已被销毁
  残留 1.incoming: False | 残留 1.bak: False
  事后 GET /api/v1/roles: 200 | 角色列表: [(2, '被落盘2', '9.9.9')] | 有 role_id=1? False
```
根因链：`role.json` 与 `./role.json` 归一化后**不相等**（`_fold_name` 不折叠 `.`）→ `validate_zip` 放行且「精确名命中」→ `read_member` 读到 `role.json`(role_id=1) 并据此算目标目录 `roles/1` → `extract_safely` 落盘时两个成员都解析到 `roles/1.incoming/role.json`，**后者覆盖前者** → 换入成功后 `load_all()` 发现 `role_id=2` 双来源（dir 1 为 9.9.9、dir 2 为 1.0.0）→ `ConfigError` 未捕获 → **500/50000**。
后果：一次性同时踩两条线 —— ① 校验被绕过（校验的是 A，落盘的是 B）；② AC-25「导入失败保留旧版本」被违反：**角色 1 从 `GET /roles` 列表消失**，旧内容被覆盖，且无 `.bak` 可恢复（`.bak` 在成功后已被 `rmtree`）。
修法：与 R2-5 同源 —— 统一「用于去重/精确名判定的归一化」为**折叠 `.` 段 + 转义分隔符 + casefold**（`_fold_name` 见上）。修好后该包会在 `validate_zip` 阶段被 `成员名归一化后冲突` 拦下，HTTP 回 400/40001，旧版本不受影响。（`role.json` + `Role.json` 的大小写冲突已被正确拦下，说明机制本身是对的，只是漏了 `.` 段。）

---

## R2-7　换入失败转 `RolePackError` → HTTP 400/40001 + 逐字节还原

**结论：修复成立。** 证据充分。

文件:行：`host/roles/rolepack.py:165-185`（换入失败 → 还原 → 统一抛 `RolePackError`）、`host/web/routers/content.py:62-63`（`except rolepack.RolePackError: return _reject(...)`）、`:30-39`（400/40001）

自写用例：`python _verify_p1/q2_5_rolepack_http_idem.py`

真实输出：
```
=== 5-1: 换入失败（rename 抛 OSError）===
  换入前 role.json sha256: 5276e977...2cce / role_id: 1
  HTTP: 400 {'code': 40001, 'message': '角色包换入失败（旧版本已还原）: [Errno 13] 模拟换入失败（目标被占用）'}
  目标目录仍在: True
  role.json 逐字节不变: True      整棵目录逐字节不变: True
  残留 1.incoming: False | 残留 1.bak: False      现 role_id: 1
=== 5-2: 对照组 · 正常包 ===   HTTP: 201 {'code':0,'data':{'role_id':1,'config_version':'2.0.0','files':2}}  落盘生效
=== 5-3: 非法包（缺 role.json）===  HTTP: 400 {'code':40001,'message':'角色包缺少 role.json'}
=== 5-6: 空串幂等键（D1）连发两次 ===
  第1次: 201 {'id':'1', ..., 'idempotency_key': None}
  第2次: 201 {'id':'2', ..., 'idempotency_key': None}      ← 不再 500（D1 已修）
=== 5-7: 非空重复 key 对照 ===
  首次: 201 id=3 text='有key'     重放(改内容): 201 id=3 text='有key'      去重到同一条: True
```
理由：D3（裸 `OSError` 漏成 500）已闭环；还原路径（`backup.rename(target)`）保证旧版本归位且无 `.incoming`/`.bak` 残留；`content.py` 的 catch 接得住。D1（空串幂等键）也已修：空串归一成 `None`，两次各自 201，不再触发唯一索引 500。

---

## 总评

**本轮三项必跑全绿（1001 passed / ruff clean / 15×回放 PASS），你点名要我打的 5 个新代码面里，2 项成立、3 项不彻底或引入新问题。**

| 项 | 判定 |
|---|---|
| R2-1 自环只回 NOTIFY | ✅ 成立，且逐条核过四条自环的 EXIT/ENTER 均无合法依赖；NOTIFY 照常发 |
| R2-2 音量键接线（进档/回原档/四态/路由） | ✅ 功能成立（真 HTTP 日志 `high→low→mid`）；⚠️ 不落库（R2-4） |
| R2-3 D2 计时器残留 | ❌ **未修彻底**（B/C 均可复现「新一轮 age≈0 被旧起点收尾」） |
| R2-4 音量档位与 `GET /settings` 一致性 | ⚠️ 新引入的不完整（分叉 + 重启丢失；boot 不 hydrate 为既有缺口） |
| R2-5 `validate_zip` 精确名 | ❌ **引入误拒**（`./role.json`、`.\role.json` 合法包被 400 拒） |
| R2-6 「校验对象≠落盘对象」 | ❌ **未堵住**（`.` 段别名；并可升级为 500 + 旧版本被销毁，违反 AC-25） |
| R2-7 换入失败 → 400/40001 + 还原 | ✅ 成立（含 D1 空串幂等键已修） |

最该先处理的两条（都指向 `host/roles/rolepack.py` 同一处归一化根因，可一并处）：
1. **R2-6**：`_fold_name` 折叠 `.` 段 → 既堵住别名绕过，又顺带修复 R2-5 的误拒。这是**安全洞**（校验被绕过 + 旧版本被销毁），优先级最高。
2. **R2-3**：录音起点清零不能依赖 tick「采样到」IDLE —— 取消与重按完全可能落在同一 tick 间隔内，真机上会造成新一轮录音被静默截断。

另：`host/core/engine.py` 的 `apply(action, payload, generation)` 第三个形参自始未被使用（既有死参，非本轮问题），可在收尾时顺手清理。

—— 本轮无阻断级新问题；上述均为可定点修复的具体缺陷，未发现你修复动作破坏了既有绿灯面。
