# -*- coding: utf-8 -*-
"""
twse_market_snapshot 歷史回填腳本

目的：不用真的等10個交易日，直接把過去N個交易日的資料一次補齊，讓
     週轉率/5日10日法人買超這類需要歷史深度的計算立刻生效。

【已修正，總指揮官指正過】
分兩種回填方式，不是同一套：
  ① T86(法人)+MI_INDEX(價量)：TWSE官方端點本身支援date參數，直接
     一次性打過去N天，速度快、無限流疑慮。
  ② MI_MARGN(融資)+BWIBBU_ALL(本益比)：openapi版沒有date參數，但
     FinMind的TaiwanStockMarginPurchaseShortSale/TaiwanStockPER這兩個
     資料集本身就支援指定years查歷史（既有fetch_institutional_history/
     fetch_pe_history這兩個函式的FinMind路徑早就這樣用）。回填是「一次性」
     操作，不是「每天都要重打」的常態負擔，FinMind限流風險只在「大量、
     反覆、常態性」使用時才是問題——一次性回填1074檔即使要花10-20分鐘、
     偶爾retry，也完全可以接受，之後這張表就有歷史了，不需要再碰FinMind。
     這裡直接呼叫既有的fetch_institutional_history(sb=None)/
     fetch_pe_history(sb=None)強制走FinMind路徑，不重寫新邏輯。

用法：GitHub Actions手動觸發，或本機直接跑
    python backfill_twse_snapshot.py [回填天數，預設15] [FinMind回填的股票清單來源]

需要環境變數：SUPABASE_URL, SUPABASE_KEY, FINMIND_TOKEN（融資/本益比
那段才需要，法人+價量那段不需要任何token）
"""
import sys
import os
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from warroom_core import (
    fetch_twse_t86_snapshot,
    fetch_twse_daily_price_value_snapshot,
    TAIPEI_TZ,
)
from supabase import create_client


def get_recent_trading_days(n):
    """
    往回抓最近n個平日（周六日跳過）。這裡不知道確切的交易日曆（國定假日
    停市），跳過的假日靠sync時該天回傳空資料自然略過，不影響其他天。
    """
    days = []
    d = datetime.now(TAIPEI_TZ).date() - timedelta(days=1)   # 從昨天開始（今天可能還沒收盤）
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d.strftime("%Y-%m-%d"))
        d -= timedelta(days=1)
    return days


def backfill_one_day(sb, trade_date):
    date_yyyymmdd = trade_date.replace("-", "")
    t86 = fetch_twse_t86_snapshot(date_yyyymmdd)
    price_value = fetch_twse_daily_price_value_snapshot(date_yyyymmdd)

    all_symbols = set(t86.keys()) | set(price_value.keys())
    if not all_symbols:
        print(f"[回填] {trade_date}：兩支端點都沒抓到資料（可能是非交易日/國定假日），跳過。")
        return 0

    rows = []
    for sym in all_symbols:
        t86_row = t86.get(sym, {})
        pv_row = price_value.get(sym, {})
        rows.append({
            "trade_date": trade_date,
            "symbol": sym,
            "f_buy": t86_row.get("f_buy"),
            "t_buy": t86_row.get("t_buy"),
            "d_buy": t86_row.get("d_buy"),
            "close_price": pv_row.get("close"),
            "trading_value": pv_row.get("trading_value"),
            "trading_volume": pv_row.get("trading_volume"),
            "source": "twse_official_backfill",
        })

    CHUNK = 500
    written = 0
    for i in range(0, len(rows), CHUNK):
        chunk = rows[i:i + CHUNK]
        try:
            # 【重要】這裡用merge_default_null_type + 只更新有值的欄位——
            # 用upsert on_conflict，但只傳這次有的欄位(f_buy/close_price等)，
            # 不會覆蓋掉當天已經存在的margin_diff/pe/revenue等其他欄位
            # (如果那天剛好也被sync_twse_market_snapshot同步過)。Supabase
            # upsert預設對沒傳的欄位不會清空，只更新有傳的欄位，這點是
            # PostgREST upsert的標準行為，可以放心用。
            sb.table("twse_market_snapshot").upsert(
                chunk, on_conflict="trade_date,symbol").execute()
            written += len(chunk)
        except Exception as e:
            print(f"[回填] {trade_date} 第{i}-{i+len(chunk)}筆寫入失敗：{type(e).__name__}: {e}")
    print(f"[回填] {trade_date} 完成，共{written}/{len(rows)}檔"
          f"（法人{len(t86)}檔／價量{len(price_value)}檔）")
    return written


def main():
    n_days = int(sys.argv[1]) if len(sys.argv) > 1 else 15
    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

    days = get_recent_trading_days(n_days)
    print(f"預計回填(T86+MI_INDEX) {len(days)} 個交易日：{days}\n")

    total = 0
    for d in days:
        total += backfill_one_day(sb, d)
        time.sleep(1)   # 對TWSE官方端點禮貌性間隔，不需要跟FinMind一樣嚴格

    print(f"\n【第一段：T86+MI_INDEX官方端點回填】完成，累計寫入 {total} 筆"
          f"（含跨天重複股票，不是去重後的檔數）。")

    # 【R97續12移除，總指揮官實測抓到：這個判斷本身是錯的】原本第二段
    # 用FinMind一次性回填融資/本益比，理由是「一次性操作不算常態負擔」——
    # 這個判斷錯了：FinMind免費帳號的額度上限本身就很小，不管是不是
    # 「一次性」，對上千檔股票操作照樣在短時間內把額度用完、全部卡在
    # rate_limited，總指揮官實測跑了1小時23分鐘全數限流，直接移除不修補。
    # 融資(margin_diff)/本益比(pe)這兩項改成完全依賴每天正常累積
    # （sync_twse_market_snapshot每天執行一次，openapi版MI_MARGN/
    # BWIBBU_ALL本身就有資料，只是累積深度需要時間，不需要回填）。
    print(f"\n融資(margin_diff)/本益比(pe)不回填——這兩項的openapi端點本身"
          f"沒有date參數可以回溯，唯一能做的FinMind回填方案已驗證行不通"
          f"(額度太小)，改成依賴每天正常累積，不需要額外動作。")
    print(f"\n全部回填完成。")


if __name__ == "__main__":
    main()
