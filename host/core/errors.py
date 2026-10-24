"""异常分类（上层统一捕获点）。

存储层异常的**唯一定义处**是 ``host.storage.errors``（下层，只 import 标准库）。
本模块只做**再导出**，以满足「异常类型唯一定义处」与「依赖只能向下」两条纪律
（Lead 裁定 A1，见 40-storage-design.md §8）。
"""
from __future__ import annotations

from host.storage.errors import (  # noqa: F401  再导出：唯一定义处在host.storage.errors
    ConstraintViolation,
    ForeignKeyViolation,
    MigrationError,
    RecordNotFound,
    StorageBusy,
    StorageError,
    UniqueViolation,
)


class HostError(RuntimeError):
    """主机侧全部受控异常的基类。

    继承 ``RuntimeError`` 以便与冻结基线 ``software/motion.py`` 的报错语义兼容
    （旧代码以 ``RuntimeError`` 表达「联锁失败 / 控制器未确认 / 状态回报超时」）。
    """


class ConfigError(HostError):
    """配置缺失/非法（环境变量、defaults.toml、端口等）。"""


class InterlockError(HostError):
    """安全联锁拒绝：未到位 / 急停未健康 / 角色不符 / 稳定期不足 / 未解锁。"""


class ProtocolError(HostError):
    """串口协议错误：``ERR`` / ``FAULT`` 行或非法响应形状。"""


class MotionTimeout(HostError):
    """运动时序超时：ARM 确认窗口、状态回报、收尾总超时。命名避免遮蔽内建 TimeoutError。"""


class MotionCancelled(HostError):
    """当前动作因停止、替换或会话失效而取消；不代表硬件故障或播放完成。"""


class ProviderError(HostError):
    """AI/网络提供方错误（含 401/403/404/429 分类）。"""


class AiCancelled(HostError):
    """当前对话已失效；停止后续提供方/工具调用，不计作工作线程故障。"""


class ToolValidationError(HostError):
    """工具调用参数非法（本地白名单二次校验拒绝）。"""


class ConflictError(HostError):
    """请求与当前状态冲突（角色不匹配 / 联锁未过 / 已在播放）。映射 HTTP 409。"""


class ClarifyError(HostError):
    """需要用户澄清（时间模糊 / 输入含糊）。映射 HTTP 422。"""


__all__ = [
    "HostError",
    "ConfigError",
    "InterlockError",
    "ProtocolError",
    "MotionTimeout",
    "MotionCancelled",
    "ProviderError",
    "AiCancelled",
    "ToolValidationError",
    "ConflictError",
    "ClarifyError",
    "StorageError",
    "StorageBusy",
    "MigrationError",
    "RecordNotFound",
    "UniqueViolation",
    "ForeignKeyViolation",
    "ConstraintViolation",
]
