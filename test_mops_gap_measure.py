"""【2026-11-15】每日重訊缺口量測（離線）：盤中重訊數、涉及候選數、戰卡看不到的數；明細只寫私有表。"""
import json
import sys

import mops_gap_measure as MM

ok = True


def chk(n, c, extra=None):
    global ok
    print(("✅ " if c else "❌ ") + n + ("" if c else f"  → {extra}"))
    ok = ok and c


DAY = "2026-11-12"


class Q:
    def __init__(self, db, name):
        self.db, self.name, self.f, self.op, self.pl, self.rg = db, name, {}, "sel", None, None

    def select(self, *a, **k):
        self.op = "sel"
        return self

    def eq(self, k, v):
        self.f[k] = v
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a):
        return self

    def range(self, a, b):
        self.rg = (a, b)
        return self

    def delete(self):
        self.op = "del"
        return self

    def insert(self, row):
        self.op, self.pl = "ins", row
        return self

    def execute(self):
        t = self.db.setdefault(self.name, [])
        if self.op == "ins":
            t.append(self.pl)
            return type("R", (), {"data": []})()
        if self.op == "del":
            self.db[self.name] = [r for r in t if not all(r.get(k) == v for k, v in self.f.items())]
            return type("R", (), {"data": []})()
        rows = [r for r in t if all(r.get(k) == v for k, v in self.f.items())]
        if self.rg:
            rows = rows[self.rg[0]:self.rg[1] + 1]
        return type("R", (), {"data": rows})()


class DB(dict):
    def table(self, n):
        return Q(self, n)


def mk(asof=None):
    db = DB()
    db["mops_events"] = [
        {"ev_date": DAY, "ev_time": "08:30:00", "symbol": "2330", "category": "自結營收", "importance": 3},   # 盤前
        {"ev_date": DAY, "ev_time": "09:15:00", "symbol": "2330", "category": "法說會", "importance": 1},    # 盤中、候選、asof 前
        {"ev_date": DAY, "ev_time": "12:40:10", "symbol": "1605", "category": "取得擴產訂單", "importance": 3},  # 盤中、候選、asof 後
        {"ev_date": DAY, "ev_time": "12:50:00", "symbol": "9999", "category": "訴訟裁罰", "importance": 4},   # 盤中、非候選
        {"ev_date": DAY, "ev_time": "13:31:00", "symbol": "1605", "category": "股利配息", "importance": 2},   # 收盤後
        {"ev_date": "2026-11-11", "ev_time": "10:00:00", "symbol": "1605", "category": "x", "importance": 1},  # 別天
    ]
    db["system_config"] = [
        {"config_key": f"tail_card:{DAY}:prep", "config_value": json.dumps({"cards": [{"symbol": "2330"}], "holds": [{"symbol": "1605"}]})},
        {"config_key": f"tail_card:{DAY}:final", "config_value": json.dumps({"cards": [{"symbol": "2330"}, {"symbol": "2454"}], "holds": []})},
    ]
    if asof:
        db["system_config"].append({"config_key": f"mops_asof:{DAY}", "config_value": json.dumps({"at": asof, "source": "官方檔產生時間"})})
    db["ui_selftest_reports"] = []
    return db


chk("盤中界線", MM.is_intraday("09:00:00") and MM.is_intraday("13:30:30") and not MM.is_intraday("08:59:59") and not MM.is_intraday("13:31:00") and not MM.is_intraday(None))
db = mk(asof=f"{DAY} 12:33")
cands = MM.candidate_symbols(db, DAY)
chk("候選＝兩次戰卡卡片與持有中", cands == {"2330", "1605", "2454"}, cands)
rep = MM.measure(db, DAY)
chk("盤中重訊 3 則", rep["intraday_total"] == 3, rep)
chk("涉及候選 2 則", rep["intraday_cand"] == 2, rep)
chk("戰卡看不到 1 則（12:33 之後發言的 1605）", rep["unseen_by_card"] == 1 and not rep["asof_unknown"], rep)
chk("明細標示 unseen", [h["unseen"] for h in rep["hits"]] == [False, True], rep["hits"])
db2 = mk()
rep2 = MM.measure(db2, DAY)
chk("時效不明 → 保守視為都看不到", rep2["asof_unknown"] and rep2["unseen_by_card"] == 2, rep2)
db3 = mk(asof="2026-11-11 23:00")
chk("別天的時效不拿來比對", MM.measure(db3, DAY)["asof_unknown"])
# 寫私有表、公開日誌只有數量
MM.save(db, rep, "r1")
MM.save(db, rep, "r2")   # 重跑覆蓋，不重複
rows = [r for r in db["ui_selftest_reports"] if r["summary"] == f"mops_gap_daily:{DAY}"]
chk("寫入 ui_selftest_reports 且重跑覆蓋", len(rows) == 1 and rows[0]["run_id"] == "r2" and rows[0]["report"]["intraday_cand"] == 2)
line = MM.public_line(rep)
chk("公開日誌只含數量（不含代號）", "盤中重訊 3 則" in line and "涉及候選 2 則" in line and "1605" not in line and "2330" not in line, line)
chk("時效不明時公開行有註記", "保守估計" in MM.public_line(rep2))
# 分頁（>1000 筆）
big = mk(asof=f"{DAY} 12:33")
big["mops_events"] = [{"ev_date": DAY, "ev_time": "10:00:00", "symbol": str(i), "category": "c", "importance": 1} for i in range(2300)]
chk("超過 1000 列分頁讀完", MM.measure(big, DAY)["intraday_total"] == 2300)
# 純函式
r = MM.compute([], set(), None)
chk("空資料不爆", r["intraday_total"] == 0 and r["unseen_by_card"] == 0)
sys.exit(0 if ok else 1)
