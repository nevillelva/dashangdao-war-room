"""finmind_quota 離線測試（假 HTTP）。python3 test_finmind_quota.py"""
import finmind_quota as Q

FAIL = []


def check(name, cond, extra=""):
    if cond:
        print("✅", name)
    else:
        FAIL.append(name)
        print("❌", name, extra)


check("token 拆分（去空白、去空值）", Q.split_tokens(" a, b ,,") == ["a", "b"])
seen_headers = []
def http_get(url, headers):
    seen_headers.append(headers)
    tok = headers["Authorization"].split()[-1]
    if tok == "good":
        return 200, {"user_count": 123, "api_request_limit": 600}
    if tok == "wrapped":
        return 200, {"data": {"user_count": 590, "api_request_limit": 600}}
    if tok == "bad":
        return 401, None
    raise TimeoutError()
rows = Q.probe(["good", "wrapped", "bad", "slow"], http_get)
check("正常回應解析", rows[0]["ok"] and rows[0]["used"] == 123 and rows[0]["limit"] == 600, rows[0])
check("外層包裝也能解析", rows[1]["ok"] and rows[1]["used"] == 590, rows[1])
check("HTTP 錯誤記為失敗", not rows[2]["ok"] and rows[2]["err"] == "HTTP 401", rows[2])
check("例外記為失敗（只記型別）", not rows[3]["ok"] and rows[3]["err"] == "TimeoutError", rows[3])
check("使用 Bearer 標頭", seen_headers[0] == {"Authorization": "Bearer good"})
txt, hot = Q.summarize(rows)
check("摘要：合計與各組", "合計 713/1200" in txt and "第1組 123/600" in txt and "第3組 查詢失敗" in txt, txt)
check("任一組 ≥80% 標記為高用量", hot is True)
check("摘要不含 token 值", "good" not in txt and "wrapped" not in txt and "slow" not in txt, txt)
check("未設定 token", Q.summarize([])[0] == "FinMind 額度：未設定 token")

print("\n全部通過" if not FAIL else f"\n失敗 {FAIL}")
raise SystemExit(1 if FAIL else 0)
