"""twse_openapi 離線測試。固定資料取自 2026-10-12 實際 OpenAPI 回應（交易日 2026-10-08）。python3 test_twse_openapi.py"""
import twse_openapi as T

FAIL = []


def check(name, cond, extra=""):
    if cond:
        print("✅", name)
    else:
        FAIL.append(name)
        print("❌", name, extra)


plain = lambda c: len(c) == 4 and c.isdigit() and not c.startswith("00")
ROWS = [
    {"Date": "1151008", "Code": "2330", "Name": "台積電", "TradeVolume": "23145193", "TradeValue": "59167379273",
     "OpeningPrice": "2550.00", "HighestPrice": "2575.00", "LowestPrice": "2550.00", "ClosingPrice": "2550.00", "Change": "-35.0000", "Transaction": "141976"},
    {"Date": "1151008", "Code": "2317", "Name": "鴻海", "TradeVolume": "40251153", "TradeValue": "10059109830",
     "OpeningPrice": "251.00", "HighestPrice": "251.00", "LowestPrice": "249.00", "ClosingPrice": "249.00", "Change": "-3.0000", "Transaction": "49163"},
    {"Date": "1151008", "Code": "0050", "Name": "元大台灣50", "TradeVolume": "99877876", "TradeValue": "11509275054",
     "OpeningPrice": "115.35", "HighestPrice": "115.55", "LowestPrice": "114.90", "ClosingPrice": "114.95", "Change": "-1.1000", "Transaction": "191636"},
    {"Date": "1151008", "Code": "4581", "Name": "光隆精密-KY", "TradeVolume": "300", "TradeValue": "14639",
     "OpeningPrice": "", "HighestPrice": "", "LowestPrice": "", "ClosingPrice": "", "Change": "", "Transaction": "1"},
]
out, info = T.parse_stock_day_all(ROWS, "2026-10-08", plain)
check("台積電收盤、成交金額、成交股數", out.get("2330") == {"close": 2550.0, "trading_value": 59167379273.0, "trading_volume": 23145193.0}, out.get("2330"))
check("鴻海與既有快照一致", out["2317"]["close"] == 249.0 and out["2317"]["trading_value"] == 10059109830.0)
check("ETF（00 開頭）依過濾規則排除", "0050" not in out)
check("無收盤價整筆略過（與原 www 版相同）", "4581" not in out)
check("筆數", info == {"date": "2026-10-08", "rows": 2}, info)
o2, i2 = T.parse_stock_day_all(ROWS, "2026-10-12", plain)
check("要求日期與回應日期不同 → 空結果，不誤植日期", o2 == {} and i2 == {"date_mismatch": ["2026-10-08"]}, i2)
check("空回應 → 空結果", T.parse_stock_day_all([], "2026-10-08", plain)[0] == {})

TAIEX = [{"日期": "1151008", "指數": "寶島股價指數", "收盤指數": "123.0", "漲跌": "+", "漲跌點數": "1", "漲跌百分比": "0.1"},
         {"日期": "1151008", "指數": "發行量加權股價指數", "收盤指數": "49313.44", "漲跌": "-", "漲跌點數": "492.93", "漲跌百分比": "-0.99", "特殊處理註記": ""}]
tx = T.parse_taiex(TAIEX)
check("加權指數：日期、收盤、帶正負號的漲跌點、百分比", tx == {"date": "2026-10-08", "close": 49313.44, "change_pt": -492.93, "change_pct": -0.99}, tx)
up = T.parse_taiex([{"日期": "1151007", "指數": "發行量加權股價指數", "收盤指數": "49806.37", "漲跌": "+", "漲跌點數": "100", "漲跌百分比": ""}])
check("上漲且百分比缺 → 自行計算", up["change_pt"] == 100.0 and abs(up["change_pct"] - round(100 / 49706.37 * 100, 2)) < 1e-9, up)
check("找不到加權指數 → None", T.parse_taiex([TAIEX[0]]) is None)

bad_json = '[{"Date":"1151008","Code":"2327","Name":"國巨\\*","ClosingPrice":"635.00"}]'
check("不合法跳脫字元仍可解析（國巨\\*）", T.loads_lenient(bad_json)[0]["Code"] == "2327")
check("合法 JSON 照常解析", T.loads_lenient('[{"a": "b\\\\c"}]')[0]["a"] == "b\\c")

print("\n全部通過" if not FAIL else f"\n失敗 {FAIL}")
raise SystemExit(1 if FAIL else 0)
