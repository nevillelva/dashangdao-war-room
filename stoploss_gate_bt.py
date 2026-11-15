"""【2026-11-15 全方位分析報告建議】停損寬度 × 族群 × 大盤多空，與「寬度門檻 × 策略」的獨立回測模組（純函式，可離線測試）。

為什麼存在：新規則實盤停損平均 -16.1%、停利平均 +11.42%，風報比倒掛；做多在這段期間平均報酬為負。報告建議
  (1) 比較三種停損寬度（停利 11% 配停損 7%、ATR 倍數、現行 12%/15%）× 族群 × 大盤多空；
  (2) 把 bt_nightly 的「寬度 < 40% 穿山惡龍停手」閘門擴大到所有做多策略，並以 5 年資料驗證「寬度門檻 × 策略」。
本模組只新增「可驗證的回測階段與報告」，不改 bt_strategy.DEFAULT_CFG 的任何正式交易規則預設值，也不碰 system_scheduler。

口徑（與正式規則 bt_strategy 相同，才能互相對照）：
  訊號日收盤後成立 → 隔日開盤價進場 → 最長持有 20 個交易日（第 20 根收盤出）；
  停損以盤中最低價觸及、停利以盤中最高價觸及，同日兩者都觸及先算停損；跳空越過價位以開盤價成交（進場日當天不套用跳空）；
  與 bt_strategy.evaluate_exit 逐筆一致（test_stoploss_gate_bt.py 以隨機資料比對）。
  同一策略、同一檔股票 20 日內不重複進場（冷卻）。尚未跑滿的交易（資料尾端）不計入，避免用未實現報酬混入。

【成本假設（寫明）】來回成本 = 手續費買賣各 0.1425%（不計券商折讓）+ 賣出證交稅 0.3% = 0.585%，每筆從報酬扣除；
  可用 fee_discount（手續費折扣，1.0＝無折扣）與 slippage_pct（單邊滑價，預設 0）調整。不含：融資利息、股利稅、停損單實際滑價、流動性衝擊。
  「勝率」＝扣成本後淨報酬 > 0 的比例；「賺賠比」＝平均賺 ÷ 平均賠（皆為淨報酬）；
  「打平所需勝率」＝ 平均賠 ÷（平均賺＋平均賠），即在該組賺賠比下期望值剛好為 0 的勝率；實際勝率高於它才有正期望。

【價格口徑】主回測用「還原價」（股利／分割調整，yfinance auto_adjust=True；除權息不會產生假跳空）。
  「未還原顯示價」（auto_adjust=False，畫面上看到的價格，只含分割調整、不含股利調整）另跑同一批進場，用 basis_check 並列對照：
  除息缺口會在未還原價上被當成下跌，可能誤觸停損。報告所有績效欄位預設是還原價；未還原只出現在 basis_check。

【樣本與結論規則】每組 n < 30 一律標「不下結論」；n ≥ 30 但樣本內或樣本外其中一邊 < 30 只標「僅描述」；
  兩邊都 ≥ 30 才看方向是否一致（只描述方向，不宣稱統計顯著）。事件在同一天高度相關（同一個大盤環境），所以另外回報不重複進場日數。
【已知限制】母體只有目前仍上市的股票（存活者偏誤）；只涵蓋用日K就能算的策略（穿山惡龍、查9 爆量回檔）——
  營收動能需要 FinMind 月營收、舊評分修復版需要評分面板，這兩條必須在 GitHub 執行器上補資料後再接入（見 SIGNAL_FINDERS 擴充點）。
"""
from __future__ import annotations

from statistics import mean

import numpy as np
import pandas as pd

# ------------------------------------------------------------------ 成本
FEE_RATE = 0.001425   # 手續費（單邊）
TAX_RATE = 0.003      # 證交稅（只有賣出）
MIN_N = 30            # 樣本不足門檻：低於此值一律「不下結論」


def round_trip_cost_pct(fee_discount=1.0, slippage_pct=0.0):
    """來回成本（百分比，例如 0.585）。fee_discount：手續費折扣（1.0＝無折扣）；slippage_pct：單邊滑價（%）。"""
    return (2 * FEE_RATE * float(fee_discount) + TAX_RATE) * 100 + 2 * float(slippage_pct)


