"""【2026-10-13】回歸測試：網頁端 Finnhub token 必須注入到 dashangdao_helpers。
根因：_get_overnight_macro_uncached 讀 globals()['FINNHUB_TOKEN']，dashangdao.py 若沒有
dashangdao_helpers.FINNHUB_TOKEN = ... 這行，token 永遠是空字串（finnhub_web 0/288）。
離線靜態檢查，不需要網路或金鑰。"""
import ast
import sys


def _assigned_attrs(path, module_name):
    tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if (isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name)
                        and t.value.id == module_name):
                    out.add(t.attr)
    return out


def main():
    failures = []
    injected = _assigned_attrs("dashangdao.py", "dashangdao_helpers")
    for name in ("FINNHUB_TOKEN", "FINMIND_TOKENS", "SHIOAJI_API_KEY", "NVIDIA_API_KEY"):
        if name not in injected:
            failures.append(f"dashangdao.py 沒有注入 dashangdao_helpers.{name}")
    src = open("dashangdao_helpers.py", encoding="utf-8").read()
    if "globals().get('FINNHUB_TOKEN'" not in src:
        failures.append("dashangdao_helpers 的 Finnhub 讀取方式改變了，請同步檢查注入")
    if failures:
        for f in failures:
            print("FAIL:", f)
        return 1
    print("PASS: Finnhub token 注入橋接完整")
    return 0


if __name__ == "__main__":
    sys.exit(main())
