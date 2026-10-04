# -*- coding: utf-8 -*-
"""guarded_app.py —— 測試專用啟動殼：先裝上「禁止寫入」防護，再執行 dashangdao.py。
只給 ui_browser_test 使用，正式環境不會用到。"""
import runpy
import sys

import json
import ui_selftest


class _LogList(list):
    def append(self, x):
        super().append(x)
        try:
            with open("blocked_writes.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(x, ensure_ascii=False, default=str) + "\n")
        except Exception:
            pass


if not isinstance(ui_selftest.BLOCKED, _LogList):
    ui_selftest.BLOCKED = _LogList()

if not getattr(ui_selftest, "_GUARDS_ON", False):
    ui_selftest.install_write_guards()
    ui_selftest._GUARDS_ON = True
sys.modules["__main__"].__dict__.setdefault("_UI_TEST_GUARDED", True)
runpy.run_path("dashangdao.py", run_name="__main__")
