# -*- coding: utf-8 -*-
"""
official_open_data.py —— 證交所／櫃買 OpenAPI 公開資料逐檔彙整（2026-10-16）

來源：openapi.twse.com.tw、www.tpex.org.tw/openapi（政府資料開放授權；站方提供的官方介接方式，不是爬網頁）。
2026-10-16 在 GitHub Actions 逐一實測全部端點（證交所 143 支、櫃買 225 支皆 HTTP 200）後，挑出系統原本沒接的：
  pledge          董監事設質（質押）彙總        TWSE t187ap11_L／TPEx mopsfin_t187ap11_O
  insider_transfer 內部人持股轉讓事前申報        TWSE t187ap12_L／TPEx mopsfin_t187ap12_O
  short_suspend   停資停券／暫停融券賣出預告    TWSE BFI84U／TPEx tpex_margin_trading_term
  daytrade_flag   暫停先賣後買當沖標的          TWSE TWTB4U／TPEx tpex_securities
  margin_otc      上櫃融資融券餘額、融資使用率  TPEx tpex_mainboard_margin_balance
  sbl_otc         上櫃借券賣出餘額              TPEx tpex_margin_sbl
  qfii_otc        上櫃外資持股比例              TPEx tpex_3insti_qfii
  attention_note  注意累計次數將達處置標準      TWSE announcement/notetrans／TPEx tpex_trading_warning_note
  trade_status    變更交易／分盤／管理股票／暫停 TWSE TWT85U／TPEx tpex_cmode
  material_news   每日重大訊息                  TWSE t187ap04_L／TPEx mopsfin_t187ap04_O
  agm             股東會召集公告（停止過戶、預擬股利） TWSE t187ap38_L
  exdiv_upcoming  除權除息預告（尚未除息者）       TWSE TWT48U_ALL／TPEx tpex_exright_prepost
另有 inst_otc：上櫃三大法人買賣超（TPEx tpex_3insti_daily_trading）→ 寫入 inst_holding，讓上櫃股可進自動掃描。
驗證（死規則1）：代號格式、日期（民國/西元轉換、範圍）、數值範圍；單一資料集被退回的列超過 30% 就整批不寫；
  融資餘額不得超過限額；每筆保留來源網址。結果只寫 official_open_data（私有，RLS 開、無 policy，僅 service key）。
"""
import datetime as dt
import json
import re

TWSE = "https://openapi.twse.com.tw/v1"
TPEX = "https://www.tpex.org.tw/openapi/v1"
HEADERS = {"Accept": "application/json", "User-Agent": "warroom-research/1.0"}
STOCK_RE = re.compile(r"^[1-9]\d{3}$")
ANY_RE = re.compile(r"^[0-9]{4,6}[A-Z]?$")
MAX_REJECT = 0.30
TABLE = "official_open_data"


# ------------------------------------------------------------------ 共用
def roc_to_iso(v):
    """'1151015'／'115/10/15'／'115年10月13日'／'20261015'／'2026-10-15' → 'YYYY-MM-DD'；失敗回 None。取區間時用第一段。"""
    s = str(v or "").strip()
    if not s:
        return None
    s = re.split(r"[~～至]", s)[0].strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        y, mo, d = int(m[1]), int(m[2]), int(m[3])
    else:
        m = re.match(r"^(\d{2,3})[/年.-](\d{1,2})[/月.-](\d{1,2})", s)
        if m:
            y, mo, d = int(m[1]) + 1911, int(m[2]), int(m[3])
        else:
            digits = re.sub(r"\D", "", s)
            if len(digits) == 8:
                y, mo, d = int(digits[:4]), int(digits[4:6]), int(digits[6:8])
            elif len(digits) == 7:
                y, mo, d = int(digits[:3]) + 1911, int(digits[3:5]), int(digits[5:7])
            else:
                return None
    try:
        return dt.date(y, mo, d).isoformat()
    except ValueError:
        return None


def num(v):
    try:
        s = str(v).replace(",", "").replace("%", "").strip()
        if s in ("", "-", "－", "--"):
            return None
        x = float(s)
        return x if x == x and abs(x) != float("inf") else None
    except (TypeError, ValueError):
        return None


