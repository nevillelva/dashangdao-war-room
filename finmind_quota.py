"""【2026-10-12】FinMind 額度探測：官方 GET https://api.web.finmindtrade.com/v2/user_info（Authorization: Bearer {token}），
回 user_count（本期已用）與 api_request_limit（上限）。來源：https://finmind.github.io/llms.txt「API Usage Count」。

只記錄「第幾組 token：已用／上限」，絕不印出 token 值。純函式＋可注入 http_get，可離線測試。
"""
USER_INFO_URL = "https://api.web.finmindtrade.com/v2/user_info"


def _find(obj, key):
    """在巢狀 dict/list 中找第一個符合 key 的值（回應格式若有外層包裝也找得到）。"""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            r = _find(v, key)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find(v, key)
            if r is not None:
                return r
    return None


def split_tokens(raw):
    return [t.strip() for t in str(raw or "").split(",") if t.strip()]


def probe(tokens, http_get):
    """http_get(url, headers) -> (status_code, json_or_None)。回傳每組結果 list[dict]。"""
    out = []
    for i, tok in enumerate(tokens, 1):
        row = {"idx": i, "used": None, "limit": None, "ok": False, "err": ""}
        try:
            code, js = http_get(USER_INFO_URL, {"Authorization": f"Bearer {tok}"})
            if code != 200 or not isinstance(js, (dict, list)):
                row["err"] = f"HTTP {code}"
            else:
                used, lim = _find(js, "user_count"), _find(js, "api_request_limit")
                row["used"] = int(used) if used is not None else None
                row["limit"] = int(lim) if lim is not None else None
                row["ok"] = row["used"] is not None and row["limit"] is not None
                if not row["ok"]:
                    row["err"] = "回應缺少 user_count／api_request_limit"
        except Exception as e:
            row["err"] = type(e).__name__
        out.append(row)
    return out


def summarize(rows):
    """回傳 (文字摘要, 是否有任一組使用率 ≥80%)。"""
    parts, hot = [], False
    for r in rows:
        if r["ok"]:
            pct = (r["used"] / r["limit"] * 100) if r["limit"] else 0
            hot = hot or pct >= 80
            parts.append(f"第{r['idx']}組 {r['used']}/{r['limit']}（{pct:.0f}%）")
        else:
            parts.append(f"第{r['idx']}組 查詢失敗（{r['err']}）")
    total_used = sum(r["used"] for r in rows if r["ok"])
    total_lim = sum(r["limit"] for r in rows if r["ok"])
    head = f"FinMind 額度：已設定 {len(rows)} 組；合計 {total_used}/{total_lim}" if rows else "FinMind 額度：未設定 token"
    return head + ("｜" + "；".join(parts) if parts else ""), hot
