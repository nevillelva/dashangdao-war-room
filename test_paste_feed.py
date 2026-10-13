"""【2026-10-13】網頁貼上表格解析測試（離線）。"""
import sys
from datetime import date
from paste_feed import parse_pasted

fails = []
TODAY = date(2026, 10, 13)


def check(name, cond):
    print(("PASS: " if cond else "FAIL: ") + name)
    if not cond:
        fails.append(name)


good = "代號\t日期\t收盤\t外資買超\n2330\t2026-10-12\t1,020\t-1,500\n2317\t2026/10/12\t210.5\t3%\n"
r = parse_pasted(good, today=TODAY)
check("正常 Tab 表格通過", r["ok"] and len(r["rows"]) == 2)
check("千分位與百分比被正規化", r["rows"][0]["收盤"] == 1020.0 and r["rows"][1]["外資買超"] == 3.0)
check("日期統一為 ISO", r["rows"][1]["trade_date"] == "2026-10-12")
check("產生 content_hash", isinstance(r["content_hash"], str) and len(r["content_hash"]) == 64)

same = "股票代號,日期,收盤\n2330,20261012,1000\n"
r2 = parse_pasted(same, today=TODAY)
check("逗號分隔＋YYYYMMDD 通過", r2["ok"] and r2["rows"][0]["trade_date"] == "2026-10-12")

r3 = parse_pasted("代號\t收盤\n2330\t1000\n", today=TODAY)
check("無日期欄也可通過", r3["ok"] and "trade_date" not in r3["rows"][0])

check("空白內容退回", not parse_pasted("   ", today=TODAY)["ok"])
check("無代號欄退回", not parse_pasted("名稱\t收盤\n台積電\t1000\n", today=TODAY)["ok"])
check("無分隔符退回", not parse_pasted("代號 收盤\n2330 1000\n", today=TODAY)["ok"])
check("只有表頭退回", not parse_pasted("代號\t收盤\n", today=TODAY)["ok"])

bad_sym = "代號\t收盤\n2330\t1000\nABC\t900\n"
r4 = parse_pasted(bad_sym, today=TODAY)
check("代號格式錯誤整份不寫入", not r4["ok"] and any("代號格式錯誤" in e for e in r4["errors"]))

bad_date = "代號\t日期\t收盤\n2330\t2026-02-30\t1000\n"
check("不存在的日期（2/30）退回", not parse_pasted(bad_date, today=TODAY)["ok"])

future = "代號\t日期\t收盤\n2330\t2030-01-01\t1000\n"
check("未來日期超出範圍退回", not parse_pasted(future, today=TODAY)["ok"])

old = "代號\t日期\t收盤\n2330\t1990-01-01\t1000\n"
check("早於 2000 年退回", not parse_pasted(old, today=TODAY)["ok"])

mismatch = "代號\t日期\t收盤\n2330\t2026-10-12\n"
check("欄數不一致退回", not parse_pasted(mismatch, today=TODAY)["ok"])

dup = "代號\t日期\t收盤\n2330\t2026-10-12\t1000\n2330\t2026-10-12\t1001\n"
r5 = parse_pasted(dup, today=TODAY)
check("同代號同日重複退回", not r5["ok"] and any("重複" in e for e in r5["errors"]))

hash_a = parse_pasted(good, today=TODAY)["content_hash"]
hash_b = parse_pasted(good.replace("\t", "\t"), today=TODAY)["content_hash"]
check("相同內容 hash 一致（防重複寫入）", hash_a == hash_b)
check("內容不同 hash 不同", hash_a != parse_pasted(good.replace("1,020", "1,021"), today=TODAY)["content_hash"])

check("ETF 代號（含結尾字母）通過", parse_pasted("代號\t收盤\n00878\t20\n00981A\t15\n", today=TODAY)["ok"])
check("空白列會被略過", len(parse_pasted("代號\t收盤\n2330\t1000\n\n\n2317\t200\n", today=TODAY)["rows"]) == 2)
check("非數字欄保留原文並計數", parse_pasted("代號\t備註\n2330\t看好\n", today=TODAY)["non_numeric"] == 1)

if fails:
    print(f"\n{len(fails)} 項失敗")
    sys.exit(1)
print("\n全部通過")
