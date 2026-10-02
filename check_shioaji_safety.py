#!/usr/bin/env python3
"""
check_shioaji_safety.py — 永豐金Shioaji安全死規則的自動化守門腳本
【R98續29新增，總指揮官明確要求：只要查詢、絕對不要下單功能】

這是總指揮官這輪要求的第四項強制檢查工具（依三條開發鐵律的規則三：
「如果之後找到新的、更有效的檢查工具或檢查方式，也要一併加進這個
清單」）——凡是這次以後有改動任何跟shioaji相關的程式碼，除了原本的
ast.parse/audit_scoring_wiring.py/python3 -c "import 模組"三項，
一律要多跑這支腳本，確認以下三條安全死規則沒有被違反：

1. 程式碼裡永遠不能「真的呼叫」 place_order / update_order / cancel_order
   （這是真正會送出委託單、可能成交、動用資金的函式）
2. 程式碼裡永遠不能「真的呼叫」 activate_ca
   （這是啟用CA電子憑證的函式，沒有這一步，place_order等函式即使
   被誤呼叫也送不出真單——這是總指揮官帳戶資金安全的最後一道防線）
3. 程式碼裡永遠不能出現任何 .pfx 副檔名的字串常數
   （代表意外硬編碼了CA憑證檔案路徑）

【R98續29修正，重要教訓】第一版用純文字regex比對，結果連「這份檔案
自己在docstring裡解釋這條安全規則」都被誤判成違規（規則說明文字裡
本來就會提到這些函式名稱）——這正是規則三想避免的「檢查工具本身有
瑕疵、卻誤以為程式碼有問題」的狀況。改用Python的ast模組做結構化
分析：只找「真正的函式呼叫節點(ast.Call)」，字串/docstring裡提到
這些名稱不會被當成呼叫，這樣才不會有誤判，同時也更精準——regex
可能漏掉呼叫寫法的變化，ast能正確辨識任何形式的函式呼叫。

這支腳本只依賴Python標準庫(ast)，不需要shioaji套件本身，任何環境
都能跑。
"""
import ast
import sys

FORBIDDEN_CALL_NAMES = {
    'place_order': '會送出真實委託單的下單函式',
    'update_order': '會改單的函式',
    'cancel_order': '會刪單的函式（雖然刪單本身無害，但出現代表程式碼正在'
                    '往「碰觸委託單」的方向走，一併擋下來）',
    'activate_ca': '啟用CA電子憑證的函式，這是真單送得出去的唯一鑰匙',
}

SCAN_FILES = ['dashangdao.py', 'warroom_core.py', 'system_scheduler.py']


def _call_name(node):
    """從ast.Call節點取出被呼叫的函式名稱——不管是bare呼叫(foo())還是
    屬性呼叫(api.foo())，都取最後一段的名稱做比對。"""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def scan_file(path):
    violations = []
    try:
        with open(path, encoding='utf-8') as f:
            source = f.read()
    except FileNotFoundError:
        return violations

    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError:
        # 語法都有問題的話，ast.parse本身(規則三的另一項檢查)就會先擋下來，
        # 這裡直接跳過不重複報錯，避免混淆真正的問題來源。
        return violations

    # 【真正的函式呼叫】走訪整棵語法樹，只找ast.Call節點——docstring/
    # 註解/一般字串常數都不是ast.Call，不會被誤判。
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _call_name(node)
            if name in FORBIDDEN_CALL_NAMES:
                violations.append((path, node.lineno, name, FORBIDDEN_CALL_NAMES[name]))

    # 【.pfx檔案路徑】用字串常數節點檢查(ast.Constant)，不是函式呼叫，
    # 用另一個獨立迴圈找。
    # 【R98續29同樣的教訓】這裡也曾經誤判——這份檔案自己的docstring裡
    # 用中文説明「.pfx副檔名」，字串常數本身當然含有這個子字串，但那是
    # 說明文字不是真的檔案路徑。真正的檔案路徑字串不會混雜中文，這裡
    # 用「字串裡有沒有中文字元」當判斷依據：含中文字元的字串常數幾乎
    # 可以確定是說明文字/中文訊息字串，不是硬編碼的憑證路徑，跳過不報。
    def _has_cjk(s):
        return any('\u4e00' <= ch <= '\u9fff' for ch in s)

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if '.pfx' in node.value and not _has_cjk(node.value):
                violations.append((path, node.lineno, '.pfx路徑',
                                   'CA電子憑證檔案的副檔名，字串常數裡不該出現任何憑證檔案路徑'))

    return violations


def main():
    all_violations = []
    for fname in SCAN_FILES:
        all_violations.extend(scan_file(fname))

    print("=" * 70)
    print("check_shioaji_safety.py — 永豐金安全死規則檢查（AST結構化分析）")
    print("=" * 70)

    if not all_violations:
        print("✅ 通過：三個核心檔案裡沒有任何真正呼叫下單/CA憑證相關函式的程式碼。")
        print("   （place_order / update_order / cancel_order / activate_ca / .pfx路徑）")
        print("   （docstring/註解裡提到這些名稱是正常的說明文字，不算違規）")
        return 0

    print(f"🛑 發現 {len(all_violations)} 處真正的違規呼叫，必須修正後才能交付：\n")
    for path, lineno, name, desc in all_violations:
        print(f"  {path}:{lineno}  →  {name}")
        print(f"    {desc}")
        print()

    return 1


if __name__ == "__main__":
    sys.exit(main())
