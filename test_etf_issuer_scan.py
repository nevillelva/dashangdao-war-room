"""投信公告解析與 robots 檢查的離線測試（不連網）。python3 test_etf_issuer_scan.py"""
import etf_issuer_scan as S

FAIL = []


def check(name, cond, extra=""):
    if cond:
        print("✅", name)
    else:
        FAIL.append(name)
        print("❌", name, extra)


SAMPLE = """元大台灣高股息基金 (0056) 分配收益公告
收益分配決定做成日 114/12/31 除息日 115/01/22 發放日 115/02/11
每受益權單位實際配發金額 0.866 元
分配收益組成占比（稅後淨額）：股利所得 25.98%  利息所得 0.00%  收益平準金 0.00%  已實現資本利得 74.02%"""

check("組成解析：股利所得 25.98%", abs(S.parse_composition(SAMPLE)["div"] - 0.2598) < 1e-9)
check("組成四項合計 100%", abs(sum(S.parse_composition(SAMPLE).values()) - 1.0) < 1e-9)
check("除息日民國年轉西元", S.parse_ex_date(SAMPLE) == "2026-01-22", S.parse_ex_date(SAMPLE))
check("每單位金額", S.parse_amount(SAMPLE) == 0.866)
check("合計不是100%直接不收", S.parse_composition(SAMPLE.replace("74.02%", "50.00%")) is None)
check("沒有股利所得不收", S.parse_composition("利息所得 100%") is None)
check("西元除息日", S.parse_ex_date("除息日 2026/07/21") == "2026-07-21")
check("無除息日回 None", S.parse_ex_date("沒有日期") is None)


class R:
    def __init__(self, status, text="", content=b""):
        self.status_code, self.text, self.content = status, text, content


def fake_get_robots_disallow(url, timeout=None):
    if url.endswith("/robots.txt"):
        return R(200, "User-agent: *\nDisallow: /")
    raise AssertionError("不應該抓取")


def fake_get_robots_404(url, timeout=None):
    if url.endswith("/robots.txt"):
        return R(404)
    return R(200, content=b"x")


def fake_get_robots_error(url, timeout=None):
    raise S.requests.ConnectionError("x")


check("robots 禁止 → 不抓", S.robots_allows("https://x.example/a.pdf", fetch=fake_get_robots_disallow) is False)
check("robots 404 → 允許", S.robots_allows("https://x.example/a.pdf", fetch=fake_get_robots_404) is True)
check("robots 讀取失敗 → 不允許（不抓）", S.robots_allows("https://x.example/a.pdf", fetch=fake_get_robots_error) is False)
res, why = S.scan_pdf("https://x.example/a.pdf", "0056", fetch=fake_get_robots_disallow)
check("scan_pdf 遇 robots 禁止直接退回", res is None and "robots" in why, why)
res2, why2 = S.scan_pdf("http://x.example/a.pdf", "0056", fetch=fake_get_robots_404)
check("非 https 退回", res2 is None and why2 == "非 https")

print("\n全部通過" if not FAIL else f"\n失敗 {len(FAIL)} 項：{FAIL}")
raise SystemExit(1 if FAIL else 0)
