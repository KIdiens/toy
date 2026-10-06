
import sys
sys.path.insert(0, r"F:\AI智能底座玩具\toy")

class Blocker:
    def find_spec(self, name, path=None, target=None):
        if name == "serial" or name.startswith("serial."):
            raise ImportError("[probe] 已屏蔽依赖: " + name)
        return None

sys.meta_path.insert(0, Blocker())
sys.argv = ["host.main", "--simulate", "--port", "1848", "--db", r"F:\AI智能底座玩具\_audit_qa3\tmp\deps.sqlite3"]
import host.main
raise SystemExit(host.main.main())
