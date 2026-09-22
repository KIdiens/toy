"""共享测试夹具。"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.support import FROZEN_SOFTWARE, TOY_ROOT, load_wave


@pytest.fixture()
def toy_root() -> Path:
    return TOY_ROOT


@pytest.fixture()
def frozen_software() -> Path:
    return FROZEN_SOFTWARE


@pytest.fixture()
def wave_action() -> dict:
    return load_wave()


@pytest.fixture()
def temp_db(tmp_path: Path):
    """一个已迁移到最新版本的临时 SQLite 门面（用例结束关闭全部连接）。"""
    from host.storage.database import Database

    database = Database(tmp_path / "toy.sqlite3")
    database.initialize()
    yield database
    database.close_all()


@pytest.fixture()
def sim_context(tmp_path: Path):
    """无硬件档位的完整运行期上下文（进程内 MCU 模拟器 + 独立临时库）。

    **不启动 worker 线程**：需要运动执行的用例显式调用 ``pump_once`` / ``drain``。
    """
    from host.adapters.runtime import build_context
    from host.config.settings import load_settings

    settings = load_settings({"DB_PATH": str(tmp_path / "toy.sqlite3")})
    context = build_context(settings, simulate=True, root=TOY_ROOT)
    yield context
    context.close()


