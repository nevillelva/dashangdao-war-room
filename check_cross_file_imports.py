"""
【R98續118新增】跨檔案import完整性檢查——用真正的AST解析(不是regex)，
比對dashangdao_helpers.py裡定義的所有頂層函式，跟dashangdao.py實際
「呼叫」了哪些、又「import」了哪些，找出「helpers有定義、main有呼叫、
但import清單漏掉」的函式。這類問題ast.parse跟import測試都抓不到
(語法完全合法，只是名字在main的命名空間裡不存在，要真正執行到那一行
才會NameError)。

用法：python3 check_cross_file_imports.py
之後任何在dashangdao_helpers.py新增函式、且要在dashangdao.py呼叫的，
部署前都應該跑一次這支腳本。
"""
import ast

def main():
    helpers_src = open('dashangdao_helpers.py', encoding='utf-8').read()
    helpers_tree = ast.parse(helpers_src)
    helper_funcs = {node.name for node in ast.walk(helpers_tree)
                    if isinstance(node, ast.FunctionDef) and node.col_offset == 0}

    main_src = open('dashangdao.py', encoding='utf-8').read()
    main_tree = ast.parse(main_src)

    imported_names = set()
    for node in ast.walk(main_tree):
        if isinstance(node, ast.ImportFrom) and node.module == 'dashangdao_helpers':
            for alias in node.names:
                imported_names.add(alias.name)

    main_own_funcs = {node.name for node in ast.walk(main_tree) if isinstance(node, ast.FunctionDef)}

    called_names = set()
    for node in ast.walk(main_tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            called_names.add(node.func.id)

    missing = sorted(
        fn for fn in helper_funcs
        if not fn.startswith('_') and fn in called_names
        and fn not in imported_names and fn not in main_own_funcs
    )

    print(f"helpers共{len(helper_funcs)}個頂層函式，dashangdao.py目前import了{len(imported_names)}個")
    if missing:
        print(f"\n❌ 發現{len(missing)}個遺漏的import：")
        for fn in missing:
            print(" -", fn)
        raise SystemExit(1)
    else:
        print("✅ 沒有遺漏，helpers裡被main呼叫的函式都有正確import。")

if __name__ == "__main__":
    main()
