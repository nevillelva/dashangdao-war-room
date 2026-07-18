#!/usr/bin/env python3
"""
54088 戰情室 — 系統自主選股排程腳本 (V160 A階段)
================================================================================
這支腳本由 GitHub Actions 排程觸發，「不用開網頁」就能自動跑，分三個階段：

  --stage signal   每交易日 22:00 執行：全市場掃描 → 選多空候選 → 寫入待執行清單
  --stage gate     隔日 8:55 執行：檢查隔夜總經，劇變則標記暫緩
  --stage execute  隔日 9:01 執行：用開盤價把待執行清單正式進場 + 檢查既有持倉出場

用法：
  python system_scheduler.py --stage signal
  python system_scheduler.py --stage gate
  python system_scheduler.py --stage execute

環境變數（在 GitHub Actions secrets 設定）：
  SUPABASE_URL, SUPABASE_KEY  — 同 Streamlit secrets
  FINMIND_TOKEN               — FinMind API token（逗號分隔多組）
  TELEGRAM_BOT_TOKEN          — Telegram Bot token（選填，設了才推播）
  TELEGRAM_CHAT_ID            — 你的 Telegram chat id（選填）

注意：這支腳本是獨立的，不 import Streamlit。它重用選股/出場的「純邏輯」，
      但資料存取直接走 Supabase（因為 GitHub Actions 環境沒有本機 SQLite）。
================================================================================
"""
import os
import sys
import argparse
from datetime import datetime

import requests

try:
    from supabase import create_client
except ImportError:
    print("需要安裝 supabase 套件：pip install supabase")
    sys.exit(1)


# ------------------------------------------------------------------------------
# 連線與工具
# ------------------------------------------------------------------------------
def get_supabase():
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_KEY", "")
    if not url or not key:
        print("❌ 缺少 SUPABASE_URL / SUPABASE_KEY 環境變數")
        sys.exit(1)
    return create_client(url, key)


