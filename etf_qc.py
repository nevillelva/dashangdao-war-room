"""【2026-10-13】ETF 價格跳動「先確認、再標記」分類器（純函式為主，Supabase 只在 load_ctx 讀取）。

老闆指示：資料異常不可選，但要先做確認，不是直接顯示異常。
所以分類結果只有兩種處置：
  ok      已有依據說明這次跳動（官方分割紀錄、除息、同日市場同步、真實行情、人工查證），可用
  adjust  確認為分割／反分割，附還原比例（分割前價 ÷ 分割後價），回測時還原
  pending 還沒有足夠依據：只顯示「資料待確認」與缺什麼，暫不納入回測；絕不寫「異常」

分類順序（先符合先採用）：
  1. etf_price_qc（人工閱讀查證結果，附來源網址）
  2. 官方分割紀錄（etf_corp_actions_v1，FinMind TaiwanStockSplitPrice）同日同檔，且與自家收盤價比對一致
  3. 除息：該區間有 etf_dividend_result 的除息日，扣息後的跳動在漲跌幅內
  4. 停牌型：兩筆收盤相隔 ≥6 天且價格變成 ≤0.6 倍或 ≥1.6 倍（分割／合併的特徵：停牌約 4～5 個交易日）→ 待確認
  5. 同日市場同步：同一天、同方向有 ≥3 檔其他 ETF 也跳動，且幅度在 35% 內
  6. 其餘：槓反型或國外成分（持股資料判斷；無漲跌幅限制）在合理範圍內視為真實行情；國內型超過單日範圍 → 待確認
  另：兩筆價格相隔 >14 天是資料缺漏（不是停牌），一律待確認並說明要先補齊日線
死規則 1：每個結論都要有依據，沒有依據就退回待確認，不默默吃進去。
"""
import json
from datetime import date

LIMIT_JUMP = 0.11            # 與 etf_price_jumps 檢視同一個門檻（單日 ±10% 加捨入容差）
HALT_GAP_DAYS = 6            # 分割／合併需停牌，復牌日與前一筆至少相隔這麼多日曆天
HALT_DOWN, HALT_UP = 0.60, 1.60
OFFICIAL_TOL = 0.15          # 官方參考價與自家復牌日收盤的容許差距（復牌首日仍會正常波動）
EXDIV_TOL = 0.10             # 扣息後的跳動需在 ±10% 內
MARKET_PEERS = 3             # 同日同方向的其他檔數門檻
MARKET_MAX_MOVE = 0.35
REAL_DOWN, REAL_UP = 0.40, 2.50   # 無漲跌幅限制的商品，單日仍須在這個倍數範圍內才當真實行情
MAX_GAP_DAYS = 14            # 與 etf_combo 相同：超過這個天數的空檔是缺資料，不是停牌
FOREIGN_MAX_TW_SHARE = 0.5   # 最新一期持股中台股代號占比低於此值、且持股≥10 檔 → 視為國外成分
FOREIGN_MIN_HOLDINGS = 10

QC_STATUS_SPLIT = "confirmed_split"
QC_STATUS_REAL = "confirmed_real_move"
QC_STATUS_PENDING = "pending"


def _days(d0, d1):
    return (date.fromisoformat(str(d1)[:10]) - date.fromisoformat(str(d0)[:10])).days


def find_jumps(rows, limit=LIMIT_JUMP):
    """rows: [(date, close)] 已排序。回傳相鄰兩筆收盤變動超過 limit 的清單。"""
    out = []
    for (d0, p), (d1, c) in zip(rows, rows[1:]):
        if p and p > 0 and c and c > 0 and abs(c / p - 1) > limit:
            out.append({"date": str(d1)[:10], "prev_date": str(d0)[:10], "prev": float(p), "cur": float(c),
                        "pct": (c / p - 1) * 100.0, "gap_days": _days(d0, d1)})
    return out


def _sign(pct):
    return 1 if pct > 0 else -1


