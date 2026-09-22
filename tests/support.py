"""跨测试复用的工具：定位工程根、导入冻结基线。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

TOY_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = TOY_ROOT.parent
FIXTURES = TOY_ROOT / "tests" / "fixtures"
ACTIONS = FIXTURES / "actions"
FROZEN_SOFTWARE = (
    REPO_ROOT / "AI角色玩具开发资料包" / "AI角色玩具开发书" / "software"
)


def load_wave() -> dict:
    return json.loads((ACTIONS / "wave.json").read_text(encoding="utf-8"))


def import_frozen_motion():
    """导入冻结基线 ``software/motion.py``；不可达或同名冲突时返回 None。"""
    path = FROZEN_SOFTWARE / "motion.py"
    if not path.is_file():
        return None
    if str(FROZEN_SOFTWARE) not in sys.path:
        sys.path.insert(0, str(FROZEN_SOFTWARE))
    import motion  # noqa: PLC0415  （延迟导入，便于不可达时 skip）

    if Path(motion.__file__).resolve() != path.resolve():
        return None
    return motion
