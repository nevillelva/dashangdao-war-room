"""【2026-10-16】官方公開資料彙整：日期轉換、各資料集正規化與驗證、同步、警示摘要。"""
import sys, datetime as dt
import official_open_data as O

fails = []
def check(n, c):
    print(("PASS: " if c else "FAIL: ") + n)
    if not c: fails.append(n)

T = dt.date(2026, 10, 16)
check("民國7碼", O.roc_to_iso("1151015") == "2026-10-15")
check("民國斜線", O.roc_to_iso("115/10/15") == "2026-10-15")
check("民國年月日", O.roc_to_iso("115年10月13日") == "2026-10-13")
check("西元8碼", O.roc_to_iso("20261015") == "2026-10-15")
check("區間取起日", O.roc_to_iso("1151016~1151022") == "2026-10-16")
check("壞日期 None", O.roc_to_iso("1151345") is None and O.roc_to_iso("") is None and O.roc_to_iso(None) is None)
check("數字解析", O.num("1,234") == 1234 and O.num("12.5%") == 12.5 and O.num("-") is None and O.num("x") is None)

# 質押：同公司多位董監彙總
pr = [{"出表日期": "1151015", "公司代號": "2330", "目前持股": "1000000", "設質股數": "400000", "設質股數佔持股比例": "40.00%", "內部人關係人設質比例": "35%", "資料年月": "11509"},
      {"出表日期": "1151015", "公司代號": "2330", "目前持股": "1000000", "設質股數": "0", "設質股數佔持股比例": "0.00%", "內部人關係人設質比例": "0%"},
      {"出表日期": "1151015", "公司代號": "2330", "目前持股": "100", "設質股數": "500"},        # 質押>持股：退回
      {"出表日期": "bad", "公司代號": "1101", "目前持股": "1", "設質股數": "0"}]
rows, rej = O.n_pledge([O.norm_row(x) for x in pr], "twse", T, "u")
check("質押彙總：20%、2人、最高個人40%", len(rows) == 1 and rows[0]["payload"]["pledge_ratio"] == 20.0 and rows[0]["payload"]["n_people"] == 2 and rows[0]["payload"]["max_person_ratio"] == 40.0)
check("質押：2列退回", rej == 2)

it = [{"出表日期": "1151014", "公司代號": "1519", "申報人身分": "董事", "姓名": "甲", "預定轉讓方式及股數-轉讓方式": "洽特定人", "預定轉讓總股數-自有持股": "1670923", "預定轉讓總股數-保留運用決定權信託股數": "0", "目前持有股數-自有持股": "14053931"},
      {"出表日期": "1151014", "公司代號": "1519", "申報人身分": "經理人", "姓名": "乙", "預定轉讓總股數-自有持股": "30000", "預定轉讓總股數-保留運用決定權信託股數": "5000"},
      {"出表日期": "1151014", "公司代號": "ABC", "預定轉讓總股數-自有持股": "1"}]
rows, rej = O.n_insider_transfer(it, "twse", T, "u")
check("轉讓彙總：合計1,705,923股、2筆", rows[0]["payload"]["total_shares"] == 1705923 and rows[0]["payload"]["n"] == 2 and rej == 1)

rows, rej = O.n_short_suspend([{"Code": "2330", "StartDate": "1151015", "EndDate": "1151020", "Reason": "分配收益"},
                               {"Code": "2317", "StartDate": "1151020", "EndDate": "1151015"}], "twse", T, "u")
check("停券：日期倒置退回", len(rows) == 1 and rej == 1 and rows[0]["data_date"] == "2026-10-16" and rows[0]["payload"]["end"] == "2026-10-20")

mo = [{"Date": "1151015", "SecuritiesCompanyCode": "6488", "MarginPurchaseBalance": "1000", "MarginPurchaseBalancePreviousDay": "900", "MarginPurchaseQuota": "5000", "ShortSaleBalance": "50", "ShortSaleBalancePreviousDay": "40", "MarginPurchaseUtilizationRate": "20"},
      {"Date": "1151015", "SecuritiesCompanyCode": "3105", "MarginPurchaseBalance": "9000", "MarginPurchaseBalancePreviousDay": "900", "MarginPurchaseQuota": "5000"}]
rows, rej = O.n_margin_otc(mo, "tpex", T, "u")
p = rows[0]["payload"]
check("上櫃融資：增減100、券資比5%、使用率20", p["margin_diff"] == 100 and p["short_margin_ratio"] == 5.0 and p["margin_use_pct"] == 20 and p["short_diff"] == 10)
check("上櫃融資：餘額超過限額退回", rej == 1)

inst = [{"Date": "1151015", "SecuritiesCompanyCode": "6488", "Foreign Investors include Mainland Area Investors (Foreign Dealers excluded)-Difference": "3,000,000",
         "SecuritiesInvestmentTrustCompanies-Difference": "-200000", "Dealers-Difference": "60000", "ForeignDealers-Difference": "999999999"},
        {"Date": "1151015", "SecuritiesCompanyCode": "00411A", "Foreign Investors include Mainland Area Investors (Foreign Dealers excluded)-Difference": "1",
         "SecuritiesInvestmentTrustCompanies-Difference": "0", "Dealers-Difference": "0"}]