def build_ctx(jump_rows=None, splits=None, divs=None, qc_rows=None, wide=None):
    """組成分類所需的背景資料。
    jump_rows: etf_price_jumps 檢視的列（symbol, jump_date, prev_close, close），用來算『同日市場同步』
    splits: [{symbol, date, factor}] 官方分割紀錄；divs: {symbol: [(ex_date, cash)]}
    qc_rows: etf_price_qc 的列；wide: 無漲跌幅限制的代號集合（槓反型或國外成分）。"""
    peers = {}
    for r in jump_rows or []:
        p, c = float(r["prev_close"]), float(r["close"])
        if p > 0:
            peers.setdefault((str(r["jump_date"])[:10], _sign(c / p - 1)), set()).add(r["symbol"])
    sp = {}
    for s in splits or []:
        if s.get("symbol") and s.get("date") and s.get("factor"):
            sp.setdefault(s["symbol"], {})[str(s["date"])[:10]] = float(s["factor"])
    qc = {(r["symbol"], str(r["jump_date"])[:10]): r for r in qc_rows or []}
    return {"peers": peers, "splits": sp, "divs": divs or {}, "qc": qc, "wide": set(wide or [])}


def _res(kind, status, reason, factor=None, source=""):
    return {"kind": kind, "status": status, "reason": reason, "factor": factor, "source": source}


def _pending(reason):
    return _res("pending", "pending", reason)


def classify(symbol, j, ctx):
    """對一次跳動 j（find_jumps 的元素）分類。回傳 {kind: ok|adjust|pending, status, reason, factor, source}。"""
    ratio = j["cur"] / j["prev"]
    # 1. 人工閱讀查證結果
    q = ctx["qc"].get((symbol, j["date"]))
    if q:
        st = q.get("status")
        if st == QC_STATUS_SPLIT:
            f = float(q.get("factor") or 0)
            if f > 0 and abs(j["prev"] / f / j["cur"] - 1) <= OFFICIAL_TOL:
                return _res("adjust", "qc_split", f"人工查證確認為分割／反分割（比例 {f:g}）", f, q.get("source_url") or "")
            return _pending("人工查證的分割比例與價格對不上，需重新確認")
        if st == QC_STATUS_REAL:
            return _res("ok", "qc_real_move", "人工查證確認為真實行情（非分割）", None, q.get("source_url") or "")
        if st == QC_STATUS_PENDING:
            return _pending("已查證但尚無結論：" + str(q.get("evidence") or "")[:80])
    # 2. 官方分割紀錄
    f = (ctx["splits"].get(symbol) or {}).get(j["date"])
    if f:
        if abs(j["prev"] / f / j["cur"] - 1) <= OFFICIAL_TOL:
            return _res("adjust", "official_split", f"官方分割紀錄（比例 {f:g}）", f, "FinMind TaiwanStockSplitPrice")
        return _pending("官方分割紀錄的比例與價格對不上，需重新確認")
    # 3. 除息
    for ex, cash in ctx["divs"].get(symbol) or []:
        if j["prev_date"] < str(ex)[:10] <= j["date"] and j["prev"] - cash > 0 \
                and abs(j["cur"] / (j["prev"] - cash) - 1) <= EXDIV_TOL:
            return _res("ok", "ex_dividend", f"除息日 {str(ex)[:10]}（配 {cash:g} 元）後在漲跌幅內", None, "etf_dividend_result")
    # 3.5 資料缺漏：空檔過長，無法判斷中間發生什麼
    if j["gap_days"] > MAX_GAP_DAYS:
        return _pending(f"兩筆價格相隔 {j['gap_days']} 天（資料缺漏），無法判斷中間的變動，需先補齊日線資料")
    # 4. 停牌型（疑似分割／合併）
    if j["gap_days"] >= HALT_GAP_DAYS and (ratio <= HALT_DOWN or ratio >= HALT_UP):
        return _pending(f"疑似分割／合併：相隔 {j['gap_days']} 天後價格變 {ratio:.2f} 倍，尚未與公告比對")
    # 5. 同日市場同步
    n_peers = len(ctx["peers"].get((j["date"], _sign(j["pct"])), set()) - {symbol})
    if n_peers >= MARKET_PEERS and abs(j["pct"]) <= MARKET_MAX_MOVE * 100:
        return _res("ok", "market_wide", f"同日有 {n_peers} 檔 ETF 同方向大幅變動（市場行情）", None, "etf_price_daily")
    # 6. 其餘
    if REAL_DOWN <= ratio <= REAL_UP:
        if symbol in ctx["wide"]:
            return _res("ok", "real_move_wide", "槓反型或國外成分（無單日漲跌幅限制），幅度在合理範圍內，視為真實行情", None, "etf_holdings")
        return _pending("超過國內 ETF 單日漲跌範圍，且沒有除息、公告或同日市場同步可說明")
    return _pending(f"單日價格變成 {ratio:.2f} 倍，超出合理範圍，沒有公告可說明")


