"""2026-10-10 貼文研究建議落實（ETF 54C 台帳、已公告配息、小額匯費、槓桿、重疊）＋早盤×戰卡對照。純函式＋假資料，不連網。
python3 test_etf_composition.py"""
import etf_core as E
import premarket as pm
import premarket_stage as ps

FAIL = []


def check(name, cond, extra=""):
    if not cond:
        FAIL.append(name)
        print("❌", name, extra)
    else:
        print("✅", name)


# ---- 台帳同步（A2）
ev = [{"symbol": "00919", "ex_date": "2026-06-16", "cash_per_unit": 1.0},
      {"symbol": "00919", "ex_date": "2026-09-16", "cash_per_unit": 1.1},
      {"symbol": "0056", "ex_date": "2026-07-21", "cash_per_unit": 1.35}]
latest = E.latest_ex_by_symbol(ev)
check("latest_ex 取每檔最近一次", latest["00919"]["ex_date"] == "2026-09-16" and latest["0056"]["ex_date"] == "2026-07-21")

led0 = {"00919": [{"ex_date": "2026-06-16", "ratio_54c": 0.0, "status": "confirmed", "source": "x"}]}
led1, pend = E.composition_sync(latest, led0)
recs = led1["00919"]
check("新除息日→新增一筆 carried 並沿用占比", recs[-1]["ex_date"] == "2026-09-16" and recs[-1]["status"] == "carried" and recs[-1]["ratio_54c"] == 0.0, recs[-1])
check("待確認清單含該檔", ("00919", "2026-09-16") in pend, pend)
check("沒有前次占比的檔 → 留空並待確認", led1["0056"][-1]["ratio_54c"] is None and ("0056", "2026-07-21") in pend, led1.get("0056"))

led2, pend2 = E.composition_sync(latest, led1)
check("同一除息日重跑不重複新增", len(led2["00919"]) == len(led1["00919"]) and pend2 == pend, pend2)

led3 = E.confirm_record(led2, "00919", "2026-09-16", 0.25, 0.0, "投信公告")
check("人工確認→confirmed 且不被覆寫", E.composition_sync(latest, led3)[0]["00919"][-1]["status"] == "confirmed"
      and E.composition_sync(latest, led3)[0]["00919"][-1]["ratio_54c"] == 0.25)
check("current_ratio 取最近有數字的一筆", E.current_ratio(led3, "00919") == 0.25 and E.current_ratio({}, "X") is None)
check("保留上限 COMPOSITION_KEEP", len(E.composition_sync(
    {"A": {"ex_date": "2030-01-01", "cash": 1}}, {"A": [{"ex_date": f"2020-01-{i+1:02d}", "ratio_54c": 0.1, "status": "confirmed"} for i in range(20)]})[0]["A"]) == E.COMPOSITION_KEEP)

# ---- 已公告配息（A1）
merged = E.merge_announced(ev, [{"symbol": "0056", "ex_date": "2026-10-22", "cash_per_unit": 1.72, "pay_date": "2026-11-11"},
                                {"symbol": "0056", "ex_date": "2026-07-21", "cash_per_unit": 9.9},   # 已存在，不重複
                                {"symbol": "", "ex_date": "2026-10-22", "cash_per_unit": 1}])
check("已公告配息併入、重複與空代號略過", len(merged) == len(ev) + 1 and merged[-1]["source"] == "manual_announced", len(merged))
nm = E.norm_events(merged)
hit = [x for x in nm if x.get("symbol") == "0056" and str(x.get("ex_date"))[:10] == "2026-10-22"]
check("併入後 norm_events 解析出 0056 的 10/22 配息 1.72", bool(hit) and abs(float(hit[0].get("cash", 0)) - 1.72) < 1e-9, hit)

