"""【P5 資料｜2026-10-12】ETF 日收盤價同步：FinMind TaiwanStockPrice（未還原）→ etf_price_daily。

依據：老闆 2026-10-12 確認「FinMind 條款允許程式化取用」（AI 未讀條款本文；此確認記於專案文件）。
本模組只負責抓取、驗證、寫入；FinMind 呼叫由呼叫端注入（system_scheduler 傳入 warroom_core._finmind_get，享有多 token 輪替與額度追蹤）。

驗證（死規則 1，不過就退回，不默默吃進去）：
  • 回傳列的 stock_id 必須等於查詢代號；日期必須合法；收盤必須是有限數且 >0；同日不重複。
  • 最新收盤與 etf_master.last_price 偏差 >5% → 整檔退回，不寫入。
  • 單檔失敗只影響該檔；連續多檔失敗（疑似額度用盡或服務異常）就提早停止，下次再補。

增量：system_config.etf_price_sync_state_v1 記每檔最後同步日；首次為回補 5 年，之後只抓最後日前 7 天起的資料（upsert 冪等）。
未還原價＋我們另加的配息（etf_dividend_events）＝含息報酬，因此不使用 TaiwanStockPriceAdj（還原表，只限 sponsor，且會與配息重複計算）。
"""
import datetime as dt
import math

TABLE = "etf_price_daily"
STATE_KEY = "etf_price_sync_state_v1"
DATASET = "TaiwanStockPrice"
API_URL = "https://api.finmindtrade.com/api/v4/data"
BACKFILL_YEARS = 5
OVERLAP_DAYS = 7
PRICE_CHECK_TOL = 0.05
PACING_SEC = 0.5
MAX_CONSECUTIVE_FAIL = 5
UPSERT_CHUNK = 500


def validate_rows(sym, raw_rows, master_last=None, master_date=None):
    """raw_rows：FinMind data 列表（dict）。回傳 (rows[(date, close)], problems[str])。
    problems 非空時整檔退回，rows 不可寫入。"""
    sym = str(sym or "").strip().upper()
    problems, seen, out = [], set(), {}
    for i, r in enumerate(raw_rows or [], 1):
        sid = str(r.get("stock_id") or "").strip().upper()
        if sid != sym:
            problems.append(f"第 {i} 列代號 {sid} 與查詢 {sym} 不符")
            continue
        d_raw = str(r.get("date") or "")[:10]
        try:
            d = dt.date.fromisoformat(d_raw).isoformat()
        except ValueError:
            problems.append(f"第 {i} 列日期不合法：{d_raw}")
            continue
        try:
            c = float(r.get("close"))
        except (TypeError, ValueError):
            problems.append(f"第 {i} 列收盤不是數字：{r.get('close')}")
            continue
        if not math.isfinite(c) or c < 0:
            problems.append(f"第 {i} 列收盤不合法：{c}")
            continue
        if c == 0:                      # FinMind 文件：無成交價的日子（掛牌前、停牌等）以 0 占位，略過不寫，不視為錯誤
            continue
        if d in seen:
            problems.append(f"{sym} 重複日期 {d}")
            continue
        seen.add(d)
        out[d] = c
    if problems:
        return [], problems[:20]
    rows = sorted(out.items())
    if not rows:
        return [], []                     # 查無資料：不是錯誤，只是沒有可寫入的列
    if master_last not in (None, "", 0):
        # 依現價日期那一天比對（etf_master 現價常比 FinMind 最新列早一天，不能拿最後一列比）。
        # 找不到該日的列就不比對（未驗證，由呼叫端計數），不因此退回。
        ref = dict(rows).get(str(master_date)[:10]) if master_date else None
        if ref is None and not master_date:
            ref = rows[-1][1]
        if ref is not None:
            dev = abs(ref - float(master_last)) / float(master_last)
            if dev > PRICE_CHECK_TOL:
                return [], [f"{sym} 現價日 {master_date or '最新'} 收盤 {ref} 與 etf_master 現價 {master_last} 相差 {dev:.1%}，超過 {PRICE_CHECK_TOL:.0%}，整檔退回"]
    return rows, []


