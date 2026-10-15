"""【2026-10-16】查11 除權息尋寶雷達：近 5 年「除息後能否填息、多久填息」回測。

做法（死規則 4：能回測就先回測，不等資料慢慢累積）：
  • 除息事件：FinMind TaiwanStockDividendResult（未還原；含除息前收盤 before_price、除息參考價 after_price）。
  • 股價：FinMind TaiwanStockPrice（未還原日收盤），與配息同口徑。
  • 填息定義：除息日起（含除息日，記為第 1 個交易日），第一次收盤價 ≥ 除息前一日收盤（before_price）。
  • 觀察窗：到下一次除息日前一天為止；沒填到＝未填息；沒有下一次除息、資料已到最新日，且除息後未滿 120 個交易日＝「觀察中」，不計入填息率分母。
  • 只計「純現金股利（息）」。含股票股利（權息）的參考價有調整股數，與未還原價無法同口徑比較，另計數、不納入。

驗證（死規則 1，不過就退回）：
  • 配息列用 etf_price_sync.validate_dividends（代號、日期、金額>0、前後價差與配息相符）；不符整檔退回。
  • 股價列用 etf_price_sync.validate_rows。
  • 事件交叉比對：除息日當天（或其後第一個交易日）收盤與參考價相差 >15% 的事件剔除並計數（疑似分割或資料錯誤）。

存放：system_config，每檔一個 key「ex_div_fill_v1:代號」，只存彙總與逐次明細（約 0.5KB）。
FinMind 呼叫由呼叫端注入（system_scheduler 傳入 _finmind_get，享有多 token 輪替與等級不足冷卻）。
"""
import datetime as dt
import json
import statistics

import etf_price_sync as PS

KEY_PREFIX = "ex_div_fill_v1:"
YEARS = 5
REFRESH_DAYS = 30
DAILY_CAP = 150
EX_PRICE_TOL = 0.15
MIN_EVENTS_FOR_RATE = 3
PENDING_MAX_DAYS = 120


def _cash_only(kind):
    return str(kind or "").strip() in ("息", "除息")


def compute_events(div_rows, closes, asof=None):
    """div_rows：validate_dividends 的輸出（依日期排序）。closes：[(date, close)] 依日期排序（未還原）。
    回傳 (events, skipped)。events 每筆：
      {"ex","cash","before","status": filled|unfilled|pending, "days": 填息交易日數(含除息日為第1日)或已觀察日數}
    skipped：{"stock_div": 含股票股利筆數, "price_mismatch": 價格交叉比對不符筆數, "no_price": 無股價筆數}"""
    asof = asof or (closes[-1][0] if closes else "")
    dates = [d for d, _ in closes]
    px = [c for _, c in closes]
    skipped = {"stock_div": 0, "price_mismatch": 0, "no_price": 0}
    cash_rows = []
    for r in div_rows:
        if _cash_only(r.get("kind")):
            cash_rows.append(r)
        else:
            skipped["stock_div"] += 1
    events = []
    for i, r in enumerate(cash_rows):
        ex, before, after = r["ex_date"], float(r["before_price"]), float(r["after_price"])
        start = next((k for k, d in enumerate(dates) if d >= ex), None)
        if start is None:
            skipped["no_price"] += 1
            continue
        if abs(px[start] - after) / after > EX_PRICE_TOL:
            skipped["price_mismatch"] += 1
            continue
        next_ex = cash_rows[i + 1]["ex_date"] if i + 1 < len(cash_rows) else None
        end = len(dates)
        if next_ex:
            end = next((k for k, d in enumerate(dates) if d >= next_ex), len(dates))
        hit = next((k for k in range(start, end) if px[k] >= before), None)
        if hit is not None:
            events.append({"ex": ex, "cash": r["cash"], "before": before, "status": "filled", "days": hit - start + 1})
        else:
            observed = end - start
            # 後面沒有下一次除息、資料也到頭，且除息後才過不到 PENDING_MAX_DAYS 個交易日：還在觀察
            still_open = next_ex is None and observed < PENDING_MAX_DAYS
            events.append({"ex": ex, "cash": r["cash"], "before": before,
                           "status": "pending" if still_open else "unfilled", "days": observed})
    return events, skipped