# ------------------------------------------------------------------ 出場變體
ATR_N = 14
ATR_SL_MIN, ATR_SL_MAX = 0.03, 0.15   # ATR 停損夾在 3%～15%（不比現行 15% 更寬、不窄到被雜訊洗出去）
VARIANTS = {
    "current": {"label": "現行 停利12%／停損15%", "tp": 0.12, "sl": 0.15},
    "fixed7": {"label": "停利11%／停損7%", "tp": 0.11, "sl": 0.07},
    "atr2": {"label": "停利11%／停損2×ATR14", "tp": 0.11, "atr_k": 2.0},
    "atr3": {"label": "停利11%／停損3×ATR14", "tp": 0.11, "atr_k": 3.0},
}
HOLD = 20
COOLDOWN = 20
BREADTH_THRESHOLDS = (0.0, 0.30, 0.40, 0.50, 0.60)   # 0.0＝不設閘門（對照組）；現行 bt_nightly 只對穿山惡龍用 0.40
STRATEGIES = ("chuan_e", "pullback_burst")           # 純日K策略；ALL＝兩者合併（同檔同進場日只算一次）
ALL = "ALL"


def sl_for_variant(spec, atr_pct):
    """某變體的停損比例（小數）。atr_pct：訊號日 ATR14 ÷ 收盤（小數）。固定停損直接回傳；ATR 款夾在 [ATR_SL_MIN, ATR_SL_MAX]。無 ATR 回 None。"""
    if "sl" in spec:
        return float(spec["sl"])
    if atr_pct is None or not np.isfinite(atr_pct) or atr_pct <= 0:
        return None
    return float(min(ATR_SL_MAX, max(ATR_SL_MIN, spec["atr_k"] * atr_pct)))


def simulate_trade(o, h, l, c, e, tp, sl, hold=HOLD, cost_pct=None):
    """與 bt_strategy.evaluate_exit 同一套成交規則（逐筆一致）。從進場日 e（開盤價進）起逐日檢查。
    回傳 (淨報酬%, 出場 index, 原因) ；進場價無效、或資料不足以跑完 hold 日且中途未觸發 → None（不計入）。"""
    cost_pct = round_trip_cost_pct() if cost_pct is None else cost_pct
    n = len(o)
    E = float(o[e]) if 0 <= e < n else float("nan")
    if not (E > 0):
        return None
    for i in range(e, min(e + hold - 1, n - 1) + 1):
        k = i - e
        oi, hi, li, ci = float(o[i]), float(h[i]), float(l[i]), float(c[i])
        if not all(np.isfinite(x) for x in (oi, hi, li, ci)):
            return None
        if li / E - 1 <= -sl:
            gross, why = (min(-sl, oi / E - 1) if k > 0 else -sl), "stop_loss"
        elif hi / E - 1 >= tp:
            gross, why = (max(tp, oi / E - 1) if k > 0 else tp), "take_profit"
        elif k >= hold - 1:
            gross, why = ci / E - 1, "time_stop"
        else:
            continue
        return gross * 100 - cost_pct, i, why
    return None


def atr_series(df, n=ATR_N):
    """簡單移動平均 TR（n 日）。回傳 numpy 陣列（前 n 根為 NaN）。"""
    h, l, c = df["High"], df["Low"], df["Close"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1, skipna=False)
    return tr.rolling(n).mean().values


# ------------------------------------------------------------------ 還原價／未還原價
def adjust_from_raw(raw):
    """從 auto_adjust=False 的日K（含 'Adj Close'）重建還原價：O/H/L 乘上 Adj Close ÷ Close。回傳新 DataFrame（Volume 原樣）。"""
    f = (raw["Adj Close"] / raw["Close"]).replace([np.inf, -np.inf], np.nan)
    out = raw.copy()
    for k in ("Open", "High", "Low"):
        out[k] = raw[k] * f
    out["Close"] = raw["Adj Close"]
    return out.drop(columns=["Adj Close"])


# ------------------------------------------------------------------ 訊號（純日K）
def find_chuan_e(df):
    import backtest_rules as br
    return [(int(e), k) for e, k in br.find_chuan_e_events(df, 60, 0.40, 0.03, 3, 10, entries_only=True)]


def find_pullback(df):
    import bt_strategy as bts
    m = bts.pullback_burst_mask(df, 2.0, 0.05)
    return [(int(i) + 1, "burst") for i in np.flatnonzero(m) if i + 1 < len(df)]


