#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bt_strategy.py —— 「回測驗證進場規則」的純函式層（2026-10-05 新增）

【為什麼存在】實盤(模擬倉)做多勝率只有 19.8%，但回測(backtest_entry_final.py，樣本內外 + 母體 300/500 檔
穩健性 + 投組層級模擬)找到一組勝率樣本內外皆 >50%、期望值為正的規則。這個模組讓「排程」使用和回測完全相同的
判斷與成交邏輯，避免實盤與回測各寫一套而悄悄分歧：

  進場訊號：穿山惡龍 MA60／前漲 40%（backtest_rules.find_chuan_e_events，參數與回測相同）＋ 大盤寬度閘門
            （母體中收盤站上 MA60 的比例 ≥ breadth_min；回測用 0.4/0.5）。
  進場價：訊號日隔日「開盤價」。
  出場：停利 12%（日內最高價觸及）／停損 15%（日內最低價觸及，同日兩者皆觸及視為先停損）／持有滿 20 個交易日
        收盤出；跳空越過停利/停損價時，以開盤價成交（與回測 simulate 完全一致）。
  成本：來回 0.585%（手續費買賣各 0.1425% + 賣出證交稅 0.3%，不計折讓）從報酬扣除。
  資金：每檔等額（notional，預設 10 萬）、同時最多 k_slots 檔、每日最多新進 max_new_per_day 檔，訊號依官網式均線分數高者優先。