def norm_row(row):
    """欄位名稱去掉前後空白（證交所有些欄位名稱尾巴帶空白）。"""
    return {str(k).strip(): v for k, v in row.items()}


def pick(row, *keys):
    for k in keys:
        if k in row and str(row[k]).strip() != "":
            return row[k]
    return None


def code_of(row):
    return str(pick(row, "公司代號", "SecuritiesCompanyCode", "Code", "股票代號", "證券代號", "TWSECode") or "").strip()


def fetch(site, path, get, timeout=40):
    base = TWSE if site == "twse" else TPEX
    r = get(base + path, headers=HEADERS, timeout=timeout)
    if getattr(r, "status_code", 200) != 200:
        raise RuntimeError(f"HTTP {r.status_code}")
    data = r.json()
    if not isinstance(data, list):
        raise RuntimeError("回傳格式不是列表")
    return [norm_row(x) for x in data if isinstance(x, dict)]


def _row(dataset, sym, date, payload, url):
    date = date.isoformat() if hasattr(date, "isoformat") else date
    return {"dataset": dataset, "data_date": date, "symbol": sym, "payload": payload, "source_url": url}


# ------------------------------------------------------------------ 各資料集正規化：回傳 (rows, rejected)
def n_pledge(rows, site, today, url):
    agg, rej = {}, 0
    for r in rows:
        sym = code_of(r)
        d = roc_to_iso(pick(r, "出表日期", "Date"))
        hold, pled = num(r.get("目前持股")), num(r.get("設質股數"))
        if not STOCK_RE.match(sym) or not d or hold is None or pled is None or hold < 0 or pled < 0 or pled > hold * 1.0001:
            rej += 1
            continue
        a = agg.setdefault(sym, {"date": d, "hold": 0.0, "pled": 0.0, "n": 0, "max_person": 0.0,
                                 "grp_ratio": None, "ym": pick(r, "資料年月")})
        a["hold"] += hold
        a["pled"] += pled
        a["n"] += 1
        pr = num(r.get("設質股數佔持股比例"))
        if pr is not None and 0 <= pr <= 100:
            a["max_person"] = max(a["max_person"], pr)
        gr = num(r.get("內部人關係人設質比例"))
        if gr is not None and 0 <= gr <= 100:
            a["grp_ratio"] = max(a["grp_ratio"] or 0.0, gr)
    out = []
    for sym, a in agg.items():
        ratio = round(a["pled"] / a["hold"] * 100, 2) if a["hold"] > 0 else 0.0
        out.append(_row("pledge", sym, a["date"], {
            "pledge_ratio": ratio, "pledged_shares": int(a["pled"]), "held_shares": int(a["hold"]), "n_people": a["n"],
            "max_person_ratio": round(a["max_person"], 2), "group_ratio": a["grp_ratio"], "data_ym": a["ym"], "market": site}, url))
    return out, rej


def n_insider_transfer(rows, site, today, url):
    by, rej = {}, 0
    for r in rows:
        sym = code_of(r)
        d = roc_to_iso(pick(r, "出表日期", "Date"))
        tot = num(pick(r, "預定轉讓總股數-自有持股"))
        if not STOCK_RE.match(sym) or not d or tot is None or tot < 0:
            rej += 1
            continue
        b = by.setdefault((sym, d), {"total": 0.0, "items": []})
        b["total"] += tot + (num(pick(r, "預定轉讓總股數-保留運用決定權信託股數")) or 0.0)
        if len(b["items"]) < 8:
            b["items"].append({"who": str(pick(r, "申報人身分", "申請人身分") or ""), "name": str(r.get("姓名") or ""),
                               "method": str(pick(r, "預定轉讓方式及股數-轉讓方式") or ""), "shares": int(tot),
                               "hold": int(num(pick(r, "目前持有股數-自有持股")) or 0),
                               "period": str(pick(r, "有效轉讓期間") or "")})
    return [_row("insider_transfer", s, d, {"total_shares": int(b["total"]), "n": len(b["items"]), "items": b["items"], "market": site}, url)
            for (s, d), b in by.items()], rej


