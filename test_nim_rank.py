"""NIM 排行（2026-10-09）＋ Gemini 兩組金鑰備援：純函式＋假呼叫，不連網。python3 test_nim_rank.py"""
import os
import warroom_core as wc
import system_scheduler as ss

FAIL = []


def check(name, cond, extra=""):
    if not cond:
        FAIL.append(name)
        print("❌", name, extra)
    else:
        print("✅", name)


# --- merge / order
prev = None
r1 = wc.merge_nim_rank(prev, {"a": "ok(2.0s)", "b": "ok(1.0s)", "c": "404"}, {"b": {"ok": False, "s": 60, "err": "APITimeoutError"}}, "t1")
check("真任務失敗累計 streak", r1["models"]["b"]["streak_fail"] == 1 and r1["models"]["b"]["real_fail"] == 1)
r2 = wc.merge_nim_rank(r1, {"a": "ok(2.0s)", "b": "ok(1.0s)"}, {"b": {"ok": False, "s": 60, "err": "APITimeoutError"}}, "t2")
check("連續失敗 2 次", r2["models"]["b"]["streak_fail"] == 2)
check("探測速度第一但連續真任務失敗的模型排最後", wc.order_nim_models([(1.0, "b"), (2.0, "a")], r2) == ["a", "b"])
r3 = wc.merge_nim_rank(r2, {"a": "ok(2.0s)", "b": "ok(1.0s)"}, {"b": {"ok": True, "s": 8.5, "err": ""}}, "t3")
check("成功一次就清掉連續失敗", r3["models"]["b"]["streak_fail"] == 0 and r3["models"]["b"]["real_ok"] == 1)
check("上次真任務成功的排最前", wc.order_nim_models([(1.0, "x"), (5.0, "b"), (2.0, "a")], r3) == ["b", "x", "a"], wc.order_nim_models([(1.0, "x"), (5.0, "b"), (2.0, "a")], r3))
check("沒有歷史時＝依探測秒數", wc.order_nim_models([(3.0, "p"), (1.0, "q")], None) == ["q", "p"])
check("merge 不改動傳入的 prev", "streak_fail" in r1["models"]["b"] and r1["models"]["b"]["streak_fail"] == 1)

# --- Gemini 金鑰
for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
    os.environ.pop(k, None)
check("沒設金鑰＝空清單", ss._gemini_keys() == [])
os.environ["GOOGLE_API_KEY"] = "g2"
check("只有 GOOGLE_API_KEY 也能用", ss._gemini_keys() == ["g2"])
os.environ["GEMINI_API_KEY"] = "g1"
check("兩把：GEMINI 優先、GOOGLE 備用", ss._gemini_keys() == ["g1", "g2"])
os.environ["GOOGLE_API_KEY"] = "g1"
check("相同金鑰去重", ss._gemini_keys() == ["g1"])
os.environ["GOOGLE_API_KEY"] = "g2"

used = []
orig = wc.call_openai_compatible
try:
    def fake(base, key, model, sp, up, timeout=45, max_tokens=1500):
        used.append(key)
        return (False, "RateLimitError: 429") if key == "g1" else (True, "第二把金鑰回覆內容夠長夠長夠長夠長")
    wc.call_openai_compatible = fake
    errs = []
    ok, res = ss._premarket_gemini("s", "u", None, errs)
    check("第一把失敗→換第二把成功", ok and used == ["g1", "g2"] and any("Gemini1" in e for e in errs), (ok, used, errs))

    # NIM 第一輪失敗 → 先 Gemini，不先跑 NIM 第二輪
    calls = []
    o2 = (wc.working_nim_models, wc.call_nim_validated, ss.NVIDIA_API_KEY)
    ss.NVIDIA_API_KEY = "k-x"
    wc.working_nim_models = lambda key, limit=4, probe_timeout=20, **kw: ["m1"]
    wc.call_nim_validated = lambda *a, **k: (calls.append(1) or (False, "全部模型失敗：x:APITimeoutError"))
    used.clear()
    ok, res = ss._premarket_call_ai("s", "u")
    check("NIM 第一輪失敗後由 Gemini 接手且只跑一次 NIM", ok and len(calls) == 1 and used == ["g1", "g2"], (ok, res, calls, used))
    wc.working_nim_models, wc.call_nim_validated, ss.NVIDIA_API_KEY = o2
finally:
    wc.call_openai_compatible = orig
    for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        os.environ.pop(k, None)

print("全部通過" if not FAIL else f"失敗 {len(FAIL)} 項：{FAIL}")
raise SystemExit(1 if FAIL else 0)
