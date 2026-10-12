"""【P5 資料｜2026-10-12】ETF 日收盤價、配息、公司行為同步（FinMind，全部未還原、同口徑）。

依據：老闆 2026-10-12 確認「FinMind 條款允許程式化取用」（政策頁另以瀏覽器渲染閱讀，結論記於專案文件）。
本模組只負責抓取、驗證、寫入；FinMind 呼叫由呼叫端注入（system_scheduler 傳入 warroom_core._finmind_get，享有多 token 輪替與額度追蹤）。

資料集（皆為 FinMind 免費方案，見 https://finmind.github.io/llms-full.txt）：
  • TaiwanStockPrice          日收盤（未還原）              → etf_price_daily
  • TaiwanStockDividendResult 除權息結果（未還原每股配息）   → etf_dividend_result
  • TaiwanStockSplitPrice     分割／反分割（全市場一次）      → system_config.etf_corp_actions_v1
  • TaiwanStockDelisting      下市櫃名單（全市場一次）        → system_config.etf_corp_actions_v1

驗證（死規則 1，不過就退回，不默默吃進去）：
  • 股價：stock_id 等於查詢代號；日期合法；收盤有限且 ≥0（0＝FinMind 無價日占位，略過）；同日不重複；
    與 etf_master 現價交叉比對：取現價日當天，沒有就取現價日之前最後一筆（成交稀少的 ETF 沒成交日沒有列）；偏差 >5% 整檔退回。
  • 配息：stock_id 相符；日期合法；配息 >0；除息前後參考價 >0；純現金配息（類型「息」）時「除息前價−除息參考價」須與配息相符（容許一檔價位跳動）。不符整檔退回。
  • 分割：日期合法、前後價 >0、類型為分割／反分割、比例不得近於 1；不符的列剔除並計數。
  • 單檔失敗只影響該檔；連續 5 檔失敗（疑似額度或服務異常）提早停止，下次再補。

省額度：官方下市名單內且 etf_master 無現價者不抓；回傳 empty_data 的代號 30 天內不重抓；配息每檔每 7 天才重抓、每天上限 DIV_DAILY_CAP 檔。
"""
import datetime as dt
import json
import math

API_URL = "https://api.finmindtrade.com/api/v4/data"
TABLE = "etf_price_daily"
DIV_TABLE = "etf_dividend_result"
STATE_KEY = "etf_price_sync_state_v1"
NODATA_KEY = "etf_price_nodata_v1"
DIV_STATE_KEY = "etf_dividend_sync_state_v1"
CORP_KEY = "etf_corp_actions_v1"
DATASET = "TaiwanStockPrice"
DIV_DATASET = "TaiwanStockDividendResult"
SPLIT_DATASET = "TaiwanStockSplitPrice"
DELIST_DATASET = "TaiwanStockDelisting"
BACKFILL_YEARS = 5
OVERLAP_DAYS = 7
PRICE_CHECK_TOL = 0.05
PACING_SEC = 0.5
MAX_CONSECUTIVE_FAIL = 5
UPSERT_CHUNK = 500
NO_DATA_RETRY_DAYS = 30
DIV_REFRESH_DAYS = 7
DIV_DAILY_CAP = 70
DIV_BACKFILL_CAP = 500


# ------------------------------------------------------------------ 共用
def _cfg_get(sb, key, default):
    try:
        r = sb.table("system_config").select("config_value").eq("config_key", key).limit(1).execute().data or []
        if r:
            v = r[0]["config_value"]
            v = json.loads(v) if isinstance(v, str) else v
            return v if isinstance(v, type(default)) else default
    except Exception:
        pass
    return default


def _cfg_set(sb, key, value):
    sb.table("system_config").upsert({"config_key": key, "config_value": json.dumps(value, ensure_ascii=False)},
                                     on_conflict="config_key").execute()


def _load_state(sb):
    return _cfg_get(sb, STATE_KEY, {})


def _save_state(sb, state):
    _cfg_set(sb, STATE_KEY, state)


def _iso(d_raw):
    try:
        return dt.date.fromisoformat(str(d_raw or "")[:10]).isoformat()
    except ValueError:
        return ""