rows, rej = O.n_inst_otc([O.norm_row(x) for x in inst], T)
check("上櫃法人：外資3000張、投信-200、自營60，外資自營商不併入", rows[0]["foreign_buy"] == 3000 and rows[0]["trust_buy"] == -200 and rows[0]["dealer_buy"] == 60)
check("上櫃法人：ETF 不收", len(rows) == 1)

news = [{"出表日期": "1151015", "發言日期": "1151015", "發言時間": "150000", "公司代號": "2330", "主旨 ": "公告董事會決議", "符合條款": "第11款"}]
rows, rej = O.n_material_news([O.norm_row(x) for x in news], "twse", T, "u")
check("重大訊息：欄位尾空白可用", rows and rows[0]["payload"]["items"][0]["subject"] == "公告董事會決議")

# 同步：假資料源與假資料庫
class Q:
    def __init__(s, db, t): s.db, s.t = db, t
    def upsert(s, rows, on_conflict=None, ignore_duplicates=False): s.db.setdefault(s.t, []).extend(rows); return s
    def execute(s): return s
class SB:
    def __init__(s): s.db = {}
    def table(s, n): return Q(s.db, n)
class R:
    def __init__(s, d, code=200): s.d, s.status_code = d, code
    def json(s): return s.d
DATA = {"/opendata/t187ap11_L": pr[:2], "/mopsfin_t187ap11_O": [], "/tpex_mainboard_margin_balance": mo[:1],
        "/tpex_3insti_daily_trading": inst}
def get(url, headers=None, timeout=0):
    for p, d in DATA.items():
        if url.endswith(p): return R(d)
    return R([], 500)
sb = SB()
msg, s = O.sync(sb, get, today=T, sleep=lambda x: None)
check("同步寫入 pledge/margin_otc/inst_otc", s["datasets"]["pledge"]["rows"] == 1 and s["datasets"]["margin_otc"]["rows"] == 1 and s["datasets"]["inst_otc"]["rows"] == 1)
check("失敗端點只計入 errors 不中斷", s["errors"] >= 1 and "official_open_data" in sb.db and "inst_holding" in sb.db)
check("inst_holding 只寫 3 欄", set(sb.db["inst_holding"][0]) == {"date", "symbol", "foreign_buy", "trust_buy", "dealer_buy"})

# 退回超過 30% 整批不寫
bad = [dict(pr[0], 目前持股="x") for _ in range(10)] + [pr[0]]
DATA["/opendata/t187ap11_L"] = bad
sb2 = SB()
msg2, s2 = O.sync(sb2, get, today=T, only={"pledge"}, sleep=lambda x: None)
check("退回>30% 整批不寫", s2["datasets"]["pledge"]["rows"] == 0 and s2["datasets"]["pledge"]["error"] and "official_open_data" not in sb2.db)

# 過舊的上櫃法人不寫入
DATA["/tpex_3insti_daily_trading"] = [dict(inst[0], Date="1150801")]
sb3 = SB()
msg3, s3 = O.sync(sb3, get, today=T, only={"inst_otc"}, sleep=lambda x: None)
check("法人日期過舊不寫 inst_holding", "inst_holding" not in sb3.db and s3["datasets"]["inst_otc"]["error"])

al = O.summarize_alerts({"pledge": {"pledge_ratio": 55.0, "pledged_shares": 5000000, "held_shares": 9000000, "data_date": "2026-10-15"},
                         "insider_transfer": {"total_shares": 3000000, "n": 1, "data_date": "2026-10-15"},
                         "short_suspend": {"start": "2026-10-15", "end": "2026-10-20", "reason": "分配收益"}})
check("警示：質押危險、轉讓、停券", [a[0] for a in al] == ["danger", "warn", "warn"])
check("質押低於門檻不警示", O.summarize_alerts({"pledge": {"pledge_ratio": 10.0, "pledged_shares": 1, "held_shares": 1, "data_date": "x"}}) == [])
def test_exdiv_upcoming():
    import datetime as _dt
    import official_open_data as _OD
    t = _dt.date(2026, 10, 16)
    rows = [{"Date": "1151021", "Code": "2884", "Name": "玉山金", "Exdividend": "息", "CashDividend": "0.5"},
            {"Date": "1151010", "Code": "2885", "Name": "已過", "Exdividend": "息", "CashDividend": "1"},
            {"Date": "1151021", "Code": "00878", "Name": "ETF", "Exdividend": "息", "CashDividend": "0.4"},
            {"Date": "", "Code": "1101", "Name": "壞", "CashDividend": "1"}]
    out, rej = _OD.n_exdiv_upcoming(rows, "twse", t, "u")
    assert [o["symbol"] for o in out] == ["2884"] and rej == 1 and out[0]["data_date"] == "2026-10-21"
    out2, _ = _OD.n_exdiv_upcoming([{"ExRrightsExDividendDate": "1151030", "SecuritiesCompanyCode": "6548",
                                     "CompanyName": "長科", "ExRrightsExDividend": "除息", "CashDividend": "0.00000000"}], "tpex", t, "u")
    assert out2[0]["payload"]["cash"] is None


try:
    test_exdiv_upcoming()
    check("除息預告：只留未除息普通股、未公告股利為 None", True)
except AssertionError:
    check("除息預告：只留未除息普通股、未公告股利為 None", False)
print(f"\n{len(fails)} failed"); sys.exit(1 if fails else 0)
