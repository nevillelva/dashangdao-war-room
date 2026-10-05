# -*- coding: utf-8 -*-
"""
broker_style.py —— 券商分點「型態」分類：隔日沖型 / 建倉型 / 外資型 / 未判。
2026-10-05 新增（使用者需求第 4 項：隔日沖功能取消後，分點連續性的有效資料要能在戰卡與戰情速覽
「一目了然」看出：目前是隔日沖的券商買得多？還是有真建倉的券商買得多？）。

純函式、不依賴 Streamlit / Supabase / pandas，排程端（寫入 broker_style_daily）與網頁端（顯示）共用。

【資料來源與單位】broker_flows：DJ 免費公開頁，每檔每日只有「買超前 15 + 賣超前 15」分點，單位＝張。
輸入列需有 broker_code（2026-10-05 起寫入；之前的舊列沒有且慣用名稱不一致，不納入分類）。

【分類規則（啟發式，不是精算；一個分點底下客戶眾多）】對每個(標的, 分點)，只用「截至資料日」的歷史：
  追蹤每次「淨買超日 T」的隔天結果（用交易日曆的下一個資料日 T+1）：
    ・隔天賣出 ≥ 前一日淨買的 50%            → 倒貨（flip）
    ・隔天沒賣（仍買超，或沒進賣超前 15 且「若賣掉一半一定會進榜」）→ 續抱（hold）
    ・其餘（沒進榜但賣出量不足以進榜、無法判斷）→ 不計
  分類（先到先得）：
    1. flips ≥ 1 且 flips/(flips+holds) ≥ 50%           → 隔日沖型（實測）
    2. 外資名單（美林/摩根/高盛/瑞銀/花旗…）              → 外資型
    3. holds ≥ 1 且 flips = 0 且近期累計淨買 > 0          → 建倉型（買了隔天沒賣、累計淨買為正）
    4. 命中隔日沖名單（靜態＋動態）且還沒有觀察結果        → 隔日沖型（名單，尚未實測）
    5. 其他（新進、無歷史）                                → 未判
  行為證據優先於名單：名單券商只要實際行為是「買了不賣」，會被歸為建倉。
  今天才第一次出現的分點，T+1 結果還不知道，所以只能「未判」或「名單」——這是誠實的限制。

【彙總】以資料日 D 當天「淨買超分點」依型態加總張數 → 隔日沖% / 建倉% / 外資% / 未判%，
再給主導判讀（建倉主導/隔日沖主導/外資主導/拉鋸/未判/資料不足）。
另算：隔日沖型當日賣超（昨日隔日沖買盤正在倒貨）、建倉型當日賣超（建倉者出貨警示）、建倉型近 N 日累計淨買。
"""
from collections import defaultdict

WINDOW_DAYS = 20            # 往回看幾個交易日
FLIP_SELL_RATIO = 0.5       # 隔天賣出 ≥ 前一日淨買的 50% 視為倒貨
MIN_HIST_DAYS = 3           # 這檔至少要有幾個資料日才下判讀
SELLER_LIST_SIZE = 15       # DJ 賣超榜長度
DOMINANT_PCT = 35.0         # 主導：該型態佔買超 ≥35%
DOMINANT_RATIO = 1.5        # 且 ≥ 對方的 1.5 倍
MIN_CLASSIFIED_PCT = 40.0   # 隔日沖+建倉+外資 合計 <40% → 多為新進/未判分點，不下主導判讀

# 國內券商前綴（先排除，避免「大和國泰」「群益金鼎-高盛」被當外資）
DOMESTIC_PREFIXES = (
    "群益", "元大", "凱基", "富邦", "國泰", "永豐", "統一", "國票", "台新", "兆豐", "新光", "康和", "華南",
    "玉山", "中國信託", "合庫", "臺銀", "台銀", "宏遠", "第一金", "日盛", "大和國泰", "彰銀", "土銀", "三信",
    "大昌", "犇亞", "亞東", "福邦", "遠智", "致和", "陽信", "德信", "安泰", "上海商銀", "元富", "寶盛", "盈溢",
)
FOREIGN_KEYWORDS = (
    "美林", "摩根", "高盛", "瑞銀", "花旗", "匯豐", "麥格理", "野村", "德意志", "法興", "里昂", "巴克萊",
    "星展", "法銀", "港商", "美商", "新加坡商", "香港商", "瑞士", "渣打", "瑞穗", "三菱", "大和",
)
# DJ 外資券商代號（名稱關鍵字抓不到時的保險）
FOREIGN_CODES = {"1440", "1470", "1480", "1650", "8440", "1560", "1590", "1360", "8960", "8900"}