# 擴充點：營收動能、舊評分修復版需要額外資料（FinMind 月營收／評分面板），在執行器補好後在此註冊 finder(df)->[(entry_i, kind)]
SIGNAL_FINDERS = {"chuan_e": find_chuan_e, "pullback_burst": find_pullback}


def market_regime(mkt_close, ma_n=60):
    """大盤多空：收盤 ≥ MA60 → 'bull'，否則 'bear'；資料不足為 NaN。mkt_close：加權指數收盤 Series（DatetimeIndex）。"""
    s = mkt_close.sort_index().astype(float)
    ma = s.rolling(ma_n).mean()
    out = pd.Series(np.where(ma.notna(), np.where(s >= ma, "bull", "bear"), None), index=s.index, dtype=object)
    return out


# ------------------------------------------------------------------ 事件
def build_events(sym, adj, raw, sector, regime, breadth, finders=None, variants=None, cost_pct=None, hold=HOLD, cooldown=COOLDOWN):
    """單一股票的事件。adj：還原價日K（Open/High/Low/Close[/Volume]）；raw：未還原日K 或 None；regime／breadth：以日期為 index 的 Series。
    訊號日＝進場日前一個交易日；ATR、大盤多空、寬度都取訊號日（收盤後已知，無前視）。
    回傳 list of dict：sym, strategy, date, sector, regime, breadth, atr_pct, rets{variant:%}, why{variant}, rets_raw{variant:%}|None。"""
    finders = SIGNAL_FINDERS if finders is None else finders
    variants = VARIANTS if variants is None else variants
    cost_pct = round_trip_cost_pct() if cost_pct is None else cost_pct
    adj = adj.dropna(subset=["Close"])
    n = len(adj)
    if n < 100:
        return []
    o, h, l, c = (adj[k].values.astype(float) for k in ("Open", "High", "Low", "Close"))
    atr = atr_series(adj)
    idx = adj.index
    ds = list(idx.strftime("%Y-%m-%d"))
    reg = regime.reindex(idx, method="ffill") if regime is not None and len(regime) else None
    brd = breadth.reindex(idx, method="ffill") if breadth is not None and len(breadth) else None
    if raw is not None:
        raw = raw.reindex(idx)
        ro, rh, rl, rc = (raw[k].values.astype(float) for k in ("Open", "High", "Low", "Close"))
    out = []
    for strat, finder in finders.items():
        last_e = -10 ** 9
        for e, _kind in sorted(finder(adj), key=lambda x: x[0]):
            if e < 1 or e >= n or e - last_e < cooldown:
                continue
            s = e - 1
            ap = atr[s] / c[s] if np.isfinite(atr[s]) and c[s] > 0 else None
            rets, why, rets_raw, ok = {}, {}, ({} if raw is not None else None), True
            for name, spec in variants.items():
                sl = sl_for_variant(spec, ap)
                if sl is None:
                    ok = False
                    break
                r = simulate_trade(o, h, l, c, e, spec["tp"], sl, hold, cost_pct)
                if r is None:
                    ok = False
                    break
                rets[name], why[name] = round(r[0], 4), r[2]
                if raw is not None:
                    rr = simulate_trade(ro, rh, rl, rc, e, spec["tp"], sl, hold, cost_pct)
                    rets_raw[name] = None if rr is None else round(rr[0], 4)
            if not ok:
                continue
            last_e = e
            rg = reg.iloc[s] if reg is not None else None
            bv = float(brd.iloc[s]) if brd is not None and pd.notna(brd.iloc[s]) else None
            out.append({"sym": sym, "strategy": strat, "date": ds[e], "sector": sector, "atr_pct": round(float(ap) * 100, 3),
                        "regime": rg if isinstance(rg, str) else None, "breadth": bv,
                        "rets": rets, "why": why, "rets_raw": rets_raw})
    return out


def add_all_strategy(events):
    """加上 ALL：兩個策略合併，同一檔同一進場日只留第一個（依 STRATEGIES 順序）。回傳 events + ALL 事件（複製，strategy='ALL'）。"""
    seen, extra = set(), []
    order = {s: i for i, s in enumerate(STRATEGIES)}
    for ev in sorted(events, key=lambda x: (order.get(x["strategy"], 99), x["date"], x["sym"])):
        key = (ev["sym"], ev["date"])
        if key in seen:
            continue
        seen.add(key)
        extra.append({**ev, "strategy": ALL})
    return list(events) + extra


