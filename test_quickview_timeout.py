"""【2026-10-15】戰情速覽：有一檔卡住時，整批逾時後放棄該檔、其餘照常顯示，不再整頁卡死。"""
import concurrent.futures
import sys
import textwrap
import time
import pandas as pd

SRC = open("dashangdao.py", encoding="utf-8").read()
a = SRC.index("            _QO_TOTAL_TIMEOUT = 60")
b = SRC.index("            finally:\n                executor.shutdown(wait=False, cancel_futures=True)") + len("            finally:\n                executor.shutdown(wait=False, cancel_futures=True)")
BLOCK = textwrap.dedent(SRC[a:b]).replace("_QO_TOTAL_TIMEOUT = 60", "_QO_TOTAL_TIMEOUT = 2")


class _Prog:
    def progress(self, *a, **k): pass


class _Ph:
    def dataframe(self, *a, **k): pass


class _St:
    warnings = []
    def warning(self, m): self.warnings.append(m)


def worker(code, cfg, ctx):
    if code == "HANG":
        time.sleep(4)
    return {"signal_text": "偏多攻擊", "price": 10, "gain": 1, "score": 5}


SAVED = {}
def save_warcard_quickview_cache(m, d):
    SAVED.update(m)


ns = dict(time=time, save_warcard_quickview_cache=save_warcard_quickview_cache, _qo_today_str="20261015", concurrent=concurrent, calculate_signals_worker=worker, codes_to_compute=["A", "B", "HANG"],
          _qo_config={}, _qo_ctx=None, _qo_prog=_Prog(), results={}, TW_STOCK_NAMES={}, pd=pd, st=_St(),
          _qo_partial_placeholder=_Ph(), _qo_done=0, _qo_fail_count=0, _qo_last_err="")
t0 = time.time()
exec(BLOCK, ns)
dt = time.time() - t0
fails = []
def check(n, c):
    print(("PASS: " if c else "FAIL: ") + n)
    if not c: fails.append(n)
check(f"卡住的那檔不拖住整批（{dt:.1f}s 內回來，不是 4s 以上）", dt < 3.5)
check("正常的兩檔結果保留", set(ns["results"]) == {"A", "B"})
check("卡住那檔記為失敗並說明", ns["_qo_fail_count"] == 1 and "HANG" in ns["_qo_last_err"])
check("畫面有提示略過了哪一檔", any("HANG" in w for w in ns["st"].warnings))
time.sleep(3)
check("逾時那檔背景算完後仍寫入持久化快取（下次免重算）", "HANG" in SAVED)
check("正常檔不重複寫（由主流程批次寫）", "A" not in SAVED and "B" not in SAVED)
src_ok = "_QO_TOTAL_TIMEOUT = 60" in SRC and "max_workers=12" in SRC
check("正式碼：整批上限 60 秒、並行 12", src_ok)
if fails:
    print(f"\n{len(fails)} 項失敗"); sys.exit(1)
print("\n全部通過")
