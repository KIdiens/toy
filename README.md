# toy — 可换公仔 AI 语音动作玩具 · 主机程序（无硬件档位）

在**普通电脑**上就能完整运行的中央编排系统：状态机、音频、AI 路由、动作调度、SQLite 长期记忆、网页管理与关键帧编排。硬件（ESP32 / 舵机 / 声卡）与云端（百炼 ASR/LLM/TTS）以「**适配器 + 确定性替身**」预留接口，使「按键 -> AI -> 动作 -> 记忆」全链路在**无实物、不联网**的条件下可运行、可确定性回归。

> **本目录交付的是「无硬件档位」的完整软件骨架，不是可上市的整机成品。**
> 软件闭环可复现；实物项（四轴真实运动、舵机中位、电气急停、看门狗实测时延、音效同步、温升、机械装配、真实云 AI、配网/systemd/OTA、断电时钟、真实儿童内容安全）**本阶段无法验收**。
> 逐项说明与证据见 `docs/DELIVERY.md`。

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
| 五个管理页面 + 网页动作编排（校验 / 播放 / 停止 / 导入） | 真实云 AI 准确率 / 延迟 / 音色 / 费用 / 限流（C9） |
| 全量 pytest 955 passed、`ruff check` 全仓 clean | 配网、systemd 自启、OTA 分层回滚（C10） |
|  | 断电后时钟有效性 -> 绝对时间提醒（C11，Pi 4B 无 RTC） |
|  | 真实儿童内容安全（C12，需真实模型边界问题集） |
|  | 桩级审核的误拦边界（C13，第三人称具体人名 + 已经 + 完成 -> 误拦，fail-safe 方向） |

> 右侧全部为 Spec §10.1 的**实物待验证项 C1–C13**；本阶段对它们的唯一合法表述是「待验证」。

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
INFO host.main: 主机服务就绪：http://127.0.0.1:8000（simulate=True，AI=stub）
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
```

打开浏览器访问 `http://127.0.0.1:8000/`（状态面板），另有 `/editor` `/memories` `/reminders` `/settings`。
可用 `--host` / `--port` / `--db` / `--log-level` 覆盖默认配置；默认只绑 `127.0.0.1`。

不带 `--simulate` 即进入「串口真机档位」（默认连 `socket://127.0.0.1:5555`，可用 `MOTION_PORT` 覆盖为 `COM5` 或 `/dev/serial/by-id/...`）。

## 测试

```bash
pytest -q                                     # 全量：955 passed
ruff check .                                  # 静态检查：All checks passed
python -m sim --replay tests/fixtures/sessions/*.jsonl --diff   # 黄金回放：15/15 PASS
```

## 目录导航

| 目录 / 文件 | 职责（一句话） |
|---|---|
| `host/main.py` | 入口：只装配（读配置、建上下文、起 worker、挂 web），78 行 |
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
| `docs/SPEC-host-mvp.md` | 本阶段唯一开发依据（范围、契约、验收标准 AC-01..AC-26） |
| `docs/PRD-host-mvp.md` | 产品需求（用户画像、功能清单、RICE） |
| `docs/architecture/` | 分层、目录约束、状态机与协议、技术选型、追溯表；§10.1 = 实物待验证项 C1–C13 |
| `docs/api/openapi.yaml` | 网页管理 API 契约（OpenAPI 3.0.3） |
| `docs/decisions/` | ADR-001..012，以及**未关闭决策登记 `OPEN-DECISIONS.md`（OD-1..OD-8）** |

## 已知边界（必须知悉）

内容安全守卫是**桩级**，`host/ai/safety.py` 的越界声称检测**无法穷举**合法叙事句式，存在两条互补边界：

- **误拦（Spec §10.1 的 C13）**：第三人称**具体人名** + 「已经」 + 完成收尾，形如「小熊已经拿到了一颗糖」的合法叙事句会被拦；纯代词「他」放行（「他已经关好窗户啦」）。这是 **fail-safe 方向**（宁可否决，不漏放），不是安全漏洞，边界很窄。
- **漏放（`docs/decisions/OPEN-DECISIONS.md` 的 OD-8）**：`'我替你转账了'` 会被放行（第一人称 + 替/帮 + 动作 + 了，但无「把/将」，后置锚未命中）。

两条都不会让玩具真的去做现实动作。完整边界、实例句与逐条证据见 `docs/DELIVERY.md` 第 4 节。
