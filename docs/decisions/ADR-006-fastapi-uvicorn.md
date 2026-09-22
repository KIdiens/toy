# ADR-006: 使用 FastAPI + uvicorn 作为网页后端

## Status
Accepted (2026-09-21)；上界修订 2026-09-22（见 Decision 末注）

## Background
开发书 §13.1 要求「静态页面和接口同源，避免额外跨域配置；仅本机运行时绑定 127.0.0.1；开放局域网需登录与权限控制」，并明确「后台可以采用 Python FastAPI」。§13.2 给出 8 个端点合同。架构要求产出**机器可读契约**（`openapi.yaml`）作为前后端唯一依据。

## Decision
**FastAPI 0.128.0 + uvicorn 0.53.x**（锁 `fastapi>=0.128,<0.129`、`uvicorn>=0.34,<0.54`；Pydantic `>=2.7`）。

依据：①自动生成 OpenAPI，直接满足「产出 openapi.yaml 契约」；②Pydantic 让 §13.2 的 `request_id`、预期 `role_id`、动作版本等约束声明式落地；③`StaticFiles` 支持静态页与 API 同源；④原生 WebSocket，承载状态实时推送；⑤开发书明确许可 FastAPI。

> 版本细节：FastAPI 0.126.0 起最低 Pydantic 为 2.7.0，并移除 Pydantic v1 支持；0.125.0 起要求 Python ≥3.9（编制时最新 0.128.0）。`uvicorn` 的 `[standard]` extra 在 linux-aarch64 上若 `uvloop`/`httptools` 无 wheel 会自动回退纯 Python，功能不受影响，仅性能略降——因此**不强制 `[standard]`**，用基础 `uvicorn` 以免部署时踩编译坑。
>
> **上界修订（2026-09-22）**：原锁 `uvicorn>=0.34,<0.53` 与实际验证环境不符——本机实测安装 `uvicorn 0.53.0`，且在该版本上全量回归 **955 passed**、离屏 E2E 与黄金回放全绿，兼容性无问题。`pyproject.toml` 是团队声明的「依赖唯一真源」，真源与实际环境不一致比版本号本身更成问题，故**修订上界为 `<0.54`（跟上 minor），不回落环境到 0.52.x**。FastAPI 上界 `>=0.128,<0.129` 维持不变（该版本实测为 0.128.8）。

排除：Flask 3.1（无自动 OpenAPI、无原生 WebSocket）；Starlette 裸用（需自建校验层，重复 FastAPI 的活）。

## Consequences
- 正面：契约自动产出；校验声明式；同源托管；WS 推送。
- 负面：FastAPI/uvicorn 版本仍在 0.x，需按 minor 锁定并每次升级跑回归。
- 安全：默认绑定 `127.0.0.1`；开放局域网时加认证与 CSRF 保护（写操作），密钥不回显（§13.2）。

## Related ADRs
ADR-003（HTTP 循环与核心经队列通信）、ADR-007（前端静态资源同源）、ADR-012（令牌与图标）。
