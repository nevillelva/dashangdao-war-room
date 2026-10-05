#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sector_map.py —— 個股 → 「族群(細產業)」對照的純函式層（2026-10-06 新增）

【為什麼要自己整理】FinMind TaiwanStockInfo 的 industry_category 同一檔股票可能有多列（例如同時有「電子工業」這種粗分類
與「半導體業」細分類），而且上市(TWSE)與上櫃(TPEx)對同一產業用字不一致（「金融保險／金融業」「數位雲端／數位雲端類」…）。
系統原本 dict(zip(stock_id, industry_category)) 是「最後一列贏」，會讓同一檔股票在不同次抓取被分到不同族群，
也會讓 304 檔掉進粗分類「電子工業」。回測與實盤若分類不一致，族群勝率就沒有意義，所以：
  1) 先把別名統一（normalize_sector）。
  2) 同一檔有多個分類時，優先取「細分類」、粗分類(電子工業/化學生技醫療/其他…)只在沒有細分類時才用（pick_sector）。
  3) 母體中檔數太少的族群併成「小族群合併」，避免單一族群樣本不足（merge_small）。
回測(backtest_sector.py)與實盤(system_scheduler/dashangdao)都用這同一份，分類才會一致。

純函式，不碰網路/資料庫。
"""

# 別名 → 統一名稱（上市/上櫃用字不同、或 FinMind 有多種寫法）
ALIASES = {
    "金融業": "金融保險", "金融保險業": "金融保險",
    "其他電子類": "其他電子業",
    "數位雲端類": "數位雲端",
    "居家生活類": "居家生活",
    "綠能環保類": "綠能環保",
    "運動休閒類": "運動休閒",
    "農業科技業": "農業科技",
    "觀光事業": "觀光餐旅",
    "文化創意業": "文化創意",
    "油電燃氣": "油電燃氣業",
}

# 優先順序（越前面越優先）：先上市標準產業，再櫃買主題類股
STANDARD_ORDER = [
    "半導體業", "電腦及週邊設備業", "光電業", "通信網路業", "電子零組件業", "電子通路業", "資訊服務業", "其他電子業",
    "電機機械", "電器電纜", "化學工業", "生技醫療業", "塑膠工業", "橡膠工業", "鋼鐵工業", "汽車工業", "紡織纖維",
    "食品工業", "水泥工業", "玻璃陶瓷", "造紙工業", "建材營造", "航運業", "觀光餐旅", "貿易百貨", "油電燃氣業",
    "金融保險", "農業科技", "綠能環保", "數位雲端", "運動休閒", "居家生活", "文化創意",
]
_ORDER_IDX = {n: i for i, n in enumerate(STANDARD_ORDER)}

# 粗分類：只在「沒有任何細分類」時才採用
COARSE_FALLBACK = {
    "電子工業": "電子其他(未細分)",
    "化學生技醫療": "化學生技醫療(未細分)",
    "其他": "其他",
}
# 不是一般個股的分類 → 排除
EXCLUDE = {"Index", "大盤", "ETF", "ETN", "受益證券", "存託憑證", "創新板股票", "上櫃指數", "上市指數", "指數",
           "受益證券-資產基礎證券", "不動產投資信託", "ETF(上市)", "ETF(上櫃)"}
SMALL_NAME = "小族群合併"


def normalize_sector(name):
    """統一別名；空值回 None。"""
    if name is None:
        return None
    s = str(name).strip()
    if not s or s.lower() in ("none", "nan"):
        return None
    return ALIASES.get(s, s)


def pick_sector(categories):
    """categories：同一檔股票所有的 industry_category（可含重複/別名/粗分類）。回傳單一族群名或 None。"""
    norm = [normalize_sector(c) for c in (categories or [])]
    norm = [c for c in norm if c]
    if not norm:
        return None
    norm = [c for c in norm if c not in EXCLUDE]      # ETF/指數/存託憑證等非一般個股分類一律忽略
    if not norm:
        return None
    fine = [c for c in norm if c not in COARSE_FALLBACK and c not in EXCLUDE]
    if fine:
        known = [c for c in fine if c in _ORDER_IDX]
        if known:
            return sorted(set(known), key=lambda c: _ORDER_IDX[c])[0]
        return sorted(set(fine))[0]          # 不在標準清單的新分類：字典序取第一個（確定性）
    for c in norm:
        if c in COARSE_FALLBACK:
            return COARSE_FALLBACK[c]
    return None


def build_sector_map(rows):
    """rows：[{stock_id, industry_category, ...}]（FinMind TaiwanStockInfo）。回傳 {stock_id: 族群}。"""
    cats = {}
    for r in rows or []:
        sid = str(r.get("stock_id") or "").strip()
        if not sid:
            continue
        cats.setdefault(sid, []).append(r.get("industry_category"))
    out = {}
    for sid, cs in cats.items():
        s = pick_sector(cs)
        if s:
            out[sid] = s
    return out


def sector_map_from_flat(stock_to_ind):
    """備援：只有 {stock_id: 單一分類} 時（system_config.industry_map_backup_cache）。分類已被『最後一列贏』選過，
    只能做別名統一與粗分類改名，品質較差（電子工業會較多）。"""
    out = {}
    for sid, ind in (stock_to_ind or {}).items():
        s = pick_sector([ind])
        if s:
            out[str(sid)] = s
    return out


def merge_small(sector_by_symbol, symbols, min_n=12):
    """只看『母體 symbols』內各族群的檔數；少於 min_n 的併成 SMALL_NAME。回傳 (新對照 {symbol: 族群}, {族群: 檔數})。
    母體中查不到分類的個股，歸入 SMALL_NAME。"""
    cnt = {}
    for s in symbols:
        sec = sector_by_symbol.get(s)
        if sec:
            cnt[sec] = cnt.get(sec, 0) + 1
    out = {}
    for s in symbols:
        sec = sector_by_symbol.get(s)
        out[s] = sec if (sec and cnt.get(sec, 0) >= min_n) else SMALL_NAME
    final_cnt = {}
    for v in out.values():
        final_cnt[v] = final_cnt.get(v, 0) + 1
    return out, final_cnt


def winrate_by_sector(rows, sector_of, sides=("long", "short")):
    """把已實現交易依『族群×方向』統計。rows：[{symbol, side, realized_roi(%), ...}]；sector_of：{symbol: 族群} 或 callable。
    回傳 list of {sector, side, n, wins, win_pct, avg_roi_pct}，依(方向, 筆數 大→小)排序；查不到族群者歸『未分類』。
    純函式。realized_roi 為百分比數字（與 system_portfolio.realized_roi 一致）。"""
    look = sector_of if callable(sector_of) else (lambda s: (sector_of or {}).get(str(s)))
    acc = {}
    for r in rows or []:
        side = r.get("side")
        if side not in sides:
            continue
        try:
            roi = float(r.get("realized_roi") or 0.0)
        except (TypeError, ValueError):
            continue
        sec = look(r.get("symbol")) or "未分類"
        a = acc.setdefault((side, sec), [0, 0, 0.0])
        a[0] += 1
        a[1] += 1 if roi > 0 else 0
        a[2] += roi
    out = [{"sector": sec, "side": side, "n": n, "wins": w, "win_pct": round(w / n * 100, 1),
            "avg_roi_pct": round(tot / n, 2)} for (side, sec), (n, w, tot) in acc.items()]
    out.sort(key=lambda x: (x["side"], -x["n"], x["sector"]))
    return out
