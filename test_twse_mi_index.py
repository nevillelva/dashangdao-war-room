# -*- coding: utf-8 -*-
"""
TWSE MI_INDEX 全市場每日價量批次端點 —— 可行性驗證腳本（第二輪）

目的：驗證能不能用這支端點取代 fetch_stock_price_and_value_history()
      裡逐檔打的 FinMind TaiwanStockPrice——如果可行，compute_interval_
      turnover() 的另外一半FinMind負擔（價量歷史）也能一次全市場批次
      拿到，不用逐檔打。

跟第一輪(test_twse_official.py)驗證T86/MI_MARGN/BWIBBU_ALL是同一套
規格：唯讀查詢、不寫資料庫、不需要token，可以安心跑。

用法：python test_twse_mi_index.py
"""
import time
import json
import sys

try:
    import requests
except ImportError:
    print("需要 requests 套件：pip install requests")
    sys.exit(1)

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
    "Accept": "application/json",
}


def _guess_recent_weekday_yyyymmdd():
    import datetime
    d = datetime.date.today()
    while d.weekday() >= 5:
        d = d - datetime.timedelta(days=1)
    return d.strftime("%Y%m%d")


DATE = _guess_recent_weekday_yyyymmdd()


def run():
    print(f"\n測試日期參數：{DATE}")
    print("=" * 78)
    print("■ MI_INDEX 全市場每日價量 (type=ALL) —— 對應現在的")
    print("  fetch_stock_price_and_value_history 逐檔FinMind呼叫")
    print("=" * 78)

    url = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
    params = {"date": DATE, "type": "ALL", "response": "json"}
    try:
        t0 = time.time()
        r = requests.get(url, params=params, headers=HEADERS, timeout=20)
        elapsed = time.time() - t0
        print(f"HTTP狀態：{r.status_code}   耗時：{elapsed:.2f}s   回傳大小：{len(r.content):,} bytes")
        if r.status_code != 200:
            print(f"⚠️ 非200，前300字：{r.text[:300]}")
            return
        try:
            data = r.json()
        except Exception:
            print(f"⚠️ 不是JSON，前300字：{r.text[:300]}")
            return

        print(f"\n頂層keys：{list(data.keys())}")
        # MI_INDEX的回傳結構比較複雜，通常在tables[]裡有多個子表
        # （大盤統計 / 個股統計 各一個table），逐一印出來看
        tables = data.get("tables", [])
        if not tables:
            # 也可能是舊格式，直接在data['data']
            recs = data.get("data", [])
            fields = data.get("fields", [])
            print(f"\n（舊格式）筆數：{len(recs):,}，欄位：{fields}")
            if recs:
                print(f"樣本1：{recs[0]}")
            return

        print(f"\n共有 {len(tables)} 個子表，逐一列出：")
        for i, t in enumerate(tables):
            title = t.get("title", "(無標題)")
            fields = t.get("fields", [])
            trecs = t.get("data", [])
            print(f"\n  ── 子表[{i}]：{title}")
            print(f"     筆數：{len(trecs):,}")
            print(f"     欄位：{fields}")
            if trecs:
                print(f"     樣本1：{trecs[0]}")
                print(f"     樣本2：{trecs[1] if len(trecs) > 1 else '(無)'}")
    except requests.exceptions.Timeout:
        print("❌ 逾時（20s）")
    except Exception as e:
        print(f"❌ 例外：{type(e).__name__}: {e}")

    print("\n" + "=" * 78)
    print("■ 連打壓力測試：連續10次，看會不會被限流")
    print("=" * 78)
    ok = 0
    for i in range(10):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=20)
            note = ""
            if r.status_code == 200:
                try:
                    r.json()
                    ok += 1
                    note = "（成功解析JSON）"
                except Exception:
                    note = "（HTTP 200但不是有效JSON）"
            print(f"  第{i+1:2d}次：HTTP {r.status_code}{note}")
        except Exception as e:
            print(f"  第{i+1:2d}次：例外 {type(e).__name__}: {e}")
        time.sleep(0.3)
    print(f"\n→ 10次裡真正成功 {ok} 次。")

    print("\n驗證結束。請把以上完整輸出貼回來，我們一起確認：")
    print("  1) 這支端點的回傳結構長怎樣（tables/子表/欄位名稱）")
    print("  2) 有沒有個股逐檔的成交金額(Trading_money對應欄位)+收盤價")
    print("  3) 筆數是不是接近全市場上市股票數量")
    print("  4) 連打10次會不會被限流")
    print("  5)【重要】這支只有「當日」，沒有回溯能力——如果要拿它取代")
    print("     compute_interval_turnover()需要的近10天週轉率，做法會是")
    print("     跟T86一樣，每天同步一次累積進資料庫，累積夠10天後才完全")
    print("     生效，這點跟第一輪的解法是同一個模式，不是新問題。")


if __name__ == "__main__":
    run()
