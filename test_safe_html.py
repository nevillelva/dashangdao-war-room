"""safe_html 與被修改渲染函式的跳脫測試（不連網、不連 Supabase）。

執行：/home/claude/venv311/bin/python test_safe_html.py
"""
import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from safe_html import esc  # noqa: E402

FAIL = []


def check(name, cond, extra=""):
    if not cond:
        FAIL.append(name)
        print("❌", name, extra)
    else:
        print("✅", name)


PAYLOAD = "<script>alert(1)</script>"

# ---- esc 本身 ----
check("esc: <script> 被跳脫", esc(PAYLOAD) == "&lt;script&gt;alert(1)&lt;/script&gt;", esc(PAYLOAD))
check("esc: 雙引號被跳脫", esc('a"b') == "a&quot;b", esc('a"b'))
check("esc: 單引號被跳脫", esc("a'b") == "a&#x27;b", esc("a'b"))
check("esc: & 先跳脫、不重複跳脫既有實體以外的字", esc("A&B") == "A&amp;B", esc("A&B"))
check("esc: None 視為空字串", esc(None) == "")
check("esc: 數字轉字串", esc(12.5) == "12.5", esc(12.5))
check("esc: 中文與 emoji 原樣保留", esc("🔥台積電") == "🔥台積電", esc("🔥台積電"))

# ---- premarket_tab：bias_badge 與重點個股標題 ----
import premarket_tab as PT  # noqa: E402


class _StubST:
    def __init__(self):
        self.calls = []

    def markdown(self, body, **kw):
        self.calls.append(("markdown", body, kw))

    def caption(self, body, **kw):
        self.calls.append(("caption", body, kw))

    def __getattr__(self, name):  # 其他 st.* 一律no-op
        return lambda *a, **k: None


_real_st = PT.st
try:
    stub = _StubST()
    PT.st = stub
    badge = PT.bias_badge(PAYLOAD)
    check("bias_badge: 非預期偏向字串被跳脫", "<script>" not in badge and "&lt;script&gt;" in badge, badge)
    check("bias_badge: 正常偏向仍保留顏色標籤", "color:" in PT.bias_badge("偏多") and "偏多</span>" in PT.bias_badge("偏多"))

    stub.calls.clear()
    PT._pick_card({
        "symbol": "2330", "name": PAYLOAD, "sector": "<img src=x onerror=1>", "own": False,
        "bias": "偏多", "events": [], "why": None, "risk": None, "tech": None,
    })
    heads = [c[1] for c in stub.calls if c[0] == "markdown"]
    head_html = heads[0] if heads else ""
    check("_pick_card: 股名與族群在標題中被跳脫", "<script>" not in head_html and "<img" not in head_html, head_html)
    check("_pick_card: 跳脫後文字仍可讀", "&lt;script&gt;alert(1)&lt;/script&gt;（2330）" in head_html, head_html)
finally:
    PT.st = _real_st

# ---- dashangdao_helpers：分點型態區塊、處置股徽章 ----
# 本測試只驗證純 HTML 組字，不呼叫 OpenAI；若環境沒裝 openai 套件，只放一個假模組讓 import 通過。
try:
    import openai  # noqa: F401
except ImportError:
    import types
    _fake_openai = types.ModuleType("openai")
    _fake_openai.OpenAI = object
    sys.modules["openai"] = _fake_openai
import dashangdao_helpers as H  # noqa: E402

rec = {
    "log_date": "2026-11-13", "verdict": "flip", "hist_days": 12,
    "flip_pct": 30, "build_pct": 10, "foreign_pct": 5,
    "flip_buy": 1200, "flip_sell": 0, "build_buy": 0, "build_sell": 0, "build_net_win": 0,
    "detail": {
        "flip": [{"b": PAYLOAD, "n": 800, "why": "x"}],
        "build": [], "foreign": [], "cnt": {}, "window": 5,
    },
}
block = H._fmt_broker_style_block({"broker_style": {"latest": rec, "hist": []}, "vol": 0})
check("分點型態: 分點名稱被跳脫", "<script>" not in block and "&lt;script&gt;alert(1)&lt;/script&gt;" in block,
      block[:200])
check("分點型態: 無資料時仍是固定文字", "分點型態" in H._fmt_broker_style_block({}))

_orig_lists = H._get_cached_disposal_attention_lists
_orig_status = H.check_disposal_attention_status
try:
    H._get_cached_disposal_attention_lists = lambda: (None, None, None)
    H.check_disposal_attention_status = lambda sym, a, t, x: {"disposal": True, "detail": PAYLOAD}
    badge = H.get_disposal_attention_badge("2330")
    check("處置股徽章: 官方說明文字被跳脫", "<script>" not in badge and "&lt;script&gt;" in badge, badge[:200])
    H.check_disposal_attention_status = lambda sym, a, t, x: {"attention": True, "detail": "一般說明"}
    badge2 = H.get_disposal_attention_badge("2330")
    check("注意股徽章: 正常說明保留", "一般說明" in badge2 and "注意股" in badge2, badge2[:200])
finally:
    H._get_cached_disposal_attention_lists = _orig_lists
    H.check_disposal_attention_status = _orig_status

# ---- dashangdao.py：無法直接 import（啟動即跑 Streamlit 應用），改用 AST 檢查關鍵呼叫 ----
src_path = os.path.join(HERE, "dashangdao.py")
tree = ast.parse(open(src_path, encoding="utf-8").read())
imports_esc = any(
    isinstance(n, ast.ImportFrom) and n.module == "safe_html" and any(a.name == "esc" for a in n.names)
    for n in tree.body
)
check("dashangdao.py: 從 safe_html 匯入 esc", imports_esc)


def _calls_esc_on_get(node, key):
    """節點內是否有 esc(c.get(key)) 形式的呼叫。"""
    for sub in ast.walk(node):
        if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) and sub.func.id == "esc"
                and sub.args and isinstance(sub.args[0], ast.Call)
                and isinstance(sub.args[0].func, ast.Attribute) and sub.args[0].func.attr == "get"
                and sub.args[0].args and isinstance(sub.args[0].args[0], ast.Constant)
                and sub.args[0].args[0].value == key):
            return True
    return False


card_fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "render_stock_card_ui")
check("render_stock_card_ui: 股名 c.get('name') 經 esc", _calls_esc_on_get(card_fn, "name"))
check("render_stock_card_ui: 股號 c.get('code') 經 esc", _calls_esc_on_get(card_fn, "code"))

print()
if FAIL:
    print(f"失敗 {len(FAIL)} 項：", FAIL)
    sys.exit(1)
print("全部通過")
