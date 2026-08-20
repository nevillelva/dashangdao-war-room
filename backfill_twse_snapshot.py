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
    fetch_institutional_history,
    fetch_pe_history,
    set_finmind_tokens,
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

    # ========================================================================
    # 第二段：融資(MI_MARGN)+本益比(BWIBBU_ALL) —— 用FinMind一次性回填
    # ========================================================================
    print(f"\n{'='*70}\n【第二段】融資+本益比，用FinMind一次性回填（一次性操作，不是常態負擔）\n{'='*70}")
    set_finmind_tokens((os.environ.get("FINMIND_TOKEN") or "").split(","))

    # 回填對象：目前twse_market_snapshot裡已經出現過的股票代號聯集
    # （代表這些是系統實際會用到、真的需要歷史的股票，不用對全市場
    # 1074檔都回填融資/本益比，那樣才會真的觸發限流疑慮；只回填「用得到」
    # 的這批，範圍小很多）
    try:
        res = sb.table("twse_market_snapshot").select("symbol").execute()
        symbols = sorted(set(r["symbol"] for r in (res.data or [])))
    except Exception as e:
        print(f"[回填] 讀取現有symbol清單失敗，改用空清單（第二段不執行）：{type(e).__name__}: {e}")
        symbols = []

    print(f"回填對象共 {len(symbols)} 檔（來自twse_market_snapshot目前已有的股票）")

    fm_pacing = 0.5
    mp_written = 0
    for i, sym in enumerate(symbols):
        try:
            inst_df = fetch_institutional_history(sym, years=1, token=None, sb=None)  # sb=None強制走FinMind
            pe_df = fetch_pe_history(sym, token=None, years=1, sb=None)

            rows_to_upsert = []
            if inst_df is not None and not inst_df.empty and "margin_diff" in inst_df.columns:
                for idx, row in inst_df.iterrows():
                    d_str = str(idx)[:10]
                    if row.get("margin_diff") is not None and str(row.get("margin_diff")) != "nan":
                        rows_to_upsert.append({"trade_date": d_str, "symbol": sym,
                                               "margin_diff": float(row["margin_diff"])})
            if pe_df is not None and not pe_df.empty and "PER" in pe_df.columns:
                pe_df2 = pe_df.reset_index()
                date_col = "date" if "date" in pe_df2.columns else pe_df2.columns[0]
                for _, row in pe_df2.iterrows():
                    d_str = str(row[date_col])[:10]
                    if row.get("PER") is not None and str(row.get("PER")) != "nan":
                        rows_to_upsert.append({"trade_date": d_str, "symbol": sym,
                                               "pe": float(row["PER"])})

            if rows_to_upsert:
                # 同一天同一檔可能融資+本益比分兩筆，這裡合併成一筆再upsert，
                # 避免同一個(trade_date,symbol) upsert兩次時後者蓋掉前者
                merged = {}
                for r in rows_to_upsert:
                    key = (r["trade_date"], r["symbol"])
                    merged.setdefault(key, {"trade_date": r["trade_date"], "symbol": r["symbol"]})
                    merged[key].update({k: v for k, v in r.items() if k not in ("trade_date", "symbol")})
                sb.table("twse_market_snapshot").upsert(
                    list(merged.values()), on_conflict="trade_date,symbol").execute()
                mp_written += len(merged)
                print(f"[回填 {i+1}/{len(symbols)}] {sym}：融資+本益比 {len(merged)} 天")
        except Exception as e:
            print(f"[回填 {i+1}/{len(symbols)}] {sym} 失敗：{type(e).__name__}: {e}")
        time.sleep(fm_pacing)

    print(f"\n【第二段：FinMind融資+本益比回填】完成，累計寫入 {mp_written} 筆。")
    print(f"\n全部回填完成。")


if __name__ == "__main__":
    main()
