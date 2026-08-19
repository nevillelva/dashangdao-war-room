# -*- coding: utf-8 -*-
"""
TWSE 官方全市場批次端點 —— 可行性驗證腳本（選項A）

目的：證明「一支 API 就能拿到全市場所有股票的法人/融資/本益比資料，
      而且不會像 FinMind 那樣被限流」，用真實回傳資料驗證，再決定要不要
      正式改核心程式。

用法：
    python test_twse_official.py

不需要任何 token、不需要金鑰。只需要 requests（大多環境已內建；沒有的話
pip install requests）。pandas 可有可無（沒有也能跑，只是少印一點統計）。

這支腳本只做「唯讀查詢 + 印出結果」，不寫任何資料庫、不改任何東西，
純驗證，可以安心跑。
"""
import time
import json
import sys

try:
    import requests
except ImportError:
    print("需要 requests 套件：pip install requests")
    sys.exit(1)

try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False

# ──────────────────────────────────────────────────────────────────────
# 要測的三個官方端點。每個都準備兩種來源：
#   (A) openapi.twse.com.tw —— 最乾淨的 JSON，回傳「最近一個交易日」的全市場
#   (B) www.twse.com.tw/rwd —— 舊版端點，可以帶 date 參數查「指定某一天」
#       （歷史回溯會用到這種，這裡一併驗證它通不通）
# ──────────────────────────────────────────────────────────────────────

HEADERS = {
    # 帶一個一般瀏覽器 UA，避免少數端點對空 UA 過濾
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/120.0 Safari/537.36"),
    "Accept": "application/json",
}

# 用「最近的工作日」當 date 參數；若當天非交易日，端點會回空，屬正常，
# 這裡只是驗證「連得到、格式對」，不追求一定有資料。
def _guess_recent_weekday_yyyymmdd():
    import datetime
    d = datetime.date.today()
    # 往回找到最近的週一~週五（不管有沒有真的開盤，只為了給端點一個合法日期）
    while d.weekday() >= 5:
        d = d - datetime.timedelta(days=1)
    return d.strftime("%Y%m%d")

DATE = _guess_recent_weekday_yyyymmdd()

TESTS = [
    {
        "名稱": "三大法人買賣超日報 (T86) —— 對應現在的 fetch_institutional_history 法人部分"
                "【已查證：openapi.twse.com.tw官方目錄裡沒有T86這個路徑，"
                "只有rwd版有，這裡openapi來源保留只是留紀錄，預期會是錯誤頁】",
        "來源": [
            ("openapi(已知無此路徑，僅留存對照)", "https://openapi.twse.com.tw/v1/exchangeReport/T86"),
            ("rwd(帶日期，唯一正確來源)", f"https://www.twse.com.tw/rwd/zh/fund/T86?date={DATE}&selectType=ALL&response=json"),
        ],
    },
    {
        "名稱": "融資融券餘額 (MI_MARGN) —— 對應 fetch_institutional_history 融資部分",
        "來源": [
            ("openapi", "https://openapi.twse.com.tw/v1/exchangeReport/MI_MARGN"),
            ("rwd(帶日期)", f"https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?date={DATE}&selectType=ALL&response=json"),
        ],
    },
    {
        "名稱": "個股日本益比/殖利率/淨值比 (BWIBBU) —— 對應 fetch_pe_history",
        "來源": [
            ("openapi", "https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL"),
            ("rwd(帶日期)", f"https://www.twse.com.tw/rwd/zh/afterTrading/BWIBBU_d?date={DATE}&selectType=ALL&response=json"),
        ],
    },
]


def _extract_records(data):
    """不同端點回傳結構不一樣，統一抽出『記錄列表』。"""
    if isinstance(data, list):
        return data, None
    if isinstance(data, dict):
        # rwd 版通常是 {'stat':'OK','data':[[...],[...]], 'fields':[...]}
        if "data" in data and isinstance(data["data"], list):
            return data["data"], data.get("fields")
        # 有些是 {'tables':[{'data':[...],'fields':[...]}]}
        if "tables" in data and data["tables"]:
            t0 = data["tables"][0]
            return t0.get("data", []), t0.get("fields")
    return [], None