def summarize(events, skipped=None):
    done = [e for e in events if e["status"] in ("filled", "unfilled")]
    filled = [e for e in done if e["status"] == "filled"]
    days = sorted(e["days"] for e in filled)
    out = {
        "n_done": len(done), "n_filled": len(filled), "n_unfilled": len(done) - len(filled),
        "n_pending": sum(1 for e in events if e["status"] == "pending"),
        "rate": round(len(filled) / len(done) * 100, 1) if done else None,
        "med_days": statistics.median(days) if days else None,
        "avg_days": round(sum(days) / len(days), 1) if days else None,
        "max_days": days[-1] if days else None,
        "min_days": days[0] if days else None,
        "within_5": sum(1 for d in days if d <= 5), "within_20": sum(1 for d in days if d <= 20),
        "within_60": sum(1 for d in days if d <= 60),
        "skipped": dict(skipped or {}),
        "events": [[e["ex"], e["cash"], e["status"][0], e["days"]] for e in events],   # status 首字：f/u/p
    }
    return out


def fetch_symbol(fetch, sym, today=None):
    """回傳 (summary|None, msg)。None＝查無或驗證不過（msg 說原因）。"""
    today = today or dt.date.today()
    sym = str(sym).strip().upper()
    start = (today - dt.timedelta(days=365 * YEARS + 45)).isoformat()
    params = {"data_id": sym, "start_date": start, "end_date": today.isoformat()}
    div_raw, k1 = PS._fetch_data(fetch, dict(params, dataset=PS.DIV_DATASET))
    if k1 == "error":
        return None, f"配息查詢失敗：{div_raw}"
    if not div_raw:
        return None, "近5年沒有除權息資料"
    divs, problems = PS.validate_dividends(sym, div_raw)
    if problems:
        return None, "配息驗證不過：" + "；".join(problems[:2])
    px_raw, k2 = PS._fetch_data(fetch, dict(params, dataset=PS.DATASET))
    if k2 == "error":
        return None, f"股價查詢失敗：{px_raw}"
    closes, problems = PS.validate_rows(sym, px_raw)
    if problems:
        return None, "股價驗證不過：" + "；".join(problems[:2])
    if not closes:
        return None, "查無股價"
    five_ago = (today - dt.timedelta(days=365 * YEARS)).isoformat()
    divs = [d for d in divs if d["ex_date"] >= five_ago]
    events, skipped = compute_events(divs, closes, asof=closes[-1][0])
    if not events and not any(skipped.values()):
        return None, "近5年沒有可回測的除息事件"
    s = summarize(events, skipped)
    s["asof"] = today.isoformat()
    s["last_price_date"] = closes[-1][0]
    return s, "ok"


def empty_summary(today, reason):
    return {"n_done": 0, "n_filled": 0, "n_unfilled": 0, "n_pending": 0, "rate": None, "med_days": None,
            "avg_days": None, "max_days": None, "min_days": None, "within_5": 0, "within_20": 0, "within_60": 0,
            "skipped": {}, "events": [], "asof": today.isoformat(), "no_data": True, "reason": str(reason)[:80]}


def due_symbols(symbols, cache, today, cap=DAILY_CAP):
    """沒算過的優先，其次最舊；每檔至少隔 REFRESH_DAYS 天；最多 cap 檔。"""
    never, old = [], []
    for s in symbols:
        a = (cache.get(s) or {}).get("asof")
        if not a:
            never.append(s)
            continue
        try:
            age = (today - dt.date.fromisoformat(a)).days
        except ValueError:
            never.append(s)
            continue
        if age >= REFRESH_DAYS:
            old.append((a, s))
    return (never + [s for _, s in sorted(old)])[:max(0, int(cap))]


def load_cache(sb, symbols):
    """讀每檔彙總，回傳 {symbol: summary}。讀不到的檔不在結果內。"""
    out = {}
    syms = [str(s).strip().upper() for s in symbols if s]
    for i in range(0, len(syms), 100):
        keys = [KEY_PREFIX + s for s in syms[i:i + 100]]
        try:
            rows = sb.table("system_config").select("config_key,config_value").in_("config_key", keys).execute().data or []
        except Exception:
            continue
        for r in rows:
            try:
                v = r["config_value"]
                v = json.loads(v) if isinstance(v, str) else v
                if isinstance(v, dict):
                    out[r["config_key"][len(KEY_PREFIX):]] = v
            except (ValueError, KeyError, TypeError):
                continue
    return out


