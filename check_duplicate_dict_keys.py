#!/usr/bin/env python3
"""
check_duplicate_dict_keys.py — 靜態檢查：同一個字典字面值裡不可重複寫同一個鍵。

為什麼需要：Python 字典字面值若重複寫同一個鍵，「後者覆蓋前者」且不會報錯。
曾經發生過：compute_full_signal_for() 回傳字典先放了真實計算的 "value_score"，
後面又寫了一次 "value_score": None，結果算好的價值分被 None 蓋掉，
查3(價值分>=60) 永遠判不通過、而且完全沒有任何錯誤訊息。

規則：
- 掃描專案內所有 .py（略過 .venv / __pycache__ / node_modules）。
- 只看「鍵是常數字串／數字」的字典字面值；**kwargs 展開(None 鍵)不算。
- 任何重複鍵 → 失敗（exit 1）。若兩次的值「不同」會特別標示 ⚠️（真會吃掉資料）。

用法：python check_duplicate_dict_keys.py [檔案或資料夾 ...]
"""
import ast
import os
import sys

SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules", ".mypy_cache"}


def find_duplicates(source, filename="<src>"):
    """回傳 [(lineno, key, first_lineno, differs)]。"""
    tree = ast.parse(source, filename=filename)
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        seen = {}
        for k, v in zip(node.keys, node.values):
            if k is None or not isinstance(k, ast.Constant):
                continue
            key = (type(k.value).__name__, k.value)
            vdump = ast.dump(v)
            if key in seen:
                first_line, first_dump = seen[key]
                out.append((k.lineno, k.value, first_line, first_dump != vdump))
            else:
                seen[key] = (k.lineno, vdump)
    return out


def iter_py_files(paths):
    for p in paths:
        if os.path.isfile(p) and p.endswith(".py"):
            yield p
        elif os.path.isdir(p):
            for root, dirs, files in os.walk(p):
                dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
                for f in sorted(files):
                    if f.endswith(".py"):
                        yield os.path.join(root, f)


def main(argv):
    paths = argv[1:] or ["."]
    bad = 0
    for fp in iter_py_files(paths):
        try:
            with open(fp, encoding="utf-8") as fh:
                src = fh.read()
            dups = find_duplicates(src, fp)
        except SyntaxError as e:
            print(f"⚠️ {fp}: 語法錯誤無法掃描：{e}")
            bad += 1
            continue
        for lineno, key, first, differs in dups:
            flag = "⚠️ 值不同(後者會覆蓋前者)" if differs else "值相同(多餘)"
            print(f"❌ {fp}:{lineno} 字典重複鍵 {key!r}（首次出現在第 {first} 行）{flag}")
            bad += 1
    if bad:
        print(f"\n共 {bad} 處重複鍵，請修正。")
        return 1
    print("✅ 沒有重複的字典鍵")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
