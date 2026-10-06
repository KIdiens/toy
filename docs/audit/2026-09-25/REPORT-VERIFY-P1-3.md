# P1 独立对抗性复验 · 第三轮（复验提交 `c665dac`，父 `62c603e`）

复验人：`p1-verifier`（只读；未改仓库任何文件、未提交、未 checkout/reset）
工程根：`F:/AI智能底座玩具/toy/`　探针目录：`F:/AI智能底座玩具/_verify_p1/`
Python：`C:/Users/Administrator/.workbuddy/binaries/python/versions/3.13.12/python.exe`

## 必跑（本轮实跑输出）

| 项 | 命令 | 结果 |
|---|---|---|
| 全量测试 | `python -m pytest tests -o addopts="" -q` | `1005 passed in 33.25s` |
| 静态检查 | `python -m ruff check .` | `All checks passed!` |
| 黄金回放 | `python -m sim --replay tests/fixtures/sessions/*.jsonl --diff` | `RESULT: PASS` × **15** |
| 真进程真 HTTP | `q3_4_real_http.py` | 四条链路全部符合预期（见下） |
| 工作区 | `git status -s` | 空 |
| 进程残留 | psutil 扫描 | 无 |

---

## 结论速览

| 编号 | 项 | 判定 |
|---|---|---|
| R3-1 | R2-5 前缀包误拒（上一轮回归） | ✅ **已闭环** |
| R3-2 | R2-6 「校验对象≠落盘对象」别名洞 | ✅ **已堵住**（HTTP 不再 500，旧版本保留） |
| R3-3 | R2-3 录音兜底计时器会话键 | ✅ **已修**（生产路径复现不出「新一轮被立刻收尾」） |
| R3-4 | R2-4 音量持久化 | ✅ **运行时已修**；另有 2 条低危观察（下 R3-5） |
| R3-5 | 音量持久化暴露的 2 条低危观察 | ⚠️ 非阻断，建议顺手收 |

**未发现阻断级新增缺陷。** 本轮我没有找到任何会让你回退的回归。

---

## R3-1　`read_member` 改用 `_norm_path` 后的边界 —— 误收/误拒矩阵

**结论：成立，无回归。** 18/18 用例符合期望，R2-5 的误拒闭环。

自写用例：`python _verify_p1/q3_1_rolepack.py`、`q3_5_rolepack_http.py`

`_norm_path` / `_fold_name` 纯函数（真实输出）：
```
'role.json'      _norm_path='role.json'      _fold_name='role.json'
'./role.json'    _norm_path='role.json'      _fold_name='role.json'
'.\role.json'    _norm_path='role.json'      _fold_name='role.json'
'././role.json'  _norm_path='role.json'      _fold_name='role.json'
'dir/role.json'  _norm_path='dir/role.json'  _fold_name='dir/role.json'
'role.json/'     _norm_path='role.json'      _fold_name='role.json'
'a/../role.json' _norm_path='a/../role.json' _fold_name='a/../role.json'
'Role.json'      _norm_path='Role.json'      _fold_name='role.json'
```
边界矩阵（全部符合期望，18/18）：
```
OK 1  单成员 role.json                 accept   read_member -> 1
OK 2  单成员 Role.json                 reject   角色包缺少 role.json      ← 不许蒙混过关
OK 3  前缀 ./role.json                 accept   read_member -> 1
OK 4  反斜杠 .\role.json               accept   read_member -> 1
OK 5  仅嵌套 dir/role.json             reject   角色包缺少 role.json      ← 契约要求根级
OK 6  根+嵌套同名                      accept   read_member -> 1（取根，非嵌套）
OK 7  仅目录条目 role.json/            reject   角色包缺少 role.json
OK 8  跨目录同名 a/A                   conflict 成员名归一化后冲突
OK 9  ./Role.json & role.json          conflict 成员名归一化后冲突        ← 前缀+大小写组合
OK 10 ./role.json & role.json          conflict 成员名归一化后冲突
OK 11 role.json  & Role.json           conflict 成员名归一化后冲突
OK 12 ./dir/../role.json               reject   拒绝路径穿越
OK 13 正常包                            accept   read_member -> 1
OK 14 /etc/passwd                      reject   拒绝绝对路径
OK 15 C:/x.txt                         reject   拒绝绝对路径
OK 16 run.bat                          reject   拒绝可执行脚本
OK 17 role.json:evil（ADS）            reject   拒绝含冒号的成员名
OK 18 嵌套在前、根在后                  accept   read_member -> 1（必须取根）
```
HTTP 面（`q3_5`）：
```
5-A ./role.json            -> 201 {'role_id':3,'config_version':'3.3.3'}   ← 上轮这里被 400 误拒
5-B .\role.json            -> 201
5-C role.json+./role.json  -> 400/40001「成员名归一化后冲突」；roles/1 逐字节不变、角色集合不变、无残留
5-D role.json+./Role.json  -> 400/40001       5-E a/role.json+A/role.json -> 400/40001
5-F dir/role.json（无根）  -> 400/40001「角色包缺少 role.json」
5-G 正常包                  -> 201
```
要点：`_norm_path` 把 `./` 折叠为同一路径但**保留大小写**，所以「含 role.json」判定放行前缀包、仍拒绝 `Role.json`；`_fold_name`（再叠 casefold）只用于冲突检测，于是 `./` 与大小写两类别名都被判冲突。两个键分开是对的——上一轮把两者塞进一个键才会既误拒前缀包、又漏掉 `.` 段别名。

