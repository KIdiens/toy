"""P0：畸形动作文件不得让 AttributeError 逃逸成「永久卡 PERFORMING」。

缺陷链（真实进程复现）：
1. ``validate()`` 直接 ``frame.get(...)`` —— 帧不是对象时抛 ``AttributeError``；
2. ``ports.validate_action`` 只 catch ``ValueError/TypeError``，于是异常穿出
   ``POST /actions/validate`` 与 ``POST /actions/play``（都成 500）；
3. 表演键路径 ``pump_once -> _resolve_greet`` 裸调，异常打穿 worker 循环
   （``worker_faults += 1``），``PERFORM_DONE`` 永不产生 → 永久卡 PERFORMING；
4. 可达性：``POST /content/import`` 只校验 ``role.json``，畸形动作文件能随角色包
   一起落盘。

三层修法各有一条测试钉住：形状校验（层 1）、pump_once 兜底（层 2）、
以及入口的「动作 role_id 必须等于激活角色」（P2-1）。
"""
from __future__ import annotations

import asyncio
import copy
import io
import json
import shutil
import threading
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from host.adapters.runtime import build_context
from host.config.settings import load_settings
from host.core.errors import ConflictError
from host.core.events import Command, CommandKind, EventKind
from host.core.state_machine import State
from host.motion.validation import validate
from host.web.app import create_app
from host.web.routers.content import import_content
from starlette.datastructures import Headers, UploadFile

from tests.support import TOY_ROOT, load_wave

SETTLE_TIMEOUT_S = 10.0


# ---------------------------------------------------------------- 形状语料
def _broken(mutate) -> dict:
    action = copy.deepcopy(load_wave())
    mutate(action)
    return action


def _replace_frame(action, frame) -> None:
    action["frames"][1] = frame


MALFORMED_SHAPES = {
    # 用户报告的原样输入
    "frames_are_ints": lambda a: _replace_frame(a, 7),
    "frame_is_string": lambda a: _replace_frame(a, "frame"),
    "frame_is_null": lambda a: _replace_frame(a, None),
    "frame_missing_us": lambda a: a["frames"][1].pop("us"),
    "us_not_a_list": lambda a: a["frames"][1].__setitem__("us", 1500),
    "us_length_3": lambda a: a["frames"][1].__setitem__("us", [1500, 1500, 1500]),
    "us_contains_string": lambda a: a["frames"][1]["us"].__setitem__(0, "1500"),
}


@pytest.mark.parametrize("label,mutate", MALFORMED_SHAPES.items(),
                         ids=list(MALFORMED_SHAPES))
def test_validate_rejects_malformed_shapes_with_value_error(label, mutate) -> None:
    """形状错误必须是 ``ValueError``（不是 ``AttributeError``）。"""
    action = _broken(mutate)
    with pytest.raises(ValueError) as excinfo:
        validate(action)
    assert not isinstance(excinfo.value, AttributeError)
    assert str(excinfo.value), f"{label}: 拒绝理由不能是空字符串"


@pytest.mark.parametrize("bad", [[1, 2], None, 42, "action"],
                         ids=["list", "none", "int", "str"])
def test_validate_rejects_non_mapping_action_with_value_error(bad) -> None:
    """顶层不是对象：同样必须是 ``ValueError``，且消息说明「期望对象」。"""
    with pytest.raises(ValueError) as excinfo:
        validate(bad)
    assert "对象" in str(excinfo.value), str(excinfo.value)


def test_frame_error_message_points_at_the_offending_index() -> None:
    """消息要能定位到**第几帧**，否则内容作者无从修。"""
    action = _broken(lambda a: _replace_frame(a, 7))
    with pytest.raises(ValueError) as excinfo:
        validate(action)
    assert "第 2 帧" in str(excinfo.value), str(excinfo.value)


def test_valid_actions_are_untouched(tmp_path: Path) -> None:
    """对照组：三个角色的合法动作零影响（形状校验不得误伤）。"""
    for role_id in (1, 2, 3):
        context = _context(tmp_path, f"ok{role_id}.sqlite3")
        try:
            for action_id in context.roles.actions_for(role_id):
                action = json.loads(
                    context.roles.action_path(role_id, action_id).read_text("utf-8"))
                validate(action)  # 不抛
        finally:
            context.close()


# ---------------------------------------------------------------- 上下文 / 装置
def _context(tmp_path: Path, name: str = "shape.sqlite3", *, root: Path | None = None):
    settings = load_settings({"DB_PATH": str(tmp_path / name)})
    return build_context(settings, simulate=True, root=root or TOY_ROOT)


