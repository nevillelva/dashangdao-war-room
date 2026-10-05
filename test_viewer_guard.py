#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""驗證 viewer(第二組密碼) 唯讀防護：UI 按鈕白名單 + Supabase 寫入代理。不需網路。"""
import sys
sys.path.insert(0, ".")
from streamlit.testing.v1 import AppTest


def app():
    import streamlit as st
    import dashangdao_helpers as H
    H.install_viewer_guards()
    role = st.session_state.get("role_for_test", "viewer")
    st.session_state["user_role"] = role

    class FakeBuilder:
        def __init__(s, name, log): s.name, s.log = name, log
        def select(s, *a, **k): return s
        def eq(s, *a, **k): return s
        def execute(s): return type("R", (), {"data": [{"ok": 1}]})()
        def insert(s, *a, **k): s.log.append(("insert", s.name)); return s
        def update(s, *a, **k): s.log.append(("update", s.name)); return s
        def upsert(s, *a, **k): s.log.append(("upsert", s.name)); return s
        def delete(s, *a, **k): s.log.append(("delete", s.name)); return s
    class FakeClient:
        def __init__(s): s.log = []
        def table(s, n): return FakeBuilder(n, s.log)
        def rpc(s, fn, *a, **k): s.log.append(("rpc", fn)); return FakeBuilder(fn, s.log)
    fc = FakeClient()
    c = H.ViewerGuardedClient(fc)
    c.table("user_state").upsert({"a": 1}).execute()          # 應被擋（viewer）
    c.table("system_config").update({"a": 1}).eq("k", "v").execute()  # 應被擋
    c.table("perf_log").insert({"a": 1}).execute()            # 白名單，應放行
    c.rpc("do_something_write")                                # 應被擋
    c.rpc("get_stats")                                         # 唯讀 rpc，放行
    rd = c.table("user_state").select("*").execute().data      # 讀取一律放行
    st.session_state["_wlog"] = list(fc.log)
    st.session_state["_read_ok"] = bool(rd)

    for lab in ["💾 儲存這組權重", "🚀 批次解析並寫入 mops_financial_snapshot", "🔍 查詢異常歷史", "🔄 計算族群輪動",
                "🤖 解鎖 NVIDIA 戰略推演", "🔄 立即補跑千張大戶", "➕加入雷達", "🧹 一次清空全部", "🚪 登出", "🔄 強制重整畫面"]:
        st.button(lab, key=lab)
    col = st.columns(2)[0]
    col.button("🗑 確認刪除", key="colbtn")
    col.button("📊 查詢深度財報", key="colbtn2")
    st.download_button("📄 下載設定檔", "x", "a.json", key="dl")
    st.file_uploader("上傳 54088_database.json", key="up")


at = AppTest.from_function(app, default_timeout=60).run()
assert not at.exception, [e.value for e in at.exception]
state = {b.key: b.disabled for b in at.button}
expect_enabled = {"🔍 查詢異常歷史", "🔄 計算族群輪動", "🚪 登出", "🔄 強制重整畫面", "colbtn2"}
for k, dis in state.items():
    if k in expect_enabled:
        assert not dis, f"唯讀白名單按鈕不該被停用：{k}"
    else:
        assert dis, f"唯讀不該可按：{k}"
wlog = at.session_state["_wlog"]
assert ("insert", "perf_log") in [tuple(x) for x in wlog], "白名單表應放行"
assert ("upsert", "user_state") not in [tuple(x) for x in wlog] and ("update", "system_config") not in [tuple(x) for x in wlog], "非白名單寫入必須被擋"
assert ("rpc", "do_something_write") not in [tuple(x) for x in wlog] and ("rpc", "get_stats") in [tuple(x) for x in wlog]
assert at.session_state["_read_ok"], "讀取不該被擋"
print("OK viewer guard：停用", sum(state[k] for k in state), "個、放行", sum(not v for v in state.values()), "個；寫入白名單/代理正確")

# 管理者不受影響
at2 = AppTest.from_function(app, default_timeout=60)
at2.session_state["role_for_test"] = "admin"
at2.run()
assert all(not b.disabled for b in at2.button), "admin 的按鈕不該被停用"
w2 = [tuple(x) for x in at2.session_state["_wlog"]]
assert ("upsert", "user_state") in w2 and ("update", "system_config") in w2, "admin 寫入不該被擋"
print("OK admin 不受影響")