def _upsert_rows(sb, sym, rows, source):
    payload = [{"symbol": sym, "trade_date": d, "close": c, "source": source} for d, c in rows]
    for i in range(0, len(payload), UPSERT_CHUNK):
        sb.table(TABLE).upsert(payload[i:i + UPSERT_CHUNK], on_conflict="symbol,trade_date").execute()


def _load_state(sb):
    try:
        r = sb.table("system_config").select("config_value").eq("config_key", STATE_KEY).limit(1).execute().data or []
        if r:
            import json
            v = r[0]["config_value"]
            v = json.loads(v) if isinstance(v, str) else v
            return v if isinstance(v, dict) else {}
    except Exception:
        pass
    return {}


def _save_state(sb, state):
    import json
    sb.table("system_config").upsert({"config_key": STATE_KEY, "config_value": json.dumps(state, ensure_ascii=False)},
                                     on_conflict="config_key").execute()


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


def run_price_sync(sb, fetch, today=None, sleep=None):
    """fetch(url, params) -> payload dict（含 data 列表），由呼叫端注入（生產環境傳 _finmind_get）。
    回傳 (msg, summary)。"""
    import time as _t
    today = today or dt.date.today()
    sleep = sleep or _t.sleep
    state = _load_state(sb)
    masters = _active_master(sb)
    summ = {"symbols": len(masters), "synced": 0, "rows_written": 0, "rejected": [], "errors": 0, "unverified": 0, "stopped_early": False}
    consec_fail = 0
    for m in masters:
        sym = str(m["symbol"]).strip().upper()
        last = state.get(sym)
        if last:
            start = (dt.date.fromisoformat(last) - dt.timedelta(days=OVERLAP_DAYS)).isoformat()
        else:
            start = (today - dt.timedelta(days=365 * BACKFILL_YEARS)).isoformat()
        params = {"dataset": DATASET, "data_id": sym, "start_date": start, "end_date": today.isoformat()}
        try:
            payload = fetch(API_URL, params) or {}
            raw = payload.get("data", []) if isinstance(payload, dict) else []
            consec_fail = 0
        except Exception as e:
            # FinMind 的 empty_data＝該檔沒有歷史（新掛牌常見），不是故障：不計錯誤、不計連續失敗
            if getattr(e, "reason", None) == "empty_data":
                summ["no_data"] = summ.get("no_data", 0) + 1
                consec_fail = 0
                sleep(PACING_SEC)
                continue
            # 其餘為單檔失敗，不中斷整批
            summ["errors"] += 1
            consec_fail += 1
            summ["rejected"].append(f"{sym} 抓取失敗：{type(e).__name__}: {str(e)[:120]}")
            summ.setdefault("first_error", f"{sym} {type(e).__name__}: {str(e)[:160]}")
            if consec_fail >= MAX_CONSECUTIVE_FAIL:
                summ["stopped_early"] = True
                break
            sleep(PACING_SEC)
            continue
        rows, problems = validate_rows(sym, raw, m.get("last_price"), m.get("price_date"))
        if problems:
            summ["rejected"] += problems
        elif rows:
            pd_ = str(m.get("price_date") or "")[:10]
            if pd_ and pd_ not in dict(rows):
                summ["unverified"] += 1
            _upsert_rows(sb, sym, rows, "finmind")
            state[sym] = rows[-1][0]
            summ["synced"] += 1
            summ["rows_written"] += len(rows)
        sleep(PACING_SEC)
    _save_state(sb, state)
    msg = (f"股價同步：ETF {summ['symbols']} 檔、寫入 {summ['synced']} 檔共 {summ['rows_written']} 筆；"
           f"無歷史 {summ.get('no_data', 0)} 檔；未能交叉比對 {summ['unverified']} 檔（現價日無對應列）；退回 {len(summ['rejected'])} 項、錯誤 {summ['errors']}"
           + ("；連續失敗已提早停止" if summ["stopped_early"] else ""))
    if summ["rejected"]:
        msg += "｜" + "；".join(summ["rejected"][:3])
    if summ.get("first_error"):
        msg += f"｜首個錯誤：{summ['first_error']}"
    return msg, summ


def upsert_manual(sb, sym, rows, source_url):
    """ETF 分頁手動匯入（P5 人工 CSV）寫入同一張表。rows 已通過 etf_portfolio 驗證。"""
    _upsert_rows(sb, sym, rows, source_url)
    return len(rows)