def run_one(name, sources):
    print("=" * 78)
    print(f"■ {name}")
    print("=" * 78)
    for tag, url in sources:
        print(f"\n  ── 來源[{tag}]  {url}")
        try:
            t0 = time.time()
            r = requests.get(url, headers=HEADERS, timeout=20)
            elapsed = time.time() - t0
            print(f"     HTTP狀態：{r.status_code}   耗時：{elapsed:.2f}s   回傳大小：{len(r.content):,} bytes")
            if r.status_code != 200:
                print(f"     ⚠️ 非200，前200字：{r.text[:200]}")
                continue
            try:
                data = r.json()
            except Exception:
                print(f"     ⚠️ 不是JSON，前200字：{r.text[:200]}")
                continue

            records, fields = _extract_records(data)
            n = len(records)
            print(f"     ✅ 解析成功：這一支API一次回傳 {n:,} 筆（= 全市場檔數，不是1筆1檔）")
            if fields:
                print(f"     欄位(fields)：{fields}")
            # 印前2筆當樣本
            for i, rec in enumerate(records[:2]):
                if isinstance(rec, dict):
                    # openapi 版是 dict，直接印 key
                    preview = {k: rec[k] for k in list(rec.keys())[:8]}
                    print(f"     樣本{i+1}(dict)：{json.dumps(preview, ensure_ascii=False)}")
                else:
                    print(f"     樣本{i+1}(list)：{rec[:8]}")
            if n == 0:
                print("     （0筆可能是：今天非交易日、或這個日期還沒公告，屬正常，"
                      "重點是上面『連得到、格式對』。換一個交易日再測即可。）")
        except requests.exceptions.Timeout:
            print("     ❌ 逾時（20s）")
        except Exception as e:
            print(f"     ❌ 例外：{type(e).__name__}: {e}")


def stress_test_no_ratelimit():
    """連續打同一支端點10次，證明官方端點不會像FinMind那樣很快就rate_limited。"""
    print("\n" + "=" * 78)
    print("■ 連打壓力測試：連續10次打 T86(openapi)，看會不會被限流")
    print("=" * 78)
    url = "https://openapi.twse.com.tw/v1/exchangeReport/T86"
    ok = 0
    for i in range(10):
        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
            status = r.status_code
            note = ""
            if status == 200:
                ok += 1
            else:
                note = f"  ← 注意，非200：{r.text[:80]}"
            print(f"  第{i+1:2d}次：HTTP {status}{note}")
        except Exception as e:
            print(f"  第{i+1:2d}次：例外 {type(e).__name__}: {e}")
        time.sleep(0.3)  # 禮貌性間隔
    print(f"\n  → 10次裡成功 {ok} 次。若10次都200，代表官方端點不會像FinMind"
          f"逐檔4000次那樣被限流（我們正式改法一次只會打幾支，遠低於此）。")


def _is_plain_stock_code(code: str) -> bool:
    """
    【選項A第二輪，總指揮官要求：資料要用但也要過濾】
    篩出「純股票」，排除ETF/權證/受益憑證等其他有價證券。
    規則：4碼純數字。這是業界最常用的粗篩規則——
    普通股票代碼是4碼數字(如2330、2317)；
    ETF代碼常見00開頭+4碼數字+可能帶英文字尾(如0050、00632R反1);
    權證代碼常見6碼、或4碼數字+英文字母。
    這裡先用最簡單、最不會誤殺的規則：code必須是純4位數字，且不以'00'開頭
    (00開頭幾乎都是ETF，一般股票不會用00開頭)。
    """
    if not code or not isinstance(code, str):
        return False
    code = code.strip()
    if len(code) != 4:
        return False
    if not code.isdigit():
        return False
    if code.startswith("00"):
        return False
    return True


