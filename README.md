# toy — 可换公仔 AI 语音动作玩具 · 主机程序（无硬件档位）

在**普通电脑**上就能完整运行的中央编排系统：状态机、音频、AI 路由、动作调度、SQLite 长期记忆、网页管理与关键帧编排。硬件（ESP32 / 舵机 / 声卡）与云端（百炼 ASR/LLM/TTS）以「**适配器 + 确定性替身**」预留接口，使「按键 -> AI -> 动作 -> 记忆」全链路在**无实物、不联网**的条件下可运行、可确定性回归。

> **本目录交付的是「无硬件档位」的完整软件骨架，不是可上市的整机成品。**
> 软件闭环可复现；实物项（四轴真实运动、舵机中位、电气急停、看门狗实测时延、音效同步、温升、机械装配、真实云 AI、配网/systemd/OTA、断电时钟、真实儿童内容安全）**本阶段无法验收**。
> 逐项说明与证据见 `docs/DELIVERY.md`。

最近接手过程、已修复问题与验收边界见 [接手记录（2026-10-24 快照）](docs/HANDOVER-20261024.md)。单独克隆本仓库时，外部冻结参考资料需按下方测试说明补齐；原始资料包和工程外验证产物保留在本地。

## 当前能达到 / 不能达到

| 能达到（本机实测，软件可复现） | 不能达到（等实物 / 等 Key） |
|---|---|
| 状态机 7 状态与非法迁移拒绝、故障锁定需人工确认 | 四轴真实运动、无卡死 / 无碰撞、复位（C1） |
| MCU 协议客户端 + 进程内 MCU 模拟器（复刻 `motion_controller.ino`）联锁 / 看门狗 / 末帧保持 | 舵机中位校准与「实际位置」（C2，PWM 舵机无位置反馈） |
| 动作时间线校验、离线预演、黄金回放逐行一致 | 急停**电气断电**（C3；软件 `STOP` 不等于断电） |
| 无 Key 自动落桩后端、零网络出口、工具白名单二次校验 | ADC 角色识别窗口实测（C4） |
| 儿童模式双层审核（出声前审核 + 固定拒绝模板） | 300ms 看门狗**实际时延**、20ms PWM 周期（C5） |
| SQLite 记忆查看 / 删除 / 清空 / 关闭、删除后不再注入 | 录放音质、动作与音效同步 <100ms（C6） |
| 角色注册与切换、`session_generation` 竞态丢弃 | 温升、连续 ≥2h 混合运行（C7） |
| 音频抽象层（真 / 文件 / Null 后端）与半双工互斥 | 机械耦合、插拔 100 次、尺寸公差、CAD（C8） |
| 六个管理页面 + 网页动作编排；状态页显示实际发送的四轴指令目标，停止/过期/断线清空并按角色限幅 | 真实云 AI 准确率 / 延迟 / 音色 / 费用 / 限流（C9） |
| 全量 pytest 1858 passed / 3 skipped、`ruff check` 全仓 clean | 配网、systemd 自启、OTA 分层回滚（C10） |
|  | 断电后时钟有效性 -> 绝对时间提醒（C11，Pi 4B 无 RTC） |
|  | 真实儿童内容安全（C12，需真实模型边界问题集） |
|  | 桩级审核的误拦边界（C13，第三人称具体人名 + 已经 + 完成 -> 误拦，fail-safe 方向） |

> 右侧全部为 Spec §10.1 的**实物待验证项 C1–C13**；本阶段对它们的唯一合法表述是「待验证」。

AI 播放工具会先准备白名单动作或本地音频，等审核后的回复播放完，再按顺序执行。取消、STOP、换角色或设备故障会清掉后续任务；固定安全拒绝语音仍可正常回放。工具回执表示“待执行”，不把尚未发生的动作说成完成。实现与边界见 [ADR-014](docs/decisions/ADR-014-deferred-media-tool-execution.md)；本次串行执行不等于音画并行同步验收。

## 安装

```bash
cd toy
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"     # Windows
# source .venv/bin/activate && pip install -e ".[dev]"   # Linux / Raspberry Pi OS
```