---

## R3-2　「校验对象≠落盘对象」别名洞与 HTTP 升级面

**结论：已堵住。** 不再出现 500，也不再销毁旧版本。

自写用例：`q3_1_rolepack.py`（末段）、`q3_5_rolepack_http.py`（5-C）

真实输出（同一载荷：`role.json`(role_id=1) + `./role.json`(role_id=2)）：
```
上一轮 62c603e:  validate 放行 → HTTP 500/50000 → roles/1 被 role_id=2 覆盖、角色 1 从列表消失
本轮 665dac:     validate 拒绝「成员名归一化后冲突: './role.json' 与 'role.json'」
                 HTTP 400 {'code':40001, 'message': '成员名归一化后冲突: ...'}
                 roles/1 逐字节不变: True
                 角色集合不变: True（[1,2,3] → [1,2,3]）
                 roles/1 现 role_id: 1
                 残留 1.incoming / 1.bak: False / False
                 事后 GET /roles: 200
```
理由：别名在 `validate_zip` 阶段即被冲突检测拦下，`import_rolepack` 根本没进入换入步骤，因此旧版本、角色集合、磁盘残留三者都不受影响。AC-25「失败保留旧版本」恢复成立。

---

## R3-3　`_recording_session_mark()` 稳健性 —— 生产路径复现不出「新一轮被立刻收尾」

**结论：已修。** 我按上一轮的同一路径（含加强参数）重试，均未复现；仅存在一条**绕过状态机直接赋 `state`** 的注入路径可达，而生产代码无此路径。

自写用例：`python _verify_p1/q3_2_recording.py`

真实输出：
```
A 确定性模型（VirtualClock + 真实迁移，limit=3.0s）
  进入 RECORDING 后 mark = 1 history 长 = 2
  tick@0 后 since = 0 entry_mark = 1
  录至 2500ms 内产生的 rec_stop: []
  取消+同 tick 空档重按 → mark=3 entry_mark=3 since=3000
  重按后首次 tick(age=0) 产生: []            ← 未被立刻收尾
  t=5500ms(age≈2.5s) rec_stop=[] | t=6100ms(age≈3.1s) rec_stop=['rec_stop']
  → OK：仅在超过上限后才收尾

B 真实线程 + 阻塞 recorder（limit=3.0s, block=3.5s，audio 线程被上一轮占住）
  重按后状态: RECORDING | since = 72905649 | entry_mark = 1 | recorder.calls = 1
  新一轮驻留 RECORDING 时长 = 3.50s  (上限 3.0s)
  判定: 已修（时长接近上限，未被旧起点截断）      ← 上一轮同路径为 ~0.4s

C history 为空（mark == -1）
  首次 tick: since = 0 | 事件 = []（进入瞬间不误收尾）；t=4000(>3.0) rec_stop=['rec_stop']（兜底仍能收尾）
D 强推 RECORDING + 立刻 tick（limit=0.5s）：age=0.2s rec_stop=[]  → 不误收尾
E machine.reset()（清空 history）：reset 后 since=None entry_mark=-1 mark=-1；重置后再录 since=5000、无异常跳变
F 开销（N=20000）：近尾命中 0.18 µs/次（1 次迭代） | 全扫最坏 683.71 µs/次
G 复现尝试
  G1 真实迁移 取消+重按 @age=0：未复现
  G2 SAFETY_TRIP 锁定后 mark 正常、state=FAULT_LOCKED
  G3 直接改态（绕过状态机）@age=0：复现 rec_stop（唯一可复现路径）
```
逐条回答你的三个疑点：
1. **history 为空返回 -1**：安全。首次 tick 只把 `since=now` 写进去（`since is None` 分支），不会立刻收尾；之后正常计时，超限仍能收尾（C 段）。
2. **tick 在 RECORD_START 进 core 之前先跑**：不会误判。此时 `mark` 已经因「状态机进入 RECORDING 的迁移」而变化，tick 只重置起点并 return，不判定超时（A 段 `重按后首次 tick(age=0) 产生: []`）；即使 mark 恒为 -1 的注入路径（C/D），首次也只是写起点。
3. **`machine.reset()` 清空 history**：无异常跳变。reset 后 state 回 SELF_TEST → 走「非 RECORDING」分支把两个字段都清空；再进入 RECORDING 时重新写起点（E 段）。
4. **长跑倒扫开销**：真实运行态（state=RECORDING）下，最后一条 history 就是「进入 RECORDING」的迁移（RECORDING 无自环、其它迁移都会离开它），故 `_recording_session_mark()` 恒为 **1 次迭代 ≈ 0.18 µs**；`history` 只增不裁对本函数**无影响**（不会退化到全扫）。全扫最坏 683 µs/次仅在绕过状态机的注入路径出现，生产不可达。
5. **G3 那条**：我穷举后唯一能复现「新一轮 age=0 被收尾」的构造是**直接 `state_machine.state = RECORDING` 而不产生迁移**（此时 mark 恒 -1、与 `_rec_entry_mark` 相等，旧起点不会被重置）。我 grep 了全部生产代码，`state_machine.state` 的直接赋值只出现在 `state_machine.py` 自身的构造器与 `reset()`，**没有** dev/HTTP/worker 路径直接赋 state（`dev_sim.py` 的 5 个端点全部经事件/迁移）。故该构造仅在测试注入下可达，**不构成生产缺陷**——但它解释了 `-1` 兜底分支为何必须保留。

