"""【2026-10-12】櫃買 OpenAPI「上櫃熱門股證券商進出排行」(tpex_active_broker_volume) 補充來源。
決策（老闆 10/12）：分點維持『人工上傳 bsr CSV』為主，這份官方資料當補充。
• 條款：櫃買網站使用條款第 5 條禁止自動化下載，但『經櫃買同意之方式』除外；OpenAPI 是站方公開提供的介接方式，
  資料已授權政府資料開放平臺（第 7 條）→ 可用。每日只打 1 次。
• 層級：券商『總公司』非分點，只有熱門股（約 30 檔）× 前 10 家 → 不進評分，只顯示。
• 驗證（死規則 1）：日期格式／是否為最近交易日、代號格式、數值非負、每檔券商數、與當日股價表交叉比對，不過就退回。
純函式可離線測試。"""
import re
from datetime import datetime, timedelta

URL = "https://www.tpex.org.tw/openapi/v1/tpex_active_broker_volume"
_CODE_RE = re.compile(r"^(?P<name>.+)\((?P<code>[0-9A-Za-z]{4,6})\)\s*$")


def _to_int(x):
    try:
        v = int(str(x).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return v if v >= 0 else None


def parse_rows(rows, today=None, max_age_days=5):
    """回傳 (清洗後列, 退回原因 dict)。清洗後列：{log_date,symbol,stock_name,stock_rank,firm,firm_rank,buy_shares,sell_shares}"""
    good, bad = [], {}

    def rej(why):
        bad[why] = bad.get(why, 0) + 1

    today = today or datetime.now().date()
    for r in rows or []:
        d = str(r.get("Date") or "").strip()
        if not re.fullmatch(r"\d{8}", d):
            rej("日期格式")
            continue
        try:
            dd = datetime.strptime(d, "%Y%m%d").date()
        except ValueError:
            rej("日期無效")
            continue
        if dd > today or (today - dd).days > max_age_days:
            rej("日期超出範圍")
            continue
        m = _CODE_RE.match(str(r.get("SecuritiesCompanyCodeAndCompanyName") or "").strip())
        if not m:
            rej("代號格式")
            continue
        firm = str(r.get("SecuritiesFirmsCode") or "").strip()
        buy, sell = _to_int(r.get("TotalPurchaseShares")), _to_int(r.get("TotalSellShares"))
        if not firm or buy is None or sell is None:
            rej("券商或數量無效")
            continue
        good.append({"log_date": dd.isoformat(), "symbol": m.group("code"), "stock_name": m.group("name"),
                     "stock_rank": _to_int(r.get("StockRanking")), "firm": firm,
                     "firm_rank": _to_int(r.get("SecuritiesFirmsRanking")), "buy_shares": buy, "sell_shares": sell})
    return good, bad


def validate_batch(rows, known_symbols=None, min_stocks=5):
    """批次層驗證。回傳 (ok, 原因, 統計)。known_symbols：當日股價表有的代號集合（交叉比對）；None＝略過此項。"""
    if not rows:
        return False, "清洗後沒有資料", {}
    dates = {r["log_date"] for r in rows}
    if len(dates) != 1:
        return False, f"同一批出現多個資料日 {sorted(dates)}", {}
    by = {}
    for r in rows:
        by.setdefault(r["symbol"], []).append(r)
    stats = {"stocks": len(by), "rows": len(rows), "log_date": next(iter(dates))}
    if len(by) < min_stocks:
        return False, f"檔數過少（{len(by)}）", stats
    for sym, rs in by.items():
        firms = [x["firm"] for x in rs]
        if len(set(firms)) != len(firms):
            return False, f"{sym} 券商重複", stats
    if known_symbols is not None:
        unk = [s for s in by if s not in known_symbols]
        stats["unknown_symbols"] = unk[:10]
        if len(unk) > max(2, len(by) * 0.2):
            return False, f"與當日股價表交叉比對失敗（{len(unk)}/{len(by)} 檔不在表內）", stats
        rows[:] = [r for r in rows if r["symbol"] in known_symbols]
        stats["rows_after_xcheck"] = len(rows)
    return True, "ok", stats


def net_by_firm(rows_for_symbol):
    """某檔的券商買賣超（股），由大到小。"""
    out = [{"firm": r["firm"], "buy": r["buy_shares"], "sell": r["sell_shares"], "net": r["buy_shares"] - r["sell_shares"]}
           for r in rows_for_symbol]
    return sorted(out, key=lambda x: -x["net"])


def sync(sb, fetch=None, today=None, keep_days=90):
    """抓取→驗證→寫入。回傳訊息字串。已有當日資料就不重抓。不拋例外。"""
    import requests
    try:
        raw = (fetch or (lambda: requests.get(URL, headers={"Accept": "application/json", "User-Agent": "warroom-research/1.0"},
                                              timeout=30).json()))()
    except Exception as e:  # noqa: BLE001
        return f"櫃買券商排行：抓取失敗 {type(e).__name__}"
    rows, bad = parse_rows(raw, today=today)
    if not rows:
        return f"櫃買券商排行：全部退回 {bad}"
    log_date = rows[0]["log_date"]
    try:
        have = sb.table("tpex_broker_top").select("symbol").eq("log_date", log_date).limit(1).execute().data
        if have:
            return f"櫃買券商排行：{log_date} 已有資料，略過"
    except Exception as e:  # noqa: BLE001
        return f"櫃買券商排行：查既有資料失敗 {type(e).__name__}（資料表可能尚未建立）"
    known = None
    try:
        sy = sb.table("twse_market_snapshot").select("symbol").eq("trade_date", log_date).limit(5000).execute().data or []
        known = {x["symbol"] for x in sy} or None
    except Exception:  # noqa: BLE001
        known = None
    ok, why, stats = validate_batch(rows, known)
    if not ok:
        return f"櫃買券商排行：驗證未過（{why}）{stats}"
    try:
        sb.table("tpex_broker_top").upsert(rows, on_conflict="log_date,symbol,firm").execute()
        cutoff = ((today or datetime.now().date()) - timedelta(days=keep_days)).isoformat()
        sb.table("tpex_broker_top").delete().lt("log_date", cutoff).execute()
    except Exception as e:  # noqa: BLE001
        return f"櫃買券商排行：寫入失敗 {type(e).__name__}: {str(e)[:80]}"
    return f"櫃買券商排行：{log_date} 寫入 {len(rows)} 列／{stats['stocks']} 檔（退回 {sum(bad.values())}）"