def n_short_suspend(rows, site, today, url):
    out, rej = [], 0
    for r in rows:
        sym = code_of(r)
        st = roc_to_iso(pick(r, "StartDate", "ShortSaleSuspensionStartDate"))
        en = roc_to_iso(pick(r, "EndDate", "ShortSaleSuspensionEndDate"))
        if not ANY_RE.match(sym) or not st or not en or en < st:
            rej += 1
            continue
        out.append(_row("short_suspend", sym, today, {"start": st, "end": en, "reason": str(pick(r, "Reason") or ""), "market": site}, url))
    return out, rej


def n_daytrade_flag(rows, site, today, url):
    out, rej = [], 0
    for r in rows:
        sym = code_of(r)
        flag = str(pick(r, "Suspension", "暫停現股賣出後現款買進當沖註記") or "").strip()
        if not ANY_RE.match(sym):
            rej += 1
            continue
        if flag:
            out.append(_row("daytrade_flag", sym, today, {"flag": flag, "market": site}, url))
    return out, rej


def n_margin_otc(rows, site, today, url):
    out, rej = [], 0
    for r in rows:
        sym = code_of(r)
        d = roc_to_iso(r.get("Date"))
        mb, mp = num(r.get("MarginPurchaseBalance")), num(r.get("MarginPurchaseBalancePreviousDay"))
        ml = num(r.get("MarginPurchaseQuota"))
        sb_, sp = num(r.get("ShortSaleBalance")), num(r.get("ShortSaleBalancePreviousDay"))
        if not STOCK_RE.match(sym) or not d or mb is None or mp is None or mb < 0 or (ml is not None and ml > 0 and mb > ml * 1.01):
            rej += 1
            continue
        out.append(_row("margin_otc", sym, d, {
            "margin_balance": mb, "margin_diff": mb - mp, "margin_limit": ml if ml and ml > 0 else None,
            "short_balance": sb_, "short_diff": (sb_ - sp) if (sb_ is not None and sp is not None) else None,
            "margin_use_pct": num(r.get("MarginPurchaseUtilizationRate")),
            "short_margin_ratio": round(sb_ / mb * 100, 2) if (sb_ is not None and mb > 0) else None, "market": "tpex"}, url))
    return out, rej


def n_sbl_otc(rows, site, today, url):
    out, rej = [], 0
    for r in rows:
        sym = code_of(r)
        d = roc_to_iso(r.get("Date"))
        bal, prev = num(r.get("SecuritiesBorrowingBalanceOfTheMarketDay")), num(r.get("SecuritiesBorrowingBalancePreviousDay"))
        if not STOCK_RE.match(sym) or not d or bal is None or bal < 0:
            rej += 1
            continue
        out.append(_row("sbl_otc", sym, d, {"sbl_balance": bal, "sbl_diff": (bal - prev) if prev is not None else None,
                                            "sbl_sell": num(r.get("SecuritiesBorrowingSale")), "sbl_return": num(r.get("SecuritiesBorrowingReturn")),
                                            "short_balance": num(r.get("SaleBalanceOfTheMarketDay")), "market": "tpex"}, url))
    return out, rej


def n_qfii_otc(rows, site, today, url):
    out, rej = [], 0
    for r in rows:
        sym = code_of(r)
        d = roc_to_iso(r.get("Date"))
        pct = num(r.get("PercentageOfSharesOC/FMIHeld"))
        if not STOCK_RE.match(sym) or not d or pct is None or not (0 <= pct <= 100):
            rej += 1
            continue
        out.append(_row("qfii_otc", sym, d, {"qfii_pct": pct, "held_shares": num(r.get("CurrentlySharesOC/FIHeld")),
                                             "room_pct": num(r.get("PercentageOfAvailableInvestmentForOC/FI")), "market": "tpex"}, url))
    return out, rej


def n_attention_note(rows, site, today, url):
    out, rej = [], 0
    for r in rows:
        sym = code_of(r)
        txt = str(pick(r, "RecentlyMetAttentionSecuritiesCriteria", "TradingInformation", "Reason", "DispositionReasons") or "")
        if not STOCK_RE.match(sym):
            rej += 1
            continue
        out.append(_row("attention_note", sym, roc_to_iso(r.get("Date")) or today, {"text": txt[:200], "market": site}, url))
    return out, rej


