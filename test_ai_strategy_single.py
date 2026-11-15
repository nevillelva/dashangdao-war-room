"""AI 戰略推演單一模型串行測試（2026-11-15）：假 openai client，不連網、不讀真金鑰。
執行：/home/claude/venv311/bin/python test_ai_strategy_single.py"""
import inspect
import json
import os
import sys
import types

FAKE_CALLS = []      # [(base_url, model, max_retries)]
BEHAVIOR = {}        # base_url -> 回傳文字，或 Exception 實例
FAIL = []


def chk(name, cond, extra=""):
    print(("✅ " if cond else "❌ ") + name + ("" if cond else f"  → {extra}"))
    if not cond:
        FAIL.append(name)


class _Completions:
    def __init__(self, base, retries):
        self.base, self.retries = base, retries

    def create(self, model, messages, **kw):
        FAKE_CALLS.append((self.base, model, self.retries))
        b = BEHAVIOR.get(self.base)
        if isinstance(b, Exception):
            raise b
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=b), finish_reason="stop")])


class FakeOpenAI:
    def __init__(self, base_url=None, api_key=None, max_retries=2, timeout=None):
        self.chat = types.SimpleNamespace(completions=_Completions(base_url, max_retries))


fake_mod = types.ModuleType("openai")
fake_mod.OpenAI = FakeOpenAI
sys.modules["openai"] = fake_mod   # 假 client：環境沒裝 openai 也能跑

import system_scheduler as ss  # noqa: E402
import warroom_core as wc  # noqa: E402
import strategy_view as SV  # noqa: E402

NV = "https://integrate.api.nvidia.com/v1"
GEM = ss._GEMINI_BASE
DS = ss.STRATEGY_DEEPSEEK_BASE
GOOD = json.dumps({"thesis": "投信連買且在季線上", "can_buy": "開低後量縮", "cannot_buy": "大盤跌破季線",
                   "key_level": "停損 90", "plan": "13:20 進、抱 20 日", "stance": "小量"}, ensure_ascii=False)

# 保存原狀，測完還原
_saved = {"nv_key": ss.NVIDIA_API_KEY, "discover": wc.discover_nim_models_plain, "pick": ss._gemini_pick_model}
_env_keys = ("GEMINI_API_KEY", "GOOGLE_API_KEY", "DEEPSEEK_API_KEY", "DEEPSEEK_MODEL")
_saved_env = {k: os.environ.get(k) for k in _env_keys}

wc.discover_nim_models_plain = lambda api_key, limit=15, timeout=10: ["nv/model-a", "nv/model-b"]   # 只是清單，不連網
ss._gemini_pick_model = lambda key: "gemini-test"


def setup(nv_key="nv-test", gem=True, ds=True):
    ss.NVIDIA_API_KEY = nv_key
    for k in _env_keys:
        os.environ.pop(k, None)
    if gem:
        os.environ["GEMINI_API_KEY"] = "g-test"
    if ds:
        os.environ["DEEPSEEK_API_KEY"] = "d-test"
    BEHAVIOR.clear()
    FAKE_CALLS.clear()


def run_once(validate=None):
    ch = ss._strategy_llm_chain("long", validate=validate)
    return ch("系統提示", "使用者提示")


# ── 常數與結構
chk("模型順序常數為 NVIDIA→Gemini→DeepSeek", ss.STRATEGY_MODEL_ORDER == ("nvidia", "gemini", "deepseek"), ss.STRATEGY_MODEL_ORDER)
src = inspect.getsource(ss._strategy_llm_chain)
chk("鏈的實作沒有平行執行（無 ThreadPool／as_completed）", "ThreadPool" not in src and "as_completed" not in src)

# ── (a) 主模型成功：只呼叫一次
setup()
BEHAVIOR[NV] = GOOD
BEHAVIOR[GEM] = GOOD
BEHAVIOR[DS] = GOOD
ok, text, via = run_once()
chk("(a) 主模型成功：回傳成功且來源為 NVIDIA", ok and via == "NVIDIA", (ok, via))
chk("(a) 主模型成功：只呼叫 NVIDIA 一次", len(FAKE_CALLS) == 1 and FAKE_CALLS[0][0] == NV, FAKE_CALLS)
chk("(a) 主模型成功：Gemini／DeepSeek 完全未呼叫", all(c[0] == NV for c in FAKE_CALLS), FAKE_CALLS)
ch = ss._strategy_llm_chain("long")
ch("s", "u"); ch("s", "u")
chk("(a) 連續三檔推演＝三次呼叫、每檔一次（不會一檔多次）", len(FAKE_CALLS) == 1 + 2, len(FAKE_CALLS))
chk("(a) SDK 自動重試已關閉（max_retries=0）", all(c[2] == 0 for c in FAKE_CALLS), FAKE_CALLS)

# ── (b) 主模型失敗才換下一個
setup()
BEHAVIOR[NV] = RuntimeError("503 service unavailable")
BEHAVIOR[GEM] = GOOD
BEHAVIOR[DS] = GOOD
ok, text, via = run_once()
chk("(b) NVIDIA 例外 → 改用 Gemini 成功", ok and via == "Gemini", (ok, via))
chk("(b) NVIDIA 失敗後 Gemini 只呼叫一次，DeepSeek 不呼叫", [c[0] for c in FAKE_CALLS] == [NV, GEM], FAKE_CALLS)

