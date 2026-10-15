# -*- coding: utf-8 -*-
"""
inst_history_backfill.py —— 三大法人買賣超歷史回補（2026-10-16，老闆指示：法人歷史太短，須回補，天數由我判斷）

為什麼 2 年（2024-10-01 起，約 490 個交易日）：
  • 現有因子最長窗口 60 日；回測要有樣本內外，至少涵蓋一段多頭＋一段整理／回檔。2 年足夠。
  • 資料庫目前約 234MB／500MB，inst_holding 每列約 175B；1,100 檔×490 日≈54 萬列≈95MB。5 年會再多約 150MB，風險過高。
  • FinMind 單檔查詢一次呼叫就能帶整段區間，日期範圍不影響呼叫數（每檔 1 次），所以天數只受容量限制。
來源：FinMind TaiwanStockInstitutionalInvestorsBuySell（免費、單檔）。欄位換算與既有慣例一致：
  foreign_buy＝Foreign_Investor、trust_buy＝Investment_Trust、dealer_buy＝Dealer（無則 Dealer_self＋Dealer_Hedging），股數/1000＝張。
驗證（死規則1）：日期格式與範圍、數值有限且單日絕對值 < 1e11 股、代號相符、同日重複合併；
  與資料庫已有日期交叉比對（容差 1 張），不一致只計數回報、不覆蓋既有資料（ignore_duplicates）。
續跑：進度存 system_config.inst_backfill_v1（done: {代號: 日期}）；每次最多 CAP 檔；連續失敗 5 檔提早停止。
"""
import datetime as dt
import json
import time as _time

import etf_price_sync as PS

START_DATE = "2024-10-01"
STATE_KEY = "inst_backfill_v1"
CAP = 250
MAX_ABS_SHARES = 1e11
TOL_LOTS = 1


