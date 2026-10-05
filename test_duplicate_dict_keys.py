"""check_duplicate_dict_keys 的測試 + 全專案不得有重複字典鍵 + 查3 的 value_score 不被 None 蓋掉。"""
import os
import check_duplicate_dict_keys as c

ok = 0


def check(cond, msg):
    global ok
    assert cond, msg
    ok += 1
    print("✅", msg)


# 1) 偵測：值不同
d = c.find_duplicates('x = {"a": 1, "b": 2, "a": None}')
check(len(d) == 1 and d[0][1] == "a" and d[0][3] is True, "值不同的重複鍵會被抓到並標示 differs")
# 2) 偵測：值相同
d = c.find_duplicates('x = {"a": f(1), "a": f(1)}')
check(len(d) == 1 and d[0][3] is False, "值相同的重複鍵也會被抓到")
# 3) 不誤報：**展開、不同鍵、不同字典同鍵
d = c.find_duplicates('y = {**a, "k": 1, **b, "j": 2}\nz = {"k": 1}\nw = {"k": 2}')
check(d == [], "**展開／不同字典的同名鍵不誤報")
# 4) 型別不同的鍵不算重複（1 與 "1"）
d = c.find_duplicates('x = {1: "a", "1": "b"}')
check(d == [], "整數 1 與字串 '1' 不算重複")
# 5) 全專案
here = os.path.dirname(os.path.abspath(__file__))
bad = []
for fp in c.iter_py_files([here]):
    with open(fp, encoding="utf-8") as fh:
        for ln, key, first, differs in c.find_duplicates(fh.read(), fp):
            bad.append((os.path.basename(fp), ln, key))
check(bad == [], f"全專案沒有重複字典鍵：{bad}")
# 6) compute_full_signal_for 的回傳字典不再有 "value_score": None
src = open(os.path.join(here, "system_scheduler.py"), encoding="utf-8").read()
check('"value_score": None' not in src, 'system_scheduler.py 不再有 "value_score": None 覆蓋')
print(f"\n{ok} 項通過")