def n_trade_status(rows, site, today, url):
    out, rej = [], 0
    for r in rows:
        sym = code_of(r)
        if not ANY_RE.match(sym):
            rej += 1
            continue
        flags = {k: str(v).strip() for k, v in r.items()
                 if k in ("PeriodicCallAuctionTrading", "AlteredTrading", "PeriodicTrading", "ManagedStock", "MatchingFrequency",
                          "SuspensionOfTrading", "FinancialAnnouncements") and str(v).strip()}
        if flags:
            out.append(_row("trade_status", sym, today, {"flags": flags, "market": site}, url))
    return out, rej


def n_material_news(rows, site, today, url):
    by, rej = {}, 0
    for r in rows:
        sym = code_of(r)
        d = roc_to_iso(pick(r, "發言日期", "Date"))
        subj = str(pick(r, "主旨") or "").strip()
        if not STOCK_RE.match(sym) or not d or not subj:
            rej += 1
            continue
        b = by.setdefault((sym, d), [])
        if len(b) < 6:
            b.append({"time": str(pick(r, "發言時間") or ""), "subject": subj[:120], "clause": str(pick(r, "符合條款") or "")[:60]})
    return [_row("material_news", s, d, {"n": len(v), "items": v, "market": site}, url) for (s, d), v in by.items()], rej


def n_agm(rows, site, today, url):
    out, rej = [], 0
    for r in rows:
        sym = code_of(r)
        meet = roc_to_iso(r.get("股東常(臨時)會日期-日期"))
        if not STOCK_RE.match(sym) or not meet:
            rej += 1
            continue
        out.append(_row("agm", sym, roc_to_iso(r.get("出表日期")) or today, {
            "meeting_date": meet, "kind": str(r.get("股東常(臨時)會日期-常或臨時") or ""),
            "book_close_start": roc_to_iso(r.get("停止過戶起訖日期-起")), "book_close_end": roc_to_iso(r.get("停止過戶起訖日期-訖")),
            "cash_div": num(r.get("預擬配發現金(股利)(元/股)-盈餘")), "stock_div": num(r.get("預擬配股(元/股)-盈餘")),
            "market": site}, url))
    return out, rej


def n_exdiv_upcoming(rows, site, today, url):
    """【2026-10-16】除權除息預告表（TWSE TWT48U_ALL／TPEx tpex_exright_prepost）：只留普通股、除息日 ≥ 今天。
    data_date＝除權息日；現金股利尚未公告者 cash=None（照實顯示「未公告」）。已過的日期不算退回，直接略過。"""
    out, rej = [], 0
    td = today.isoformat() if hasattr(today, "isoformat") else str(today)
    for r in rows:
        sym = code_of(r)
        d = roc_to_iso(pick(r, "Date", "ExRrightsExDividendDate"))
        if not sym or not d:
            rej += 1
            continue
        if not STOCK_RE.match(sym):
            continue          # ETF／債券等不列（不算退回）
        if d < td:
            continue
        cash = num(r.get("CashDividend"))
        stock = num(pick(r, "StockDividendRatio"))
        if (cash is not None and (cash < 0 or cash > 1000)) or (stock is not None and stock < 0):
            rej += 1
            continue
        out.append(_row("exdiv_upcoming", sym, d, {
            "ex_date": d, "kind": str(pick(r, "Exdividend", "ExRrightsExDividend") or "").strip(),
            "cash": cash if cash else None, "stock_ratio": stock if stock else None,
            "name": str(pick(r, "Name", "CompanyName") or "").strip(), "market": site}, url))
    return out, rej


# dataset → [(site, path, normalizer)]
SPECS = {
    "pledge": [("twse", "/opendata/t187ap11_L", n_pledge), ("tpex", "/mopsfin_t187ap11_O", n_pledge)],
    "insider_transfer": [("twse", "/opendata/t187ap12_L", n_insider_transfer), ("tpex", "/mopsfin_t187ap12_O", n_insider_transfer)],
    "short_suspend": [("twse", "/exchangeReport/BFI84U", n_short_suspend), ("tpex", "/tpex_margin_trading_term", n_short_suspend)],
    "daytrade_flag": [("twse", "/exchangeReport/TWTB4U", n_daytrade_flag), ("tpex", "/tpex_securities", n_daytrade_flag)],
    "margin_otc": [("tpex", "/tpex_mainboard_margin_balance", n_margin_otc)],
    "sbl_otc": [("tpex", "/tpex_margin_sbl", n_sbl_otc)],
    "qfii_otc": [("tpex", "/tpex_3insti_qfii", n_qfii_otc)],
    "attention_note": [("twse", "/announcement/notetrans", n_attention_note), ("tpex", "/tpex_trading_warning_note", n_attention_note)],
    "trade_status": [("twse", "/exchangeReport/TWT85U", n_trade_status), ("tpex", "/tpex_cmode", n_trade_status)],
    "material_news": [("twse", "/opendata/t187ap04_L", n_material_news), ("tpex", "/mopsfin_t187ap04_O", n_material_news)],
    "agm": [("twse", "/opendata/t187ap38_L", n_agm)],
    "exdiv_upcoming": [("twse", "/exchangeReport/TWT48U_ALL", n_exdiv_upcoming), ("tpex", "/tpex_exright_prepost", n_exdiv_upcoming)],
}