要求 CPython `>=3.11`（开发与部署同版本按 CPython 3.13 锚定，见 `docs/architecture/10-tech-selection-matrix.md`）。
依赖声明唯一落点：`pyproject.toml`。

## 环境自检（交付包完整性）

交付包**自带 `data/` 目录**——它是 Spec §4 目录结构的组成部分（运行时数据落点：默认库 `data/toy.sqlite3`、日志、缓存，见 `host/config/defaults.toml`）。该目录由 `data/.gitkeep` 占位、`.gitignore` 以 `data/*` + `!data/.gitkeep` 保留，确保 clone / 解包后目录仍在。**缺它会直接导致 `pytest --basetemp=data/...` 在 setup 阶段报 56 个 `FileNotFoundError`**（pytest 建 basetemp 时不创建父目录）。

解包后先跑一遍自检：

```bash
python -c "import os,sys; need=['data','data/.gitkeep','host','sim','content/audio','tests','docs']; miss=[p for p in need if not os.path.exists(p)]; print('self-check OK: 目录结构完整' if not miss else 'self-check FAIL, missing: '+', '.join(miss)); sys.exit(bool(miss))"
```

输出 `self-check OK` 即可继续；若列出缺项，说明解包不完整（`data/` 是靠 `data/.gitkeep` 保留的空目录，不要当作临时产物删除）。

## 运行（无硬件档位）

```bash
python -m host.main --simulate
```

预期输出（节选）：

```
INFO host: active role -> 1 (1.0.0)
INFO host.main: 主机服务就绪：http://127.0.0.1:8000（simulate=True，AI=stub，audio=null）
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
```

打开浏览器访问 `http://127.0.0.1:8000/`（状态面板），另有 `/editor` `/memories` `/reminders` `/settings`。
可用 `--host` / `--port` / `--db` / `--log-level` 覆盖默认配置；默认只绑 `127.0.0.1`。

不带 `--simulate` 即进入「串口真机档位」（默认连 `socket://127.0.0.1:5555`，可用 `MOTION_PORT` 覆盖为 `COM5` 或 `/dev/serial/by-id/...`）。

### 启用真实声卡（运动控制仍可仿真）

默认音频后端为 `null`，不访问麦克风或扬声器。显式选择 `sd` 才使用真实设备：

```powershell
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
& '.\.venv\Scripts\python.exe' -m sounddevice  # 列出设备名称与编号，不录音
& '.\.venv\Scripts\python.exe' -m host.main --simulate --audio sd
# 输入、输出设备不同，可分别指定；名称含空格时加引号：
& '.\.venv\Scripts\python.exe' -m host.main --simulate --audio sd --audio-input-device 2 --audio-output-device 5 --audio-sample-rate 16000
```

示例设备编号需要按本机枚举结果替换。省略设备参数时使用系统默认输入、输出。命令行优先于 `AUDIO_BACKEND`、`AUDIO_INPUT_DEVICE`、`AUDIO_OUTPUT_DEVICE`、`AUDIO_SAMPLE_RATE` 环境变量，再回落到 `host/config/defaults.toml` 的 `[audio]` 段。

启动时检查依赖、设备与采样率；失败会退出并给出配置指引，不会静默切换到空后端。真实后端仍由录音器与播放器共享；16-bit PCM 录音在发给 ASR 前封装为 WAV。`--audio sd` 只选择录放音设备，云端 AI 仍按 `AI_PROVIDER` 配置选择，桩后端不会提供真实语音识别。

再次按对话键会提前结束本次录音，保留已经采集的样本；说话时按对话键会中断旧播放，再开始新录音。每次录放音使用独立取消事件，停止信号不排在阻塞录放音之后；已取消的旧播放不再投递完成事件。

录音或播放发生设备错误时，本轮以音频失败结束并回到可重试状态；失败、空录音或已取消的请求不进入 ASR。错误保留在日志和 `worker_faults` 诊断计数中；设备恢复后可重新按键重试，程序不会自动重复录音。软件回归见 `docs/PROGRESS-20261021-audio-recovery.md`。

