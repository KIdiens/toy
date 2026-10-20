"""第 7 组 D3 契约护栏：锁冲突的对外契约（503 + Retry-After）必须**真的发生**。

两件事必须同时成立，缺一不可：

1. **契约文件**要声明 503 + Retry-After（openapi.yaml 是「前后端唯一依据」）；
2. **实现**在真撞上写锁时要真的返回 503 + Retry-After + ``code=50301``，且**快速失败**。

改前只有第 1 件：本文件 8 条用例全是读 ``docs/api/openapi.yaml`` 做字符串断言，
一条真实 HTTP 都不打。实测：把 ``host/web/app.py`` 的 ``status_code=503`` 改成 500，
这 8 条**全绿**（只有 ``test_error_envelope.py`` 里 1 条红）—— 也就是说，
「契约写了 503」与「服务真的回 503」之间没有任何绑定，文档可以独自漂亮。

现在按 ``BUSY_CAPABLE_ENDPOINTS`` 的 5 个端点各造一次**真实锁冲突**
（另开一条 sqlite 连接 ``BEGIN IMMEDIATE`` 占住写锁），断言状态码、退避头、
信封 code 与耗时上界。前置数据（profile / 记忆行 / 提醒行 / 设置行）必须
**在占锁之前**造好，否则请求会先在「找不到资源」处返回，根本碰不到锁。
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from host.adapters.runtime import build_context
from host.config.settings import load_settings
from host.web.app import create_app

from tests.support import TOY_ROOT

# 会真正触碰写锁的端点（读端点也算：默认档案不存在时服务会自动建档，见
# data/g7/probe_d3_read.py实测 —— 锁中GET /memories 在建档前同样 503）。
BUSY_CAPABLE_ENDPOINTS = [
    ("get", "/memories"),
    ("delete", "/memories/{id}"),
    ("post", "/reminders"),
    ("delete", "/reminders/{id}"),
    ("patch", "/settings"),
]

# 「快速失败」的**绝对**耗时上界。
#
# 不能写成 ``BUSY_TIMEOUT_MS/1000*N``：那样把 busy_timeout 改回 5000 时阈值同步涨，
# 「阻塞 5.5s 再回 503」照样通过 —— 阈值跟着被测对象一起变就是假绿。
# 这里锚的是缺陷本身（每个撞锁请求独占连接数秒），与实现常量无关。
LOCK_BUSY_BUDGET_S = 2.0
# 锁冲突的信封 code 与状态码都写死：契约值不该从被测模块 import 出来（否则改一处全跟着变）。
BUSY_STATUS = 503
BUSY_CODE = 50301
GENERIC_STORAGE_CODE = 50001


@pytest.fixture(scope="module")
def contract() -> dict:
    yaml = pytest.importorskip("yaml")
    text = (TOY_ROOT / "docs" / "api" / "openapi.yaml").read_text(encoding="utf-8")
    return yaml.safe_load(text)


def test_storage_busy_response_component_declares_retry_after(contract: dict) -> None:
    """必须有一个可复用的 503 组件，且它**显式声明 Retry-After**。

    没有 Retry-After 的 503 是残缺的：调用方无从判断该立即重试还是退避。
    """
    responses = contract["components"]["responses"]
    busy = responses.get("StorageBusy")
    assert busy is not None, f"components.responses 缺 StorageBusy，实际有 {list(responses)}"
    header = (busy.get("headers") or {}).get("Retry-After")
    assert header is not None, "StorageBusy 未声明 Retry-After 响应头"
    assert "503" in busy["description"], "StorageBusy 描述里应点明状态码语义"
    # 组件必须自带信封 schema，否则前端拿不到 {code,data,message} 结构
    assert "application/json" in busy["content"], busy


@pytest.mark.parametrize("method,path", BUSY_CAPABLE_ENDPOINTS)
def test_busy_capable_endpoints_declare_503(contract: dict, method: str, path: str) -> None:
    """每个会碰写锁的端点都必须声明 503，且引用同一个可复用组件。"""
    ops = contract["paths"][path]
    assert method in ops, f"{path} 没有 {method} 操作"
    responses = ops[method]["responses"]
    assert "503" in responses, (
        f"{method.upper()} {path} 会碰写锁却未声明 503（实际声明 {sorted(responses)}）"
    )
    ref = responses["503"].get("$ref")
    assert ref == "#/components/responses/StorageBusy", (
        f"{method.upper()} {path} 的 503 应引用共享组件 StorageBusy，实际 {responses['503']}"
    )


def test_components_has_no_duplicate_responses_key(contract: dict) -> None:
    """``components`` 下**有且只有一个** ``responses`` 键。

    这是本组实测踩到的真实陷阱：文件里曾存在**两个** ``responses:``（一个在
    ``components`` 下、一个误缩进到 ``schemas`` 之下）。YAML 解析器对重复键
    **静默取最后一个**，于是前一个块里定义的 ``ErrorEnvelope`` 整块消失、
    从未被任何端点引用 —— 而 YAML 依然解析成功、没有任何报错。
    这类「结构坏掉但文件合法」的状态光靠肉眼看不出来，故钉成断言。
    """
    text = (TOY_ROOT / "docs" / "api" / "openapi.yaml").read_text(encoding="utf-8")
    top_level_responses = [line for line in text.splitlines()
                           if line.startswith("  responses:")]
    assert len(top_level_responses) == 1, (
        f"components 下出现 {len(top_level_responses)} 个responses: 键"
        "（YAML 重复键静默取最后一个，会让先定义的响应组件整块失效）"
    )
    # 未使用的局部导入会触发 ruff；此处直接用已注入的 contract fixture 即可
    assert "responses" not in contract["components"]["schemas"], (
        "components.schemas 下混入了 responses 块：缩进错误，"
        "重复键会让components.responses 被静默覆盖"
    )
    # 两个组件都必须真的解析出来
    assert set(contract["components"]["responses"]) >= {"ErrorEnvelope", "StorageBusy"}, (
        f"响应组件不全: {sorted(contract['components']['responses'])}"
    )


def test_deterministic_storage_failure_stays_500(contract: dict) -> None:
    """反向锁定：**不得**为了统一状态码，把确定性错误也改成 503。

    具体锚点取两类：
    1. ``/content/import`` 的 400 是**确定性**校验失败（包损坏/ 超限），重试无意义，
       且该端点不碰 SQLite 写事务，绝不该被 503 污染；
    2. 角色切换的 409 是确定性状态冲突（角色不匹配），同样不该被 503 吞掉。
    """
    import_code = contract["paths"]["/content/import"]["post"]["responses"]
    assert "400" in import_code, "content/import 的 400（确定性校验失败）被改掉了"
    assert "503" not in import_code, (
        "content/import 不碰 SQLite 写事务，不该声明 503"
    )

    conflict = contract["paths"]["/roles/active"]["patch"]["responses"]
    assert "409" in conflict, "roles/active 的 409（确定性状态冲突）被改掉了"
    assert "503" not in conflict, (
        "roles/active 的 409 是确定性状态冲突，与可重试的锁冲突无关，不该声明 503"
    )


# =====================================================================
# 2. 真实 HTTP：每条会碰写锁的路径都必须真的回 503 + Retry-After
# =====================================================================
def _client(tmp_path: Path):
    settings = load_settings({"DB_PATH": str(tmp_path / "busy.sqlite3")})
    context = build_context(settings, simulate=True, root=TOY_ROOT)
    return context, TestClient(create_app(context), raise_server_exceptions=False)


def _reminder_body(request_id: str, key: str | None = None) -> dict:
    body = {
        "request_id": request_id,
        "profile_id": "default",
        "text": "喝水",
        "due_at_utc": "2030-01-01T00:00:00Z",
        "timezone": "Asia/Shanghai",
    }
    if key is not None:
        body["idempotency_key"] = key
    return body


# 每个 prepare 负责：① 在**未加锁**时造好前置数据；② 返回一个「发这次请求」的
# 闭包。前置数据不先造好，请求会在「资源不存在」处提前返回，根本碰不到写锁 ——
# 那样测出来的 503 是假的（或者干脆 404/200），用例失去判别力。
def _prepare_get_memories(context, client):
    """不预热：默认档案不存在时，``GET /memories`` 的建档步骤本身就是写事务。"""
    return lambda: client.get("/api/v1/memories")


def _prepare_delete_memory(context, client):
    """先建一条真实记忆行，再删它（删除是单步事务）。"""
    profile_id = context.ensure_profile()
    memory = context.add_memory(profile_id=profile_id, kind="fact", value="锁冲突删除用")
    return lambda: client.delete(f"/api/v1/memories/{memory['id']}")


def _prepare_create_reminder(context, client):
    """创建本身就是写事务，无需前置数据。"""
    return lambda: client.post("/api/v1/reminders", json=_reminder_body("busy-post"))


def _prepare_cancel_reminder(context, client):
    """软取消要 UPDATE 一行真实存在的提醒，所以先建一条。"""
    created = client.post("/api/v1/reminders",
                          json=_reminder_body("busy-seed", "busy-seed-key"))
    assert created.status_code == 201, created.text
    reminder_id = created.json()["data"]["id"]
    return lambda: client.delete(f"/api/v1/reminders/{reminder_id}")


def _prepare_patch_settings(context, client):
    """先落一行设置（不带字段的 PATCH 不会写库，也就碰不到锁）。"""
    seeded = client.patch("/api/v1/settings",
                          json={"request_id": "busy-seed", "volume_level": "mid"})
    assert seeded.status_code == 200, seeded.text
    return lambda: client.patch("/api/v1/settings",
                                json={"request_id": "busy-patch", "volume_level": "high"})


BUSY_HTTP_CASES = [
    pytest.param(_prepare_get_memories, id="get-memories"),
    pytest.param(_prepare_delete_memory, id="delete-memory"),
    pytest.param(_prepare_create_reminder, id="post-reminders"),
    pytest.param(_prepare_cancel_reminder, id="delete-reminder"),
    pytest.param(_prepare_patch_settings, id="patch-settings"),
]


@pytest.mark.parametrize("prepare", BUSY_HTTP_CASES)
def test_locked_write_returns_503_with_retry_after_and_fails_fast(tmp_path, prepare) -> None:
    """真占住写锁 → 该端点必须回 503 + ``Retry-After`` + ``code=50301``，且快速失败。

    **判别力**：把 ``host/web/app.py`` 的 ``storage_busy`` 处理器改回 500，
    这 5 条全部转红（改前它们全绿 —— 因为一条真实请求都没发）。
    """
    context, client = _client(tmp_path)
    try:
        fire = prepare(context, client)
        blocker = sqlite3.connect(str(context.settings.storage.path), isolation_level=None)
        blocker.execute("BEGIN IMMEDIATE")     # 占住写锁
        try:
            started = time.perf_counter()
            response = fire()
            elapsed = time.perf_counter() - started
        finally:
            blocker.execute("ROLLBACK")
            blocker.close()
    finally:
        context.close()

    assert response.headers["content-type"].startswith("application/json"), response.text
    body = response.json()
    assert {"code", "data", "message"} <= set(body), f"不是统一信封: {body}"
    assert response.status_code == BUSY_STATUS, (
        f"锁冲突应回可重试的 {BUSY_STATUS}，实际 {response.status_code}：{response.text}"
    )
    assert body["code"] == BUSY_CODE, body
    assert body["code"] != GENERIC_STORAGE_CODE, "锁冲突不得落回通用 50001"
    headers = {key.lower(): value for key, value in response.headers.items()}
    assert "retry-after" in headers, f"503 缺 Retry-After，调用方无从判断退避：{headers}"
    assert int(headers["retry-after"]) >= 1, headers
    assert elapsed < LOCK_BUSY_BUDGET_S, (
        f"锁冲突下耗时 {elapsed:.2f}s，超出快速失败预算 {LOCK_BUSY_BUDGET_S}s："
        "仍在阻塞等 busy_timeout，把「临时竞争」拖成了「排队数秒」"
    )