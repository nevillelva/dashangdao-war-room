"""早盤情報 AI 第二輪備援（2026-10-08）：純函式＋假呼叫，不連網。python3 test_ai_second_round.py"""
import warroom_core as wc
import system_scheduler as ss

FAIL = []


def check(name, cond, extra=""):
    if not cond:
        FAIL.append(name)
        print("❌", name, extra)
    else:
        print("✅", name)


K = "k-test"
wc._WORKING_NIM_CACHE[("probe", K)] = {
    "a/ok1": "ok(1.2s)", "b/empty": "空回覆", "c/slow": "APITimeoutError:Request timed out.", "d/gone": "404",
    "e/old": "410下架", "f/odd": "回覆不符指令", "g/bad": "BadRequestError:Error code: 400", "h/rl": "429限流"}
sec = wc.secondary_nim_models(K)
check("次要候選＝空回覆／逾時／回覆不符，不含 ok、404、410、400、429", sec == ["b/empty", "c/slow", "f/odd"], sec)
check("exclude 會排除", wc.secondary_nim_models(K, exclude=["b/empty"]) == ["c/slow", "f/odd"])
check("limit 生效", len(wc.secondary_nim_models(K, limit=1)) == 1)
check("沒探測結果回空", wc.secondary_nim_models("none") == [])

# _premarket_call_ai：第一輪失敗 → 第二輪成功
calls = []
orig = (wc.working_nim_models, wc.call_nim_validated, ss.NVIDIA_API_KEY)
try:
    ss.NVIDIA_API_KEY = K
    wc.working_nim_models = lambda key, limit=4, probe_timeout=20, **kw: ["a/ok1"]

    def fake(sp, up, key, models, validate, timeout=60, max_tokens=1500):
        calls.append((list(models), timeout, max_tokens))
        return (False, "全部模型失敗：x:APITimeoutError") if len(calls) == 1 else (True, "第二輪回覆內容夠長夠長夠長夠長夠長")
    wc.call_nim_validated = fake
    ok, res = ss._premarket_call_ai("s", "u")
    check("第一輪失敗後走第二輪並成功", ok and "第二輪" in res and len(calls) == 2, (ok, res, calls))
    check("第二輪用次要模型、逾時 90 秒", calls[1][0] == ["b/empty", "c/slow", "f/odd"] and calls[1][1] == 90, calls)

    calls.clear()
    wc.call_nim_validated = lambda *a, **k: (calls.append(1) or (True, "第一輪就成功第一輪就成功第一輪就成功"))
    ok, res = ss._premarket_call_ai("s", "u")
    check("第一輪成功就不跑第二輪", ok and len(calls) == 1)

    calls.clear()
    wc.call_nim_validated = lambda *a, **k: (calls.append(1) or (False, "全失敗"))
    import os
    os.environ.pop("GEMINI_API_KEY", None); os.environ.pop("GOOGLE_API_KEY", None); os.environ.pop("DEEPSEEK_API_KEY", None)
    ok, res = ss._premarket_call_ai("s", "u")
    check("兩輪都失敗：回 False 並記兩輪原因", (not ok) and "NIM第二輪" in res and len(calls) == 2, res)

    wc._WORKING_NIM_CACHE.pop(("probe", K), None)
    calls.clear()
    ok, res = ss._premarket_call_ai("s", "u")
    check("沒有次要候選：只跑一輪、不例外", (not ok) and len(calls) == 1, res)
finally:
    wc.working_nim_models, wc.call_nim_validated, ss.NVIDIA_API_KEY = orig

print("全部通過" if not FAIL else f"失敗 {len(FAIL)} 項：{FAIL}")
raise SystemExit(1 if FAIL else 0)