def save_symbol(sb, sym, summary):
    sb.table("system_config").upsert(
        {"config_key": KEY_PREFIX + sym, "config_value": json.dumps(summary, ensure_ascii=False)},
        on_conflict="config_key").execute()


def sync(sb, fetch, symbols, today=None, cap=DAILY_CAP, sleep=None):
    """對 symbols 補算填息統計。回傳 (msg, summ)。連續 5 檔失敗提早停止（疑似額度或服務異常）。"""
    import time as _t
    today = today or dt.date.today()
    sleep = sleep or _t.sleep
    syms = sorted({str(s).strip().upper() for s in symbols if s})
    cache = load_cache(sb, syms)
    todo = due_symbols(syms, cache, today, cap)
    summ = {"candidates": len(syms), "due": len(todo), "ok": 0, "no_data": 0, "errors": 0, "stopped_early": False}
    consec = 0
    for s in todo:
        res, msg = fetch_symbol(fetch, s, today)
        if res is None:
            is_nodata = ("沒有" in msg or "查無" in msg)
            summ["no_data" if is_nodata else "errors"] += 1
            if is_nodata:
                # 新上市或從未配息：留一筆「無紀錄」標記，30 天內不重查（否則每晚白打 2 次 FinMind）
                try:
                    save_symbol(sb, s, empty_summary(today, msg))
                except Exception:
                    pass
            consec = consec + 1 if "失敗" in msg else 0
            if consec >= PS.MAX_CONSECUTIVE_FAIL:
                summ["stopped_early"] = True
                break
            sleep(PS.PACING_SEC)
            continue
        consec = 0
        try:
            save_symbol(sb, s, res)
            summ["ok"] += 1
        except Exception:
            summ["errors"] += 1
        sleep(PS.PACING_SEC)
    note = (f"候選{summ['candidates']}檔，本次應算{summ['due']}檔：成功{summ['ok']}、無資料{summ['no_data']}、"
            f"失敗{summ['errors']}" + ("｜連續失敗提早停止" if summ["stopped_early"] else ""))
    return note, summ


# ------------------------------------------------------------------ 顯示用
def fmt_stock(s):
    """單檔一行：近5年填息 N/M 次（X%），中位 K 天。樣本不足誠實標註。"""
    if not s:
        return "待排程回測"
    n = s.get("n_done", 0)
    if s.get("no_data"):
        return "近5年無除權息紀錄（新上市或未配息）"
    if n == 0:
        extra = f"｜觀察中{s['n_pending']}次" if s.get("n_pending") else ""
        return "近5年無可回測的完整除息事件" + extra
    txt = f"近5年填息{s['n_filled']}/{n}次"
    if n < MIN_EVENTS_FOR_RATE:
        txt += f"（樣本僅{n}次，不足採信）"
    else:
        txt += f"（{s['rate']}%）"
    if s.get("med_days") is not None:
        txt += f"｜中位{s['med_days']:g}天、最久{s['max_days']}天"
    if s.get("n_pending"):
        txt += f"｜另有{s['n_pending']}次觀察中"
    return txt


def fmt_group(stats):
    """多檔彙總（給「查11」展開區標題）：只合併樣本≥MIN_EVENTS_FOR_RATE 次的個股。"""
    ok = [s for s in stats if s and s.get("n_done", 0) >= 1]
    if not ok:
        return "尚無填息回測資料（排程每晚補算）"
    n = sum(s["n_done"] for s in ok)
    f = sum(s["n_filled"] for s in ok)
    all_days = []
    for s in ok:
        all_days += [e[3] for e in s["events"] if e[2] == "f"]
    all_days.sort()
    txt = f"近5年填息率{f / n * 100:.0f}%（{len(ok)}檔、{f}/{n}次）"
    if all_days:
        txt += f"｜中位{statistics.median(all_days):g}天"
    return txt


def fmt_events(s):
    """逐次明細，一檔一串：2025-07-16 息1.5 ✅5天 ｜ 2024-… ❌未填息"""
    if not s:
        return ""
    parts = []
    for ex, cash, st, days in s.get("events", []):
        cash_txt = f"{float(cash):g}"
        if st == "f":
            parts.append(f"{ex} 息{cash_txt} ✅{days}天")
        elif st == "u":
            parts.append(f"{ex} 息{cash_txt} ❌未填息")
        else:
            parts.append(f"{ex} 息{cash_txt} ⏳觀察中(已{days}天)")
    return " ｜ ".join(parts)