# ------------------------------------------------------------------ 上櫃三大法人 → inst_holding
def _find_key(row, *must, any_of=()):
    """在欄位名稱（去空白、不分大小寫、去空格）中找同時含 must 全部字樣的第一個。"""
    for k in row:
        kk = re.sub(r"\s+", "", k).lower()
        if all(m.lower() in kk for m in must) and (not any_of or any(a.lower() in kk for a in any_of)):
            return k
    return None


def n_inst_otc(rows, today):
    """回傳 (inst_holding 列, rejected)。單位股→張（/1000 取整）。外資＝不含外資自營商；自營商＝Dealers-Difference。"""
    out, rej = [], 0
    for r in rows:
        sym = code_of(r)
        d = roc_to_iso(r.get("Date"))
        kf = (_find_key(r, "excluded", "difference") or _find_key(r, "foreigninvestors", "difference"))
        kt = _find_key(r, "securitiesinvestmenttrust", "difference")
        kd = None
        for k in r:
            if re.sub(r"\s+", "", k).lower() == "dealers-difference":
                kd = k
        f, t, dl = num(r.get(kf)) if kf else None, num(r.get(kt)) if kt else None, num(r.get(kd)) if kd else None
        if not STOCK_RE.match(sym) or not d or f is None or t is None or dl is None or max(abs(f), abs(t), abs(dl)) > 1e11:
            rej += 1
            continue
        out.append({"date": d, "symbol": sym, "foreign_buy": int(f / 1000), "trust_buy": int(t / 1000), "dealer_buy": int(dl / 1000)})
    return out, rej


# ------------------------------------------------------------------ 同步
def _upsert(sb, table, rows, conflict, chunk=500, **kw):
    n = 0
    for i in range(0, len(rows), chunk):
        sb.table(table).upsert(rows[i:i + chunk], on_conflict=conflict, **kw).execute()
        n += len(rows[i:i + chunk])
    return n


def _fresh(rows_dates, today, max_age=10):
    """資料集本身的日期是否夠新：最新一筆距今 ≤ max_age 天。"""
    ds = [d for d in rows_dates if d]
    return bool(ds) and (today - dt.date.fromisoformat(max(ds))).days <= max_age