def notify_telegram(msg):
    """推播到 Telegram（若有設定 token）。無設定則只印出。
    【修復】原本用 requests.post() 沒有檢查回傳狀態碼——如果 Telegram API 說
    「chat_id 有問題」「token 無效」這類錯誤，是用 HTTP 狀態碼回傳的，不是連線例外，
    原本的 try/except 完全抓不到，導致整個排程顯示成功、但訊息其實沒送出去，
    而且看不到任何錯誤訊息。現在會檢查狀態碼，失敗時把 Telegram 實際回傳的錯誤原因印出來。
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    print(msg)
    if not token or not chat_id:
        print("⚠️ Telegram 推播已跳過：TELEGRAM_BOT_TOKEN 或 TELEGRAM_CHAT_ID 未設定")
        return
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": msg, "parse_mode": "HTML"},
            timeout=10,
        )
        if resp.status_code == 200:
            print("✅ Telegram 推播成功")
        else:
            print(f"❌ Telegram 推播失敗（HTTP {resp.status_code}）：{resp.text}")
    except Exception as e:
        print(f"❌ Telegram 推播失敗（連線例外）: {e}")


def get_config(sb, key, default):
    try:
        r = sb.table("system_config").select("config_value").eq("config_key", key).limit(1).execute()
        if r.data:
            return r.data[0]["config_value"]
    except Exception:
        pass
    return default


# ------------------------------------------------------------------------------
# 資料抓取（yfinance + FinMind），與網頁版邏輯一致但獨立實作
# ------------------------------------------------------------------------------
def fetch_price_hist(symbol):
    """抓個股歷史股價（yfinance）。回傳 DataFrame 或 None。"""
    import yfinance as yf
    for suffix in (".TW", ".TWO"):
        try:
            tk = yf.Ticker(f"{symbol}{suffix}")
            hist = tk.history(period="3mo").dropna(subset=["Close"])
            if len(hist) >= 20:
                return hist
        except Exception:
            continue
    return None


def compute_signal_for(symbol):
    """
    精簡版訊號計算（排程專用，不依賴 Streamlit）：算評分、防守線、停利點。
    這裡只用技術面（均線/爆量/ATR），因為排程環境輕量。正式選股門檻靠評分。
    回傳 dict 或 None。
    """
    hist = fetch_price_hist(symbol)
    if hist is None:
        return None
    import numpy as np
    close = hist["Close"]
    cur = float(close.iloc[-1])
    ma5 = float(close.tail(5).mean())
    ma20 = float(close.tail(20).mean())
    prev = float(close.iloc[-2])
    gain = (cur - prev) / prev * 100 if prev else 0.0
    # ATR(14)
    high, low = hist["High"], hist["Low"]
    tr = (high - low).tail(14).mean()
    atr = float(tr) if tr and not np.isnan(tr) else cur * 0.02
    vol = hist["Volume"]
    vol_ratio = float(vol.iloc[-1] / vol.tail(20).mean()) if vol.tail(20).mean() > 0 else 1.0
    def_line = round(ma5 - 0.5 * atr, 2)
    take_profit = round(cur + atr, 2)

    # 評分（與網頁 determine_signal 精神一致的精簡版）
    score = 0
    if cur > ma5 > ma20:
        score += 2
    elif cur > ma5:
        score += 1
    elif cur < ma5:
        score -= 2
    if vol_ratio > 2.0:
        score += 1
    elif vol_ratio < 0.6:
        score -= 1
    # 爆量下殺強制偏空
    day_low = float(low.iloc[-1]); day_high = float(high.iloc[-1])
    rng = day_high - day_low
    close_near_low = rng > 0 and (cur - day_low) / rng <= 0.35
    if vol_ratio >= 2.0 and cur < float(hist["Open"].iloc[-1]) and gain < -1.0 and close_near_low:
        score = min(score, -3)

    return {"symbol": symbol, "price": cur, "score": score, "gain": round(gain, 2),
            "def_line": def_line, "take_profit": take_profit, "vol_ratio": round(vol_ratio, 2)}


def get_scan_pool(sb):
    """
    取得掃描池：從 Supabase inst_holding 抓「最新一個交易日」的完整代號清單。
    【V160 修復】原本 limit(1000) 會漏掉，且可能混到跨日期的舊代號（含已停用者）。
    改成：先找最新日期，再對那一天分頁抓完整代號（突破1000筆上限），確保是真正的
    全市場掃描池，不是被截斷的子集。這點很重要——總指揮官指出：一旦排程改成背景
    全自動執行，掃全市場對使用者體驗沒有負擔（沒人在等畫面），所以應該用完整市場
    範圍才能得到精準的判斷與勝率，不該延用網頁版為了即時互動而設的容量上限。
    """
    try:
        r = sb.table("inst_holding").select("date").order("date", desc=True).limit(1).execute()
        if not r.data:
            return []
        latest_date = r.data[0]["date"]
        syms, start, page = set(), 0, 1000
        while True:
            r2 = (sb.table("inst_holding").select("symbol")
                  .eq("date", latest_date).range(start, start + page - 1).execute())
            batch = r2.data or []
            syms.update(row["symbol"] for row in batch)
            if len(batch) < page:
                break
            start += page
        return sorted(syms)
    except Exception:
        return []


# ------------------------------------------------------------------------------
# 三個階段
# ------------------------------------------------------------------------------
def stage_signal(sb):
    """22:00 選股：掃描 → 選多空候選 → 寫入 system_portfolio（status='pending'）。"""
    run_date = datetime.now().strftime("%Y-%m-%d")
    pool = get_scan_pool(sb)
    if not pool:
        notify_telegram(f"⚠️ [{run_date}] 選股階段：掃描池為空，無法選股")
        return

    # 【V160 修復】排除已經持有中的標的（同方向），跟網頁版 system_select_candidates 邏輯一致，
    # 避免排程重跑或漏執行補跑時對同一檔重複進場。
    try:
        held = sb.table("system_portfolio").select("symbol,side").eq("status", "holding").execute().data or []
    except Exception:
        held = []
    held_long = {h["symbol"] for h in held if h.get("side") == "long"}
    held_short = {h["symbol"] for h in held if h.get("side") == "short"}

    longs, shorts = [], []
    for sym in pool:
        sig = compute_signal_for(sym)
        if not sig:
            continue
        if sig["score"] >= 3 and sym not in held_long:
            longs.append(sig)
        elif sig["score"] <= -3 and sym not in held_short:
            shorts.append(sig)
    longs.sort(key=lambda x: x["score"], reverse=True)
    shorts.sort(key=lambda x: x["score"])
    longs, shorts = longs[:5], shorts[:5]

    total_cap = int(float(get_config(sb, "system_pick_daily_capital", "300000")))

    def _mk_entries(cands, side):
        if not cands:
            return []
        per = total_cap / len(cands)
        out = []
        for c in cands:
            price = c["price"]
            shares = max(1, int(per / (price * 1000)))
            reason = (f"{'偏多攻擊' if side == 'long' else '偏空防守'}（評分{c['score']}）｜"
                      f"爆量比{c.get('vol_ratio', 0):.1f}｜漲跌{c.get('gain', 0):+.1f}%")
            out.append({
                "symbol": c["symbol"], "name": c["symbol"], "side": side,
                "entry_date": run_date, "entry_price": price, "shares": shares,
                "capital": round(shares * price * 1000, 0),
                "def_line": c["def_line"], "take_profit": c["take_profit"],
                "status": "pending",   # 待執行，等隔日開盤
                "select_reason": reason,
            })
        return out

    entries = _mk_entries(longs, "long") + _mk_entries(shorts, "short")
    if entries:
        sb.table("system_portfolio").insert(entries).execute()
    sb.table("system_run_log").insert({
        "run_date": run_date, "stage": "signal", "picked_count": len(longs) + len(shorts),
        "executed_count": 0, "gate_status": "pending",
        "note": f"選股：做多{len(longs)}檔、做空{len(shorts)}檔待執行",
    }).execute()
    notify_telegram(f"📋 [{run_date}] 選股完成\n做多候選：{len(longs)} 檔\n做空候選：{len(shorts)} 檔\n"
                    f"（明日開盤執行）" + ("\n今日無符合標的，明日空手" if not entries else ""))


def stage_gate(sb):
    """8:55 總經閘門：隔夜劇變則把今日 pending 標記暫緩。"""
    import yfinance as yf
    run_date = datetime.now().strftime("%Y-%m-%d")
    danger = []
    for name, sym in [("那斯達克", "^IXIC"), ("標普500", "^GSPC"), ("費半", "^SOX")]:
        try:
            hist = yf.Ticker(sym).history(period="5d").dropna(subset=["Close"])
            if len(hist) >= 2:
                pct = (float(hist["Close"].iloc[-1]) - float(hist["Close"].iloc[-2])) / float(hist["Close"].iloc[-2]) * 100
                if pct <= -2.0:
                    danger.append(f"{name} {pct:+.1f}%")
        except Exception:
            continue

    if danger:
        # 把今日 pending 全部標記 halted
        try:
            sb.table("system_portfolio").update({"status": "halted"}).eq("status", "pending").execute()
        except Exception:
            pass
        sb.table("system_run_log").insert({
            "run_date": run_date, "stage": "gate", "picked_count": 0, "executed_count": 0,
            "gate_status": "halted", "note": "隔夜劇變暫緩：" + "、".join(danger),
        }).execute()
        notify_telegram(f"🛑 [{run_date}] 總經閘門：隔夜劇變，今日暫緩進場\n" + "、".join(danger))
    else:
        sb.table("system_run_log").insert({
            "run_date": run_date, "stage": "gate", "picked_count": 0, "executed_count": 0,
            "gate_status": "normal", "note": "隔夜平穩，照計畫執行",
        }).execute()
        notify_telegram(f"✅ [{run_date}] 總經閘門：隔夜平穩，開盤照計畫執行")


def stage_execute(sb):
    """9:01 執行：pending → holding（進場）；同時檢查既有 holding 是否出場。"""
    run_date = datetime.now().strftime("%Y-%m-%d")

    # 1) 進場：pending 轉 holding（正式版可改用當日開盤價；這裡沿用選股時的 entry_price）
    try:
        pend = sb.table("system_portfolio").select("*").eq("status", "pending").execute().data or []
        for p in pend:
            sb.table("system_portfolio").update({"status": "holding"}).eq("id", p["id"]).execute()
        executed = len(pend)
    except Exception:
        executed = 0

    # 2) 出場：檢查 holding
    exits = []
    try:
        holds = sb.table("system_portfolio").select("*").eq("status", "holding").execute().data or []
        for h in holds:
            sig = compute_signal_for(h["symbol"])
            if not sig:
                continue
            cur = sig["price"]
            side = h.get("side", "long")
            entry = float(h.get("entry_price", 0) or 0)
            defl = float(h.get("def_line", 0) or 0)
            tp = float(h.get("take_profit", 0) or 0)
            reason = None
            if side == "long":
                if defl > 0 and cur <= defl:
                    reason = "stop_loss"
                elif tp > 0 and cur >= tp:
                    reason = "take_profit"
            else:
                if entry > 0 and cur >= entry * 1.03:
                    reason = "stop_loss"
                elif entry > 0 and cur <= entry * 0.95:
                    reason = "take_profit"
            if reason:
                shares = int(h.get("shares", 0) or 0)
                pnl = (cur - entry) * shares * 1000 if side == "long" else (entry - cur) * shares * 1000
                roi = (pnl / (entry * shares * 1000) * 100) if entry > 0 and shares > 0 else 0.0
                sb.table("system_portfolio").update({
                    "status": "closed", "exit_date": run_date, "exit_price": cur,
                    "exit_reason": reason, "realized_pnl": round(pnl, 0), "realized_roi": round(roi, 2),
                }).eq("id", h["id"]).execute()
                exits.append(f"{h['symbol']}({side},{reason},{roi:+.1f}%)")
    except Exception as e:
        print(f"出場檢查錯誤: {e}")

    sb.table("system_run_log").insert({
        "run_date": run_date, "stage": "execute", "picked_count": 0, "executed_count": executed,
        "gate_status": "normal", "note": f"進場{executed}檔；出場{len(exits)}檔",
    }).execute()
    msg = f"⚡ [{run_date}] 開盤執行\n進場：{executed} 檔"
    if exits:
        msg += "\n出場：" + "、".join(exits)
    notify_telegram(msg)


# ------------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", required=True, choices=["signal", "gate", "execute"])
    args = parser.parse_args()
    sb = get_supabase()
    if args.stage == "signal":
        stage_signal(sb)
    elif args.stage == "gate":
        stage_gate(sb)
    elif args.stage == "execute":
        stage_execute(sb)


if __name__ == "__main__":
    main()
