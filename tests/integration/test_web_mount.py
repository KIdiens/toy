"""Web 装配契约（进程内）：页面 / 静态挂载、统一错误信封、SIM 端点仅在 SIM 注册、路由与契约对齐。"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from host.adapters.runtime import build_context
from host.config.settings import load_settings
from host.web.app import create_app

from tests.support import TOY_ROOT

OPENAPI_PATH = TOY_ROOT / "docs" / "api" / "openapi.yaml"
PATH_LINE = re.compile(r"^ {2}(/[^:\s]+):\s*$")
PAGES = ("/", "/editor", "/roles", "/memories", "/reminders", "/settings")


def _context(tmp_path: Path, *, simulate: bool):
    settings = load_settings({"DB_PATH": str(tmp_path / "toy.sqlite3")})
    return build_context(settings, simulate=simulate, root=TOY_ROOT)


@pytest.fixture()
def prod_client(tmp_path: Path):
    context = _context(tmp_path, simulate=False)  # 真机档位：不注册 dev_sim
    try:
        with TestClient(create_app(context)) as client:
            yield client
    finally:
        context.close()


@pytest.fixture()
def sim_client(tmp_path: Path):
    context = _context(tmp_path, simulate=True)
    try:
        with TestClient(create_app(context)) as client:
            yield client
    finally:
        context.close()


def _openapi_paths() -> set[str]:
    lines = OPENAPI_PATH.read_text(encoding="utf-8").splitlines()
    start = lines.index("paths:") + 1
    found: set[str] = set()
    for line in lines[start:]:
        if line and not line.startswith(" "):
            break  # 离开 paths 段
        match = PATH_LINE.match(line)
        if match:
            found.add(match.group(1))
    return found


# ------------------------------------------------------------------ 挂载
def test_roles_page_is_html_and_hosts_persona_editor(prod_client: TestClient) -> None:
    """``/roles`` 是**页面**（不是 API）：必须回 HTML，且带人设编辑器的落脚点。

    为什么单独钉一条：``/api/v1/roles`` 与 ``/roles`` 只差前缀，装配时最容易把
    页面路由和 API 路由接错位（页面 404 或 API 被页面处理器抢走）。这里断言
    content-type 与关键自定义元素，装配错了就会红。
    """
    response = prod_client.get("/roles")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    html = response.text
    for marker in ("role-picker", "persona-editor", "人设", "编译预览", "恢复默认人设"):
        assert marker in html, f"/roles 页面缺少 {marker}"


def test_pages_and_static_are_served(prod_client: TestClient) -> None:
    for page in PAGES:
        response = prod_client.get(page)
        assert response.status_code == 200, page
        assert response.headers["content-type"].startswith("text/html")

    css = prod_client.get("/static/styles.css")
    assert css.status_code == 200 and "css" in css.headers["content-type"]


def test_app_stylesheet_can_load_its_design_tokens(prod_client: TestClient) -> None:
    stylesheet = prod_client.get("/static/styles.css").text
    imports = re.findall(r'@import\s+url\(["\']([^"\']+)["\']\)', stylesheet)
    assert imports, "应用样式必须引用统一设计令牌"
    for url in imports:
        response = prod_client.get(url)
        assert response.status_code == 200, f"样式导入未挂载：{url}"
        assert response.headers["content-type"].startswith("text/css")
        assert "--surface" in response.text
        resources = re.findall(r'url\(["\']?(/[^)"\']+)["\']?\)', response.text)
        for resource in resources:
            asset = prod_client.get(resource)
            assert asset.status_code == 200, f"样式资源缺失：{resource}"
            if resource.endswith(".woff2"):
                assert asset.headers["content-type"] == "font/woff2"


def test_editor_loads_font_definitions_once(prod_client: TestClient) -> None:
    html = prod_client.get("/editor").text
    stylesheets = re.findall(r'href=["\']([^"\']+\.css)["\']', html)
    assert stylesheets, "编排器必须加载应用样式"
    pattern = r'@import\s+url\(["\']/design/design-tokens\.css["\']\)'
    token_imports = sum(len(re.findall(pattern, prod_client.get(url).text)) for url in stylesheets)
    assert token_imports == 1, "编辑器附加样式不能再次导入全局字体定义"


# ------------------------------------------------------------------ 错误信封
def test_unknown_route_uses_envelope(prod_client: TestClient) -> None:
    response = prod_client.get("/api/v1/does-not-exist")
    assert response.status_code == 404
    body = response.json()
    assert body["code"] == 40400 and body["data"] is None and body["message"]


def test_validation_error_is_readable_envelope(prod_client: TestClient) -> None:
    response = prod_client.post("/api/v1/actions/play", json={"role_id": 1})
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == 42201
    assert "request_id" in body["message"], "错误描述必须点出缺失字段，便于前端定位"
    assert body["data"] is None


def test_illegal_enum_is_rejected_with_envelope(prod_client: TestClient) -> None:
    response = prod_client.patch("/api/v1/settings", json={
        "request_id": "r1", "volume_level": "deafening"})
    assert response.status_code == 422
    assert response.json()["code"] == 42201


def test_illegal_path_param_is_rejected(prod_client: TestClient) -> None:
    response = prod_client.delete("/api/v1/memories/not-an-int")
    assert response.status_code == 422
    assert response.json()["code"] == 42201


# ------------------------------------------------------------------ 档位隔离
def test_dev_endpoints_absent_in_production(prod_client: TestClient) -> None:
    for path in ("/api/v1/dev/undock", "/api/v1/dev/button", "/api/v1/dev/pump"):
        response = prod_client.post(path)
        assert response.status_code == 404, path
        assert response.json()["code"] == 40400


def test_dev_endpoints_present_in_sim(sim_client: TestClient) -> None:
    response = sim_client.post("/api/v1/dev/undock")
    assert response.status_code == 200
    assert response.json()["data"]["state"] == "FAULT_LOCKED"


# ------------------------------------------------------------------ 契约对齐
def test_api_paths_match_openapi_contract(prod_client: TestClient) -> None:
    """前后端唯一契约：实现的路由集合必须与 openapi.yaml 的 paths 完全一致。"""
    expected = {"/api/v1" + path for path in _openapi_paths()}
    actual = {route.path for route in prod_client.app.routes
              if getattr(route, "path", "").startswith("/api/v1")}
    only_impl = sorted(actual - expected)
    only_contract = sorted(expected - actual)
    assert actual == expected, f"仅在实现: {only_impl}；仅在契约: {only_contract}"


def test_action_files_endpoint_is_in_the_contract() -> None:
    assert "/actions/files" in _openapi_paths(), "新增只读端点必须写进 openapi.yaml"
