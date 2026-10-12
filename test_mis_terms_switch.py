"""【2026-10-12】證交所 MIS 依使用條款第6條停用、改走永豐金 Shioaji 的離線測試。python3 test_mis_terms_switch.py
不連網：把 HTTP 與 Shioaji 都換成假物件，驗證「完全不打 MIS」與「改由永豐金補上（含加權指數 t00）」。"""
import types

import warroom_core as W

FAIL = []


def check(name, cond, extra=""):
    if cond:
        print("✅", name)
    else:
        FAIL.append(name)
        print("❌", name, extra)


# 1) 開關預設關閉
check("MIS_ALLOWED 預設 False", W.MIS_ALLOWED is False)

# 2) 關閉時 fetch_twse_mis_batch 不發任何請求
calls = []


class _NoNet:
    def get(self, *a, **k):
        calls.append(a)
        raise AssertionError("不應呼叫 MIS")


_orig_session = W._SESSION
W._SESSION = _NoNet()
live, diag = W.fetch_twse_mis_batch([("2330", "tse"), ("2330", "otc"), ("t00", "tse")], return_diagnostics=True)
check("MIS 關閉：沒有發出請求", calls == [], calls)
check("MIS 關閉：結果為空", live == {})
check("MIS 關閉：全部列為查不到", diag.get("truly_missing_syms") == ["2330", "t00"], diag)
check("MIS 關閉：診斷標記條款停用", diag.get("mis_disabled_terms") is True)
check("MIS 關閉：非診斷模式回空 dict", W.fetch_twse_mis_batch([("2330", "tse")]) == {})

# 3) fetch_live_quotes_resilient：盤中 → 全部交給永豐金；保留漲跌點數
_orig_sj, _orig_sess_now = W.fetch_shioaji_snapshot, W.is_twse_trading_session_now
asked = []


def _fake_sj(symbols, key, secret, timeout=15):
    asked.append(sorted(symbols))
    return {"2330": {"price": 1000.0, "change_pct": -1.5, "change_pt": -15.0, "volume": 1, "time": "10:00:00"},
            "t00": {"price": 23000.0, "change_pct": 0.5, "change_pt": 115.0, "volume": 0, "time": "10:00:00"}}


W.fetch_shioaji_snapshot = _fake_sj
W.is_twse_trading_session_now = lambda: True
live, diag = W.fetch_live_quotes_resilient([("2330", "tse"), ("t00", "tse")], "k", "s", circuit_breaker_enabled=False)
check("盤中：永豐金被呼叫一次且含 t00", asked == [["2330", "t00"]], asked)
check("盤中：2330 來源 shioaji", live.get("2330", {}).get("source") == "shioaji", live.get("2330"))
check("盤中：2330 漲跌點數保留（負）", live.get("2330", {}).get("change_pt") == -15.0)
check("盤中：t00 有報價與漲跌點數", live.get("t00", {}).get("change_pt") == 115.0, live.get("t00"))
check("盤中：MIS 沒被呼叫", calls == [])
check("盤中：沒有 MIS 重試", diag.get("retry_count") == 0, diag.get("retry_count"))

# 4) 盤後 → 不打永豐金（沿用呼叫端快取），也不打 MIS
asked.clear()
W.is_twse_trading_session_now = lambda: False
live, diag = W.fetch_live_quotes_resilient([("2330", "tse")], "k", "s")
check("盤後：不呼叫永豐金", asked == [], asked)
check("盤後：標記跳過備援", diag.get("skipped_fallback_market_closed") is True)
check("盤後：MIS 沒被呼叫", calls == [])
W.fetch_shioaji_snapshot, W.is_twse_trading_session_now, W._SESSION = _orig_sj, _orig_sess_now, _orig_session

# 5) 漲跌點數：方向取 change_rate
check("漲跌點數：負", W.shioaji_change_pt(15, -1.2) == -15.0)
check("漲跌點數：change_price 已帶負號也正確", W.shioaji_change_pt(-15, -1.2) == -15.0)
check("漲跌點數：正", W.shioaji_change_pt(-3, 0.4) == 3.0)
check("漲跌點數：平盤", W.shioaji_change_pt(0, 0) == 0.0)
check("漲跌點數：缺值回 None", W.shioaji_change_pt(None, 1.0) is None)
check("漲跌點數：非數字回 None", W.shioaji_change_pt("x", 1.0) is None)

# 6) 加權指數合約：新版 IX0001 優先，舊版 Indexs.TSE['001'] 備援
new_api = types.SimpleNamespace(contracts=types.SimpleNamespace(get=lambda c: "NEW" if c == "IX0001" else None))
check("合約：新版 IX0001", W._shioaji_taiex_contract(new_api) == "NEW")


class _TSE(dict):
    pass


old_tse = _TSE({"001": "OLD"})
old_api = types.SimpleNamespace(Contracts=types.SimpleNamespace(Indexs=types.SimpleNamespace(TSE=old_tse)))
check("合約：舊版 Indexs.TSE['001']", W._shioaji_taiex_contract(old_api) == "OLD")
check("合約：都沒有 → None", W._shioaji_taiex_contract(types.SimpleNamespace()) is None)
check("指數代碼對應 t00", {"001", "IX0001"} <= W._TAIEX_SHIOAJI_CODES and W.TAIEX_SYMBOL == "t00")

# 7) T86（www 三大法人）停用：不發請求、回空 dict
check("T86_ALLOWED 預設 False", W.T86_ALLOWED is False)
_calls_t86 = []
_orig_get = W.requests.get
W.requests.get = lambda *a, **k: _calls_t86.append(a) or (_ for _ in ()).throw(AssertionError("不應呼叫 T86"))
check("T86 停用：回空 dict", W.fetch_twse_t86_snapshot("20261008") == {})
check("T86 停用：沒有發出請求", _calls_t86 == [], _calls_t86)
W.requests.get = _orig_get

print("\n全部通過" if not FAIL else f"\n失敗 {FAIL}")
raise SystemExit(1 if FAIL else 0)
