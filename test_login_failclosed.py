#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""驗證：secrets 讀不到時，登入牆必須「拒絕所有登入」(fail-closed)。需在沒有 .streamlit/secrets.toml 的環境執行。"""
import sys
sys.path.insert(0,'.')
from streamlit.testing.v1 import AppTest
at = AppTest.from_file('dashangdao.py', default_timeout=120).run()
print("exceptions:", [str(e.value)[:200] for e in at.exception])
print("errors:", [e.value[:80] for e in at.error])
print("login inputs:", len(at.text_input))
at.session_state['authenticated']  # may be False
print("auth:", at.session_state['authenticated'] if 'authenticated' in at.session_state else None)

assert at.error and "拒絕所有登入" in at.error[0].value, "應顯示拒絕登入訊息"
assert len(at.text_input) == 0, "不應出現密碼輸入框"
assert not at.session_state["authenticated"], "不應登入成功"
print("OK fail-closed")
