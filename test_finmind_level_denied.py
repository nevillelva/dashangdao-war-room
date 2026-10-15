"""【2026-10-15】FinMind 帳號等級不足（HTTP 400 Your level is register）→ 只送第一次，之後冷卻，不再累積 4xx。"""
import sys, time
import warroom_core as w

fails = []
def check(n, c):
    print(("PASS: " if c else "FAIL: ") + n)
    if not c: fails.append(n)

LEVEL_MSG = "Your level is register. Please update your user level. Detail information:https://finmindtrade.com/analysis/#/Sponsor/sponsor"

class _Res:
    def __init__(self, status, payload):
        self.status_code, self._p = status, payload
        self.text = str(payload)
    def json(self): return self._p

calls = []
class _Sess:
    def get(self, url, params=None, timeout=None):
        calls.append((params.get('dataset'), params.get('token')))
        if params.get('dataset') == 'TaiwanStockHoldingSharesPer':
            return _Res(400, {"msg": LEVEL_MSG, "status": 400, "data": []})
        return _Res(200, {"msg": "success", "status": 200, "data": [{"x": 1}]})

orig_sess, orig_chain = w._SESSION, w._fm_token_chain
w._SESSION = _Sess()
w._fm_token_chain = lambda: ["tokA", "tokB", ""]
URL = 'https://api.finmindtrade.com/api/v4/data'
try:
    w._FM_DATASET_DENIED.clear()
    reasons = []
    for code in ("2330", "2317", "2454", "3008"):
        try:
            w._finmind_get(URL, {'dataset': 'TaiwanStockHoldingSharesPer', 'data_id': code})
        except w.FinMindAPIError as e:
            reasons.append(e.reason)
    n_denied = sum(1 for d, _ in calls if d == 'TaiwanStockHoldingSharesPer')
    check("4 檔股票只送出 1 次請求（原本是 4×3 組憑證×最多4輪）", n_denied == 1)
    check("每次都回 permission_denied（呼叫端走備援）", reasons == ['permission_denied'] * 4)
    r = w._finmind_get(URL, {'dataset': 'TaiwanStockPrice', 'data_id': '2330'})
    check("免費資料集不受影響", bool(r.get('data')))
    check("不同資料集各自獨立冷卻", ('TaiwanStockPrice', 'tokA') in calls)
    # 冷卻到期後會再試一次
    w._FM_DATASET_DENIED[('TaiwanStockHoldingSharesPer', True)] = time.time() - 1
    try:
        w._finmind_get(URL, {'dataset': 'TaiwanStockHoldingSharesPer', 'data_id': '2330'})
    except w.FinMindAPIError:
        pass
    check("冷卻到期後重新確認一次（升級帳號後會自動恢復）",
          sum(1 for d, _ in calls if d == 'TaiwanStockHoldingSharesPer') == 2)
    # 大戶函式：權限不足不再把回看天數加倍重試
    import dashangdao_helpers as h
    calls.clear(); w._FM_DATASET_DENIED.clear()
    h._finmind_get = w._finmind_get
    res = h._fetch_big_holder_with_recursion_impl('2330', None, '2026-10-14')
    check("大戶查詢：第一輪被拒就停（不是 4 輪）", sum(1 for d, _ in calls if d == 'TaiwanStockHoldingSharesPer') == 1)
    check("大戶查詢：回報失敗標籤而非當成有資料", res.get('error') is not None)
finally:
    w._SESSION, w._fm_token_chain = orig_sess, orig_chain
if fails:
    print(f"\n{len(fails)} 項失敗"); sys.exit(1)
print("\n全部通過")
