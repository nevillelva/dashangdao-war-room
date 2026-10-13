"""【2026-10-13】ETF 價格跳動「先確認、再標記」測試（離線）。"""
import sys
import etf_qc as QC
from etf_combo import backtest

fails = []


def check(name, cond):
    print(("PASS: " if cond else "FAIL: ") + name)
    if not cond:
        fails.append(name)


def j(prev_date, date_, prev, cur):
    return {"date": date_, "prev_date": prev_date, "prev": prev, "cur": cur,
            "pct": (cur / prev - 1) * 100, "gap_days": QC._days(prev_date, date_)}


EMPTY = QC.build_ctx()

# 1. 官方分割紀錄：0050 1拆4（真實數字：188.65→47.16 參考價，復牌收 47.57，間隔 8 天）
ctx = QC.build_ctx(splits=[{"symbol": "0050", "date": "2025-06-18", "factor": 4.000212}])
r = QC.classify("0050", j("2025-06-10", "2025-06-18", 188.65, 47.57), ctx)
check("官方分割紀錄＋價格一致 → 還原", r["kind"] == "adjust" and abs(r["factor"] - 4.000212) < 1e-9)
r = QC.classify("0050", j("2025-06-10", "2025-06-18", 188.65, 47.57), EMPTY)
check("沒有官方紀錄的停牌型跳動 → 資料待確認（不自行猜比例）", r["kind"] == "pending" and "疑似分割" in r["reason"])
check("待確認用語不含『異常』", "異常" not in r["reason"])
ctx_bad = QC.build_ctx(splits=[{"symbol": "0050", "date": "2025-06-18", "factor": 2.0}])
check("官方比例與價格對不上 → 待確認，不採用", QC.classify("0050", j("2025-06-10", "2025-06-18", 188.65, 47.57), ctx_bad)["kind"] == "pending")

# 2. 反分割：00674R 5併1（5.18→25.9，比例 0.2）
ctx = QC.build_ctx(splits=[{"symbol": "00674R", "date": "2026-04-22", "factor": 0.2}])
r = QC.classify("00674R", j("2026-04-14", "2026-04-22", 5.18, 25.9), ctx)
check("反分割（比例<1）還原", r["kind"] == "adjust" and abs(r["factor"] - 0.2) < 1e-9)

# 3. 容許復牌首日波動：00663L 官方參考價 24.3，復牌收 27.57（+13.5%）仍在容許內
ctx = QC.build_ctx(splits=[{"symbol": "00663L", "date": "2025-06-11", "factor": 7.002058}])
check("復牌首日價格仍有正常波動（+13.5%）→ 仍採用官方比例",
      QC.classify("00663L", j("2025-06-03", "2025-06-11", 170.15, 27.57), ctx)["kind"] == "adjust")

# 4. 人工查證結果
qc = [{"symbol": "00631L", "jump_date": "2026-03-31", "status": "confirmed_split", "factor": 22, "source_url": "https://x", "evidence": "e"},
      {"symbol": "00887", "jump_date": "2024-10-07", "status": "confirmed_real_move", "factor": None, "source_url": "https://y", "evidence": "e"},
      {"symbol": "00999", "jump_date": "2025-01-10", "status": "pending", "factor": None, "source_url": None, "evidence": "只找到標題，內文讀不到"}]
ctx = QC.build_ctx(qc_rows=qc)
r = QC.classify("00631L", j("2026-03-24", "2026-03-31", 443.15, 19.26), ctx)
check("人工查證的分割比例 22 採用（官方紀錄沒有這筆）", r["kind"] == "adjust" and r["factor"] == 22 and r["source"] == "https://x")
check("人工查證為真實行情 → 可用不調整", QC.classify("00887", j("2024-10-04", "2024-10-07", 17.95, 38.83), ctx)["kind"] == "ok")
r = QC.classify("00999", j("2025-01-09", "2025-01-10", 10.0, 12.0), ctx)
check("人工查證標記為尚無結論 → 仍待確認並帶出原因", r["kind"] == "pending" and "內文讀不到" in r["reason"])
qc_wrong = [{"symbol": "00631L", "jump_date": "2026-03-31", "status": "confirmed_split", "factor": 3, "source_url": "https://x", "evidence": "e"}]
check("人工登錄的比例與價格對不上 → 待確認（防登錄錯誤）",
      QC.classify("00631L", j("2026-03-24", "2026-03-31", 443.15, 19.26), QC.build_ctx(qc_rows=qc_wrong))["kind"] == "pending")

