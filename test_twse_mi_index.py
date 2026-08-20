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
    """
    【修正，總指揮官實測抓到】MI_INDEX是收盤後才彙整的報表，拿「今天」
    這個可能還沒收盤的日期查一定是空的——不是路徑或參數錯，是查太早。
    這裡改成預設抓「昨天」往回找的最近一個平日，確保拿到的日期一定
    已經收盤過，才能真的驗證到這支端點能不能用。
    """
    import datetime
    d = datetime.date.today() - datetime.timedelta(days=1)
    while d.weekday() >= 5:
        d = d - datetime.timedelta(days=1)
    return d.strftime("%Y%m%d")


DATE = _guess_recent_weekday_yyyymmdd()


def run():
    print(f"\n測試日期參數：{DATE}")
    print("=" * 78)
    print("■ MI_INDEX 全市場每日價量 (type=ALL) —— 對應現在的")
    print("  fetch_stock_price_and_value_history 逐檔FinMind呼叫")
    print("  【修正版，上一版URL路徑錯誤(/rwd/zh/afterTrading/)已知不存在，")
    print("   正確路徑是 www.twse.com.tw/exchangeReport/MI_INDEX，個股資料")
    print("   在data9/fields9這組欄位，不是tables或頂層data/fields】")
    print("=" * 78)

    url = "https://www.twse.com.tw/exchangeReport/MI_INDEX"
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

        print(f"\nstat：{data.get('stat')}")
        print(f"頂層keys：{list(data.keys())}")

        # 【第三次修正，總指揮官實測抓到】新版TWSE MI_INDEX改成tables陣列
        # 結構（頂層有tables/type/params/stat/date），不是舊版的data9/
        # fields9，也不是我上一版猜的dataN/fieldsN。個股逐檔明細在tables
        # 裡面某個子表（通常是筆數最多的那個，前面幾個是大盤各類指數統計）。
        # 這裡自動掃所有tables、抓筆數最多的那個當個股明細，不寫死索引，
        # 避免TWSE又調整子表順序。
        tables = data.get("tables", [])
        if not tables:
            print("\n⚠️ 沒有tables結構，完整回傳前2000字：")
            print(json.dumps(data, ensure_ascii=False)[:2000])
            return

        print(f"\n共有 {len(tables)} 個子表，各自筆數與標題：")
        table_info = []
        for i, t in enumerate(tables):
            title = t.get("title", "(無標題)")
            recs = t.get("data", [])
            fields = t.get("fields", [])
            table_info.append((i, title, len(recs), fields, recs))
            print(f"  子表[{i}]：{len(recs):,} 筆 —— {title}")

        # 抓筆數最多的子表當個股明細
        table_info.sort(key=lambda x: x[2], reverse=True)
        i, title, n, fields, recs = table_info[0]
        print(f"\n最大子表（推測是個股逐檔明細）：")
        print(f"  子表[{i}]：{title}")
        print(f"  筆數：{n:,}")
        print(f"  欄位(fields)：{fields}")
        if recs:
            print(f"  樣本1：{recs[0]}")
            print(f"  樣本2：{recs[1] if len(recs) > 1 else '(無)'}")
            print(f"  樣本3：{recs[2] if len(recs) > 2 else '(無)'}")
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
    print("  1) 哪一組dataN/fieldsN是個股逐檔明細，筆數是不是接近全市場上市股票數量")
    print("  2) 欄位裡有沒有成交金額+收盤價（對應現在fetch_stock_price_and_value_history要的東西）")
    print("  3) 連打10次會不會被限流")
    print("  4)【重要】這支只有「當日」，沒有回溯能力——如果要拿它取代")
    print("     compute_interval_turnover()需要的近10天週轉率，做法會是")
    print("     跟T86一樣，每天同步一次累積進資料庫，累積夠10天後才完全")
    print("     生效，這點跟第一輪的解法是同一個模式，不是新問題。")


if __name__ == "__main__":
    run()
