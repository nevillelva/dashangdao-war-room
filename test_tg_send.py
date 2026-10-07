#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_tg_send.py —— Telegram 統一發送器：長訊息分段(<4096)、重試、429、錯誤回報。不連網。"""
import tg_send
from tg_send import split_message, send_telegram

bad = 0
def chk(name, cond, extra=""):
    global bad
    print("✅" if cond else "❌", name, extra if not cond else "")
    bad += (not cond)

# 1) 短訊息原封不動
chk("短訊息單則", split_message("hello") == ["hello"])
# 2) 長訊息：每則 ≤ 4096、無遺失、有 (i/n)
para = "第一段內容" * 40 + "\n\n"
long_text = para * 40       # 約 9,600 字
parts = split_message(long_text)
chk("長訊息分成多則", len(parts) >= 3, f"parts={len(parts)}")
chk("每則 ≤ 4096", all(len(p) <= 4096 for p in parts), [len(p) for p in parts])
chk("每則 ≤ 3800", all(len(p) <= tg_send.TG_SAFE_LIMIT for p in parts))
chk("有 (i/n) 標記", parts[0].startswith(f"(1/{len(parts)})") and parts[-1].startswith(f"({len(parts)}/{len(parts)})"))
joined = "".join(p.split("\n", 1)[1] for p in parts)
chk("內容不遺失（去掉空白後相等）", "".join(joined.split()) == "".join(long_text.split()))
# 3) 無換行的超長字串硬切
hard = "字" * 9000
hp = split_message(hard)
chk("無換行硬切仍 ≤ 4096", all(len(p) <= 4096 for p in hp) and len(hp) >= 3)
# 4) 單行很長但有空白
# 5) 發送：成功
class R:
    def __init__(self, code, js=None, text="x"): self.status_code, self._js, self.text = code, js or {}, text
    def json(self): return self._js
class S:
    def __init__(self, seq): self.seq, self.calls = list(seq), []
    def post(self, url, json=None, timeout=None):
        self.calls.append(json["text"]); return self.seq.pop(0) if self.seq else R(200)
sleeps = []
ok, det = send_telegram(long_text, token="t", chat_id="c", session=S([]), sleep=sleeps.append)
chk("多則全部成功", ok and len(det) == len(parts) and all(d["ok"] for d in det))
# 6) 429 → 等 retry_after 後重試成功
s = S([R(429, {"parameters": {"retry_after": 2}}), R(200)])
ok, det = send_telegram("abc", token="t", chat_id="c", session=s, sleep=sleeps.append)
chk("429 重試後成功", ok and len(s.calls) == 2 and 2 in sleeps, (ok, s.calls, sleeps))
# 7) 400 不重試
s = S([R(400, text="bad request")])
ok, det = send_telegram("abc", token="t", chat_id="c", session=s, sleep=lambda x: None)
chk("400 不重試且回報失敗", (not ok) and len(s.calls) == 1 and "400" in det[0]["error"])
# 8) 例外重試後仍失敗
class Boom:
    def post(self, *a, **k): raise RuntimeError("net down")
ok, det = send_telegram("abc", token="t", chat_id="c", session=Boom(), sleep=lambda x: None, retries=2)
chk("例外不拋出、回報失敗", (not ok) and "net down" in det[0]["error"])
# 9) 沒設定 token
import os
os.environ.pop("TELEGRAM_BOT_TOKEN", None); os.environ.pop("TELEGRAM_CHAT_ID", None)
ok, det = send_telegram("abc")
chk("沒設定 token → (False, [])", ok is False and det == [])
raise SystemExit(1 if bad else 0)