# ------------------------------------------------------------------ 統計
def group_stats(rets, min_n=MIN_N):
    """rets：淨報酬（%）。回傳 n、勝率%、平均%、平均賺%、平均賠%（負值）、賺賠比、打平所需勝率%、勝率減打平（百分點）、是否足夠樣本。
    n<min_n → verdict='不下結論'（數字仍列出，但不得據此下結論）。"""
    xs = [float(x) for x in rets if x is not None and np.isfinite(x)]
    n = len(xs)
    if n == 0:
        return {"n": 0, "verdict": "不下結論", "enough": False}
    wins = [x for x in xs if x > 0]
    loss = [x for x in xs if x <= 0]
    win_pct = 100.0 * len(wins) / n
    aw = mean(wins) if wins else None
    al = mean(loss) if loss else None
    payoff = (aw / abs(al)) if (aw is not None and al is not None and al < 0) else None
    be = (100.0 * abs(al) / (aw + abs(al))) if (aw is not None and al is not None and al < 0) else None
    return {"n": n, "win_pct": round(win_pct, 2), "avg": round(mean(xs), 3),
            "avg_win": None if aw is None else round(aw, 3), "avg_loss": None if al is None else round(al, 3),
            "payoff": None if payoff is None else round(payoff, 3),
            "breakeven_win_pct": None if be is None else round(be, 2),
            "edge_pp": None if be is None else round(win_pct - be, 2),
            "enough": n >= min_n, "verdict": "不下結論" if n < min_n else "可描述"}


def default_split(events, frac=0.7):
    """樣本內外切點：事件進場日（去重）前 frac 為樣本內。事件太少回 None。"""
    ds = sorted({e["date"] for e in events})
    return ds[int(len(ds) * frac)] if len(ds) >= 10 else None


def _seg(evs, variant, split, min_n):
    xs = [e["rets"][variant] for e in evs]
    st = group_stats(xs, min_n)
    st["days"] = len({e["date"] for e in evs})   # 不重複進場日數：事件同日相關，有效獨立樣本比 n 少
    if split is not None:
        a = [e["rets"][variant] for e in evs if e["date"] <= split]
        b = [e["rets"][variant] for e in evs if e["date"] > split]
        st["is"] = {"n": len(a), "avg": round(mean(a), 3) if a else None}
        st["oos"] = {"n": len(b), "avg": round(mean(b), 3) if b else None}
        st["verdict"] = _verdict(st, min_n)
    return st


def _verdict(st, min_n=MIN_N):
    """保守文字：n<30 不下結論；樣本內或外有一邊 <30 僅描述；兩邊 ≥30 才描述方向是否一致（不宣稱顯著）。"""
    if st.get("n", 0) < min_n:
        return "不下結論"
    i, o = st.get("is"), st.get("oos")
    if not i or not o or i["n"] < min_n or o["n"] < min_n or i["avg"] is None or o["avg"] is None:
        return "僅描述（樣本內或樣本外不足30）"
    if i["avg"] > 0 and o["avg"] > 0:
        return "樣本內外平均皆為正"
    if i["avg"] < 0 and o["avg"] < 0:
        return "樣本內外平均皆為負"
    return "樣本內外方向不一致"


def stoploss_matrix(events, split=None, min_n=MIN_N, variants=None):
    """停損寬度 × 策略 × 族群 × 大盤多空。回傳扁平 rows（維度欄位 None＝該維度不分組）。
    維度組合：(策略) 、(策略, 多空)、(策略, 族群)、(策略, 族群, 多空)；各 variant 各一列。"""
    variants = list(VARIANTS) if variants is None else list(variants)
    rows = []
    strategies = sorted({e["strategy"] for e in events})
    for variant in variants:
        for strat in strategies:
            es = [e for e in events if e["strategy"] == strat]
            keysets = {(None, None): es}
            for e in es:
                sec_k, reg_k = e["sector"] or "未分類", e["regime"] or "未知"
                keysets.setdefault((sec_k, None), []).append(e)
                keysets.setdefault((None, reg_k), []).append(e)
                keysets.setdefault((sec_k, reg_k), []).append(e)
            for (sec, reg), evs in keysets.items():
                st = _seg(evs, variant, split, min_n)
                rows.append({"variant": variant, "strategy": strat, "sector": sec, "regime": reg, **st})
    return rows