软件装配与设备替身测试不等于实物录放音验收。真实音质、停止与打断的设备实测延迟、热插拔和动作同步仍需继续验证；C6、C9 保留为待验证。最新软件回归见 `docs/PROGRESS-20261021-audio-cancel.md`。

## 测试

```bash
pytest -q                                     # 全量：1858 passed, 3 skipped（2026-10-24 本机）
ruff check .                                  # 静态检查：All checks passed
python tests/frontend/selfcheck_static.py      # 前端静态门禁：违规或输入缺失返回非零
python -m sim --replay tests/fixtures/sessions/*.jsonl --diff   # 黄金回放：15/15 PASS
```

PowerShell 不会为原生命令展开上述通配符，回放时先取文件列表：

```powershell
$replayFiles = @(Get-ChildItem tests/fixtures/sessions -Filter '*.jsonl' | ForEach-Object { $_.FullName })
python -m sim --replay @replayFiles --diff
```

全量测试包含前端门禁的违规样本、角色页未保存切换控制器回归，以及真实 Uvicorn/TCP 的 WebSocket 升级、状态推送与断线回收。编排器控制器已拆出导入/导出处理，单文件恢复到 300 行以内；设计令牌通过同源路由加载。

四种字体及各自许可已放在 `host/web/static/fonts/`，全部同源加载，不依赖外网字体服务或本机已安装字体。Noto Sans SC 是当前程序文字子集，未覆盖的动态输入保留系统回退；来源、哈希与重建命令见该目录的 [字体说明](host/web/static/fonts/README.md)。新增校验覆盖资源内容、字体绑定、许可哈希、中文覆盖、WOFF2 类型及重复导入。隔离 Chrome 已在阻断外网请求、禁用缓存时确认实际使用自托管字形；六页桌面和四页窄屏检查通过。提醒页的页头按钮不再挤断，列表独立横向滚动，表头与数据列对齐，键盘可到达取消确认控件。

角色人设保存还覆盖等待期间继续编辑、清空草稿、跨角色并发、重复提交与错误重试。旧响应不会覆盖新草稿；成功后按落盘值更新脏检查基线；迟到的字段错误不会标到另一个角色或已经修改的内容上。“恢复默认”仍需清空后点击保存，保存中途清空不会被前一次响应撤销。

动作停止现在会中断在途运动，而非等待整条时间线结束：停止、替换、关停和安全锁定在运动循环的等待/发帧阶段检查取消，随后执行 STOP/关闭连接；旧结果不会覆盖新动作，也不计为正常完成。正常动作仍保持原有线序和末帧保持。`tests/unit/test_motion_cancellation.py` 覆盖各阶段及结果返回边界，`tests/integration/test_motion_interrupt.py` 使用真实 pyserial/TCP 与固件等价模拟器验证收尾和锁定保留。TCP 模拟器在没有输入时也持续采样、发状态并运行看门狗，见 `test_tcp_idle.py`。这些不替代实物 C3/C5。

2026-10-22 在 Windows 的隔离 Chrome 中回放了桌面及 430px 窄屏：关键帧修改与插入、播放/停止、导出后服务端校验、非法资源包拒绝，以及角色未保存修改的取消/确认；窄屏播放栏控件已检查完整可见。使用模拟设备、空音频后端和隔离数据，不代表实物或真实云 AI 验收；本轮未运行远端 CI。

> 3 条 skip 是 `test_review_role_limits_runtime` 中角色 1 无法构造针对性样本的跳过：
> 其角色限幅已覆盖固件区间，无法同时满足“越角色限幅、仍在固件区间内”，不是失败，也不要求真机。
> 另有 **504 条** `tests/contract/test_motion_parity.py` 依赖仓库**外**的冻结基线
> `../AI角色玩具开发资料包/AI角色玩具开发书/software/motion.py`：该文件缺失时它们会
> **skip 而非 fail**（skipped 从 3 涨到 500+）。**看到 skipped 暴涨说明基线没放对位置，
> 不是代码坏了。** 交付包已把这份基线一并附上，按原目录结构放回即可。

## 目录导航

