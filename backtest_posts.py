#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backtest_posts.py —— 把「股票莊爸」貼文(8個rar)裡能用日K/公開資料驗證的規則，逐條回測（R99新增，跑在 Actions，唯讀）

只認勝率 > 5 成的做法。每條規則的通過條件（全部要成立）：
  樣本內勝率>50% 且 樣本外勝率>50% ∧ 樣本內外期望值>0 ∧ 樣本外勝率與期望都贏過「同出場、同市場條件」的隨機進場基準
  ∧ 逐年勝率穩定(>50%的年份占7成以上) ∧ 樣本外「依進場日平均」的勝率>50%（避免同一天大盤事件讓樣本灌水）。

驗證的貼文規則 → 對應的進場訊號：
  [P1] 常客榜(N20/N5)       ：每日全母體分數排名前10%算「在榜」，N20=近20日在榜天數，N5=近5日；貼文論點「過去N20＋現在N5要一起看」
  [P2] 三關(去掉籌碼)       ：官網式分數≥門檻 ∧ 突破20日高 ∧ 近10日收斂(振幅小)
  [P3] 穿山惡龍SOP          ：前漲≥30%、實體紅K≥3%、3日內站回(快) / 之後守MA20(慢)；出場=守MA20 / 停利停損網格
  [P4] 頭肩底/W底加強版     ：帶量突破頸線、站穩月線(收盤>MA20且MA20不下彎)；SOP出場=量測目標＋跌破左肩低點作廢
  [P5] 融資餘額閘門(相對化) ：全市場融資金額在近250日分位≤30%且20日下降 → 「殺乾淨」才進場（貼文的絕對金額 5300/5000億 不能直接用，改相對化）
  [P6] 族群同步             ：同產業≥3檔(母體內)同日分數≥12 ∧ 個股分數≥12；族群平均分排名前20%
