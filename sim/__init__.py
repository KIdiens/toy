"""MCU 模拟器（复刻 firmware/motion_controller/motion_controller.ino 的协议语义）。

设计立场（见 docs/architecture/20-mcu-simulator.md）：模拟器是**设备模型**，
不是 happy-path 假回答器；故障路径是一等公民。

本包刻意**不 import host.***：模拟器属基础设施层，反向依赖业务层会破坏
「依赖只能向下」的门禁。时钟是最小鸭子类型（只要求 now_ms()）。
"""

__all__ = ["clock", "controller", "devices", "scenario", "tcp_bridge", "wire"]