def _num(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x and abs(x) != float("inf") else None


def parse_rows(sym, raw, start=START_DATE, today=None):
    """FinMind 原始列 → ([{date,symbol,foreign_buy,trust_buy,dealer_buy}], problems)。"""
    today = (today or dt.date.today()).isoformat()
    by = {}
    bad = 0
    for r in raw or []:
        try:
            d = str(r.get("date"))[:10]
            dt.date.fromisoformat(d)
        except (ValueError, TypeError):
            bad += 1
            continue
        if d < start or d > today or str(r.get("stock_id", sym)).strip() != sym:
            bad += 1
            continue
        b, s = _num(r.get("buy")), _num(r.get("sell"))
        if b is None or s is None or abs(b) > MAX_ABS_SHARES or abs(s) > MAX_ABS_SHARES:
            bad += 1
            continue
        by.setdefault(d, {}).setdefault(str(r.get("name")), 0.0)
        by[d][str(r.get("name"))] += (b - s)
    out = []
    for d, names in sorted(by.items()):
        dealer = names.get("Dealer")
        if dealer is None:
            dealer = names.get("Dealer_self", 0.0) + names.get("Dealer_Hedging", 0.0)
        out.append({"date": d, "symbol": sym,
                    "foreign_buy": int(names.get("Foreign_Investor", 0.0) / 1000),
                    "trust_buy": int(names.get("Investment_Trust", 0.0) / 1000),
                    "dealer_buy": int(dealer / 1000)})
    problems = []
    if raw and not out:
        problems.append("全部列驗證不過")
    elif bad and bad > 0.2 * max(len(raw), 1):
        problems.append(f"{bad}/{len(raw)} 列驗證不過")
    return out, problems


def cross_check(new_rows, existing):
    """existing：{date: (f,t,d)}。回傳 (overlap, mismatch)。"""
    ov = mm = 0
    for r in new_rows:
        e = existing.get(r["date"])
        if not e:
            continue
        ov += 1
        if (abs(e[0] - r["foreign_buy"]) > TOL_LOTS or abs(e[1] - r["trust_buy"]) > TOL_LOTS
                or abs(e[2] - r["dealer_buy"]) > TOL_LOTS):
            mm += 1
    return ov, mm


def load_state(sb):
    try:
        rows = sb.table("system_config").select("config_value").eq("config_key", STATE_KEY).execute().data or []
        if rows:
            v = rows[0]["config_value"]
            v = json.loads(v) if isinstance(v, str) else v
            if isinstance(v, dict) and v.get("start") == START_DATE:
                return v
    except Exception:
        pass
    return {"start": START_DATE, "done": {}}


def save_state(sb, st):
    sb.table("system_config").upsert({"config_key": STATE_KEY, "config_value": json.dumps(st, ensure_ascii=False)},
                                     on_conflict="config_key").execute()


def load_existing(sb, sym, start):
    out = {}
    try:
        rows = (sb.table("inst_holding").select("date,foreign_buy,trust_buy,dealer_buy").eq("symbol", sym)
                .gte("date", start).limit(2000).execute().data) or []
        for r in rows:
            out[str(r["date"])[:10]] = (int(r.get("foreign_buy") or 0), int(r.get("trust_buy") or 0), int(r.get("dealer_buy") or 0))
    except Exception:
        pass
    return out


def list_symbols(sb):
    """最新一個有資料日的 inst_holding 代號（排除 ETF/權證等非 4 碼普通股）。"""
    last = (sb.table("inst_holding").select("date").order("date", desc=True).limit(1).execute().data) or []
    if not last:
        return []
    got, off = [], 0
    while True:
        page = (sb.table("inst_holding").select("symbol").eq("date", last[0]["date"])
                .range(off, off + 999).execute().data) or []
        got += [str(r["symbol"]).strip() for r in page]
        if len(page) < 1000:
            break
        off += 1000
    return sorted({s for s in got if len(s) == 4 and s.isdigit() and s[0] != "0"})


def sync(sb, fetch, symbols, today=None, cap=CAP, sleep=None, extra_symbols=()):
    today = today or dt.date.today()
    sleep = sleep or _time.sleep
    st = load_state(sb)
    syms = sorted({str(s).strip() for s in list(symbols) + list(extra_symbols) if s})
    todo = [s for s in syms if s not in st["done"]][:cap]
    summ = {"candidates": len(syms), "already": len(syms) - len([s for s in syms if s not in st["done"]]),
            "todo": len(todo), "ok": 0, "no_data": 0, "errors": 0, "rows": 0, "overlap": 0, "mismatch": 0,
            "stopped_early": False}
    consec = 0
    for s in todo:
        raw, kind = PS._fetch_data(fetch, {"dataset": "TaiwanStockInstitutionalInvestorsBuySell", "data_id": s,
                                           "start_date": START_DATE, "end_date": today.isoformat()})
        if kind == "error":
            summ["errors"] += 1
            consec += 1
            if consec >= PS.MAX_CONSECUTIVE_FAIL:
                summ["stopped_early"] = True
                break
            sleep(PS.PACING_SEC)
            continue
        consec = 0
        if kind == "empty" or not raw:
            summ["no_data"] += 1
            st["done"][s] = today.isoformat()
            sleep(PS.PACING_SEC)
            continue
        rows, problems = parse_rows(s, raw, today=today)
        if problems:
            summ["errors"] += 1          # 驗證不過就退回，不寫入、不標完成（下次重試）
            sleep(PS.PACING_SEC)
            continue
        ov, mm = cross_check(rows, load_existing(sb, s, START_DATE))
        summ["overlap"] += ov
        summ["mismatch"] += mm
        try:
            for i in range(0, len(rows), 500):
                sb.table("inst_holding").upsert(rows[i:i + 500], on_conflict="date,symbol", ignore_duplicates=True).execute()
            summ["rows"] += len(rows)
            summ["ok"] += 1
            st["done"][s] = today.isoformat()
        except Exception:
            summ["errors"] += 1
        sleep(PS.PACING_SEC)
    try:
        save_state(sb, st)
    except Exception:
        pass
    left = len([s for s in syms if s not in st["done"]])
    note = (f"候選{summ['candidates']}檔，本次處理{summ['todo']}檔：成功{summ['ok']}（寫入{summ['rows']}列）、無資料{summ['no_data']}、"
            f"失敗{summ['errors']}｜與既有資料重疊{summ['overlap']}日、不一致{summ['mismatch']}日｜尚餘{left}檔"
            + ("｜連續失敗提早停止" if summ["stopped_early"] else ""))
    return note, summ