def _num(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def _active_master(sb):
    out, off = [], 0
    while True:
        r = sb.table("etf_master").select("symbol,last_price,price_date,active").range(off, off + 999).execute()
        rows = r.data or []
        out += rows
        if len(rows) < 1000:
            break
        off += 1000
    return [m for m in out if m.get("active") is not False and m.get("symbol")]


def _fetch_data(fetch, params):
    """回傳 (rows, kind)：kind=ok / empty / error；error 時 rows 為錯誤字串。"""
    try:
        payload = fetch(API_URL, params) or {}
        return (payload.get("data", []) if isinstance(payload, dict) else []), "ok"
    except Exception as e:
        if getattr(e, "reason", None) == "empty_data":
            return [], "empty"
        return f"{type(e).__name__}: {str(e)[:160]}", "error"


# ------------------------------------------------------------------ 股價
def ref_close(rows, master_date):
    """交叉比對用的收盤：現價日當天；沒有就取現價日之前最後一筆；都沒有回 None。
    master_date 為空時取最後一筆。"""
    if not rows:
        return None
    if not master_date:
        return rows[-1][1]
    md = str(master_date)[:10]
    prior = [c for d, c in rows if d <= md]
    return prior[-1] if prior else None


def validate_rows(sym, raw_rows, master_last=None, master_date=None):
    """raw_rows：FinMind data 列表（dict）。回傳 (rows[(date, close)], problems[str])。problems 非空時整檔退回。"""
    sym = str(sym or "").strip().upper()
    problems, out = [], {}
    for i, r in enumerate(raw_rows or [], 1):
        sid = str(r.get("stock_id") or "").strip().upper()
        if sid != sym:
            problems.append(f"第 {i} 列代號 {sid} 與查詢 {sym} 不符")
            continue
        d = _iso(r.get("date"))
        if not d:
            problems.append(f"第 {i} 列日期不合法：{r.get('date')}")
            continue
        c = _num(r.get("close"))
        if c is None:
            problems.append(f"第 {i} 列收盤不是數字：{r.get('close')}")
            continue
        if c < 0:
            problems.append(f"第 {i} 列收盤不合法：{c}")
            continue
        if c == 0:                      # FinMind 文件：無成交價的日子（掛牌前、停牌等）以 0 占位，略過不寫，不視為錯誤
            continue
        if d in out:
            problems.append(f"{sym} 重複日期 {d}")
            continue
        out[d] = c
    if problems:
        return [], problems[:20]
    rows = sorted(out.items())
    if not rows:
        return [], []                     # 查無資料：不是錯誤
    if master_last not in (None, "", 0):
        ref = ref_close(rows, master_date)
        if ref is not None:
            dev = abs(ref - float(master_last)) / float(master_last)
            if dev > PRICE_CHECK_TOL:
                return [], [f"{sym} 現價日 {master_date or '最新'}（或之前最後成交）收盤 {ref} 與 etf_master 現價 {master_last} "
                            f"相差 {dev:.1%}，超過 {PRICE_CHECK_TOL:.0%}，整檔退回"]
    return rows, []


def _upsert_rows(sb, sym, rows, source):
    payload = [{"symbol": sym, "trade_date": d, "close": c, "source": source} for d, c in rows]
    for i in range(0, len(payload), UPSERT_CHUNK):
        sb.table(TABLE).upsert(payload[i:i + UPSERT_CHUNK], on_conflict="symbol,trade_date").execute()


def should_skip(sym, master_row, corp, nodata, today):
    """官方下市名單內且 etf_master 沒有現價 → 跳過；30 天內確認過無資料 → 跳過。回傳原因或 ''。"""
    if sym in (corp.get("delisted") or {}) and master_row.get("last_price") in (None, ""):
        return "delisted"
    nd = nodata.get(sym)
    if nd:
        try:
            if (today - dt.date.fromisoformat(nd)).days < NO_DATA_RETRY_DAYS:
                return "no_data_recent"
        except ValueError:
            pass
    return ""


def run_price_sync(sb, fetch, today=None, sleep=None, masters=None, corp=None):
    """fetch(url, params) -> payload dict（含 data 列表）。回傳 (msg, summary)。"""
    import time as _t
    today = today or dt.date.today()
    sleep = sleep or _t.sleep
    state = _load_state(sb)
    nodata = _cfg_get(sb, NODATA_KEY, {})
    corp = corp if corp is not None else _cfg_get(sb, CORP_KEY, {})
    masters = masters if masters is not None else _active_master(sb)
    summ = {"symbols": len(masters), "synced": 0, "rows_written": 0, "rejected": [], "errors": 0, "unverified": 0,
            "no_data": 0, "skipped_delisted": 0, "skipped_no_data": 0, "stopped_early": False}
    consec_fail = 0
    for m in masters:
        sym = str(m["symbol"]).strip().upper()
        why = should_skip(sym, m, corp, nodata, today)
        if why:
            summ["skipped_delisted" if why == "delisted" else "skipped_no_data"] += 1
            continue
        last = state.get(sym)
        start = ((dt.date.fromisoformat(last) - dt.timedelta(days=OVERLAP_DAYS)) if last
                 else (today - dt.timedelta(days=365 * BACKFILL_YEARS))).isoformat()
        raw, kind = _fetch_data(fetch, {"dataset": DATASET, "data_id": sym, "start_date": start, "end_date": today.isoformat()})
        if kind == "empty":
            summ["no_data"] += 1
            nodata[sym] = today.isoformat()
            consec_fail = 0
            sleep(PACING_SEC)
            continue
        if kind == "error":
            summ["errors"] += 1
            consec_fail += 1
            summ["rejected"].append(f"{sym} 抓取失敗：{raw}")
            summ.setdefault("first_error", f"{sym} {raw}")
            if consec_fail >= MAX_CONSECUTIVE_FAIL:
                summ["stopped_early"] = True
                break
            sleep(PACING_SEC)
            continue
        consec_fail = 0
        rows, problems = validate_rows(sym, raw, m.get("last_price"), m.get("price_date"))
        if problems:
            summ["rejected"] += problems
        elif rows:
            if m.get("last_price") not in (None, "", 0) and ref_close(rows, m.get("price_date")) is None:
                summ["unverified"] += 1
            _upsert_rows(sb, sym, rows, "finmind")
            state[sym] = rows[-1][0]
            nodata.pop(sym, None)
            summ["synced"] += 1
            summ["rows_written"] += len(rows)
        sleep(PACING_SEC)
    _save_state(sb, state)
    _cfg_set(sb, NODATA_KEY, nodata)
    msg = (f"股價：ETF {summ['symbols']} 檔、寫入 {summ['synced']} 檔共 {summ['rows_written']} 筆；"
           f"跳過下市 {summ['skipped_delisted']}、跳過近期無資料 {summ['skipped_no_data']}、新確認無資料 {summ['no_data']}；"
           f"未能交叉比對 {summ['unverified']}；退回 {len(summ['rejected'])} 項、錯誤 {summ['errors']}"
           + ("；連續失敗已提早停止" if summ["stopped_early"] else ""))
    if summ["rejected"]:
        msg += "｜" + "；".join(summ["rejected"][:3])
    return msg, summ


def upsert_manual(sb, sym, rows, source_url):
    """ETF 分頁手動匯入（P5 人工 CSV）寫入同一張表。rows 已通過 etf_portfolio 驗證。"""
    _upsert_rows(sb, sym, rows, source_url)
    return len(rows)


# ------------------------------------------------------------------ 公司行為（分割、下市）
def parse_splits(raw, symbols):
    """回傳 (splits[dict], problems[str])。只保留 symbols 內的代號。factor＝分割前價÷分割後參考價（1 拆 4 ≈ 4）。"""
    keep, out, problems = {str(s).upper() for s in symbols}, [], []
    for i, r in enumerate(raw or [], 1):
        sym = str(r.get("stock_id") or "").strip().upper()
        if sym not in keep:
            continue
        d = _iso(r.get("date"))
        b, a = _num(r.get("before_price")), _num(r.get("after_price"))
        typ = str(r.get("type") or "").strip()
        if not d or not b or not a or b <= 0 or a <= 0 or typ not in ("分割", "反分割"):
            problems.append(f"分割第 {i} 列欄位不合法（{sym} {r.get('date')} {typ}）")
            continue
        f = b / a
        if 0.9 <= f <= 1.1:
            problems.append(f"分割第 {i} 列比例近於 1（{sym} {d} {f:.3f}），不採用")
            continue
        out.append({"date": d, "symbol": sym, "type": typ, "before": b, "after": a, "factor": round(f, 6)})
    out.sort(key=lambda x: (x["symbol"], x["date"]))
    return out, problems


def parse_delisting(raw, symbols):
    keep, out = {str(s).upper() for s in symbols}, {}
    for r in raw or []:
        sym = str(r.get("stock_id") or "").strip().upper()
        d = _iso(r.get("date"))
        if sym in keep and d:
            out[sym] = max(out.get(sym, ""), d)
    return out


def sync_corporate_actions(sb, fetch, symbols, today=None):
    """分割與下市名單各 1 次呼叫（全市場）。失敗時保留上次結果。回傳 (corp, msg)。"""
    today = today or dt.date.today()
    prev = _cfg_get(sb, CORP_KEY, {})
    corp = dict(prev)
    notes = []
    raw, kind = _fetch_data(fetch, {"dataset": SPLIT_DATASET})
    if kind == "error":
        notes.append(f"分割表抓取失敗（沿用上次）：{raw}")
    else:
        splits, probs = parse_splits(raw, symbols)
        corp["splits"] = splits
        notes.append(f"分割 {len(splits)} 筆" + (f"、剔除 {len(probs)} 列" if probs else ""))
    raw, kind = _fetch_data(fetch, {"dataset": DELIST_DATASET})
    if kind == "error":
        notes.append(f"下市表抓取失敗（沿用上次）：{raw}")
    else:
        corp["delisted"] = parse_delisting(raw, symbols)
        notes.append(f"下市 {len(corp['delisted'])} 檔")
    corp["updated"] = today.isoformat()
    _cfg_set(sb, CORP_KEY, corp)
    return corp, "公司行為：" + "、".join(notes)


# ------------------------------------------------------------------ 配息（除權息結果）
def validate_dividends(sym, raw_rows):
    """回傳 (rows[dict], problems)。只收含現金股利（『息』）的列；純除息時以除息前後參考價核對金額。"""
    sym = str(sym or "").strip().upper()
    out, problems = {}, []
    for i, r in enumerate(raw_rows or [], 1):
        sid = str(r.get("stock_id") or "").strip().upper()
        if sid != sym:
            problems.append(f"配息第 {i} 列代號 {sid} 與查詢 {sym} 不符")
            continue
        kind = str(r.get("stock_or_cache_dividend") or "").strip()
        if "息" not in kind:
            continue                       # 純除權（股票股利）不計入現金配息
        d = _iso(r.get("date"))
        cash = _num(r.get("stock_and_cache_dividend"))
        b, a = _num(r.get("before_price")), _num(r.get("after_price"))
        if not d or cash is None or cash <= 0 or not b or not a or b <= 0 or a <= 0:
            problems.append(f"{sym} 配息第 {i} 列欄位不合法（{r.get('date')}）")
            continue
        if kind in ("息", "除息"):        # FinMind 實際值為「息」（純現金）；「權息」含股票股利，不做價差核對
            tol = 0.06 + 0.01 * cash     # 參考價依升降單位四捨五入，容許約一檔跳動
            if abs((b - a) - cash) > tol:
                problems.append(f"{sym} {d} 配息 {cash} 與除息前後價差 {b - a:.4f} 不符")
                continue
        if d in out:
            problems.append(f"{sym} 配息重複日期 {d}")
            continue
        out[d] = {"ex_date": d, "cash": cash, "before_price": b, "after_price": a, "kind": kind}
    if problems:
        return [], problems[:10]
    return [out[k] for k in sorted(out)], []


def due_for_dividends(symbols, div_state, today, cap):
    """需要（重新）抓配息的代號：從未抓過優先，其次最久未抓；每檔至少間隔 DIV_REFRESH_DAYS 天；最多 cap 檔。"""
    never, old = [], []
    for s in symbols:
        last = div_state.get(s)
        if not last:
            never.append(s)
            continue
        try:
            age = (today - dt.date.fromisoformat(last)).days
        except ValueError:
            never.append(s)
            continue
        if age >= DIV_REFRESH_DAYS:
            old.append((last, s))
    return (never + [s for _, s in sorted(old)])[:max(0, int(cap))]


def sync_dividends(sb, fetch, symbols, today=None, cap=DIV_DAILY_CAP, sleep=None):
    """回傳 (msg, summary)。symbols 應為已有股價資料的代號（避免浪費在已下市代號）。"""
    import time as _t
    today = today or dt.date.today()
    sleep = sleep or _t.sleep
    st_ = _cfg_get(sb, DIV_STATE_KEY, {})
    todo = due_for_dividends([str(s).upper() for s in symbols], st_, today, cap)
    summ = {"due": len(todo), "fetched": 0, "symbols_with_div": 0, "rows_written": 0, "no_div": 0,
            "rejected": [], "errors": 0, "stopped_early": False}
    consec_fail = 0
    start = (today - dt.timedelta(days=365 * BACKFILL_YEARS)).isoformat()
    for sym in todo:
        raw, kind = _fetch_data(fetch, {"dataset": DIV_DATASET, "data_id": sym, "start_date": start, "end_date": today.isoformat()})
        if kind == "error":
            summ["errors"] += 1
            consec_fail += 1
            summ["rejected"].append(f"{sym} 配息抓取失敗：{raw}")
            if consec_fail >= MAX_CONSECUTIVE_FAIL:
                summ["stopped_early"] = True
                break
            sleep(PACING_SEC)
            continue
        consec_fail = 0
        summ["fetched"] += 1
        rows, problems = ([], []) if kind == "empty" else validate_dividends(sym, raw)
        if problems:
            summ["rejected"] += problems
        elif rows:
            payload = [dict(r, symbol=sym, source="finmind") for r in rows]
            for i in range(0, len(payload), UPSERT_CHUNK):
                sb.table(DIV_TABLE).upsert(payload[i:i + UPSERT_CHUNK], on_conflict="symbol,ex_date").execute()
            summ["symbols_with_div"] += 1
            summ["rows_written"] += len(rows)
            st_[sym] = today.isoformat()
        else:
            summ["no_div"] += 1
            st_[sym] = today.isoformat()
        sleep(PACING_SEC)
    _cfg_set(sb, DIV_STATE_KEY, st_)
    msg = (f"配息：本次應抓 {summ['due']} 檔、完成 {summ['fetched']}；有配息 {summ['symbols_with_div']} 檔共 {summ['rows_written']} 筆、"
           f"無配息 {summ['no_div']}；退回 {len(summ['rejected'])} 項、錯誤 {summ['errors']}"
           + ("；連續失敗已提早停止" if summ["stopped_early"] else ""))
    if summ["rejected"]:
        msg += "｜" + "；".join(summ["rejected"][:3])
    return msg, summ


# ------------------------------------------------------------------ 整體流程（排程用）
def run_all(sb, fetch, today=None, div_cap=DIV_DAILY_CAP, sleep=None):
    """公司行為 → 股價 → 配息。回傳 (msg, summary)。"""
    today = today or dt.date.today()
    masters = _active_master(sb)
    syms = [str(m["symbol"]).strip().upper() for m in masters]
    corp, corp_msg = sync_corporate_actions(sb, fetch, syms, today)
    p_msg, p_summ = run_price_sync(sb, fetch, today=today, sleep=sleep, masters=masters, corp=corp)
    with_price = sorted(_load_state(sb).keys())
    d_msg, d_summ = sync_dividends(sb, fetch, with_price, today=today, cap=div_cap, sleep=sleep)
    summ = {"price": p_summ, "dividend": d_summ, "splits": len(corp.get("splits") or []),
            "delisted": len(corp.get("delisted") or {})}
    return f"{corp_msg}｜{p_msg}｜{d_msg}", summ
