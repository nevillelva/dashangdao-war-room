"""【2026-10-12】FinMind 共用客戶端：依官方 IP 封鎖政策（https://finmind.github.io/BanIPPolicy/）
4xx 不重試、402 換憑證、ip banned 全站冷卻。假的 HTTP 回應，不打網路。python3 test_finmind_client.py"""
import time
import warroom_core as W

FAIL = []


def check(name, cond, extra=""):
    if cond:
        print("✅", name)
    else:
        FAIL.append(name)
        print("❌", name, extra)


class Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body
        self.text = __import__("json").dumps(body)
    def json(self):
        return self._body


class FakeSession:
    def __init__(self, script):
        self.script, self.calls = list(script), []
    def get(self, url, params=None, timeout=None):
        self.calls.append(dict(params or {}))
        return self.script.pop(0) if self.script else Resp(200, {"msg": "success", "data": [{"x": 1}]})


def reset(tokens):
    W._FM_IP_BANNED_UNTIL = 0.0
    W._FM_KEY_EXHAUSTED.clear()
    W.set_finmind_tokens(tokens)


orig_session, orig_sleep = W._SESSION, time.sleep
time.sleep = lambda s: None
try:
    # 1) 正常
    reset(["t1", "t2"])
    W._SESSION = FakeSession([Resp(200, {"msg": "success", "data": [{"a": 1}]})])
    check("正常回應", W._finmind_get("u", {"dataset": "X"})["data"] == [{"a": 1}])

    # 2) 402：不在同一組重試，換下一組
    reset(["t1", "t2"])
    W._SESSION = FakeSession([Resp(402, {"msg": "Requests reach the upper limit."}),
                              Resp(200, {"msg": "success", "data": [{"b": 2}]})])
    r = W._finmind_get("u", {"dataset": "X"})
    toks = [c.get("token", "") for c in W._SESSION.calls]
    check("402 立即換下一組憑證", r["data"] == [{"b": 2}] and toks == ["t1", "t2"], toks)

    # 3) ip banned：立刻停止、不換憑證，之後全站快速失敗、不送請求
    reset(["t1", "t2"])
    W._SESSION = FakeSession([Resp(403, {"msg": "ip banned", "status": 403})])
    try:
        W._finmind_get("u", {"dataset": "X"})
        check("ip banned 拋出", False)
    except W.FinMindAPIError as e:
        check("ip banned 拋出 ip_banned", e.reason == "ip_banned", e.reason)
    check("ip banned 只送 1 次（不換憑證）", len(W._SESSION.calls) == 1, W._SESSION.calls)
    n_before = len(W._SESSION.calls)
    try:
        W._finmind_get("u", {"dataset": "X"})
        check("冷卻中快速失敗", False)
    except W.FinMindAPIError as e:
        check("冷卻中快速失敗、不送請求", e.reason == "ip_banned" and len(W._SESSION.calls) == n_before, e.reason)
    check("冷卻約 30 分鐘", 1700 < W._FM_IP_BANNED_UNTIL - time.time() <= 1800)

    # 4) 其他 4xx（參數錯誤）：不重試、直接回報
    reset(["t1"])
    W._SESSION = FakeSession([Resp(400, {"msg": "parameter error"})])
    try:
        W._finmind_get("u", {"dataset": "X"}, max_retries=3)
        check("400 拋出", False)
    except W.FinMindAPIError as e:
        check("400 不重試（只送 1 次）", e.reason == "api_rejected" and len(W._SESSION.calls) == 1, (e.reason, W._SESSION.calls))

    # 5) 5xx 仍可重試（官方建議 backoff）
    reset(["t1"])
    W._SESSION = FakeSession([Resp(500, {"msg": "server error"}), Resp(200, {"msg": "success", "data": [{"c": 3}]})])
    r = W._finmind_get("u", {"dataset": "X"}, max_retries=3)
    check("5xx 重試後成功", r["data"] == [{"c": 3}] and len(W._SESSION.calls) == 2, W._SESSION.calls)

    # 6) empty_data 照舊
    reset(["t1"])
    W._SESSION = FakeSession([Resp(200, {"msg": "success", "data": []})])
    try:
        W._finmind_get("u", {"dataset": "X"})
    except W.FinMindAPIError as e:
        check("empty_data 照舊回報", e.reason == "empty_data", e.reason)
finally:
    W._SESSION, time.sleep = orig_session, orig_sleep
    W._FM_IP_BANNED_UNTIL = 0.0

print("\n全部通過" if not FAIL else f"\n失敗 {FAIL}")
raise SystemExit(1 if FAIL else 0)
