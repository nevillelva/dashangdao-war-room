# -*- coding: utf-8 -*-
"""
登入失敗節流的純函式（不依賴 streamlit，可直接單元測試）。

設計重點：
1. 正確密碼（總指揮官 / 唯讀訪客）在任何狀態下都放行，節流只影響錯誤嘗試。
2. 錯誤嘗試依「視窗內失敗次數」遞增延遲；延遲以「下次可嘗試時間」(next_ok_at)
   記錄，不用 time.sleep，避免佔住 Streamlit 執行緒。
3. fail-closed：commander_pin 讀不到或空白時一律拒絕（連訪客也不放行）。
4. 視窗過期後失敗次數歸零。

狀態格式：{"fails": [失敗時間戳, ...], "next_ok_at": 下次可嘗試的時間戳}
decide_login() 不會修改傳入的 state，永遠回傳新的 state。
"""
from __future__ import annotations

import math

MAX_FREE_FAILS = 2        # 視窗內前幾次錯誤不延遲
WINDOW_SEC = 600          # 計算失敗次數的視窗（10 分鐘）
DELAY_BASE_SEC = 1        # 第 MAX_FREE_FAILS+1 次錯誤的延遲秒數
DELAY_CAP_SEC = 30        # 延遲上限

DECISION_ALLOW_ADMIN = "allow_admin"
DECISION_ALLOW_VIEWER = "allow_viewer"
DECISION_DENY_CONFIG = "deny_config"
DECISION_WRONG = "wrong"


def new_state():
    """乾淨的登入失敗狀態。"""
    return {"fails": [], "next_ok_at": 0.0}


def delay_for_fail_count(n):
    """視窗內第 n 次失敗後要等的秒數：前 MAX_FREE_FAILS 次為 0，之後每次加倍，上限 DELAY_CAP_SEC。"""
    if n <= MAX_FREE_FAILS:
        return 0
    exp = min(n - MAX_FREE_FAILS - 1, 16)  # 防止指數過大；上限本來就只有 30 秒
    return min(DELAY_CAP_SEC, DELAY_BASE_SEC * (2 ** exp))


def decide_login(state, now, pin_configured, is_admin, is_viewer):
    """
    回傳 (decision, new_state, wait_sec)。

    decision:
      - DECISION_DENY_CONFIG：commander_pin 未設定，一律拒絕（fail-closed）
      - DECISION_ALLOW_ADMIN / DECISION_ALLOW_VIEWER：密碼正確，放行，清空失敗紀錄
      - DECISION_WRONG：密碼錯誤，wait_sec 為還需等待的秒數（0 表示可直接重試）
    """
    if not pin_configured:
        return DECISION_DENY_CONFIG, dict(state), 0

    if is_admin:
        return DECISION_ALLOW_ADMIN, new_state(), 0
    if is_viewer:
        return DECISION_ALLOW_VIEWER, new_state(), 0

    prev_fails = [t for t in state.get("fails", []) if now - t < WINDOW_SEC]
    fails = prev_fails + [now]
    delay = delay_for_fail_count(len(fails))
    next_ok_at = max(float(state.get("next_ok_at", 0.0)), now + delay)
    wait_sec = max(0, math.ceil(next_ok_at - now))
    return DECISION_WRONG, {"fails": fails, "next_ok_at": next_ok_at}, wait_sec
