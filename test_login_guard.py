#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""驗證 login_guard.decide_login 純函式：正確密碼放行、錯誤累積、延遲遞增且有上限、視窗過期歸零、fail-closed。不需網路、不需 streamlit。"""
import sys
sys.path.insert(0, ".")
import login_guard as lg

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"✅ {name}")
    else:
        FAIL += 1
        print(f"❌ {name} {detail}")


# 1. 鎖定中（失敗很多次、next_ok_at 在未來）正確密碼仍放行
locked = {"fails": [1000.0] * 20, "next_ok_at": 1000.0 + 900}
d, s, w = lg.decide_login(locked, 1005.0, True, True, False)
check("鎖定中總指揮官正確密碼仍放行", d == lg.DECISION_ALLOW_ADMIN, f"got {d}")
check("放行後失敗紀錄清空", s == lg.new_state() and w == 0, f"state={s} wait={w}")

d, s, w = lg.decide_login(locked, 1005.0, True, False, True)
check("鎖定中唯讀訪客正確密碼仍放行", d == lg.DECISION_ALLOW_VIEWER, f"got {d}")

# 2. 錯誤嘗試會累積
st_ = lg.new_state()
counts = []
for i in range(5):
    d, st_, w = lg.decide_login(st_, 100.0 + i * 31, True, False, False)
    counts.append(len(st_["fails"]))
    assert d == lg.DECISION_WRONG
check("錯誤嘗試依序累積 1..5", counts == [1, 2, 3, 4, 5], f"got {counts}")

# 3. 延遲遞增且有上限（每次間隔 31 秒，確保前一次等待已過、都在視窗內）
expected = [0, 0, 1, 2, 4, 8, 16, 30, 30, 30, 30, 30]
st_ = lg.new_state()
waits = []
t = 0.0
for i in range(len(expected)):
    d, st_, w = lg.decide_login(st_, t, True, False, False)
    waits.append(w)
    t += 31
check("延遲序列 0,0,1,2,4,8,16,30 且上限30秒", waits == expected, f"got {waits}")
check("delay_for_fail_count 上限", lg.delay_for_fail_count(10**6) == lg.DELAY_CAP_SEC)

# 4. 等待期間錯誤會顯示剩餘秒數，且失敗紀錄不被清掉
st_ = lg.new_state()
for i in range(4):  # 第 4 次錯誤 => 延遲 2 秒
    _, st_, _ = lg.decide_login(st_, 200.0 + i * 100, True, False, False)
d, st2, w = lg.decide_login(st_, st_["next_ok_at"] - 1.0, True, False, False)
check("等待中再次錯誤仍是 wrong 且有剩餘秒數", d == lg.DECISION_WRONG and w > 0, f"d={d} w={w}")
check("等待中錯誤不清空失敗紀錄", len(st2["fails"]) == len(st_["fails"]) + 1)

# 5. 視窗過期後歸零
st_ = lg.new_state()
for i in range(6):
    _, st_, _ = lg.decide_login(st_, 300.0 + i, True, False, False)
d, st2, w = lg.decide_login(st_, 300.0 + 5 + lg.WINDOW_SEC + 1, True, False, False)
check("視窗過期後失敗次數歸零（新一次錯誤等待0秒）", w == 0 and len(st2["fails"]) == 1, f"w={w} n={len(st2['fails'])}")

# 6. fail-closed：commander_pin 空白時一律拒絕（即使 is_admin / is_viewer 為真）
d, s, w = lg.decide_login(lg.new_state(), 0.0, False, True, False)
check("commander_pin 空白時 admin 也被拒絕", d == lg.DECISION_DENY_CONFIG, f"got {d}")
d, s, w = lg.decide_login(lg.new_state(), 0.0, False, False, True)
check("commander_pin 空白時 viewer 也被拒絕", d == lg.DECISION_DENY_CONFIG, f"got {d}")
d, s, w = lg.decide_login(lg.new_state(), 0.0, False, False, False)
check("commander_pin 空白時錯誤密碼也被拒絕", d == lg.DECISION_DENY_CONFIG, f"got {d}")

# 7. decide_login 不修改傳入的 state
orig = {"fails": [1.0, 2.0], "next_ok_at": 5.0}
snapshot = {"fails": list(orig["fails"]), "next_ok_at": orig["next_ok_at"]}
lg.decide_login(orig, 10.0, True, False, False)
check("不修改傳入的 state", orig == snapshot, f"orig={orig}")

print(f"\n結果：{PASS} 通過 / {FAIL} 失敗")
if FAIL:
    sys.exit(1)