def breadth_matrix(events, thresholds=BREADTH_THRESHOLDS, split=None, min_n=MIN_N, variants=None):
    """寬度門檻 × 策略 × 停損變體。keep＝訊號日寬度 ≥ 門檻（放行）；drop＝低於門檻（被閘門擋下）；寬度缺值的事件只在門檻 0 時放行，其餘算 missing。
    回傳 rows：每列含 keep 與 drop 兩組統計，才看得出閘門擋掉的是不是壞交易。"""
    variants = list(VARIANTS) if variants is None else list(variants)
    rows = []
    for strat in sorted({e["strategy"] for e in events}):
        es = [e for e in events if e["strategy"] == strat]
        for thr in thresholds:
            keep = [e for e in es if (e["breadth"] is not None and e["breadth"] >= thr) or (thr <= 0)]
            drop = [e for e in es if thr > 0 and e["breadth"] is not None and e["breadth"] < thr]
            miss = sum(1 for e in es if thr > 0 and e["breadth"] is None)
            for variant in variants:
                rows.append({"strategy": strat, "breadth_min": thr, "variant": variant,
                             "kept_share_pct": round(100.0 * len(keep) / len(es), 1) if es else None, "missing_breadth": miss,
                             "keep": _seg(keep, variant, split, min_n), "drop": _seg(drop, variant, split, min_n) if drop else {"n": 0, "verdict": "不下結論"}})
    return rows


def basis_check(events, variants=None):
    """還原價 vs 未還原顯示價：同一批進場、同一停損停利比例，在兩種價格上各算一次。只含兩邊都算得出的事件。"""
    variants = list(VARIANTS) if variants is None else list(variants)
    out = {}
    for v in variants:
        pairs = [(e["rets"][v], e["rets_raw"][v]) for e in events
                 if e.get("rets_raw") and e["rets_raw"].get(v) is not None and e["strategy"] != ALL]
        if not pairs:
            out[v] = {"n": 0}
            continue
        diff = [b - a for a, b in pairs]
        out[v] = {"n": len(pairs), "avg_adj": round(mean(a for a, _ in pairs), 3), "avg_raw": round(mean(b for _, b in pairs), 3),
                  "avg_diff_pp": round(mean(diff), 3), "n_diff_over_0_5pp": sum(1 for d in diff if abs(d) > 0.5),
                  "note": "未還原價上除息缺口會被當成下跌，可能誤觸停損；主結論一律用還原價"}
    return out


# ------------------------------------------------------------------ 結論（保守）
def conclusions(sl_rows, br_rows, min_n=MIN_N):
    """只輸出「資料撐得住」的句子；其餘一律『不下結論』。不含任何對未驗證事項的宣稱。"""
    notes = []
    for strat in sorted({r["strategy"] for r in sl_rows}):
        base = next((r for r in sl_rows if r["strategy"] == strat and r["variant"] == "current" and r["sector"] is None and r["regime"] is None), None)
        if not base or base["n"] < min_n:
            notes.append(f"{strat}：樣本不足（n={0 if not base else base['n']}），停損寬度比較不下結論")
            continue
        for v in ("fixed7", "atr2", "atr3"):
            r = next((x for x in sl_rows if x["strategy"] == strat and x["variant"] == v and x["sector"] is None and x["regime"] is None), None)
            if not r or r["n"] < min_n:
                notes.append(f"{strat}/{v}：樣本不足，不下結論")
                continue
            d_is = (r["is"]["avg"] - base["is"]["avg"]) if r.get("is") and base.get("is") and r["is"]["n"] >= min_n and r["is"]["avg"] is not None and base["is"]["avg"] is not None else None
            d_oos = (r["oos"]["avg"] - base["oos"]["avg"]) if r.get("oos") and base.get("oos") and r["oos"]["n"] >= min_n and r["oos"]["avg"] is not None and base["oos"]["avg"] is not None else None
            if d_is is None or d_oos is None:
                notes.append(f"{strat}/{v}：樣本內或外不足30，僅描述（全期平均 {r['avg']:+.2f}% vs 現行 {base['avg']:+.2f}%），不下結論")
            elif d_is > 0 and d_oos > 0:
                notes.append(f"{strat}/{v}：平均報酬樣本內外都高於現行（+{d_is:.2f}／+{d_oos:.2f} 點）；僅為描述，未做顯著性檢定、事件同日相關、母體含存活者偏誤")
            elif d_is < 0 and d_oos < 0:
                notes.append(f"{strat}/{v}：平均報酬樣本內外都低於現行（{d_is:.2f}／{d_oos:.2f} 點）")
            else:
                notes.append(f"{strat}/{v}：與現行的差距樣本內外方向不一致，不下結論")
    for strat in sorted({r["strategy"] for r in br_rows}):
        r4 = next((x for x in br_rows if x["strategy"] == strat and x["variant"] == "current" and abs(x["breadth_min"] - 0.40) < 1e-9), None)
        if not r4:
            continue
        k, d = r4["keep"], r4["drop"]
        if d.get("n", 0) < min_n or k.get("n", 0) < min_n:
            notes.append(f"{strat}：寬度<40% 被擋下的事件 n={d.get('n', 0)}（放行 n={k.get('n', 0)}），不下結論")
            continue
        di, do = d.get("is"), d.get("oos")
        if not di or not do or di["n"] < min_n or do["n"] < min_n:
            notes.append(f"{strat}：寬度<40% 擋下組平均 {d['avg']:+.2f}%（放行組 {k['avg']:+.2f}%），但樣本內或外不足30，不下結論")
        elif di["avg"] < k["is"]["avg"] and do["avg"] < k["oos"]["avg"]:
            notes.append(f"{strat}：寬度<40% 擋下組平均 {d['avg']:+.2f}%（放行 {k['avg']:+.2f}%），樣本內外方向一致偏差；僅為描述，非因果證明")
        else:
            notes.append(f"{strat}：寬度<40% 擋下組與放行組的差距樣本內外方向不一致，不下結論")
    return notes