不能用日K驗證的（籌碼暴增雷達=集保/分點/法人合成指標、營收/EPS故事）列在報告最後，只說明不測的原因。
【限制】日K同日停利停損視為先停損；母體有存活者偏誤；樣本外是多頭；通過組數要扣掉多重檢定的運氣份量。
"""
import os
import sys
import json
import argparse
import datetime as dt

import numpy as np
import pandas as pd

import backtest_rules as br
import backtest_winrate_tuning as bt

NO = 9.99
bt.TP_GRID = (0.05, 0.08, 0.12, NO)
bt.SL_GRID = (0.06, 0.08, NO)
bt.HOLD_GRID = (5, 10, 20)
bt.MAX_HOLD = 20
TRAIL_HOLD = 40
MIN_N_IS, MIN_N_OOS = int(os.environ.get("BT_MIN_N_IS") or 120), int(os.environ.get("BT_MIN_N_OOS") or 50)
THRESH = float(os.environ.get("WIN_TARGET") or 0.50)
BASE, BASE_G = "隨機進場(基準)", "隨機進場(基準)+融資殺乾淨"
GATE_TAG = "+融資殺乾淨"


# ------------------------------------------------------------------ 公開資料：融資餘額、產業別
def fetch_margin(token_raw):
    """全市場融資金額(TaiwanStockTotalMarginPurchaseShortSale 的 MarginPurchaseMoney.TodayBalance)。失敗回 None。"""
    try:
        import validate_etf_sources as V
        rows, err = V.fm_get("TaiwanStockTotalMarginPurchaseShortSale", token_raw, start_date="2018-06-01")
        if not rows:
            print(f"[融資] 取得失敗：{err}")
            return None
        d = pd.DataFrame(rows)
        d = d[d["name"] == "MarginPurchaseMoney"]
        s = pd.Series(d["TodayBalance"].astype(float).values, index=pd.to_datetime(d["date"])).sort_index()
        s = s[~s.index.duplicated(keep="last")]
        print(f"[融資] {len(s)} 日，{s.index[0].date()}~{s.index[-1].date()}")
        return s
    except Exception as e:
        print(f"[融資] 例外：{type(e).__name__}: {e}")
        return None


def fetch_industry(token_raw, symbols):
    try:
        import validate_etf_sources as V
        rows, err = V.fm_get("TaiwanStockInfo", token_raw)
        if not rows:
            print(f"[產業] 取得失敗：{err}")
            return None
        m = {}
        for r in rows:
            sid = str(r.get("stock_id", "")).strip()
            cat = str(r.get("industry_category", "")).strip()
            if sid in symbols and cat and "ETF" not in cat.upper() and cat not in ("大盤", "Index", "其他"):
                m[sid] = cat
        print(f"[產業] 對到 {len(m)}/{len(symbols)} 檔，{len(set(m.values()))} 個產業")
        return m
    except Exception as e:
        print(f"[產業] 例外：{type(e).__name__}: {e}")
        return None


# ------------------------------------------------------------------ 跨股票背景（全部只用當日以前資料）
def build_context(prices, margin, industry):
    idx = sorted(set().union(*[set(df.index) for df in prices.values()]))
    idx = pd.DatetimeIndex(idx)
    S = pd.DataFrame({s: br.official_score_series(df["Close"]).reindex(idx) for s, df in prices.items()})
    rank_pct = S.rank(axis=1, ascending=False, pct=True)
    intop = ((rank_pct <= 0.10) & S.notna()).astype(int)
    ctx = {"idx": idx, "S": S, "N20": intop.rolling(20, min_periods=20).sum(), "N5": intop.rolling(5, min_periods=5).sum(),
           "intop": intop, "breadth": bt.market_breadth(prices).reindex(idx)}
    if margin is not None:
        m = margin.reindex(idx.union(margin.index)).sort_index().ffill().reindex(idx)
        pct = m.rolling(250, min_periods=120).apply(lambda w: (w[:-1] <= w[-1]).mean(), raw=True)
        chg = m / m.shift(20) - 1
        ctx["gate"] = ((pct <= 0.30) & (chg < 0)).fillna(False)
        print(f"[融資閘門] 符合日數 {int(ctx['gate'].sum())}/{len(idx)}")
    else:
        ctx["gate"] = None
    if industry:
        ctx["industry"] = industry
        grp_cnt, grp_avg = {}, {}
        for cat in sorted(set(industry.values())):
            mem = [s for s, c_ in industry.items() if c_ == cat and s in S.columns]
            if len(mem) < 4:
                continue
            grp_cnt[cat] = (S[mem] >= 12).sum(axis=1)
            grp_avg[cat] = S[mem].mean(axis=1)
        ctx["grp_cnt"] = pd.DataFrame(grp_cnt)
        ctx["grp_avg_rank"] = pd.DataFrame(grp_avg).rank(axis=1, ascending=False, pct=True)
    return ctx


def families(sym, df, ctx):
    """回傳 {家族名: (訊號日index陣列, 基準家族名)}。訊號在該日收盤後成立，隔日開盤進場。"""
    c, h, lo = df["Close"], df["High"], df["Low"]
    n = len(df)
    liq = br.liq_ok_array(df)
    s15 = br.official_score_series(c).values
    base = liq & np.isfinite(s15)
    b = np.nan_to_num(ctx["breadth"].reindex(df.index).values, nan=0.0)
    gate = None if ctx["gate"] is None else ctx["gate"].reindex(df.index).fillna(False).values.astype(bool)
    n20 = ctx["N20"][sym].reindex(df.index).values if sym in ctx["N20"] else np.full(n, np.nan)
    n5 = ctx["N5"][sym].reindex(df.index).values if sym in ctx["N5"] else np.full(n, np.nan)
    intop = ctx["intop"][sym].reindex(df.index).values if sym in ctx["intop"] else np.zeros(n)
    hi20_prev = c.shift(1).rolling(20).max().values
    rng10 = ((h.rolling(10).max() - lo.rolling(10).min()) / c).values
    cv = c.values
    F = {}

    def add(name, mask, base_name=BASE):
        F[name] = (np.where(mask)[0], base_name)

    add(BASE, base)
    if gate is not None:
        add(BASE_G, base & gate, BASE_G)

    # [P1] 常客榜
    ok1 = base & np.isfinite(n20) & np.isfinite(n5)
    add("P1 常客榜 N20≥10", ok1 & (n20 >= 10))
    add("P1 常客榜 N20≥10 且 N5≥3", ok1 & (n20 >= 10) & (n5 >= 3))
    add("P1 常客榜 N20≥10 且 N5≥4", ok1 & (n20 >= 10) & (n5 >= 4))
    add("P1 反例 N20≥10 但 N5=0(榜上掉光)", ok1 & (n20 >= 10) & (n5 == 0))
    add("P1 今日在榜 且 N5≥4", ok1 & (intop == 1) & (n5 >= 4))
    add("P1 剛回榜(今日在榜 N5=1 N20≥8)", ok1 & (intop == 1) & (n5 == 1) & (n20 >= 8))
    add("P1 常客榜 N20≥10 且 N5≥3 且 寬度≥50%", ok1 & (n20 >= 10) & (n5 >= 3) & (b >= 0.5))

    # [P2] 三關(去掉籌碼)
    brk = cv >= hi20_prev
    add("P2 三關 分數≥12 且 突破20日高", base & (s15 >= 12) & brk)
    add("P2 三關 分數≥12 收斂≤10% 且 突破20日高", base & (s15 >= 12) & brk & (rng10 <= 0.10))
    add("P2 三關 分數≥10 收斂≤8% 且 突破20日高", base & (s15 >= 10) & brk & (rng10 <= 0.08))
    add("P2 三關 分數≥12 收斂≤10% 突破 且 寬度≥50%", base & (s15 >= 12) & brk & (rng10 <= 0.10) & (b >= 0.5))

    # [P5] 融資閘門(搭配其他進場)
    if gate is not None:
        add("P5 分數≥12" + GATE_TAG, base & (s15 >= 12) & gate, BASE_G)
        add("P5 分數≥12 且3日回檔≥3%" + GATE_TAG, base & (s15 >= 12) & ((cv / c.shift(3).values - 1) <= -0.03) & gate, BASE_G)
        add("P5 突破20日高 分數≥10" + GATE_TAG, base & (s15 >= 10) & brk & gate, BASE_G)

    # [P6] 族群同步
    if "grp_cnt" in ctx and sym in ctx.get("industry", {}):
        cat = ctx["industry"][sym]
        if cat in ctx["grp_cnt"].columns:
            gc = ctx["grp_cnt"][cat].reindex(df.index).values
            gr = ctx["grp_avg_rank"][cat].reindex(df.index).values
            add("P6 族群同步(同產業≥3檔分數≥12) 且 個股分數≥12", base & (s15 >= 12) & (gc >= 3))
            add("P6 族群平均分前20% 且 個股分數≥12", base & (s15 >= 12) & (gr <= 0.2))
            add("P6 族群同步≥3檔 且 個股突破20日高", base & (s15 >= 10) & (gc >= 3) & brk)
    return F


def pattern_entries(df, ctx, sym):
    """穿惡/頭肩底/W底：回傳 {家族名: (訊號日index, 基準名)} 以及 SOP出場交易 {家族名: (dates, rets)}。"""
    c = df["Close"]
    n = len(df)
    ma20 = c.rolling(20).mean().values
    cv = c.values
    gate = None if ctx["gate"] is None else ctx["gate"].reindex(df.index).fillna(False).values.astype(bool)
    b = np.nan_to_num(ctx["breadth"].reindex(df.index).values, nan=0.0)
    ents, sop = {}, {}

    def ev_idx(ev):
        return np.array(sorted({e[0] - 1 for e in ev}), dtype=int)

    def add(name, idx, base_name=BASE):
        ents[name] = (np.asarray(idx, dtype=int), base_name)

    def ma_up_ok(idx):
        idx = np.asarray(idx, dtype=int)
        if len(idx) == 0:
            return idx
        ok = (cv[idx] > ma20[idx]) & (ma20[idx] >= np.where(idx >= 3, ma20[np.maximum(idx - 3, 0)], np.inf))
        return idx[ok]

    # [P3] 穿山惡龍
    for body in (0.03, 0.05):
        ev = br.find_chuan_e_events(df, 20, 0.30, body, 3, 5, entries_only=True)
        allx = ev_idx(ev)
        fast = ev_idx([e for e in ev if e[1] == "fast"])
        slow = ev_idx([e for e in ev if e[1] == "slow"])
        tag = f"P3 穿惡 MA20 前漲30% 實體≥{int(body*100)}%"
        add(tag + " 全部", allx)
        add(tag + " 3日內站回(強)", fast)
        add(tag + " 慢(站穩5日)", slow)
        if body == 0.03 and len(allx):
            add(tag + " 全部 且 收盤>MA60", allx[cv[allx] > c.rolling(60).mean().values[allx]])
            add(tag + " 全部 且 寬度≥50%", allx[b[allx] >= 0.5])
            if gate is not None:
                add(tag + " 全部" + GATE_TAG, allx[gate[allx]], BASE_G)
    tr = br.find_chuan_e_events(df, 20, 0.30, 0.03, 3, 5)
    if tr:
        sop["P3 穿惡 MA20 前漲30% 實體≥3% 全部"] = ([t[0] for t in tr], [t[1] for t in tr])
        trf = [t for t in tr if t[2] == "fast"]
        if trf:
            sop["P3 穿惡 MA20 前漲30% 實體≥3% 3日內站回(強)"] = ([t[0] for t in trf], [t[1] for t in trf])
    # [P4] 頭肩底 / W底
    for kind, w, tol, stop_mode, label in (("hs", 3, 0.0, "left_shoulder", "頭肩底"), ("w", 8, 0.03, "any", "W底")):
        for vol in (1.0, 1.5, 2.0):
            ev = br.find_bottom_events(df, w, kind, vol, tol, stop_mode, entries_only=True)
            idx = ev_idx(ev)
            tag = f"P4 {label} 突破量比≥{vol:g}"
            add(tag, idx)
            if vol == 1.5 and len(idx):
                add(tag + " 且 站穩月線", ma_up_ok(idx))
                add(tag + " 且 寬度≥50%", idx[b[idx] >= 0.5])
                if gate is not None:
                    add(tag + GATE_TAG, idx[gate[idx]], BASE_G)
                    add(tag + " 站穩月線" + GATE_TAG, ma_up_ok(idx)[gate[ma_up_ok(idx)]] if len(ma_up_ok(idx)) else idx[:0], BASE_G)
        trs = br.find_bottom_events(df, w, kind, 1.5, tol, stop_mode, target_mult=1.0)
        if trs:
            sop[f"P4 {label} 突破量比≥1.5"] = ([t[0] for t in trs], [t[1] for t in trs])
    return ents, sop


# ------------------------------------------------------------------ 出場
def trailing_exit(df, t_idx, ma_n=20, max_hold=TRAIL_HOLD):
    """守MA20：進場後第一次收盤跌破MA20，隔日開盤出；到期收盤出。回傳淨報酬陣列。"""
    o, c = df["Open"].values, df["Close"].values
    ma = pd.Series(c).rolling(ma_n).mean().values
    below = np.isfinite(ma) & (c < ma)
    n = len(df)
    out = np.full(len(t_idx), np.nan)
    for k, t in enumerate(t_idx):
        e = t + 1
        if e + max_hold + 1 >= n or not np.isfinite(o[e]) or o[e] <= 0:
            continue
        j_end = e + max_hold
        seg = below[e:j_end + 1]
        px = o[e + int(seg.argmax()) + 1] if seg.any() else c[j_end]
        out[k] = px / o[e] - 1 - br.COST_ROUND_TRIP
    return out


def exit_label(tp, sl, hold):
    if tp >= NO and sl >= NO:
        return f"自然出場(持有{hold}日)"
    t = "不設" if tp >= NO else f"{tp:.0%}"
    s = "不設" if sl >= NO else f"{sl:.0%}"
    return f"停利{t}/停損{s}/{hold}日"


TRAIL_LABEL = "守MA20(最長40日)"
SOP_LABEL = "貼文SOP出場(量測目標/左肩作廢/守MA20)"


def fmt(s):
    if not s or not s.get("n"):
        return "n=0"
    return f"n={s['n']} 勝{s['win']*100:.1f}% 期望{s['exp_pct']}%"


def day_cluster_win(dates, rets):
    """依進場日平均的勝率：同一天的訊號先算該日勝率，再對所有日期平均。"""
    if len(rets) == 0:
        return None
    d = pd.DataFrame({"d": dates, "w": (np.asarray(rets) > 0).astype(float)})
    return float(d.groupby("d")["w"].mean().mean())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=int(os.environ.get("BT_N") or 300))
    ap.add_argument("--years", type=int, default=int(os.environ.get("BT_YEARS") or 6))
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--out", default="tuning_out")
    a = ap.parse_args()
    prices = br.synthetic_prices() if a.synthetic else br.download_prices(br.load_universe(a.n), a.years)
    if len(prices) < 5:
        print("❌ 有效股票太少")
        sys.exit(1)
    split = br.pick_split_date(prices)
    tok = os.environ.get("FINMIND_TOKEN", "").strip()
    margin = None if a.synthetic else fetch_margin(tok)
    industry = None if a.synthetic else fetch_industry(tok, set(prices))
    ctx = build_context(prices, margin, industry)

    acc = {}                                 # (family, exit_label) -> [(dates, rets)]
    base_of = {}

    def put(fam, ex, dates, rets):
        rets = np.asarray(rets, dtype=float)
        ok = np.isfinite(rets)
        if ok.any():
            acc.setdefault((fam, ex), []).append((pd.DatetimeIndex(dates)[ok], rets[ok]))

    for si, (sym, df) in enumerate(prices.items()):
        n = len(df)
        fams = families(sym, df, ctx)
        ents, sop = pattern_entries(df, ctx, sym)
        fams.update(ents)
        for fam, (idx, bname) in fams.items():
            idx = np.asarray(idx, dtype=int)
            idx = idx[(idx >= 0) & (idx + 2 + TRAIL_HOLD < n)]
            idx = bt.apply_cooldown(idx)
            if len(idx) == 0:
                continue
            base_of[fam] = bname
            res, dates = bt.simulate_family(df, idx)
            for (tp, sl, hold), arr in res.items():
                put(fam, exit_label(tp, sl, hold), dates, arr)
            put(fam, TRAIL_LABEL, df.index[idx + 1], trailing_exit(df, idx))
        for fam, (dts, rets) in sop.items():
            put(fam, SOP_LABEL, dts, rets)
        if (si + 1) % 50 == 0:
            print(f"  進度 {si + 1}/{len(prices)}")

    table = {}
    for key, parts in acc.items():
        dates = np.concatenate([p[0].values for p in parts])
        rets = np.concatenate([p[1] for p in parts])
        is_m = dates < np.datetime64(split)
        yrs = pd.DatetimeIndex(dates).year
        ro = rets[~is_m]
        mo = len(set(pd.DatetimeIndex(dates[~is_m]).strftime("%Y-%m"))) if len(ro) else 0
        table[key] = {"IS": bt.stats(rets[is_m]), "OOS": bt.stats(rets[~is_m]),
                      "q05_oos": (float(np.percentile(ro, 5)) * 100 if len(ro) else None), "months_oos": mo,
                      "cl_oos": day_cluster_win(dates[~is_m], rets[~is_m]),
                      "by_year": {int(y): bt.stats(rets[yrs == y]) for y in sorted(set(yrs))}}
    rows = []
    for (fam, ex), v in table.items():
        if fam in (BASE, BASE_G):
            continue
        bname = base_of.get(fam, BASE)
        bkey = (bname, TRAIL_LABEL if ex == SOP_LABEL else ex)
        bo = table.get(bkey)
        rows.append({"family": fam, "exit": ex, **v, "base_OOS": bo["OOS"] if bo else None,
                     "base_IS": bo["IS"] if bo else None, "base_name": bname})
    ok_n = lambda r: r["IS"].get("n", 0) >= MIN_N_IS and r["OOS"].get("n", 0) >= MIN_N_OOS

    def stable(r):
        ys = [v for v in r["by_year"].values() if v.get("n", 0) >= 15]
        return bool(ys) and sum(1 for v in ys if v["win"] > THRESH) >= 0.7 * len(ys)

    def passed(r):
        i, o, bo, bi = r["IS"], r["OOS"], r["base_OOS"] or {}, r["base_IS"] or {}
        return (ok_n(r) and r["exit"] != SOP_LABEL                      # SOP出場沒有同結構的隨機基準，不能算通過
                and r["months_oos"] >= 12                               # 樣本外要分散在 ≥12 個月，避免只靠一兩段行情
                and i["win"] > THRESH and o["win"] > THRESH and i["exp_pct"] > 0 and o["exp_pct"] > 0
                and o["exp_pct"] > bo.get("exp_pct", 1e9) and o["win"] > bo.get("win", 1.0)
                and i["exp_pct"] > bi.get("exp_pct", 1e9) and i["win"] > bi.get("win", 1.0)   # 樣本內也要贏基準
                and (r["cl_oos"] or 0) > THRESH and stable(r))

    good = sorted([r for r in rows if passed(r)], key=lambda r: -r["OOS"]["exp_pct"])
    n_tests = sum(1 for r in rows if ok_n(r))

    L = [f"# 莊爸貼文規則回測：勝率 > {THRESH:.0%}（{dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}）", "",
         f"- 母體 {len(prices)} 檔；樣本外切點 {split.date()}；成本來回 {br.COST_ROUND_TRIP*100:.3f}%；進場=訊號隔日開盤",
         f"- 融資閘門資料：{'有' if margin is not None else '取得失敗(相關列略過)'}；產業別資料：{'有' if industry else '取得失敗(族群列略過)'}",
         f"- 總共評估 {n_tests} 組(進場規則×出場)；通過全部條件 {len(good)} 組。",
         "  通過條件：樣本內外勝率>50%、期望值>0、樣本內外的勝率與期望都贏『同出場同市場條件』的隨機進場基準、逐年穩定、依進場日平均的樣本外勝率>50%、樣本外分散在≥12個月。",
         "  多重檢定：試的組數很多，純運氣也會有一些通過；只差一點點的通過者不要當真。", ""]
    # 一、每條規則的整體裁決(取通過數、勝率>50%數)
    L += ["## 一、每條規則的裁決", "",
          "| 規則 | 可評估出場數 | 樣本內外勝率皆>50% | 其中期望值皆>0 | 其中贏同條件隨機基準 | 全條件通過 | 最佳自然出場(持有20日) 樣本外勝率 vs 基準 |", "|---|---|---|---|---|---|---|"]
    for f in sorted({r["family"] for r in rows}):
        rr = [r for r in rows if r["family"] == f and ok_n(r)]
        if not rr:
            L.append(f"| {f} | 0 (樣本不足) | - | - | - | - | - |")
            continue
        w = [r for r in rr if r["IS"]["win"] > THRESH and r["OOS"]["win"] > THRESH]
        e = [r for r in w if r["IS"]["exp_pct"] > 0 and r["OOS"]["exp_pct"] > 0]
        beat = [r for r in e if r["base_OOS"] and r["OOS"]["exp_pct"] > r["base_OOS"]["exp_pct"] and r["OOS"]["win"] > r["base_OOS"]["win"]]
        p = [r for r in rr if passed(r)]
        nat = [r for r in rr if r["exit"] == exit_label(NO, NO, 20)]
        ns = "-"
        if nat:
            r0 = nat[0]
            bo = r0["base_OOS"] or {}
            ns = f"{r0['OOS']['win']*100:.1f}% vs {bo.get('win', 0)*100:.1f}%"
        L.append(f"| {f} | {len(rr)} | {len(w)} | {len(e)} | {len(beat)} | {len(p)} | {ns} |")
    # 一之二、貼文原本的出場方式
    L += ["", "## 一之二、貼文原本的出場方式（守MA20 / 量測目標＋左肩作廢）", "",
          "| 規則 | 出場 | 樣本內 | 樣本外 | 樣本外參考(隨機進場守MA20；SOP出場沒有同結構基準，只能當參考，不算通過) |", "|---|---|---|---|---|"]
    for r in sorted([r for r in rows if r["exit"] in (SOP_LABEL, TRAIL_LABEL) and ok_n(r)], key=lambda r: (r["family"], r["exit"])):
        L.append(f"| {r['family']} | {r['exit']} | {fmt(r['IS'])} | {fmt(r['OOS'])} | {fmt(r['base_OOS'])} |")
    # 二、通過清單
    L += ["", f"## 二、通過全部條件：{len(good)} 組", ""]
    if good:
        L += ["| 規則 | 出場 | 樣本內 | 樣本內基準 | 樣本外 | 樣本外基準 | 樣本外賺均/賠均/最差5% | 樣本外每日平均勝率 | 各年勝率 |", "|---|---|---|---|---|---|---|---|---|"]
        for r in good[:60]:
            yr = " ".join(f"{y}:{v['win']*100:.0f}%" for y, v in r["by_year"].items() if v.get("n", 0) >= 15)
            o = r["OOS"]
            L.append(f"| {r['family']} | {r['exit']} | {fmt(r['IS'])} | {fmt(r['base_IS'])} | {fmt(o)} | {fmt(r['base_OOS'])} | "
                     f"+{o.get('avg_win_pct')}% / {o.get('avg_loss_pct')}% / {r['q05_oos']:.1f}% | {r['cl_oos']*100:.1f}% | {yr} |")
    else:
        L.append("沒有任何組合通過。")
    # 三、基準
    L += ["", "## 三、隨機進場基準（同樣出場）", "", "| 基準 | 出場 | 樣本內 | 樣本外 | 樣本外每日平均勝率 |", "|---|---|---|---|---|"]
    for bn in (BASE, BASE_G):
        for ex in (exit_label(NO, NO, 10), exit_label(NO, NO, 20), TRAIL_LABEL):
            v = table.get((bn, ex))
            if v:
                L.append(f"| {bn} | {ex} | {fmt(v['IS'])} | {fmt(v['OOS'])} | {(v['cl_oos'] or 0)*100:.1f}% |")
    L += ["", "## 四、無法用日K/公開資料驗證、因此沒有測的項目", "",
          "- 籌碼暴增雷達(集保大戶＋分點大戶＋三大法人合成週指標)：門檻不公開，合成方式不明，不亂湊。",
          "- 營收年增、EPS、題材故事：貼文是事後解釋；若要驗證需歷史營收資料，另案。",
          "- 融資餘額絕對金額門檻(5,300/5,000億)：只有近年一個週期的樣本，改用相對化(250日分位≤30%且20日下降)驗證。",
          "- 高股息ETF貼文(小白版配置、月領1萬要準備多少、五邊戰士比較)：已納入ETF分頁的規劃器/新增欄位，不屬進出場規則。"]
    rep = "\n".join(L)
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "posts_report.md"), "w", encoding="utf-8") as f:
        f.write(rep)
    with open(os.path.join(a.out, "posts_results.json"), "w", encoding="utf-8") as f:
        json.dump({"split": str(split.date()), "n_symbols": len(prices), "n_tests": n_tests, "passed": good[:80],
                   "all_rows": [r for r in rows if ok_n(r)]}, f, ensure_ascii=False, default=str)
    print(rep)
    summ = os.environ.get("GITHUB_STEP_SUMMARY")
    if summ:
        with open(summ, "a", encoding="utf-8") as f:
            f.write(rep)


if __name__ == "__main__":
    main()
