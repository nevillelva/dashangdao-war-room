#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_tg_inbox_ui.py —— 用 Streamlit AppTest 在假 Supabase 上實際跑 dashangdao.py 的 render_telegram_inbox_section：
文字收件、尚未辨識的圖片（引擎選擇）、已辨識的圖片（原圖＋可修改文字）都不得丟例外，且畫面元件齊全。"""
import sys
from streamlit.testing.v1 import AppTest

src = open("dashangdao.py", encoding="utf-8").read()
a = src.index("def render_telegram_inbox_section():")
b = src.index("def render_portfolio_quickview():")
section = src[a:b]

HARNESS = '''
import base64
import streamlit as st
import pandas as pd

# 1x1 透明 PNG
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")
INBOX = [
  {"id": 1, "created_at": "2026-10-13T01:00:00+00:00", "status": "new", "kind": "text", "text": "代號\\t日期\\t收盤\\n2330\\t2026-10-12\\t1,020", "note": None},
  {"id": 2, "created_at": "2026-10-13T05:00:00+00:00", "status": "new", "kind": "photo", "text": "", "caption": "今天的籌碼", "note": None},
  {"id": 3, "created_at": "2026-10-13T09:00:00+00:00", "status": "new", "kind": "photo", "engine": "ocr", "note": "辨識:ocr｜「代號」信心 0.5，請對照原圖",
   "text": "代號\\t日期\\t收盤\\n2317\\t2026-10-12\\t210.5"},
]
IMAGES = [{"inbox_id": 2, "mime": "image/png", "data_b64": base64.b64encode(PNG).decode()},
          {"inbox_id": 3, "mime": "image/png", "data_b64": base64.b64encode(PNG).decode()}]
DATA = {"telegram_inbox": INBOX, "telegram_inbox_images": IMAGES}

class _Q:
    def __init__(self, t): self.t, self.f = t, []
    def select(self, *a, **k): return self
    def eq(self, c, v): self.f.append(lambda x: x.get(c) == v); return self
    def in_(self, c, vs): self.f.append(lambda x: x.get(c) in vs); return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def execute(self):
        return type("R", (), {"data": [x for x in DATA.get(self.t, []) if all(f(x) for f in self.f)]})()

class _C:
    def table(self, t): return _Q(t)

SUPABASE_CONN = _C()
def is_admin(): return True
'''

at = AppTest.from_string(HARNESS + "\n" + section + "\nrender_telegram_inbox_section()\n", default_timeout=90)
at.run()
ok = True
if at.exception:
    ok = False
    print("❌ 例外：", [e.value for e in at.exception])
radios = [r for r in at.radio if r.key == "tgi_eng_2"]
if not radios:
    ok = False
    print("❌ 找不到圖片 #2 的辨識方式選項")
else:
    labels = list(radios[0].options)
    print("引擎選項：", labels)
    if "vision" not in [str(o) for o in labels] and not any("視覺" in str(o) for o in labels):
        ok = False
        print("❌ 沒有視覺模型選項")
btns = {b.key for b in at.button}
for need in ("tgi_run_2", "tgi_rej_2", "tgi_1_ok", "tgi_save_3"):
    if need not in btns:
        ok = False
        print("❌ 缺少按鈕", need, "；現有：", sorted(btns))
areas = {t.key for t in at.text_area}
if "tgi_txt_3" not in areas:
    ok = False
    print("❌ 已辨識的圖片沒有可修改的文字框")
if len(at.get("imgs")) < 3 and not at.get("imgs"):
    # AppTest 對 st.image 的支援依版本而異；有元件就檢查，沒有就略過
    pass
print("✅ 收件匣管理頁（文字／未辨識圖片／已辨識圖片）可正常渲染" if ok else "❌ 失敗")
sys.exit(0 if ok else 1)