---

## R3-4　音量持久化：按键 ↔ settings ↔ 重启

**结论：运行时已修，端到端成立。**

自写用例：`python _verify_p1/q3_3_volume_persist.py`、`q3_4_real_http.py`

真实输出：
```
D 全新库（无 settings 行）：player=mid | GET /settings=mid | DB volume=<无>  → OK 一致
A 三档循环各按一次：(player, settings) = [('mid','mid'),('high','high'),('low','low'),('mid','mid')]
                     每次一致: True | 回到原档: True | worker_faults: 0
B 重启水合：按键后 player=high DB volume=high → 重启后 player=high GET /settings=high  → OK 已水合
E DB 写并发（3×60 次 PATCH /settings × 2×60 次按键）：
   并发调用异常: 无 | worker_faults 增量: 0 | 最终 player=high DB volume=high GET /settings=high
F 读码：_handle_audio_command 不含 'with self._lock' → VOLUME_STEP 分支未持锁；patch_settings 走 DB 事务，不嵌套同锁
真 HTTP 链路3：状态快照 ['IDLE','IDLE','IDLE','IDLE'] | GET /settings 序列 ['mid','high','low','mid'] | 回原档 True | fault None
```
理由：`VOLUME_STEP` → `cycle_level()` → `patch_settings({"volume_level": level})`（写 DB + `player.set_level`）；`runtime.build_context` 用 `settings_repo.get(db,"volume")` 水合 `Player(level=...)`。两条真源现在同向更新，`GET /settings` 与运行期一致、重启保留。DB 并发在 WAL + `busy_timeout=5000ms` 下未出现抢锁失败。

---

## R3-5　音量持久化暴露的 2 条低危观察（非阻断，建议顺手收）

### R3-5a　非法持久值：装配安全回落，但 `GET /settings` 原样回吐非法值
真实输出（`q3_3` C 段）：
```
存 'loud' / '' / '   ' / 'HIGH' / 'x'*5000 / 'mid\n' / '1' / 'None'
→ 启动 player 一律 'mid'（OK 安全回落，不抛）
→ 但 GET /settings.volume_level 原样回吐 'loud' / '' / '   ' / 'HIGH' / 'xxxx…' / 'mid\n' / '1' / 'None'
```
字段：`host/adapters/dto.py:47` `values.get("volume","mid")` + `host/web/schemas.py:144` `SettingsOut.volume_level: str = "mid"`（**无枚举约束**）。
后果：纵深防御不对称——运行期做了白名单回落，出站 API 没做。前端拿到越界档位时 volume-select 可能显示为空。可达性低（`PATCH /settings` 的 `SettingsPatch.volume_level` 是 `Literal["low","mid","high"]`，正常写不进非法值；仅直接改库/历史数据可达），故**低危**。
修法（可选）：`settings_dto` 里把 volume 白名单化（非 `low/mid/high` 一律回落 `mid`），或把 `SettingsOut.volume_level` 收成同款 `Literal`。

