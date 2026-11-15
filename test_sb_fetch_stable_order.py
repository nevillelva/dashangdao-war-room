"""【2026-11-15】分頁抓取穩定排序的靜態檢查：確認 _SB_STABLE_ORDER 內的欄位與主鍵一致。"""
import ast, sys
src = open("dashangdao_helpers.py", encoding="utf-8").read()
tree = ast.parse(src)
order = None
for node in tree.body:
    if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "_SB_STABLE_ORDER" for t in node.targets):
        order = ast.literal_eval(node.value)
PK = {"inst_holding": ("date", "symbol"), "big_holder_history": ("code", "date"), "big_holder_weekly": ("id",),
      "broker_flows": ("id",), "twse_market_snapshot": ("trade_date", "symbol"),
      "market_signal_snapshot": ("trade_date", "symbol"), "etf_price_daily": ("symbol", "trade_date"),
      "official_open_data": ("id",), "system_portfolio": ("id",), "factor_snapshot": ("id",),
      "intel_performance": ("id",), "watchlist_entry_log": ("id",)}
ok = True
def chk(n, c):
    global ok
    print(("✅ " if c else "❌ ") + n); ok = ok and c
chk("已讀取排序設定", isinstance(order, dict) and len(order) > 0)
chk("每張表的排序欄位等於主鍵", all(order.get(t) == pk for t, pk in PK.items()) and set(order) == set(PK))
chk("分頁迴圈內有套用排序", ".order(_oc)" in src)
print("全部通過" if ok else "有失敗")
sys.exit(0 if ok else 1)