| 目录 / 文件 | 职责（一句话） |
|---|---|
| `host/main.py` | 入口：配置装配、启动 worker、挂载 web 与进程收尾；参数解析在 `host/config/cli.py` |
| `host/config/` | 唯一配置加载点（env 覆盖 `defaults.toml`），密钥只从环境变量读 |
| `host/core/` | 确定性核心：显式状态机、事件总线、时钟（`RealClock`/`VirtualClock`）、异常分类 |
| `host/motion/` | 串口协议客户端与动作：`MotionLink` 三实现（Serial/Sim/Replay）、校验、插值、时间线 |
| `host/audio/` | 音频抽象层（真 / Null / 文件后端）、录音、播放、内容库 |
| `host/ai/` | AI 路由与提供方（Stub / Qwen）、儿童内容审核、工具白名单 |
| `host/tools/` | 本地工具实现：天气、提醒、内容与动作播放 |
| `host/roles/` | 角色注册 / 校验 / 切换，资源包（zip）安全解包 |
| `host/storage/` | `sqlite3` 门面 + `user_version` 迁移 + 四个仓储（只做读写） |
| `host/web/` | FastAPI 装配、路由、Pydantic 出入参、静态页与设计令牌 |
| `host/adapters/` | 端口到 HTTP/WS 的桥接与 DTO 转换；运行期装配（`runtime.py`） |
| `host/updater/` | **空目录（OTA 骨架占位）**：本阶段未实装，对应实物项 C10「配网 / systemd / OTA 分层回滚」待验证 |
| `sim/` | MCU 模拟器：逐字复刻固件语义 + 故障注入 + 虚拟时钟 + 回放 |
| `content/` | 角色配置与动作文件（`roles/<id>/`）、本地音频（`audio/`） |
| `content/audio/` | **空目录（运行时音频导出目录）**：内容不入库；无硬件档位用 NullAudio，可留空 |
| `data/` | 运行时数据落点（默认库 `toy.sqlite3` / 日志 / 缓存）；由 `data/.gitkeep` 保留目录、内容不入库，**不可删除** |
| `firmware/` | 冻结基线固件源码（只读参考，本阶段不改动、不编译） |
| `tests/` | unit / contract / integration / fixtures（含 15 份黄金录制） |
| `docs/` | PRD / SPEC / 架构 / ADR / OpenAPI / 交付文档 |

## 文档索引

| 文档 | 用途 |
|---|---|
| `docs/DELIVERY.md` | **交付说明**：能达到 / 不能达到（C1–C13）、已知边界、运行命令、交接要点、生产就绪清单 |
| `docs/DELIVERY.html` | 上述交付说明的可打印 HTML（离线、无外部依赖） |
| `docs/SPEC-host-mvp.md` | 开发依据：范围、契约与验收条目 |
| `docs/PRD-host-mvp.md` | 产品需求（用户画像、功能清单、RICE） |
| `docs/architecture/` | 分层、目录约束、状态机与协议、技术选型、追溯表；§10.1 = 实物待验证项 C1–C13 |
| `docs/api/openapi.yaml` | 网页管理 API 契约（OpenAPI 3.0.3） |
| `docs/decisions/` | ADR-001..013；开放事项 OD-1–OD-7，OD-8 的关闭记录见 ADR-013 |

## 已知边界（必须知悉）

内容安全守卫仍是**桩级**，`host/ai/safety.py` 的句式检测不能穷尽语义；当前边界与修复记录如下：

- **误拦（Spec §10.1 的 C13）**：第三人称**具体人名** + 「已经」 + 完成收尾，形如「小熊已经拿到了一颗糖」的合法叙事句会被拦；纯代词「他」放行（「他已经关好窗户啦」）。这是 **fail-safe 方向**（宁可否决，不漏放），不是安全漏洞，边界很窄。
- **OD-8 已修复并关闭**：`我替你转账了` 等直接代办完成声称会在 TTS 前替换为固定话术；疑问、提醒和明确故事语境的本轮对照通过。范围与限制见 `docs/decisions/ADR-013-direct-benefactive-output-claims.md`。

真实模型内容安全 C12 和既有误拦 C13 继续待验收。修复范围与证据见 ADR-013 及 `docs/DELIVERY.md` 第 4 节。