def build_report(events, split=None, cost_pct=None, meta=None, min_n=MIN_N):
    """整份報告（可 JSON 化）。events 應已含 ALL（add_all_strategy）。"""
    cost_pct = round_trip_cost_pct() if cost_pct is None else cost_pct
    split = default_split([e for e in events if e["strategy"] != ALL]) if split is None else split
    sl_rows = stoploss_matrix(events, split, min_n)
    br_rows = breadth_matrix(events, BREADTH_THRESHOLDS, split, min_n)
    return {
        "assumptions": {
            "cost_round_trip_pct": round(cost_pct, 4),
            "cost_note": "手續費買賣各0.1425%（無折扣）+賣出證交稅0.3%；不含融資利息、股利稅、停損單滑價、流動性衝擊",
            "price_basis": "還原價（auto_adjust=True）；未還原顯示價只在 basis_check 對照",
            "entry": "訊號日收盤後成立，隔日開盤價進場；最長持有20日（第20根收盤出）；同日觸及停損停利先算停損",
            "variants": {k: v for k, v in VARIANTS.items()},
            "atr": f"ATR{ATR_N}（簡單平均TR）÷訊號日收盤；停損夾在{ATR_SL_MIN:.0%}～{ATR_SL_MAX:.0%}",
            "regime": "大盤多空＝加權指數訊號日收盤 ≥ MA60 為多、否則為空",
            "breadth": "母體中收盤站上MA60的比例（訊號日）",
            "min_n": min_n, "split_date": split,
            "limits": "存活者偏誤；只含日K可算的策略（營收動能、舊評分修復版未涵蓋）；事件同日相關；未做顯著性檢定"},
        "meta": meta or {},
        "n_events": {s: sum(1 for e in events if e["strategy"] == s) for s in sorted({e['strategy'] for e in events})},
        "stoploss_matrix": sl_rows,
        "breadth_matrix": br_rows,
        "basis_check": basis_check(events),
        "conclusions": conclusions(sl_rows, br_rows, min_n),
    }


def render_text(report, top_sectors=0):
    """精簡文字摘要（只含總體與多空分組；族群別留在 JSON）。n<30 的列標「不下結論」。"""
    lines = [f"來回成本 {report['assumptions']['cost_round_trip_pct']}%；價格口徑：{report['assumptions']['price_basis']}"]
    for r in report["stoploss_matrix"]:
        if r["sector"] is not None:
            continue
        tag = r["regime"] or "全部"
        if r["n"] == 0:
            continue
        lines.append(f"{r['strategy']:<15}{r['variant']:<8}大盤{tag:<3} n={r['n']:<5} 勝率={r.get('win_pct')}% 平均={r.get('avg')}% "
                     f"賺賠比={r.get('payoff')} 打平勝率={r.get('breakeven_win_pct')}% [{r['verdict']}]")
    return "\n".join(lines)
