# ADR-001: 使用 CPython 3.13 + venv 作为主机程序语言与运行时

## Status
Accepted (2026-09-21)

## Background
`toy/host` 需要一套语言运行时，同时满足：Windows 上开发、树莓派 4B 上部署；能驱动串口、音频、SQLite、HTTP/WebSocket；并且必须复用既有资产。既有 `software/motion.py`、`software/cloud_chat.py`、`software/test_*.py` 全部是 Python，开发书附录 A 的参考资料与第 8 章的安装步骤也以 Python 为准。开发书 §8.1 要求「Python 3.11 或更新兼容版本」。

## Decision
使用 **CPython 3.13**（锁定 `>=3.13,<3.14`），虚拟环境 `venv`，依赖用 `pyproject.toml` 声明并生成 `requirements.lock`。

依据：
1. **版本同一**：Raspberry Pi OS **Trixie**（Debian 13，2025-10 发布）默认 Python **3.13**；Windows 侧安装 3.13.x（编制时末位补丁 3.13.15）。开发与部署同大版本，消除「开发 3.13 / 部署 3.11」的行为差异。
2. **地板 3.11**：旧卡上的 Raspberry Pi OS **Bookworm** 默认 3.11.2；代码不使用 3.12+ 独有语法即可在两种卡上跑，`requires-python` 取 `>=3.11` 作为兼容地板，CI 主测 3.13。
3. **venv 强制**：Trixie/Bookworm 均为 PEP 668 externally-managed 环境，`pip install` 直接装会报 `externally-managed-environment`；用 `python3 -m venv .venv` 是官方推荐路径。
4. **生态齐备**：`sqlite3` 为标准库；`pyserial`、`sounddevice`、`soundfile`、`fastapi` 均提供 Windows 与 linux-aarch64 wheel。

排除：Node.js（零代码复用、aarch64 原生编译负担）；Go（零复用、PortAudio 需 cgo）。

## Consequences
- 正面：最大化复用既有代码与团队熟悉度；单版本降低环境漂移；`sqlite3` 免依赖。
- 负面：需在两台机器各建 venv；树莓派 4B 上纯 Python 的 CPU 占用需关注（音频/网络的阻塞 I/O 已下沉线程，见 ADR-003）。
- 约束：`pyproject.toml` 是依赖版本的唯一来源；业务代码不得硬编码版本/URL/密钥。

## Related ADRs
ADR-003（并发模型）、ADR-005（SQLite）、ADR-008（音频后端）。