def run_t86_with_filter():
    """驗證T86(rwd帶日期版)過濾後，是不是真的能降到接近1074檔掃描池的量級。"""
    print("\n" + "=" * 78)
    print("■ T86過濾驗證：套用純股票代碼規則，看筆數會不會降到合理範圍")
    print("=" * 78)
    url = f"https://www.twse.com.tw/rwd/zh/fund/T86?date={DATE}&selectType=ALL&response=json"
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        data = r.json()
        records, fields = _extract_records(data)
        print(f"  過濾前：{len(records):,} 筆")
        code_idx = 0  # 證券代號通常是第一欄，實際請以fields核對
        if fields:
            try:
                code_idx = fields.index("證券代號")
            except ValueError:
                pass
        filtered = [rec for rec in records if _is_plain_stock_code(rec[code_idx] if isinstance(rec, list) else rec.get("證券代號", ""))]
        print(f"  過濾後（純股票代碼）：{len(filtered):,} 筆")
        print(f"  → 對照現有掃描池1074檔上市股票，過濾後數字是否接近這個量級，"
              f"請人工核對（不同日期會有零股/剛下市等微小差異，屬正常）。")
        if filtered:
            sample = filtered[0]
            print(f"  過濾後樣本1：{sample[:8] if isinstance(sample, list) else sample}")
    except Exception as e:
        print(f"  ❌ 例外：{type(e).__name__}: {e}")


def stress_test_t86_rwd():
    """
    【選項A第二輪，總指揮官要求：連打會不會被限流，繼續補測】
    上一輪的壓力測試打的是openapi版T86(已確認路徑不存在，回傳的是錯誤頁)，
    不能代表真正會用到的rwd版T86抗不抗得住連打。這裡改連打rwd版，
    才是正式會拿來用的版本。
    """
    print("\n" + "=" * 78)
    print("■ 連打壓力測試(修正版)：連續10次打 T86(rwd帶日期版)，看會不會被限流")
    print("=" * 78)
    url = f"https://www.twse.com.tw/rwd/zh/fund/T86?date={DATE}&selectType=ALL&response=json"
    ok = 0
    for i in range(10):
        try:
            t0 = time.time()
            r = requests.get(url, headers=HEADERS, timeout=20)
            elapsed = time.time() - t0
            status = r.status_code
            note = ""
            is_valid_json = False
            if status == 200:
                try:
                    data = r.json()
                    recs, _ = _extract_records(data)
                    is_valid_json = True
                    note = f"  （真的解析成JSON，{len(recs):,}筆）"
                    ok += 1
                except Exception:
                    note = "  ← 注意，HTTP 200但不是有效JSON"
            else:
                note = f"  ← 注意，非200：{r.text[:80]}"
            print(f"  第{i+1:2d}次：HTTP {status}  耗時{elapsed:.2f}s{note}")
        except Exception as e:
            print(f"  第{i+1:2d}次：例外 {type(e).__name__}: {e}")
        time.sleep(0.3)
    print(f"\n  → 10次裡真正解析成功 {ok} 次。這才是正式會使用的rwd版T86的真實抗限流能力。")


if __name__ == "__main__":
    print(f"\n測試日期參數(rwd版用)：{DATE}")
    print("開始驗證 TWSE 官方批次端點……\n")
    for t in TESTS:
        run_one(t["名稱"], t["來源"])
        print()
    stress_test_no_ratelimit()
    run_t86_with_filter()
    stress_test_t86_rwd()
    print("\n驗證結束。請把以上完整輸出貼回來，我們一起確認：")
    print("  1) 哪個來源(openapi / rwd帶日期)通得過")
    print("  2) 一支API是不是真的一次回傳上千筆(全市場)")
    print("  3) 欄位怎麼對應到現在的 f_buy/t_buy/d_buy/margin_diff/pe")
    print("  4) T86過濾後的筆數是否接近1074檔量級")
    print("  5) T86(rwd版)連打10次會不會被限流")
