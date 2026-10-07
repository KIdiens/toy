"""第 7 组 D3 契约护栏：锁冲突的对外契约（503 + Retry-After）必须写进 openapi。

**为什么要有这条**：契约文件是「前后端唯一依据」（openapi.yaml info.description）。
实现改成 503 之后若不补文档，前端只会按旧的 500 处理，契约就出现了空洞 ——
而 D3 的根因正是「契约没规定，实现顺手选了 500」。

**判别力**：删掉任一 503 声明、或把 code 改回 50001，本测试即转红。
"""
from __future__ import annotations

import pytest

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