def sync(sb, get, today=None, only=None, sleep=None, skip_inst_otc=False):
    """抓取、驗證、寫入。回傳 (訊息, 摘要 dict)。單一端點失敗不影響其他。"""
    import time as _t
    today = today or dt.date.today()
    sleep = sleep or _t.sleep
    summ = {"datasets": {}, "errors": 0, "rows": 0}
    for ds, specs in SPECS.items():
        if only and ds not in only:
            continue
        info = {"rows": 0, "rejected": 0, "raw": 0, "stale": False, "error": None}
        summ["datasets"][ds] = info
        good = []
        for site, path, fn in specs:
            url = (TWSE if site == "twse" else TPEX) + path
            try:
                raw = fetch(site, path, get)
            except Exception as e:
                info["error"] = f"{site}{path}: {type(e).__name__}: {str(e)[:80]}"
                summ["errors"] += 1
                sleep(0.7)
                continue
            rows, rej = fn(raw, site, today, url)
            info["raw"] += len(raw)
            info["rejected"] += rej
            if len(raw) > 0 and rej > MAX_REJECT * len(raw):
                info["error"] = f"{site}{path}: 退回 {rej}/{len(raw)} 列，超過 {int(MAX_REJECT*100)}%，整批不寫"
                summ["errors"] += 1
                sleep(0.7)
                continue
            good += rows
            sleep(0.7)
        if good:
            try:
                info["rows"] = _upsert(sb, TABLE, good, "dataset,data_date,symbol")
                summ["rows"] += info["rows"]
            except Exception as e:
                info["error"] = f"寫入失敗 {type(e).__name__}"
                summ["errors"] += 1
    if not only or "inst_otc" in only:
        if not skip_inst_otc:
            info = {"rows": 0, "rejected": 0, "raw": 0, "error": None}
            summ["datasets"]["inst_otc"] = info
            try:
                raw = fetch("tpex", "/tpex_3insti_daily_trading", get)
                rows, rej = n_inst_otc(raw, today)
                info["raw"], info["rejected"] = len(raw), rej
                # 只收 4 碼股票；ETF／權證等不在掃描範圍，被濾掉不算退回
                if rows and rej <= MAX_REJECT * max(len(raw), 1) + sum(1 for x in raw if not STOCK_RE.match(code_of(x))):
                    dates = {r["date"] for r in rows}
                    if len(dates) == 1 and _fresh(dates, today, 5):
                        info["rows"] = _upsert(sb, "inst_holding", rows, "date,symbol", ignore_duplicates=True)
                        summ["rows"] += info["rows"]
                    else:
                        info["error"] = f"日期異常或過舊：{sorted(dates)[:3]}"
                        summ["errors"] += 1
                elif not rows:
                    info["error"] = "解析不到任何列"
                    summ["errors"] += 1
            except Exception as e:
                info["error"] = f"{type(e).__name__}: {str(e)[:80]}"
                summ["errors"] += 1
    parts = [f"{k}:{v['rows']}" for k, v in summ["datasets"].items()]
    msg = f"官方公開資料同步：寫入 {summ['rows']} 列｜失敗 {summ['errors']} 項｜" + " ".join(parts)
    return msg, summ


# ------------------------------------------------------------------ 讀取與顯示
def load_symbol(sb, sym, datasets=None, days=60):
    """回傳 {dataset: 最新一筆 payload+data_date}。查不到的不在結果內。"""
    out = {}
    since = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    try:
        q = sb.table(TABLE).select("dataset,data_date,payload").eq("symbol", sym).gte("data_date", since).order("data_date", desc=True).limit(80)
        rows = q.execute().data or []
    except Exception:
        return out
    for r in rows:
        ds = r.get("dataset")
        if datasets and ds not in datasets:
            continue
        if ds not in out:
            p = r.get("payload")
            p = json.loads(p) if isinstance(p, str) else (p or {})
            out[ds] = dict(p, data_date=r.get("data_date"))
    return out


PLEDGE_WARN, PLEDGE_DANGER = 30.0, 50.0


def summarize_alerts(info):
    """把 load_symbol 結果轉成給人看的警示列表 [(級別, 文字)]；級別：danger／warn／info。"""
    out = []
    p = info.get("pledge")
    if p:
        r = p.get("pledge_ratio", 0)
        lvl = "danger" if r >= PLEDGE_DANGER else "warn" if r >= PLEDGE_WARN else None
        if lvl:
            out.append((lvl, f"董監質押比 {r:.1f}%（{p['pledged_shares']//1000:,}張／持股{p['held_shares']//1000:,}張，資料{p['data_date']}）"))
    t = info.get("insider_transfer")
    if t and t.get("total_shares", 0) > 0:
        out.append(("warn", f"內部人申報轉讓 {t['total_shares']//1000:,}張（{t['n']}筆，{t['data_date']}）"))
    s = info.get("short_suspend")
    if s:
        out.append(("warn", f"暫停融券賣出／停資停券 {s['start']}~{s['end']}（{s.get('reason') or '原因未載'}）"))
    a = info.get("attention_note")
    if a:
        out.append(("warn", f"注意股累計次數：{a.get('text', '')[:60]}"))
    ts = info.get("trade_status")
    if ts:
        out.append(("warn", "交易狀態：" + "、".join(f"{k}={v}" for k, v in ts["flags"].items())))
    d = info.get("daytrade_flag")
    if d:
        out.append(("info", f"暫停先賣後買當沖（{d['flag']}）"))
    return out