def scan_series(symbol, rows, ctx):
    """對單檔的日收盤序列做分類。回傳 {adjust: {date: factor}, pending: [{date, pct, reason}], notes: [...]}。"""
    adjust, pending, notes = {}, [], []
    for j in find_jumps(rows):
        r = classify(symbol, j, ctx)
        if r["kind"] == "adjust":
            adjust[j["date"]] = r["factor"]
            notes.append({"date": j["date"], "pct": round(j["pct"], 2), "status": r["status"], "reason": r["reason"], "factor": r["factor"]})
        elif r["kind"] == "ok":
            notes.append({"date": j["date"], "pct": round(j["pct"], 2), "status": r["status"], "reason": r["reason"], "factor": None})
        else:
            pending.append({"date": j["date"], "pct": round(j["pct"], 2), "reason": r["reason"]})
    return {"adjust": adjust, "pending": pending, "notes": notes}


def apply_adjustments(rows, adjust):
    """rows: [(date, close)]。adjust: {date: 分割前價÷分割後價}。復牌日之前的價格除以比例，與復牌日接續。"""
    if not adjust:
        return list(rows)
    out, cum = [], 1.0
    # 由後往前累積：越早的價格要除以越多次比例
    after = [1.0] * len(rows)
    for k in range(len(rows) - 2, -1, -1):
        after[k] = after[k + 1] * adjust.get(str(rows[k + 1][0])[:10], 1.0)
    for k, (d, c) in enumerate(rows):
        out.append((d, c / after[k]))
    return out


def pending_by_symbol(jump_rows, ctx):
    """選擇清單用：由 etf_price_jumps 的列分類，回傳 {symbol: [{date, pct, reason}]}（只含仍待確認者）。"""
    out = {}
    for r in jump_rows or []:
        p, c = float(r["prev_close"]), float(r["close"])
        if p <= 0 or c <= 0:
            continue
        d1 = str(r["jump_date"])[:10]
        j = {"date": d1, "prev_date": str(r["prev_date"])[:10], "prev": p, "cur": c,
             "pct": (c / p - 1) * 100.0, "gap_days": _days(r["prev_date"], r["jump_date"])}
        res = classify(r["symbol"], j, ctx)
        if res["kind"] == "pending":
            out.setdefault(r["symbol"], []).append({"date": d1, "pct": round(j["pct"], 2), "reason": res["reason"]})
    return out


def summarize(jump_rows, ctx):
    """整體統計（排程診斷用）：回傳 {total, by_status, pending: {symbol: [...]}}。"""
    by, pend = {}, {}
    for r in jump_rows or []:
        p, c = float(r["prev_close"]), float(r["close"])
        if p <= 0 or c <= 0:
            continue
        j = {"date": str(r["jump_date"])[:10], "prev_date": str(r["prev_date"])[:10], "prev": p, "cur": c,
             "pct": (c / p - 1) * 100.0, "gap_days": _days(r["prev_date"], r["jump_date"])}
        res = classify(r["symbol"], j, ctx)
        by[res["status"]] = by.get(res["status"], 0) + 1
        if res["kind"] == "pending":
            pend.setdefault(r["symbol"], []).append({"date": j["date"], "pct": round(j["pct"], 2), "reason": res["reason"]})
    return {"total": sum(by.values()), "by_status": by, "pending": pend}


# ───────────── Supabase 讀取（呼叫端傳入 sb，方便離線測試） ─────────────
def _page(q, size=1000):
    out, off = [], 0
    while True:
        rows = q.range(off, off + size - 1).execute().data or []
        out += rows
        if len(rows) < size:
            return out
        off += size


def load_jump_rows(sb):
    return _page(sb.table("etf_price_jumps").select("symbol,prev_date,jump_date,prev_close,close")
                 .order("symbol").order("jump_date"))


def load_qc_rows(sb):
    try:
        return _page(sb.table("etf_price_qc").select("symbol,jump_date,status,factor,source_url,evidence"))
    except Exception:  # noqa: BLE001  # 表還沒建立時視為沒有人工查證，全部走規則（更保守）
        return []


def load_splits(sb):
    try:
        r = sb.table("system_config").select("config_value").eq("config_key", "etf_corp_actions_v1").limit(1).execute().data or []
        v = json.loads(r[0]["config_value"]) if r and isinstance(r[0]["config_value"], str) else (r[0]["config_value"] if r else {})
        return [x for x in (v.get("splits") or []) if isinstance(x, dict)]
    except Exception:  # noqa: BLE001
        return []


