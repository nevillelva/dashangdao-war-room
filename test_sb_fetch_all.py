#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_sb_fetch_all.py —— PostgREST 1000 列分頁：資料 2500 筆要完整取回；剛好 1000 筆、0 筆、失敗都要正確。"""
import warroom_core as wc

class Q:
    def __init__(self, rows): self.rows, self.lo, self.hi = rows, 0, None
    def range(self, lo, hi): self.lo, self.hi = lo, hi; return self
    def execute(self): return type("R", (), {"data": self.rows[self.lo:self.hi + 1]})()

bad = 0
def chk(n, c, e=""):
    global bad; print("✅" if c else "❌", n, "" if c else e); bad += (not c)

for n in (0, 1, 999, 1000, 1001, 2500, 3000):
    rows = [{"id": i} for i in range(n)]
    got = wc.sb_fetch_all(lambda: Q(rows))
    chk(f"{n} 筆完整取回", len(got) == n and [r["id"] for r in got] == list(range(n)), len(got))

class Boom(Q):
    def execute(self):
        if self.lo >= 1000: raise RuntimeError("page2 failed")
        return super().execute()
try:
    wc.sb_fetch_all(lambda: Boom([{"id": i} for i in range(2500)]))
    chk("第二頁失敗要丟例外（不能回傳不完整資料）", False)
except RuntimeError:
    chk("第二頁失敗要丟例外（不能回傳不完整資料）", True)
raise SystemExit(1 if bad else 0)