def _isolated_root(tmp_path: Path, name: str) -> Path:
    root = tmp_path / name
    shutil.copytree(TOY_ROOT / "content", root / "content")
    return root


def _await_state(context, target: str, timeout_s: float = SETTLE_TIMEOUT_S) -> str:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if context.state_machine.state.value == target:
            return target
        time.sleep(0.02)
    return context.state_machine.state.value


class _Worker:
    """真实 motion worker（``run_forever``）；``close`` 里一定 join，绝不留后台线程。"""

    def __init__(self, context) -> None:
        self.context = context
        self.stop = threading.Event()
        self.thread = None

    def __enter__(self):
        self.context.worker_running = True
        self.context.start_workers()
        self.thread = threading.Thread(
            target=self.context.run_forever, args=(self.stop,), daemon=True,
            name="test-shape-worker")
        self.thread.start()
        time.sleep(0.2)
        return self.context

    def __exit__(self, *exc) -> None:
        self.stop.set()
        if self.thread is not None:
            self.thread.join(timeout=3.0)
        self.context.close()


# ---------------------------------------------------------------- 入口层
def test_validate_action_returns_verdict_instead_of_raising(tmp_path: Path) -> None:
    """``AppContext.validate_action`` 面对畸形输入必须返回 ``valid=False``。"""
    context = _context(tmp_path, "verdict.sqlite3")
    try:
        for label, mutate in MALFORMED_SHAPES.items():
            verdict = context.validate_action(_broken(mutate))
            assert verdict["valid"] is False, f"{label}: {verdict}"
            assert verdict["errors"], f"{label}: 没给出理由"
        assert context.validate_action([1, 2])["valid"] is False
    finally:
        context.close()


def test_http_validate_endpoint_does_not_500(tmp_path: Path) -> None:
    """``POST /actions/validate`` 对畸形输入不得 500。"""
    context = _context(tmp_path, "http.sqlite3")
    try:
        client = TestClient(create_app(context), raise_server_exceptions=False)
        response = client.post("/api/v1/actions/validate", json={
            "request_id": "v1",
            "action": {"schema": 1, "role_id": 1, "frames": [0, 1]},
        })
        assert response.status_code != 500, response.text
        body = response.json()
        assert body["data"]["valid"] is False, body
    finally:
        context.close()


# ---------------------------------------------------------------- 兜底层
def test_pump_once_survives_greet_resolution_exception(tmp_path: Path) -> None:
    """``_resolve_greet`` 抛任何异常都必须被接住：投 PERFORM_DONE、不打死 worker。

    判别力：去掉 pump_once 的 try/except，本测试里 ``pump_once`` 会直接抛
    ``RuntimeError``，PERFORM_DONE 永不产生。
    """
    context = _context(tmp_path, "resolver.sqlite3")
    events: list[EventKind] = []
    original_publish = context.bus.publish

    def record(event):
        events.append(event.kind)
        return original_publish(event)

    context.bus.publish = record

    def boom():
        raise RuntimeError("解析器炸了（模拟未预期缺陷）")

    context.resolve_greet_action = boom
    try:
        # 上电会在 work 队列里留下 NOTIFY 等命令：不排空的话 pump_once 消费的是它们，
        # 本条会「什么都没验到」地通过（假绿）。
        context.drain()
        assert context.work.qsize() == 0
        context._router.put(Command(
            kind=CommandKind.PLAY_ACTION,
            payload={"perform_intent": "greet"},
            generation=context.state_machine.generation))
        assert context.pump_once() is True
        assert EventKind.PERFORM_DONE in events, (
            f"解析异常后没有收尾事件，状态机会卡在 PERFORMING：{events}")
        assert context.state_machine.state is State.IDLE
    finally:
        context.close()


# ---------------------------------------------------------------- 端到端
def _pack_root(tmp_path: Path) -> tuple[Path, Path]:
    root = _isolated_root(tmp_path, "root")
    role_json = json.loads(
        (TOY_ROOT / "content" / "roles" / "2" / "role.json").read_text("utf-8"))
    role_json["config_version"] = "9.9.9"
    pack = tmp_path / "pack.zip"
    with zipfile.ZipFile(pack, "w") as zf:
        zf.writestr("role.json", json.dumps(role_json, ensure_ascii=False))
        zf.writestr("actions/greet.json", json.dumps({
            "schema": 1, "role_id": 2,
            "frames": [{"t_ms": 0, "us": [1500, 1500, 1500, 1500]}, 7],
        }))
    return root, pack


