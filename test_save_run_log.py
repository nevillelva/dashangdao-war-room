"""save_run_log 離線測試：遮蔽密鑰、摘取尾段與重點行。python3 test_save_run_log.py"""
import save_run_log as L

FAIL = []


def check(name, cond, extra=""):
    if cond:
        print("✅", name)
    else:
        FAIL.append(name)
        print("❌", name, extra)


env = {"FINMIND_TOKEN": "tokAAAAAAA1, tokBBBBBBB2", "SUPABASE_KEY": "sbkey-xyz-123456", "TELEGRAM_BOT_TOKEN": ""}
sec = L.secret_values(env)
check("逗號分隔的多組 token 都納入", "tokAAAAAAA1" in sec and "tokBBBBBBB2" in sec and "sbkey-xyz-123456" in sec, sec)
txt = "連線 sbkey-xyz-123456 與 tokBBBBBBB2 成功"
out = L.redact(txt, sec)
check("已知密鑰值被遮蔽", "sbkey" not in out and "tokBBBBBBB2" not in out and out.count("***") == 2, out)
jwt = "auth eyJhbGciOiJIUzI1NiJ9.eyJyb2xlIjoic2VydmljZV9yb2xlIn0.abcdefgHIJK done"
check("JWT 樣式被遮蔽", "eyJ" not in L.redact(jwt, []), L.redact(jwt, []))
gh = "token ghp_" + "a" * 36 + " end"
check("GitHub token 樣式被遮蔽", "ghp_" not in L.redact(gh, []))
tg = "bot 123456789:" + "A" * 35
check("Telegram token 樣式被遮蔽", "123456789:" not in L.redact(tg, []))
check("一般中文與數字不受影響", L.redact("寫入 391 檔共 281906 筆", []) == "寫入 391 檔共 281906 筆")

lines = [f"line {i}" for i in range(500)]
lines[10] = "❌ 第 10 行失敗"
lines[450] = "⚠️ 尾段警告"
tail, hl = L.select_lines(lines, tail=100, hl_max=50)
check("尾段 100 行", len(tail) == 100 and tail[-1] == "line 499")
check("尾段之前的重點行被保留", hl == ["❌ 第 10 行失敗"], hl)
check("尾段內的重點行不重複列", "⚠️ 尾段警告" not in hl)

rep = L.build_report("a\nsbkey-xyz-123456\nb", "etf_price_sync", "success", "123", sec)
check("報告摘要格式", rep["summary"] == "runlog:etf_price_sync:success" and rep["run_id"] == "123")
check("報告內容已遮蔽", "sbkey" not in str(rep["report"]), rep["report"])

print("\n全部通過" if not FAIL else f"\n失敗 {FAIL}")
raise SystemExit(1 if FAIL else 0)