# 5. 除息：00901 2025-11-25 前收 26.26 配 2.6 → 扣息 23.66，收 23.24
ctx = QC.build_ctx(divs={"00901": [("2025-11-25", 2.6)]})
r = QC.classify("00901", j("2025-11-24", "2025-11-25", 26.26, 23.24), ctx)
check("除息日造成的 -11.5% → 有依據可用", r["kind"] == "ok" and r["status"] == "ex_dividend")
check("沒有除息紀錄的同樣跳動（國內型）→ 待確認", QC.classify("00901", j("2025-11-24", "2025-11-25", 26.26, 23.24), EMPTY)["kind"] == "pending")
ctx = QC.build_ctx(divs={"00901": [("2025-11-25", 0.1)]})
check("除息金額太小解釋不了的跳動 → 仍待確認", QC.classify("00901", j("2025-11-24", "2025-11-25", 26.26, 23.24), ctx)["kind"] == "pending")

# 6. 同日市場同步：2025-04-07 多檔同跌
jumps = [{"symbol": f"E{i}", "prev_date": "2025-04-02", "jump_date": "2025-04-07", "prev_close": 20.0, "close": 17.0} for i in range(5)]
ctx = QC.build_ctx(jump_rows=jumps)
r = QC.classify("E0", j("2025-04-02", "2025-04-07", 20.0, 17.0), ctx)
check("同日 4 檔其他 ETF 同方向 → 市場同步，有依據", r["kind"] == "ok" and r["status"] == "market_wide")
check("同日同方向但只有 1 檔 → 不算市場同步", QC.classify("E0", j("2025-04-02", "2025-04-07", 20.0, 17.0),
      QC.build_ctx(jump_rows=jumps[:2]))["kind"] == "pending")
r = QC.classify("E0", j("2025-04-07", "2025-04-08", 20.0, 11.0), QC.build_ctx(jump_rows=[dict(jumps[0], jump_date="2025-04-08", prev_close=20, close=11)] + [dict(jumps[i], jump_date="2025-04-08", prev_close=20, close=11) for i in range(1, 5)]))
check("市場同步也有幅度上限（-45% 不當市場行情）", r["kind"] == "pending")

# 7. 無漲跌幅限制的商品：真實行情
ctx = QC.build_ctx(wide=["00715L"])
r = QC.classify("00715L", j("2026-03-06", "2026-03-09", 38.16, 61.95), ctx)
check("槓反／國外成分的大波動（+62%）→ 真實行情，可用", r["kind"] == "ok" and r["status"] == "real_move_wide")
check("同樣的 +62% 國內型 → 待確認", QC.classify("00700", j("2026-03-06", "2026-03-09", 38.16, 61.95), ctx)["kind"] == "pending")
check("無限制商品也有上限（單日 5 倍）→ 待確認", QC.classify("00715L", j("2026-03-06", "2026-03-09", 10.0, 50.0), ctx)["kind"] == "pending")
check("單日價格剩 10% 且無停牌 → 待確認（可能是資料錯誤）", QC.classify("00715L", j("2026-03-06", "2026-03-09", 10.0, 1.0), ctx)["kind"] == "pending")

# 7b. 國外成分由持股資料判斷
rows_h = [{"symbol": "00783", "n": 475, "with_code": 0, "tw_share": 0.0}, {"symbol": "0050", "n": 50, "with_code": 50, "tw_share": 1.0},
          {"symbol": "00895", "n": 30, "with_code": 3, "tw_share": 0.1}, {"symbol": "XNEW", "n": 4, "with_code": 0, "tw_share": 0.0},
          {"symbol": "BAD", "n": None, "with_code": None, "tw_share": None}]
fs = QC.foreign_from_rows(rows_h)
check("持股資料判斷國外成分：台股代號占比低且持股夠多才算", fs == {"00783", "00895"})
ctx = QC.build_ctx(wide=fs)
r = QC.classify("00783", j("2024-02-05", "2024-02-15", 15.18, 17.38), ctx)
check("國外成分 ETF 春節後 +14.5% → 真實行情，可用", r["kind"] == "ok" and r["status"] == "real_move_wide")
check("同樣 +14.5% 但持股是台股 → 待確認", QC.classify("0050", j("2024-02-05", "2024-02-15", 15.18, 17.38), ctx)["kind"] == "pending")
r = QC.classify("00643K", j("2021-11-23", "2024-09-23", 4.49, 2.32), QC.build_ctx(wide=["00643K"]))
check("相隔上千天的跳動是資料缺漏（不是分割、不是真實行情）→ 待確認並說明", r["kind"] == "pending" and "資料缺漏" in r["reason"])

