"""舊評分修復版（old_score_v2）規則的純函式測試。python3 test_oldscore_rule.py"""
import numpy as np
import pandas as pd
import bt_strategy as bts

FAIL = []


def check(name, cond, extra=""):
    if not cond:
        FAIL.append(name)
        print("❌", name, extra)
    else:
        print("✅", name)


idx = pd.bdate_range(end="2026-10-12", periods=420)


def mkdf(seed, last_idx=None):
    r = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(r.normal(0.0003, 0.012, len(idx))))
    df = pd.DataFrame({"Open": c, "High": c * 1.01, "Low": c * 0.99, "Close": c, "Volume": r.uniform(1e6, 2e6, len(idx))}, index=idx)
    return df if last_idx is None else df[df.index <= last_idx]


prices = {"1111": mkdf(1), "2222": mkdf(2), "3333": mkdf(3), "4444": mkdf(4, "2026-10-09"), "5555": mkdf(5).iloc[-100:]}
cands = [
    {"symbol": "1111", "score": 7, "score_nochase": 7, "reasons": ["站穩多頭", "爆量"]},
    {"symbol": "2222", "score": 3, "score_nochase": 8, "reasons": "布林過熱·不建議追價"},   # 被追高懲罰壓低，不扣懲罰後 8
    {"symbol": "3333", "score": 5, "score_nochase": 5, "reasons": []},                       # 不到 6
    {"symbol": "4444", "score": 9, "score_nochase": 9, "reasons": []},                       # 最後一根不是訊號日
    {"symbol": "5555", "score": 9, "score_nochase": 9, "reasons": []},                       # 日K不足 300
    {"symbol": "9999", "score": 9, "score_nochase": 9, "reasons": []},                       # 沒有日K
    {"symbol": "6666", "score": "x", "score_nochase": None},                                 # 壞值
]
sg = bts.oldscore_signals(cands, prices, "2026-10-12")
check("dechase：2222 用不扣懲罰分數 8 入選，排在 1111 前面", [s["symbol"] for s in sg] == ["2222", "1111"], [s["symbol"] for s in sg])
check("不到 6 / 非訊號日 / 日K不足 / 沒日K / 壞值都被略過", all(s["symbol"] in ("1111", "2222") for s in sg))
check("訊號格式與 find_signals 相容", all(s["rule"] == bts.RULE_OLDSCORE and s["signal_date"] == "2026-10-12" and s["ref_close"] > 0
                                      and s["breadth"] is None and "score15" in s for s in sg))
sg2 = bts.oldscore_signals(cands, prices, "2026-10-12", dechase=False)
check("dechase=False 用原分數：只剩 1111（2222 原分 3）", [s["symbol"] for s in sg2] == ["1111"], [s["symbol"] for s in sg2])
check("max_n 上限", len(bts.oldscore_signals(cands, prices, "2026-10-12", max_n=1)) == 1 and bts.oldscore_signals(cands, prices, "2026-10-12", max_n=0) == [])
check("空候選/None 不爆", bts.oldscore_signals([], prices, "2026-10-12") == [] and bts.oldscore_signals(None, prices, "2026-10-12") == [])
check("score_nochase 缺值退回原分數", [s["symbol"] for s in bts.oldscore_signals([{"symbol": "1111", "score": 6}], prices, "2026-10-12")] == ["1111"])

# 個別停損
cfg = dict(bts.DEFAULT_CFG)
check("預設：old_score_v2 停損 10%、其餘 15%", abs(bts.sl_for(cfg, "old_score_v2") - 0.10) < 1e-12 and abs(bts.sl_for(cfg, "pullback_burst") - 0.15) < 1e-12
      and abs(bts.sl_for(cfg, None) - 0.15) < 1e-12)
check("壞值退回全域 sl", bts.sl_for(dict(cfg, sl_by_rule={"old_score_v2": "abc"}), "old_score_v2") == 0.15
      and bts.sl_for(dict(cfg, sl_by_rule={"old_score_v2": 1.5}), "old_score_v2") == 0.15
      and bts.sl_for(dict(cfg, sl_by_rule={"old_score_v2": 0}), "old_score_v2") == 0.15
      and bts.sl_for(dict(cfg, sl_by_rule=None), "old_score_v2") == 0.15
      and bts.sl_for(dict(cfg, sl_by_rule="x"), "old_score_v2") == 0.15)
check("預設 rules 含第四條，且排在最後（優先序最低）", bts.DEFAULT_CFG["rules"][-1] == bts.RULE_OLDSCORE and len(bts.DEFAULT_CFG["rules"]) == 4)
check("RULE_LABELS 有標籤", bts.RULE_OLDSCORE in bts.RULE_LABELS)
check("舊做多仍預設停用（舊出場路徑不復活）", bts.DEFAULT_CFG["old_long_enabled"] is False)

# 與 find_signals 合併：同一檔被高優先序規則選中只留一筆；old 在最後
big = {"1111": mkdf(1), "2222": mkdf(2)}
extra = bts.oldscore_signals(cands, prices, "2026-10-12")
allsig, info = bts.find_signals(big, dict(cfg, rules=["old_score_v2"]), as_of="2026-10-12", extra=extra)
check("find_signals 吃舊評分修復版並計數", info["by_rule"].get(bts.RULE_OLDSCORE) == 2, info)

# 出場：同一段走勢，停損 10% 與 15% 的結果不同（驗證 per-rule 停損真的被用到）
rows = pd.DataFrame({"Open": [100, 100, 92, 80], "High": [101, 101, 93, 82], "Low": [99, 99, 89, 79], "Close": [100, 100, 90, 80]},
                    index=pd.bdate_range("2026-10-01", periods=4))
r10 = bts.evaluate_exit(rows, 100.0, 0.12, bts.sl_for(cfg, "old_score_v2"), 20)
r15 = bts.evaluate_exit(rows, 100.0, 0.12, bts.sl_for(cfg, "pullback_burst"), 20)
check("停損10%在第3日(低89)出場；15%要等到第4日", r10 and r10["reason"] == "stop_loss" and r10["days_held"] == 3
      and r15 and r15["reason"] == "stop_loss" and r15["days_held"] == 4, (r10, r15))
check("停損10%跳空以開盤價成交不優於-10%", r10 and r10["gross_ret"] <= -0.10 + 1e-12)

# 配置合併：使用者只改 dechase 不會弄丟 sl_by_rule
m = bts.merge_cfg({"old_score_v2_dechase": False})
check("merge_cfg 保留其他預設", m["old_score_v2_dechase"] is False and m["sl_by_rule"] == {"old_score_v2": 0.10})

print("\n結果：", "全部通過" if not FAIL else f"失敗 {len(FAIL)} 項：{FAIL}")
raise SystemExit(1 if FAIL else 0)