def _upload(pack: Path) -> UploadFile:
    return UploadFile(file=io.BytesIO(pack.read_bytes()), filename="pack.zip",
                      headers=Headers({"content-type": "application/zip"}))


def test_malformed_action_can_be_imported_and_must_not_stick(tmp_path: Path) -> None:
    """端到端：畸形动作经导入落盘 → 切角色 → 按表演键 → **必须回 IDLE**。

    判别力：回退 ``validate`` 的类型检查 → 这里 ``AttributeError`` 打穿 worker，
    状态永久停在 PERFORMING，本测试转红。
    """
    root, pack = _pack_root(tmp_path)
    context = _context(tmp_path, "e2e.sqlite3", root=root)
    try:
        result = asyncio.run(import_content(context, request_id="e2e", file=_upload(pack)))
        assert result["code"] == 0, result
        assert result["data"]["role_id"] == 2

        with _Worker(context):
            context.switch_active_role(2)
            context.sim.role_res.role = 2

            # 判别力关键：畸形**内容**必须被判定成「内容非法」（返回 None），
            # 而不是把校验层的异常抛出来靠 worker 兜底。少了 validate 的形状检查，
            # 这一行会直接抛 AttributeError → 本测试转红。
            # 这条同时钉住「内容错误」与「内部异常」的分类边界：前者是 warning +
            # 本轮无动作，后者才是 error（会被 pump_once 的兜底记录）。
            assert context.resolve_greet_action() is None, (
                "畸形动作没有被判定为内容非法（resolve 仍抛异常或返回了载荷）")

            context.press_button(2)

            final = _await_state(context, "IDLE")
            assert final == "IDLE", (
                f"畸形动作让设备卡在 {final}（历史 "
                f"{[(r.trigger.value, r.dest.value) for r in context.state_machine.history]}）")
            assert context.fault is None, f"内容问题不该进故障锁定：{context.fault}"
            assert context.state_machine.state is not State.FAULT_LOCKED
    finally:
        context.close()


def test_legal_greet_button_round_trip_for_every_role(tmp_path: Path) -> None:
    """对照组：三个角色的合法 greet 按键仍要真播完并回 IDLE。"""
    for role_id in (1, 2, 3):
        context = _context(tmp_path, f"legal{role_id}.sqlite3")
        try:
            with _Worker(context):
                if role_id != context.roles.active_role_id:
                    context.switch_active_role(role_id)
                    context.sim.role_res.role = role_id
                    time.sleep(0.2)
                context.press_button(2)
                final = _await_state(context, "IDLE")
                assert final == "IDLE", f"role{role_id} 合法动作卡在 {final}"
                assert context.fault is None, context.fault
        finally:
            context.close()


# ---------------------------------------------------------------- 入口角色一致性
def test_action_file_role_mismatch_is_rejected_at_entry(tmp_path: Path) -> None:
    """动作自称的 role_id 与激活角色不一致：入口 409，**不得**落到 SAFETY_TRIP 锁机。

    构造：role3 目录（限幅 1430–1570）里放一个自称 role1、脉宽 1405 的 greet。
    ``validate_action`` 按动作自称的 role1 限幅（1400–1600）判它合法；``_run_play``
    再按激活的 role3 限幅复核就会判越限 → SAFETY_TRIP。必须在入口先拒绝。
    """
    root = _isolated_root(tmp_path, "mismatch")
    greet = root / "content" / "roles" / "3" / "actions" / "greet.json"
    greet.write_text(json.dumps({
        "schema": 1, "role_id": 1, "name": "冒名动作",
        "frames": [
            {"t_ms": 0, "us": [1500, 1500, 1500, 1500]},
            {"t_ms": 500, "us": [1405, 1500, 1500, 1500]},
            {"t_ms": 1000, "us": [1500, 1500, 1500, 1500]},
        ],
    }), encoding="utf-8")

    context = _context(tmp_path, "mismatch.sqlite3", root=root)
    try:
        context.switch_active_role(3)
        with pytest.raises(ConflictError) as excinfo:
            context.play_action(role_id=3, action_id="greet")
        assert "role_id" in str(excinfo.value), str(excinfo.value)
        assert context.fault is None
        assert context.state_machine.state is not State.FAULT_LOCKED
        # 按键路径没有 409 入口：必须静默降级成「本轮无动作」，同样不得锁机
        assert context.resolve_greet_action() is None
    finally:
        context.close()