# 8. 還原計算
rows = [("2025-06-02", 200.0), ("2025-06-09", 190.0), ("2025-06-18", 47.5), ("2025-06-19", 48.0)]
adj = QC.apply_adjustments(rows, {"2025-06-18": 4.0})
check("復牌日之前除以比例、之後不動", abs(adj[0][1] - 50.0) < 1e-9 and abs(adj[1][1] - 47.5) < 1e-9 and adj[2][1] == 47.5 and adj[3][1] == 48.0)
adj2 = QC.apply_adjustments([("a1", 100.0), ("2025-02-01", 100.0), ("2025-03-01", 25.0), ("2025-04-01", 25.0), ("2025-05-01", 100.0)],
                            {"2025-03-01": 4.0, "2025-05-01": 0.25})
check("多次分割／反分割連乘還原（全部接成同一條線）", [round(c, 6) for _, c in adj2] == [100.0] * 5)

# 9. 掃描整條序列
seq = [("2025-06-09", 190.0), ("2025-06-10", 188.65), ("2025-06-18", 47.57), ("2025-06-19", 47.9)]
sc = QC.scan_series("0050", seq, QC.build_ctx(splits=[{"symbol": "0050", "date": "2025-06-18", "factor": 4.000212}]))
check("scan_series：有官方比例 → 無待確認、記下還原", not sc["pending"] and "2025-06-18" in sc["adjust"])
sc = QC.scan_series("0050", seq, EMPTY)
check("scan_series：沒有依據 → 列為待確認", len(sc["pending"]) == 1)

# 10. 選擇清單用
jr = [{"symbol": "0050", "prev_date": "2025-06-10", "jump_date": "2025-06-18", "prev_close": 188.65, "close": 47.57},
      {"symbol": "00700", "prev_date": "2026-03-06", "jump_date": "2026-03-09", "prev_close": 38.16, "close": 61.95}]
ctx = QC.build_ctx(jr, splits=[{"symbol": "0050", "date": "2025-06-18", "factor": 4.000212}])
pb = QC.pending_by_symbol(jr, ctx)
check("pending_by_symbol：只列仍待確認的檔", list(pb) == ["00700"])


# 11. 回測整合
def daily(n, start="2024-01-01", p0=100.0, g=1.001, step=1):
    from datetime import date, timedelta
    out, d, p = [], date.fromisoformat(start), p0
    for _ in range(n):
        while d.weekday() >= 5:
            d += timedelta(days=1)
        out.append((d.isoformat(), p))
        p *= g
        d += timedelta(days=step)
    return out


base = daily(80)
split = [(d, c if i < 40 else c / 4.0) for i, (d, c) in enumerate(base)]
split_date = split[40][0]
r = backtest({"A": split}, {"A": 100})
check("回測：未查證的停牌型大跳動 → 資料待確認、不計算", (not r["ok"]) and r.get("pending") and "資料待確認" in r["message"] and "異常" not in r["message"])
r = backtest({"A": split}, {"A": 100}, qc_ctx=QC.build_ctx(qc_rows=[
    {"symbol": "A", "jump_date": split_date, "status": "confirmed_split", "factor": 4, "source_url": "https://x", "evidence": "e"}]))
check("回測：人工查證登錄的分割（比例與價格一致）→ 採用並還原", r["ok"] and r["adjustments"]["A"][0]["factor"] == 4)
# 帶停牌空檔（8 天）的真分割
from datetime import date, timedelta
seq = daily(80)
head = seq[:40]
last = date.fromisoformat(head[-1][0])
tail = []
d = last + timedelta(days=8)
for i in range(40):
    while d.weekday() >= 5:
        d += timedelta(days=1)
    tail.append((d.isoformat(), seq[40 + i][1] / 4.0))
    d += timedelta(days=1)
real_split = head + tail
rd = tail[0][0]
r = backtest({"S": real_split}, {"S": 100})
check("回測：停牌後價格變 1/4 且沒有依據 → 待確認", (not r["ok"]) and r.get("pending") and r["symbol"] == "S")
ctxS = QC.build_ctx(splits=[{"symbol": "S", "date": rd, "factor": 4.0}])
r = backtest({"S": real_split}, {"S": 100}, qc_ctx=ctxS)
check("回測：有官方分割紀錄 → 還原後算出結果", r["ok"] and r["adjustments"]["S"][0]["factor"] == 4.0)
check("回測：還原後不再有人為的暴跌（最大回撤接近 0，標的每日 +0.1%）", r["ok"] and r["mdd_pct"] < 1.0)
ok_series = daily(120)
check("回測：沒有大跳動的資料照常計算", backtest({"N": ok_series}, {"N": 100})["ok"])

if fails:
    print(f"\n{len(fails)} 項失敗")
    sys.exit(1)
print("\n全部通過")