setup()
BEHAVIOR[NV] = "我覺得可以買"          # 非 JSON → 格式不合，視同失敗
BEHAVIOR[GEM] = GOOD
ok, text, via = run_once()
chk("(b) NVIDIA 格式不合 → 換 Gemini", ok and via == "Gemini" and [c[0] for c in FAKE_CALLS] == [NV, GEM], (via, FAKE_CALLS))

setup()
BEHAVIOR[NV] = RuntimeError("401")
BEHAVIOR[GEM] = RuntimeError("429 quota")
BEHAVIOR[DS] = GOOD
ok, text, via = run_once()
chk("(b) NVIDIA、Gemini 都失敗 → DeepSeek 成功，各只一次", ok and via == "DeepSeek" and [c[0] for c in FAKE_CALLS] == [NV, GEM, DS], (via, FAKE_CALLS))

setup(nv_key="")                        # 沒有 NVIDIA 金鑰：跳過、不呼叫
BEHAVIOR[GEM] = GOOD
ok, text, via = run_once()
chk("(b) 沒有 NVIDIA 金鑰 → 直接用 Gemini，NVIDIA 完全不呼叫", ok and via == "Gemini" and all(c[0] == GEM for c in FAKE_CALLS), FAKE_CALLS)

# ── (c) 完全沒有金鑰：不呼叫、回明確未設定訊息
setup(nv_key="", gem=False, ds=False)
ok, text, via = run_once()
chk("(c) 無任何金鑰：不呼叫任何模型", len(FAKE_CALLS) == 0, FAKE_CALLS)
chk("(c) 無任何金鑰：回傳失敗且訊息明確寫『未設定任何 AI 金鑰』", (not ok) and "未設定任何 AI 金鑰" in text and via == "", (ok, text))
chk("(c) 失敗訊息標示僅供參考、不參與買賣判定", "僅供參考，不參與買賣判定" in text, text)

# ── 結果是否能被 strategy_view 正確吃進（不影響既有流程）
setup()
BEHAVIOR[NV] = GOOD
chk("推演結果仍能通過 strategy_view.parse", SV.parse(GOOD) is not None)
chk("推送用標示含『僅供參考，不參與買賣判定』", "僅供參考，不參與買賣判定" in SV.UNVERIFIED)

# ── 排程端 AI 點評（run_ai_commentary_for_picks）：串行、單一模型、標示僅供參考
setup()
src_c = inspect.getsource(ss.run_ai_commentary_for_picks)
chk("點評函式沒有平行執行（無 ThreadPool／as_completed）", "ThreadPool" not in src_c and "as_completed" not in src_c)
BEHAVIOR[NV] = GOOD
picks = [{"symbol": "2330", "price": 900.0, "score": 7}, {"symbol": "2317", "price": 100.0, "score": 6},
         {"symbol": "0000", "price": 0}]
res_c = ss.run_ai_commentary_for_picks(picks, name_map={"2330": "台積電"})
chk("點評：每檔一次呼叫、只用 NVIDIA、缺現價者不呼叫", len(FAKE_CALLS) == 2 and all(c[0] == NV for c in FAKE_CALLS), FAKE_CALLS)
chk("點評：有效回應標示『僅供參考，不參與買賣判定』", "僅供參考，不參與買賣判定" in res_c.get("2330", ""), res_c)
chk("點評：缺現價檔不做推演", "未做 AI 推演" in res_c.get("0000", ""), res_c)

setup()
BEHAVIOR[NV] = "短"                       # 太短（疑似分類器）→ 退回，換 Gemini
BEHAVIOR[GEM] = GOOD
res_c = ss.run_ai_commentary_for_picks([{"symbol": "2330", "price": 900.0}])
chk("點評：NVIDIA 回應過短 → 換 Gemini，且 Gemini 只呼叫一次", [c[0] for c in FAKE_CALLS] == [NV, GEM] and "Gemini" in res_c.get("2330", ""), (FAKE_CALLS, res_c))

setup(nv_key="", gem=False, ds=False)
res_c = ss.run_ai_commentary_for_picks([{"symbol": "2330", "price": 900.0}])
chk("點評：無任何金鑰 → 不呼叫、回傳空結果（與原行為一致）", len(FAKE_CALLS) == 0 and res_c == {}, (FAKE_CALLS, res_c))

# ── 網頁端與排程端共用同一條鏈（dashangdao 不再自己平行呼叫）
chk("warroom_core 提供共用鏈 build_strategy_llm_chain", callable(getattr(wc, "build_strategy_llm_chain", None)))
chk("排程端 _strategy_llm_chain 委派給共用鏈", "build_strategy_llm_chain" in inspect.getsource(ss._strategy_llm_chain))

# ── 還原
wc.discover_nim_models_plain = _saved["discover"]
ss._gemini_pick_model = _saved["pick"]
ss.NVIDIA_API_KEY = _saved["nv_key"]
for k, v in _saved_env.items():
    if v is None:
        os.environ.pop(k, None)
    else:
        os.environ[k] = v

print("全部通過" if not FAIL else f"失敗 {len(FAIL)} 項")
sys.exit(1 if FAIL else 0)