def load_divs(sb, symbols):
    out = {}
    syms = sorted(set(symbols))
    for i in range(0, len(syms), 50):
        try:
            rows = _page(sb.table("etf_dividend_result").select("symbol,ex_date,cash").in_("symbol", syms[i:i + 50]))
        except Exception:  # noqa: BLE001
            rows = []
        for x in rows:
            if x.get("cash") is not None:
                out.setdefault(x["symbol"], []).append((str(x["ex_date"])[:10], float(x["cash"])))
    return out


def foreign_from_rows(rows):
    """etf_holdings_foreign 的列 → 國外成分代號集合（台股代號占比低且持股夠多）。"""
    out = set()
    for r in rows or []:
        try:
            if int(r["n"]) >= FOREIGN_MIN_HOLDINGS and float(r["tw_share"]) < FOREIGN_MAX_TW_SHARE:
                out.add(r["symbol"])
        except (KeyError, TypeError, ValueError):
            continue
    return out


def load_foreign_symbols(sb):
    """由現有持股資料判斷國外成分 ETF；讀不到回傳空集合（更保守：這些檔的大跳動會維持待確認）。"""
    try:
        return foreign_from_rows(_page(sb.table("etf_holdings_foreign").select("symbol,n,with_code,tw_share")))
    except Exception:  # noqa: BLE001
        return set()


def load_ctx(sb, wide=None, jump_rows=None):
    """讀齊分類所需資料。回傳 (ctx, jump_rows)。wide 會再併入由持股資料判斷的國外成分。"""
    wide = set(wide or []) | load_foreign_symbols(sb)
    jr = jump_rows if jump_rows is not None else load_jump_rows(sb)
    ctx = build_ctx(jr, load_splits(sb), load_divs(sb, [r["symbol"] for r in jr]), load_qc_rows(sb), wide)
    return ctx, jr


# ------------------------------------------------------------------ 日線空檔診斷（2026-10-13）
def gap_probe(raw_rows, db_dates, max_gap=MAX_GAP_DAYS):
    """比對 FinMind 原始日線（dict 列表）與資料庫已有日期，判斷「空檔」是缺資料還是沒成交。
    db_dates：資料庫該檔已有的日期（YYYY-MM-DD）。空檔＝資料庫相鄰兩列相隔 > max_gap 日曆天。
    回傳 dict（只有數量，無價格明細）：
      raw_total 原始列數；raw_zero_close 收盤 0 的占位列；raw_zero_volume 成交量 0 的列；
      raw_positive 收盤 > 0 的列；positive_not_in_db 原始有價但資料庫沒有的日期數（真缺資料）；
      db_not_in_raw 資料庫有、原始沒有（不應發生）；
      gaps：每段空檔的 {from,to,days,raw_rows_inside,raw_positive_inside,raw_placeholder_inside}。"""
    from datetime import date
    pos, zero, zvol = set(), set(), 0
    for r in raw_rows or []:
        d = str(r.get("date") or "")[:10]
        try:
            date.fromisoformat(d)
        except ValueError:
            continue
        try:
            c = float(r.get("close"))
        except (TypeError, ValueError):
            continue
        if c > 0:
            pos.add(d)
        else:
            zero.add(d)
        v = r.get("Trading_Volume")
        try:
            if v is not None and float(v) == 0:
                zvol += 1
        except (TypeError, ValueError):
            pass
    db = sorted(set(db_dates or []))
    gaps = []
    for a, b in zip(db, db[1:]):
        days = (date.fromisoformat(b) - date.fromisoformat(a)).days
        if days > max_gap:
            gaps.append({"from": a, "to": b, "days": days,
                         "raw_positive_inside": sum(1 for d in pos if a < d < b),
                         "raw_placeholder_inside": sum(1 for d in zero if a < d < b)})
    for g in gaps:
        g["raw_rows_inside"] = g["raw_positive_inside"] + g["raw_placeholder_inside"]
    return {"raw_total": len(pos) + len(zero), "raw_positive": len(pos), "raw_zero_close": len(zero),
            "raw_zero_volume": zvol, "positive_not_in_db": len(pos - set(db)),
            "db_not_in_raw": len(set(db) - pos), "gaps": gaps}
