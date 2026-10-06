"""Probe 08: 依赖缺失 —— 临时屏蔽 pyserial，看 --simulate 是否仍能起。

已知项确认：runtime.py:27 顶层 `from host.motion.serial_link import SerialLink`
→ serial_link 顶层 `import serial`。缺 pyserial 时 SIM 档位也会在 import 期失败。
"""
from __future__ import annotations

import os
import subprocess
import sys

sys.path.insert(0, r"F:/AI智能底座玩具/_audit_qa3")
from probe_lib import PY, REPO, TMP, free_port  # noqa: E402

BLOCKER = '''
import sys
sys.path.insert(0, r"__REPO__")

class Blocker:
    def find_spec(self, name, path=None, target=None):
        if name == "serial" or name.startswith("serial."):
            raise ImportError("[probe] 已屏蔽依赖: " + name)
        return None

sys.meta_path.insert(0, Blocker())
sys.argv = ["host.main", "--simulate", "--port", "__PORT__", "--db", r"__DB__"]
import host.main
raise SystemExit(host.main.main())
'''

script = REPO.parent / "_audit_qa3" / "_block_serial.py"
port = free_port()
text = (BLOCKER.replace("__REPO__", str(REPO))
        .replace("__PORT__", str(port))
        .replace("__DB__", str(TMP / "deps.sqlite3")))
script.write_text(text, encoding="utf-8")

env = dict(os.environ)
env["NO_PROXY"] = "127.0.0.1,localhost"
p = subprocess.run([PY, str(script)], cwd=str(REPO), env=env,
                   capture_output=True, text=True, timeout=30)
print("rc:", p.returncode)
print("--- STDOUT ---")
print(p.stdout[-1500:])
print("--- STDERR ---")
print(p.stderr[-2200:])
