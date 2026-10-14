"""【2026-10-14】自助回測面板測試：結果表整理＋Streamlit AppTest 實際按下「執行回測」。"""
import sys
import bt_lab as B
import bt_lab_tab as T
from bt_lab_testdata import mk_panel

fails = []


def check(name, cond):
    print(("PASS: " if cond else "FAIL: ") + name)
    if not cond:
        fails.append(name)


P = mk_panel(n_sym=60, n_days=500, effect=0.0, seed=3)
D = B.prepare(P)
spec = B.Spec(score_min=6, exits=[{"type": "hold", "n": 5}, {"type": "hold", "n": 20}, {"type": "ma20"}])
res = B.run(D, spec, n_tried=3)
tbl = T.result_table(res)
check("結果表一列一種出場", len(tbl) == 3 and "判定" in tbl.columns)
check("判定欄只含三種結果", set(tbl["判定"]) <= {"✅ 通過", "🟡 待觀察", "❌ 不通過"})
res10 = B.run(D, spec, n_tried=10)
check("試 10 組以上才出現多重檢定提醒", not any("多重檢定" in w for r in res["results"].values() for w in r["warnings"])
      and all(any("多重檢定" in w for w in r["warnings"]) for r in res10["results"].values()))
empty = T.result_table({"results": {"x": {"summary": {"n": 0}, "verdict": "待觀察", "reasons": [], "warnings": []}}})
check("零筆不當機", len(empty) == 1 and empty.iloc[0]["筆數"] == 0)
check("數字格式", T.pct(0.0123) == "+1.23%" and T.pct(None) == "—" and T.num(float("inf")) == "∞")

from streamlit.testing.v1 import AppTest


def app():
    import bt_lab as B2
    import bt_lab_tab as T2
    from bt_lab_testdata import mk_panel as mk
    T2._load_dataset = lambda _sb: ({"universe_n": 60, "built_at": "2026-10-14"}, B2.prepare(mk(n_sym=60, n_days=500, effect=0.0, seed=3)))
    T2.render_bt_lab(object())


at = AppTest.from_function(app, default_timeout=120).run()
check("面板載入無例外", not at.exception)
btn = [b for b in at.button if "執行回測" in str(b.label)]
if btn:
    btn[0].click()
    at.run()
    check("按下執行回測無例外", not at.exception)
    check("產生結果表", len(at.dataframe) >= 1)
    btn = [b for b in at.button if "執行回測" in str(b.label)]
    btn[0].click(); at.run()
    check("再按一次累計嘗試次數", at.session_state["btl_tried"] == 2)
else:
    check("找到執行回測按鈕", False)

if fails:
    print(f"\n{len(fails)} 項失敗"); sys.exit(1)
print("\n全部通過")