TYPE_LABELS = {
    "flip": "隔日沖型", "build": "建倉型", "foreign": "外資型", "unknown": "未判",
}
VERDICT_LABELS = {
    "build": ("🏗️", "建倉主導"),
    "flip": ("🎲", "隔日沖主導"),
    "foreign": ("🌐", "外資主導"),
    "mixed": ("⚖️", "拉鋸"),
    "unclear": ("❔", "未判（多為新進分點）"),
    "nodata": ("⚪", "資料不足"),
}


def is_foreign_broker(name, code=None):
    name = str(name or "")
    if code is not None and str(code) in FOREIGN_CODES:
        return True
    if any(name.startswith(p) for p in DOMESTIC_PREFIXES):
        return False
    return any(k in name for k in FOREIGN_KEYWORDS)


def _i(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return 0


def trading_dates_from_rows(rows, min_symbols=20):
    """用資料本身推交易日曆：有「足夠多檔」都有分點資料的日期才算交易日（避免單檔補抓的零星日期被當成交易日）。"""
    cnt = defaultdict(set)
    for r in rows:
        cnt[str(r["log_date"])[:10]].add(r["symbol"])
    if not cnt:
        return []
    top = max(len(v) for v in cnt.values())
    thr = max(1, min(min_symbols, int(top * 0.3)))
    return sorted(d for d, v in cnt.items() if len(v) >= thr)


def group_by_symbol(rows):
    out = defaultdict(list)
    for r in rows:
        if r.get("broker_code") in (None, "", "None"):
            continue            # 舊列（無 broker_code）名稱慣用不一致、單位來源不同，不納入分類
        out[r["symbol"]].append(r)
    return out


def classify_broker(name, code, flips, holds, net_win, listed):
    """回傳 (類型, 依據)；類型 ∈ flip/build/foreign/unknown。"""
    obs = flips + holds
    if flips >= 1 and obs > 0 and flips / obs >= 0.5:
        return "flip", "實測"
    if is_foreign_broker(name, code):
        return "foreign", "外資"
    if holds >= 1 and flips == 0 and net_win > 0:
        return "build", "實測"
    if listed and obs == 0:
        return "flip", "名單"
    return "unknown", ""


def compute_symbol_style(sym_rows, as_of, trading_dates, listed_names=(), window=WINDOW_DAYS):
    """
    單一標的、單一資料日的型態彙總。sym_rows：該標的所有（含 broker_code 的）分點列，可含 as_of 之後的日期（會被忽略）。
    回傳 dict；as_of 當天沒有資料、或 as_of 不在交易日曆內 → None。
    """
    win = [d for d in trading_dates if d <= as_of][-window:]
    if not win or win[-1] != as_of:
        return None
    pos = {d: i for i, d in enumerate(win)}
    by = {}
    for r in sym_rows:
        d = str(r["log_date"])[:10]
        if d not in pos:
            continue
        buy, sell = _i(r.get("buy_shares")), _i(r.get("sell_shares"))
        net = _i(r["net_shares"]) if r.get("net_shares") is not None else buy - sell
        key = r.get("broker_code") or r["broker_name"]
        b = by.setdefault(key, {"name": r["broker_name"], "code": r.get("broker_code"), "days": {}})
        old = b["days"].get(d)
        b["days"][d] = (buy + old[0], sell + old[1], net + old[2]) if old else (buy, sell, net)
    sym_dates = {d for b in by.values() for d in b["days"]}
    if as_of not in sym_dates:
        return None

    # 每個資料日「賣超榜」門檻：榜滿 15 家時，賣超絕對值低於榜上最小者的分點不會出現在榜上
    sellers = defaultdict(list)
    for b in by.values():
        for d, (_, _, net) in b["days"].items():
            if net < 0:
                sellers[d].append(-net)
    floor = {d: (min(v) if len(v) >= SELLER_LIST_SIZE else 0) for d, v in sellers.items()}

    listed_names = tuple(listed_names or ())
    cls_rows = []
    today_i = pos[as_of]
    for key, b in by.items():
        days = b["days"]
        flips = holds = 0
        for d, (_, _, net) in days.items():
            if net <= 0:
                continue
            i = pos[d]
            if i + 1 >= len(win):
                continue
            nd = win[i + 1]
            if nd not in sym_dates:
                continue
            nrow = days.get(nd)
            if nrow is not None:
                if nrow[1] >= FLIP_SELL_RATIO * net:
                    flips += 1
                else:
                    holds += 1
            elif FLIP_SELL_RATIO * net >= floor.get(nd, 0):
                holds += 1              # 若真的賣掉一半以上，一定會進賣超榜；沒進榜 → 沒賣
        streak, k = 0, today_i
        while k >= 0 and win[k] in days and days[win[k]][2] > 0:
            streak += 1
            k -= 1
        net_win = sum(v[2] for v in days.values())
        t = days.get(as_of)
        listed = any(n and n in b["name"] for n in listed_names)
        typ, basis = classify_broker(b["name"], b["code"], flips, holds, net_win, listed)
        cls_rows.append({
            "name": b["name"], "code": b["code"], "type": typ, "basis": basis,
            "today": t[2] if t else 0, "streak": streak, "net_win": net_win,
            "flips": flips, "holds": holds, "days": len(days),
        })

    agg = {"flip": 0, "build": 0, "foreign": 0, "unknown": 0}
    cnt = {"flip": 0, "build": 0, "foreign": 0, "unknown": 0}
    flip_sell = build_sell = foreign_sell = 0
    build_net_win = 0
    for c in cls_rows:
        if c["today"] > 0:
            agg[c["type"]] += c["today"]
            cnt[c["type"]] += 1
        elif c["today"] < 0:
            if c["type"] == "flip":
                flip_sell += -c["today"]
            elif c["type"] == "build":
                build_sell += -c["today"]
            elif c["type"] == "foreign":
                foreign_sell += -c["today"]
        if c["type"] == "build":
            build_net_win += c["net_win"]
    total = sum(agg.values())

    def pct(v):
        return round(v * 100.0 / total, 1) if total > 0 else 0.0

    flip_pct, build_pct, foreign_pct, unk_pct = pct(agg["flip"]), pct(agg["build"]), pct(agg["foreign"]), pct(agg["unknown"])
    hist_days = len(sym_dates)
    if hist_days < MIN_HIST_DAYS or total <= 0:
        verdict = "nodata"
    elif flip_pct + build_pct + foreign_pct < MIN_CLASSIFIED_PCT:
        verdict = "unclear"
    elif build_pct >= DOMINANT_PCT and build_pct >= DOMINANT_RATIO * flip_pct and build_pct >= foreign_pct:
        verdict = "build"
    elif flip_pct >= DOMINANT_PCT and flip_pct >= DOMINANT_RATIO * build_pct and flip_pct >= foreign_pct:
        verdict = "flip"
    elif foreign_pct >= 40.0 and foreign_pct > max(build_pct, flip_pct):
        verdict = "foreign"
    else:
        verdict = "mixed"

    def top(typ, buy=True, n=4):
        sel = [c for c in cls_rows if c["type"] == typ and ((c["today"] > 0) if buy else (c["today"] < 0))]
        sel.sort(key=lambda c: -abs(c["today"]))
        out = []
        for c in sel[:n]:
            out.append({"b": c["name"], "n": c["today"], "k": c["streak"], "w": c["net_win"],
                        "f": c["flips"], "h": c["holds"], "why": c["basis"]})
        return out

    detail = {
        "flip": top("flip"), "build": top("build"), "foreign": top("foreign"), "unknown": top("unknown", n=3),
        "build_sell": top("build", buy=False, n=3), "flip_sell": top("flip", buy=False, n=3),
        "cnt": cnt, "window": len(win),
    }
    return {
        "log_date": as_of, "verdict": verdict, "top15_buy": total,
        "flip_buy": agg["flip"], "build_buy": agg["build"], "foreign_buy": agg["foreign"], "other_buy": agg["unknown"],
        "flip_pct": flip_pct, "build_pct": build_pct, "foreign_pct": foreign_pct,
        "flip_sell": flip_sell, "build_sell": build_sell, "build_net_win": build_net_win,
        "hist_days": hist_days, "detail": detail, "brokers": cls_rows,
    }


def compute_style_rows(rows, as_of_dates, listed_names=(), window=WINDOW_DAYS, trading_dates=None):
    """
    批次：rows 為多檔、多日的 broker_flows 列（含 broker_code）；as_of_dates 要算的資料日。
    回傳可直接 upsert 進 broker_style_daily 的 dict list（不含 brokers 明細）。
    """
    by_sym = group_by_symbol(rows)
    all_rows = [r for lst in by_sym.values() for r in lst]
    tdates = trading_dates or trading_dates_from_rows(all_rows)
    out = []
    for sym, lst in by_sym.items():
        for d in as_of_dates:
            rec = compute_symbol_style(lst, d, tdates, listed_names, window)
            if rec is None:
                continue
            rec = dict(rec)
            rec.pop("brokers", None)
            rec["symbol"] = sym
            out.append(rec)
    return out


# ───────────────────────── 顯示用字串（網頁端與排程端共用）─────────────────────────
def verdict_label(verdict):
    emoji, text = VERDICT_LABELS.get(verdict, ("⚪", "資料不足"))
    return f"{emoji}{text}"


def short_label(rec):
    """戰情速覽用：『🏗️建倉主導 建41/沖9/外12』；沒資料 → 『—』。"""
    if not rec:
        return "—"
    v = rec.get("verdict")
    if v in (None, "nodata"):
        return "⚪資料不足"
    return (f"{verdict_label(v)} 建{rec.get('build_pct', 0):.0f}/沖{rec.get('flip_pct', 0):.0f}"
            f"/外{rec.get('foreign_pct', 0):.0f}")