# ---- 小額匯費（C3）、槓桿（C4）
rows = [{"symbol": "A", "per_payment_avg": 1500}, {"symbol": "B", "per_payment_avg": 9000}, {"symbol": "C", "per_payment_avg": None}]
check("小額配息旗標", E.small_payment_flags(rows) == ["A"], E.small_payment_flags(rows))
check("匯費占比", abs(E.fee_share(1000) - 0.01) < 1e-9 and E.fee_share(0) is None)
check("正二判為槓桿", E.is_leveraged("00631L", "元大台灣50正2") is True)
check("一般高息 ETF 非槓桿", E.is_leveraged("0056", "元大高股息") is False)

# ---- 早盤方向 × 戰卡（B）
check("偏多 + 偏多攻擊 → 一致", pm.card_alignment("偏多", 6) == "一致")
check("偏多 + 轉弱謹慎(-2) → 相反", pm.card_alignment("偏多", -2) == "相反")
check("偏多 + 中立(1) → 中性", pm.card_alignment("偏多", 1) == "中性")
check("偏空 + 偏空防守(-7) → 一致", pm.card_alignment("偏空", -7) == "一致")
check("偏空 + 觀察偏多(3) → 相反", pm.card_alignment("偏空", 3) == "相反")
check("無卡／NaN／壞值 → 無卡", pm.card_alignment("偏多", None) == "無卡" and pm.card_alignment("偏多", float("nan")) == "無卡"
      and pm.card_alignment("偏多", "x") == "無卡")
check("中性方向不比對", pm.card_alignment("中性", 8) == "中性")

picks = [{"symbol": "2330", "bias": "偏多", "risk": "", "name": "台積電"}, {"symbol": "3685", "bias": "偏多", "risk": "x", "name": "元創"}]
views = {"2330": {"signal": "🔥 偏多攻擊", "score": 7, "date": "20261005"}, "3685": {"signal": "🔵 偏空防守", "score": -7, "date": "20261005"}}
ps.attach_card_views(picks, views)
check("一致的不加風險", picks[0]["card_align"] == "一致" and picks[0]["risk"] == "")
check("相反的加風險提醒", picks[1]["card_align"] == "相反" and "相反" in picks[1]["risk"] and picks[1]["risk"].startswith("x"), picks[1])
p3 = [{"symbol": "9999", "bias": "偏多", "name": "無卡股"}]
ps.attach_card_views(p3, {})
check("沒有戰卡 → 無卡且 signal 為 None", p3[0]["card_align"] == "無卡" and p3[0]["card_signal"] is None)


class _Q:
    def __init__(self, data):
        self.data = data

    def __getattr__(self, _):
        return lambda *a, **k: self


class FakeSB:
    def __init__(self, last, rows):
        self.last, self.rows = last, rows

    def table(self, name):
        q = _Q(None)
        orig = q.__class__
        outer = self

        class T:
            def select(self_, cols):
                self_.cols = cols
                return self_

            def eq(self_, *a):
                return self_

            def in_(self_, *a):
                return self_

            def order(self_, *a, **k):
                return self_

            def limit(self_, *a):
                return self_

            def execute(self_):
                return _Q(outer.last if "trade_date" in getattr(self_, "cols", "") and "payload" not in getattr(self_, "cols", "") else outer.rows)
        return T()


import json as _j
fake = FakeSB([{"trade_date": "20261005"}], [{"symbol": "2330", "payload": _j.dumps({"signal_text": "🔥 偏多攻擊", "score": 7})},
                                             {"symbol": "3685", "payload": "not json"}])
got = ps.load_warcard_views(fake, ["2330", "3685"])
check("讀戰卡快取：壞 JSON 略過、正常取判定", got.get("2330", {}).get("signal") == "🔥 偏多攻擊" and "3685" not in got, got)
check("讀戰卡失敗回空", ps.load_warcard_views(None, ["2330"]) == {})
check("沒有代號回空", ps.load_warcard_views(fake, []) == {})

print("全部通過" if not FAIL else f"失敗 {len(FAIL)} 項：{FAIL}")
raise SystemExit(1 if FAIL else 0)