### R3-5b　audio worker 内落库失败会被吞成 `worker_faults`，且 player 与 settings 静默不一致
真实输出（`q3_3` G 段，注入 `patch_settings` 抛错）：
```
patch_settings 调用次数: 1 | worker_faults 增量: 1 | player.level = low | 状态 = IDLE
日志：[audio worker] 异常（已捕获，线程继续运行）：注入：settings 写入失败
```
链路：`workers.py:142-147` VOLUME_STEP 分支先 `cycle_level()`（player 已进档）再 `patch_settings`（写库）；若写库抛错，`_audio_loop:127-130` 的 `except Exception` 把它转成 `_on_worker_error("audio", exc)` → `worker_faults` +1。**后果**：档位已变但设置未落库 → player 与 settings 分叉（正是 R2-4 想消灭的那种分叉，只是触发条件换成了「DB 写失败」），并多出一条 worker 故障。可达性低（需盘满 / 锁等待超 5s / 库损坏），且并非完全静默（`/status` 能看到 worker 故障），故**低危**。
修法（可选，任选）：① 把这次落库单独 `try/except` 包住并降级为 `_log_warn`（音量已生效，落库失败不影响本轮体验，只记一笔）；② 或改成「先写库、成功后再进档」，保证两者要么都成、要么都不成。

---

## 覆盖面清单（我这一轮实际跑过的用例面）

- **必跑**：全量 pytest（1005）、ruff、黄金回放 15 条、真进程真 HTTP 四条链路（对话键闭环 / 表演键 / 音量键连按 3 次 + `GET /settings` 联动 / 取消录音后立刻重按并保持 RECORDING 3.05s）。
- **rolepack（导入/校验）**：`_norm_path`/`_fold_name` 纯函数 10 例；validate_zip+read_member 18 例（单成员大小写、`./` 与 `.\` 前缀、嵌套、根+嵌套、目录条目、跨目录同名、`./`×大小写组合、`./`×精确名、穿越、绝对路径、盘符、可执行后缀、ADS 冒号、取根优先）；真导入前缀包；别名包验证+导入双路径；HTTP 7 例（前缀 201、反斜杠 201、别名 400/40001、大小写别名 400、跨目录同名 400、嵌套缺根 400、正常 201）+ 旧版本逐字节不变 + 角色集合不变 + 无残留 + 事后 `/roles` 可用。
- **录音计时器**：确定性模型（真实迁移，含同 tick 空档取消+重按、超限才收尾的时间点核对）；真实线程 + 阻塞 recorder（limit=3.0/block=3.5，测驻留时长）；history 为空；强推 RECORDING + 立刻 tick；`machine.reset()`；20000 条 history 的开销；3 条复现尝试（G1/G2/G3）。
- **音量持久化**：全新库装配；三档循环一致性；跨重启水合；8 种非法持久值装配回落；DB 写并发压测（3×60 PATCH × 2×60 按键）；持锁面读码核实；落库失败注入。
- **未覆盖/明确不算本轮问题**：你列的已知未修项（`sim/wire.py:45-48`、`safety.py` 不做 NFKC/零宽、`_net_loop` 无外层兜底、THINKING 换角色 60s 兜底、`clear_fault` 不校验硬件、`worker_alive` 长动作误报、真机录/放不可打断、前端 4 条）我均未复查、也未计入。

---

## 总评

第三轮三处修复**全部成立**，且我上一轮报的 R2-5 回归已闭环、R2-6 的 HTTP 升级面（500 + 旧版本被销毁）已消除。**未发现新增缺陷**，只有 2 条低危观察（R3-5a/b）供你决定是否顺手收，不收也不阻断。

一点流程上的确认：你说「第二轮自测 1001 全绿、反向验证全绿，仍然引入了一个回归」——这轮我用 18 例边界矩阵 + 7 例 HTTP 面把 `rolepack` 的语义面基本穷举了，前缀/大小写/嵌套/取根/冲突/穿越/ADS 都各自单独立了断言。

我核对了你本轮新增的 4 例（`test_dot_prefixed_pack_is_still_accepted`、`test_dot_alias_collision_is_rejected`、`test_new_recording_is_not_truncated_when_no_tick_saw_the_idle_gap`、`test_volume_key_persists_and_hydrates_across_restart`）：主干都覆盖到了，但矩阵里仍有若干分支**未固化成用例**——`.\role.json`（反斜杠前缀）、`./Role.json` + `role.json`（前缀×大小写组合）、`a/role.json` + `A/role.json`（跨目录同名）、`dir/role.json` 单独（嵌套缺根）、`role.json` + `dir/role.json`（必须取根）、目录条目 `role.json/`、`./dir/../role.json`（穿越）。建议把这些补成一张参数化表（例如 `(entries, expected)` 的 `pytest.mark.parametrize`），否则下次再动 `_norm_path`/`_fold_name` 时，这些分支仍然只靠人工反向验证。
