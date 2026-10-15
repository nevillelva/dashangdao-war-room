"""大盤與衍生性商品（FinMind 免費資料集，2026-10-16 實測可用）。
期貨三大法人、選擇權三大法人、整體法人、整體融資融券、TXO 未平倉 P/C ratio → official_open_data（dataset=mkt_*）。
驗證：日期須為 YYYY-MM-DD 且不晚於今天、數值須可轉整數且 >=0；退回超過 30% 整批不寫。"""
import datetime as dt

URL = "https://api.finmindtrade.com/api/v4/data"
MAX_REJECT = 0.3
SRC = "https://api.finmindtrade.com/api/v4/data"


def _int(v):
    try:
        x = int(float(str(v).replace(",", "")))
        return x if x >= 0 else None
    except Exception:
        return None


def _date_ok(s, today):
    try:
        d = dt.date.fromisoformat(str(s))
        return d <= today and (today - d).days <= 40
    except Exception:
        return False


def _rows(ds, key_fn, fields, raw, today):
    out, rej = [], 0
    for r in raw:
        if not _date_ok(r.get("date"), today):
            rej += 1
            continue
        p = {f: _int(r.get(f)) for f in fields}
        if any(v is None for v in p.values()):
            rej += 1
            continue
        out.append({"dataset": ds, "data_date": str(r["date"]), "symbol": key_fn(r), "payload": p, "source_url": SRC})
    return out, rej


OI = ["long_deal_volume", "short_deal_volume", "long_open_interest_balance_volume", "short_open_interest_balance_volume"]


def parse_fut(raw, today):
    return _rows("mkt_fut_inst", lambda r: f"{r.get('futures_id')}|{r.get('institutional_investors')}", OI, raw, today)


def parse_opt(raw, today):
    return _rows("mkt_opt_inst", lambda r: f"{r.get('option_id')}|{r.get('call_put')}|{r.get('institutional_investors')}", OI, raw, today)


def parse_total_inst(raw, today):
    return _rows("mkt_total_inst", lambda r: str(r.get("name")), ["buy", "sell"], raw, today)


def parse_total_margin(raw, today):
    return _rows("mkt_total_margin", lambda r: str(r.get("name")), ["TodayBalance", "YesBalance", "buy", "sell"], raw, today)


def pc_ratio(raw, today):
    """TXO 未平倉 P/C（各到期合約加總；只取一般交易時段 position）。回傳 (rows, rej)。"""
    agg, rej = {}, 0
    for r in raw:
        if not _date_ok(r.get("date"), today):
            rej += 1
            continue
        if str(r.get("trading_session", "position")) == "after_market":
            continue
        oi, vol = _int(r.get("open_interest")), _int(r.get("volume"))
        cp = str(r.get("call_put"))
        if oi is None or vol is None or cp not in ("call", "put"):
            rej += 1
            continue
        a = agg.setdefault(str(r["date"]), {"call_oi": 0, "put_oi": 0, "call_vol": 0, "put_vol": 0})
        a[cp + "_oi"] += oi
        a[cp + "_vol"] += vol
    out = []
    for d, a in agg.items():
        if a["call_oi"] <= 0 or a["call_vol"] <= 0:
            continue
        a["pc_oi"] = round(a["put_oi"] / a["call_oi"] * 100, 1)
        a["pc_vol"] = round(a["put_vol"] / a["call_vol"] * 100, 1)
        out.append({"dataset": "mkt_pc_ratio", "data_date": d, "symbol": "TXO", "payload": a, "source_url": SRC})
    return out, rej


DATASETS = [("TaiwanFuturesInstitutionalInvestors", {"data_id": "TX"}, parse_fut),
            ("TaiwanOptionInstitutionalInvestors", {"data_id": "TXO"}, parse_opt),
            ("TaiwanStockTotalInstitutionalInvestors", {}, parse_total_inst),
            ("TaiwanStockTotalMarginPurchaseShortSale", {}, parse_total_margin),
            ("TaiwanOptionDaily", {"data_id": "TXO"}, pc_ratio)]


def sync(sb, get, today=None, days=10, sleep=None):
    import time as _t
    today = today or dt.date.today()
    sleep = sleep or _t.sleep
    start = (today - dt.timedelta(days=days)).isoformat()
    summ = {"datasets": {}, "errors": 0, "rows": 0}
    for ds, extra, fn in DATASETS:
        info = summ["datasets"][ds] = {"rows": 0, "rejected": 0, "error": None}
        params = {"dataset": ds, "start_date": start}
        params.update(extra)
        try:
            payload = get(URL, params)
            raw = payload.get("data", []) if isinstance(payload, dict) else []
            rows, rej = fn(raw, today)
        except Exception as e:
            info["error"] = type(e).__name__ + ":" + str(getattr(e, "reason", "") or e)[:60]
            summ["errors"] += 1
            if "level" in info["error"].lower() or "400" in info["error"]:
                break
            continue
        info["rejected"] = rej
        if raw and rej > MAX_REJECT * len(raw):
            info["error"] = f"退回 {rej}/{len(raw)} 超過30%，整批不寫"
            summ["errors"] += 1
            continue
        if rows:
            for i in range(0, len(rows), 500):
                sb.table("official_open_data").upsert(rows[i:i + 500], on_conflict="dataset,data_date,symbol").execute()
            info["rows"] = len(rows)
            summ["rows"] += len(rows)
        sleep(1.5)
    return f"大盤衍生性：{summ['rows']} 列，{summ['errors']} 個錯誤", summ