純函式，不碰網路/資料庫（下載價格與寫表在 system_scheduler.py）。
"""
import numpy as np
import pandas as pd

DEFAULT_CFG = {
    "enabled": True,
    "rule": "chuan_e_ma60_40",
    # 2026-10-05 第二條通過「樣本內外 >50%＋期望值>0＋贏同出場隨機基準＋逐年穩定」的規則（backtest_cmd_rules.py，結果在私有表
    # ui_selftest_reports）：查9「均線糾結爆量突破」(量比≥2) ＋ 3日回檔≥5%，同一組出場(停利12%/停損15%/20日)：樣本內 60.2%／樣本外 63.4%。
    # 注意：這條『不加大盤寬度閘門』——回測顯示加寬度≥50% 反而不過關。
    "rules": ["chuan_e_ma60_40", "pullback_burst"],
    "pb_vol_ratio": 2.0, "pb_drop3": 0.05,
    "ma_n": 60, "rally_min": 0.40, "body_min": 0.03, "fast_days": 3, "slow_wait": 10,
    "breadth_min": 0.40,
    "tp": 0.12, "sl": 0.15, "hold": 20,
    "k_slots": 10, "max_new_per_day": 3,
    "notional": 100000,
    "universe_n": 300, "years": 2,
    "cooldown_days": 20,
    "old_long_enabled": False,     # 舊規則（評分≥6 的做多）是否仍新增部位；預設停用，只保留既有持倉自然出場
}
STRATEGY_TAG = "chuan_e_ma60_40"
RULE_PULLBACK = "pullback_burst"
RULE_LABELS = {"chuan_e_ma60_40": "穿山惡龍 MA60／前漲≥40%／大盤寬度閘門",
               "pullback_burst": "查9 爆量(量比≥2)＋3日回檔≥5%"}
TRADE_TYPE = "swing_bt"
TRIGGER_SOURCE = "bt_rule"


def merge_cfg(raw):
    cfg = dict(DEFAULT_CFG)
    if isinstance(raw, dict):
        for k, v in raw.items():
            if k in cfg and v is not None:
                cfg[k] = v
    return cfg


def _cost():
    import backtest_rules as br
    return br.COST_ROUND_TRIP


def append_future_rows(df, n=3):
    """在日K尾端補 n 列「虛擬未來列」（複製最後一列，日期往後遞增），僅供 find_chuan_e_events 能在最後一個真實
    交易日找到『隔日進場』的事件；不影響任何 <= 最後真實日的指標（均線等皆為因果計算）。"""
    last = df.iloc[-1]
    idx = [df.index[-1] + pd.Timedelta(days=i + 1) for i in range(n)]
    fut = pd.DataFrame([last.values] * n, index=pd.DatetimeIndex(idx), columns=df.columns)
    return pd.concat([df, fut])


def compute_breadth(prices):
    """母體中收盤站上 MA60 的比例（只用當日以前資料）。回傳 Series(index=日期)。"""
    import backtest_winrate_tuning as bt
    return bt.market_breadth(prices)


def pullback_burst_mask(df, vol_ratio=2.0, drop3=0.05):
    """
    「查9 爆量＋3日回檔」規則在每一天是否成立（布林陣列，與 backtest_cmd_rules.py 的『查9 均線糾結爆量突破 +3日回檔≥5%』同一套定義）：
      量比 = 當日量 ÷ 含當日的5日均量 ≥ vol_ratio（與 warroom_core._filter_backtest_one_stock 的 vol_ratio 同定義）
      且 近3日報酬 ≤ -drop3（收盤 ÷ 3日前收盤 − 1）
      且 近20日平均成交值 ≥ 流動性下限（point-in-time）且 官網式分數可計算（>=360日資料）。
    """
    import backtest_rules as br
    c, v = df["Close"], df["Volume"]
    vr = (v / v.rolling(5).mean()).values
    ret3 = (c / c.shift(3) - 1).values
    s15 = br.official_score_series(c).values
    liq = br.liq_ok_array(df)
    m = liq & np.isfinite(s15) & (vr >= float(vol_ratio)) & (ret3 <= -float(drop3))
    return np.nan_to_num(m.astype(float), nan=0.0).astype(bool)


def find_signals(prices, cfg, breadth=None, as_of=None):
    """
    回傳 (signals, info)。
    signals：list of {symbol, signal_date, ref_close, score15, breadth, rule}，依「規則優先序（cfg['rules'] 順序）→ score15 高者」排序。
    as_of：要偵測的「訊號日」(Timestamp/str)；預設取母體中最新的共同交易日。
    info：{signal_date, breadth, gated, n_scanned, n_candidates, by_rule}。gated＝穿山惡龍因大盤寬度不足而整條停手（爆量回檔規則不受寬度閘門影響）。
    """
    import backtest_rules as br
    rules = [r for r in (cfg.get("rules") or [cfg.get("rule") or "chuan_e_ma60_40"])]
    if not prices:
        return [], {"signal_date": None, "breadth": None, "gated": False, "n_scanned": 0, "n_candidates": 0, "by_rule": {}}
    breadth = compute_breadth(prices) if breadth is None else breadth
    if as_of is None:
        as_of = max(df.index[-1] for df in prices.values())
    as_of = pd.Timestamp(as_of)
    b_now = float(breadth.reindex([as_of]).iloc[0]) if as_of in breadth.index else float("nan")
    info = {"signal_date": str(as_of.date()), "breadth": None if np.isnan(b_now) else round(b_now, 4),
            "gated": False, "n_scanned": 0, "n_candidates": 0, "by_rule": {}}
    out = []
    use_chuan = "chuan_e_ma60_40" in rules
    use_pb = RULE_PULLBACK in rules
    gated = np.isnan(b_now) or b_now < float(cfg["breadth_min"])
    if use_chuan and gated:
        info["gated"] = True
    scan_chuan = use_chuan and not gated
    for sym, df in prices.items():
        if len(df) < 300 or df.index[-1] != as_of:
            continue
        info["n_scanned"] += 1
        L = len(df) - 1
        s15 = None
        if scan_chuan:
            d2 = append_future_rows(df, 3)
            evs = br.find_chuan_e_events(d2, int(cfg["ma_n"]), float(cfg["rally_min"]), float(cfg["body_min"]),
                                         int(cfg["fast_days"]), int(cfg["slow_wait"]), entries_only=True)
            if any(e[0] == L + 1 for e in evs):
                s15 = br.official_score_series(df["Close"]).values[L]
                out.append({"symbol": sym, "signal_date": str(as_of.date()), "ref_close": float(df["Close"].iloc[L]),
                            "score15": float(s15) if np.isfinite(s15) else 0.0, "breadth": info["breadth"],
                            "rule": "chuan_e_ma60_40"})
        if use_pb:
            m = pullback_burst_mask(df, cfg.get("pb_vol_ratio", 2.0), cfg.get("pb_drop3", 0.05))
            if m[L]:
                if s15 is None:
                    s15 = br.official_score_series(df["Close"]).values[L]
                out.append({"symbol": sym, "signal_date": str(as_of.date()), "ref_close": float(df["Close"].iloc[L]),
                            "score15": float(s15) if np.isfinite(s15) else 0.0, "breadth": info["breadth"],
                            "rule": RULE_PULLBACK})
    prio = {r: i for i, r in enumerate(rules)}
    out.sort(key=lambda r: (prio.get(r["rule"], 99), -r["score15"], r["symbol"]))
    # 同一檔被兩條規則同時選中：只留優先序高的那一筆（一檔只掛一張單）
    seen, dedup = set(), []
    for r in out:
        if r["symbol"] in seen:
            continue
        seen.add(r["symbol"])
        dedup.append(r)
    out = dedup
    info["n_candidates"] = len(out)
    for r in out:
        info["by_rule"][r["rule"]] = info["by_rule"].get(r["rule"], 0) + 1
    return out, info


def pick_new_entries(signals, n_open, k_slots, max_new_per_day, recently_traded=()):
    """依名額與每日上限挑出今天要新增的訊號（已持有/冷卻期內的標的先排除）。"""
    room = max(0, int(k_slots) - int(n_open))
    n = min(room, int(max_new_per_day))
    if n <= 0:
        return []
    banned = set(recently_traded)
    picked = []
    for s in signals:
        if s["symbol"] in banned:
            continue
        picked.append(s)
        if len(picked) >= n:
            break
    return picked


def evaluate_exit(rows, entry_price, tp, sl, hold):
    """
    與 backtest_entry_final.sim_cfg 同一套成交規則。
    rows：從「進場日」(含)開始的日K DataFrame（欄位 Open/High/Low/Close，index=日期，已收盤的 K 棒）。
    回傳 None（尚未出場）或 dict(exit_date, exit_price, reason, gross_ret, net_ret, days_held)。
    進場日當天(k=0)只看最高/最低（開盤就是進場價）；k>0 若跳空越過停損/停利價，以開盤價成交。
    """
    cost = _cost()
    E = float(entry_price)
    for k, (dt, r) in enumerate(rows.iterrows()):
        o, h, l, c = float(r["Open"]), float(r["High"]), float(r["Low"]), float(r["Close"])
        hit_sl = (l / E - 1) <= -sl
        hit_tp = (h / E - 1) >= tp
        if hit_sl:
            gross = min(-sl, (o / E - 1) if k > 0 else -sl)
            reason = "stop_loss"
        elif hit_tp:
            gross = max(tp, (o / E - 1) if k > 0 else tp)
            reason = "take_profit"
        elif k >= hold - 1:
            gross = c / E - 1
            reason = "time_stop"
        else:
            continue
        return {"exit_date": str(pd.Timestamp(dt).date()), "exit_price": round(E * (1 + gross), 4), "reason": reason,
                "gross_ret": gross, "net_ret": gross - cost, "days_held": k + 1}
    return None


def mark_to_market(entry_price, last_close):
    """未實現報酬（淨，已預扣來回成本；用於顯示，與已實現口徑一致）。"""
    return float(last_close) / float(entry_price) - 1 - _cost()
